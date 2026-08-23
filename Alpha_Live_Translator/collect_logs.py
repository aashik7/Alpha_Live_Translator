r"""Collect everything needed to diagnose a problem, into one file (item 49).

WHY THIS EXISTS
---------------
Once this is installed on someone else's machine there is no terminal to open,
no debugger to attach and no code to change, and uninstalling to "start clean"
throws away the evidence of whatever went wrong. So the operator needs one
button that gathers the whole trail into a single file they can send back.

Run from the Start Menu shortcut, or:

    python collect_logs.py [--with-audio] [--runs N] [--out DIR] [--no-dialog]

ONE COMMAND, FROM ANYWHERE, THAT PRINTS THE PATH
------------------------------------------------
For driving this from a script or a support call, where the person needs to be
told exactly which file to send. Finds the app whether it was installed or
extracted portable, and prints the bundle's full path last:

    $app = @("$env:LOCALAPPDATA\Programs\Alpha Live Translator",
             "$env:USERPROFILE\Alpha Live Translator") |
           Where-Object { Test-Path "$_\app\collect_logs.py" } | Select-Object -First 1
    $out = & "$app\python\python.exe" "$app\app\collect_logs.py" `
             --with-audio --runs 5 --no-dialog
    ($out | Select-String '^LOG_BUNDLE=(.+)$').Matches.Groups[1].Value

`--no-dialog` matters there: without it the message box blocks the command
until somebody clicks it, and the caller never reaches the line that prints the
path. Measured on a real bundle, that is the difference between 4.8 seconds and
waiting forever.

WHAT GOES IN
------------
Everything the app writes, in full:

    troubleshooting/runs/<run-id>/   accuracy, artifacts, evidence_streams,
                                    health, logs, transcripts, translation
    troubleshooting/latest/          the convenience copies
    logs/                            crash guard, and the console log that
                                     exists only because pythonw has no console
    a summary.txt                    versions, machine, what was included

The recorded audio (`audio_temp/`) is EXCLUDED by default. It is about 1.1 MB of
every 1.5 MB per session and is almost never what a diagnosis turns on; without
it a session is roughly 400 KB, which sends by email. `--with-audio` includes it
when the question really is about the sound.

WHAT IS KEPT OUT
----------------
`.env` is never added, and any value that looks like one of its keys is redacted
from every text file that goes in. A diagnostic bundle is emailed, forwarded and
attached to tickets; it must not be a way to leak the credentials.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import sys
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent

# Text-ish files get scanned for secrets before they are added. Everything else
# (wav, png) is added as-is; a key cannot hide in a waveform.
TEXT_SUFFIXES = {".txt", ".log", ".jsonl", ".json", ".md", ".csv", ".ini", ".cfg", ".py"}


def secrets_to_redact() -> list[str]:
    """The live key values, read from `.env` so nothing is hard-coded here."""
    values: list[str] = []
    env_path = APP_ROOT / ".env"
    if not env_path.is_file():
        return values
    try:
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            _name, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            # Short values are settings, not secrets, and redacting "auto" would
            # mangle half the logs.
            if len(value) >= 16:
                values.append(value)
    except Exception:
        pass
    return values


def redact(data: bytes, secrets: list[str]) -> bytes:
    if not secrets:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for secret in secrets:
        text = text.replace(secret, "<redacted>")
    return text.encode("utf-8")


def windows_ui_language() -> str:
    """The Windows display language as "ja" or "en", or "" if unreadable.

    A deliberate copy of `alpha/ui/strings.py`'s version rather than an import,
    for the reason in `ui_language()` below. `GetUserDefaultUILanguage` is the
    USER's display language, which is not the same thing as the system's or the
    regional format -- on the machine this was written on the three disagree
    (user English, system Japanese), and picking the wrong one reports the
    wrong answer with total confidence.
    """
    try:
        import ctypes

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "ja" if (int(lcid) & 0x3FF) == 0x11 else "en"
    except Exception:
        return ""


def ui_language() -> str:
    """Which language the window was in, and why.

    Read straight from the file rather than by importing alpha.ui.strings:
    this script has to keep working even when the app itself cannot start,
    which is exactly when a bundle gets collected.

    The order below mirrors `strings._resolve_language`. It has to: this
    function used to stop after the saved choice and report "never changed from
    the shipped default", which was true before the app learned to follow
    Windows and quietly wrong afterwards. On a Japanese Windows with nothing
    chosen it would have said the window was English while it was Japanese --
    the one question a diagnostic bundle exists to answer.
    """
    override = os.environ.get("ALPHA_UI_LANGUAGE", "").strip()
    if override:
        return override.lower() + "  (forced by ALPHA_UI_LANGUAGE)"
    try:
        data = json.loads(
            (APP_ROOT / "user_settings.json").read_text(encoding="utf-8")
        )
        chosen = str(data.get("ui_language", "")).strip()
        if chosen:
            return chosen.lower() + "  (chosen in the app)"
    except Exception:
        pass
    from_windows = windows_ui_language()
    if from_windows:
        return from_windows + "  (nothing chosen; following the Windows display language)"
    return "en  (nothing chosen, and the Windows display language could not be read)"


def newest_runs(limit: int) -> list[Path]:
    runs_dir = APP_ROOT / "troubleshooting" / "runs"
    if not runs_dir.is_dir():
        return []
    runs = [p for p in runs_dir.iterdir() if p.is_dir() and not p.name.startswith("_")]
    runs.sort(key=lambda p: p.name, reverse=True)
    return runs[:limit] if limit > 0 else runs


def summary(included: list[str], skipped_audio: bool) -> str:
    lines = [
        "Alpha Live Translator - diagnostic bundle",
        "collected    " + datetime.datetime.now().isoformat(timespec="seconds"),
        "app folder   " + str(APP_ROOT),
        "python       " + sys.version.split()[0],
        "executable   " + str(sys.executable),
        "windows      " + platform.platform(),
        "machine      " + platform.machine(),
        "ui language  " + ui_language(),
        "",
        "audio        " + ("EXCLUDED (re-run with --with-audio)" if skipped_audio else "included"),
        "",
        "contents:",
    ]
    lines += ["  " + name for name in included]
    lines += [
        "",
        "API keys are redacted and .env is not included.",
    ]
    return "\n".join(lines) + "\n"


def add_tree(
    zf: zipfile.ZipFile,
    root: Path,
    arc_prefix: str,
    secrets: list[str],
    with_audio: bool,
) -> int:
    count = 0
    if not root.exists():
        return count
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if not with_audio and "audio_temp" in rel.parts:
            continue
        if path.name == ".env":
            continue
        try:
            data = path.read_bytes()
        except Exception:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            data = redact(data, secrets)
        zf.writestr(f"{arc_prefix}/{rel.as_posix()}", data)
        count += 1
    return count


# Set by --no-dialog. A module-level switch rather than a parameter threaded
# through every call site, because `tell` is also reached from the failure path.
_SHOW_DIALOG = True


def tell(message: str, *, error: bool = False) -> None:
    """Say it on screen, whichever screen there is.

    The dialog exists because the Start Menu shortcut runs `pythonw.exe` and has
    no console to print to. From a script it is the opposite problem: a modal
    box blocks the command until somebody clicks it, and the caller never gets
    to print the path.

    Which case this is cannot be detected reliably -- measured on this machine,
    `sys.stdout.isatty()` and `GetConsoleWindow()` BOTH report "no console" when
    the output is merely being captured by the caller. So it is not detected at
    all: `--no-dialog` says so explicitly, and the shortcut simply never passes
    it.
    """
    print(message, flush=True)
    if not _SHOW_DIALOG:
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if error:
            messagebox.showerror("Alpha Live Translator", message)
        else:
            messagebox.showinfo("Alpha Live Translator", message)
        root.destroy()
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Alpha diagnostic logs.")
    parser.add_argument("--with-audio", action="store_true", help="include the recorded audio")
    parser.add_argument("--runs", type=int, default=5, help="newest N sessions (0 = all)")
    parser.add_argument("--out", default="", help="where to write the zip (default: Desktop)")
    parser.add_argument(
        "--no-dialog",
        action="store_true",
        help="print only; do not pop the message box (use when scripting this)",
    )
    args = parser.parse_args()

    global _SHOW_DIALOG
    _SHOW_DIALOG = not args.no_dialog

    out_dir = Path(args.out) if args.out else Path.home() / "Desktop"
    if not out_dir.is_dir():
        out_dir = Path.home()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"AlphaLogs-{stamp}.zip"

    secrets = secrets_to_redact()
    included: list[str] = []

    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for run in newest_runs(args.runs):
                n = add_tree(zf, run, f"runs/{run.name}", secrets, args.with_audio)
                included.append(f"runs/{run.name}  ({n} files)")

            settings = APP_ROOT / "user_settings.json"
            if settings.is_file():
                zf.writestr(
                    "user_settings.json",
                    redact(settings.read_bytes(), secrets),
                )
                included.append("user_settings.json")

            for name in ("logs", "troubleshooting/latest", "debug"):
                src = APP_ROOT / name
                n = add_tree(zf, src, name.replace("/", "_"), secrets, args.with_audio)
                if n:
                    included.append(f"{name}  ({n} files)")

            if not included:
                included.append("(nothing found - has a session been run yet?)")
            zf.writestr("summary.txt", summary(included, not args.with_audio))
    except Exception as exc:
        tell(f"Could not create the log bundle:\n\n{exc}", error=True)
        raise SystemExit(1)

    size_mb = out_path.stat().st_size / 1024 / 1024
    tell(
        "Diagnostic logs collected.\n\n"
        f"{out_path}\n\n"
        f"{size_mb:.1f} MB. Send this file to whoever supports the app.\n"
        "API keys have been removed from it."
    )
    # Last line, on its own, always the same shape. Whatever is driving this --
    # a person reading the console or a script parsing it -- gets the full path
    # without having to pick it out of a sentence.
    print(f"LOG_BUNDLE={out_path}", flush=True)


if __name__ == "__main__":
    main()
