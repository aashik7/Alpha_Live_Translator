"""Collect everything needed to diagnose a problem, into one file (item 49).

WHY THIS EXISTS
---------------
Once this is installed on someone else's machine there is no terminal to open,
no debugger to attach and no code to change, and uninstalling to "start clean"
throws away the evidence of whatever went wrong. So the operator needs one
button that gathers the whole trail into a single file they can send back.

Run from the Start Menu shortcut, or:

    python collect_logs.py [--with-audio] [--runs N] [--out DIR]

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


def tell(message: str, *, error: bool = False) -> None:
    """Say it on screen: launched from a shortcut there is no console to read."""
    print(message)
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
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
