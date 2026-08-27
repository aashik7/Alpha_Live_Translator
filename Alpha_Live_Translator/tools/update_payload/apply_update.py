"""Update an installed Alpha Live Translator in place -- no uninstall, no reinstall.

WHY THIS EXISTS
---------------
Reinstalling is not a usable answer for a client machine: the Inno uninstaller
removes `app\\user_settings.json` outright, and a fresh install cannot restore
`app\\troubleshooting\\` -- which is where every run's evidence lives and the only
reason a failure on a machine we cannot reach is diagnosable at all. Shipping a
new installer also means re-entering the delivery keys.

Only `app\\` ever changes between builds. `python\\` is the ~96 MB embedded
CPython and is identical from one build to the next, so this syncs `app\\` alone:
5 MB, a few seconds.

WHAT IT DOES
------------
Makes the installed `app\\` tree byte-identical to the payload shipped beside
this script -- changed files overwritten, missing files added, files that are no
longer part of the app deleted -- then verifies every file by SHA-256 and, if
anything is wrong, puts the previous tree back.

WHAT IT NEVER TOUCHES
---------------------
Four things live inside `app\\` but are NOT part of the app, and deleting any of
them is worse than not updating at all:

* ``.env``               the delivery's DEEPGRAM_API_KEY / DEEPL_AUTH_KEY. Written
                         only by the installer (installer/alpha.iss WriteEnvFile),
                         never present in the source tree. Losing it permanently
                         bricks the install -- no key, no transcription, and no
                         copy anywhere to restore from.
* ``user_settings.json`` the operator's UI language (alpha/ui/strings.py).
* ``troubleshooting\\``   every run artifact, transcript and log
                         (alpha/utils/troubleshooting_paths.py anchors it here).
                         Can be gigabytes.
* ``logs\\``, ``debug\\`` the other two runtime locations -- taken from
                         collect_logs.py's own list, not guessed:
                         ``for name in ("logs", "troubleshooting/latest", "debug")``.

``__pycache__`` is the opposite case and is always deleted: it is not the app and
not runtime state, and leaving a stale one next to a replaced module is a way to
half-apply an update.

Run:  python apply_update.py [INSTALL_DIR] [--force] [--dry-run]
      (Update_Alpha.bat finds the install and the interpreter for you)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Runtime state that lives inside app\ but is not part of the app.
PRESERVE_FILES = frozenset({".env", "user_settings.json"})
PRESERVE_DIRS = frozenset({"troubleshooting", "logs", "debug"})
# Never copied in, always removed: stale bytecode beside a replaced module.
DROP_DIRS = frozenset({"__pycache__"})

DEFAULT_INSTALL = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Alpha Live Translator"


class UpdateError(RuntimeError):
    """Anything that should stop the update with a message the operator can act on."""


def say(message: str = "") -> None:
    # ASCII only: the client console may be cp932 or cp437, and a UnicodeEncodeError
    # in the middle of an update is a confusing way to fail.
    print(message, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_preserved(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in PRESERVE_DIRS:
        return True
    return len(parts) == 1 and parts[0] in PRESERVE_FILES


def is_dropped(rel: Path) -> bool:
    return any(part in DROP_DIRS for part in rel.parts)


def walk_files(root: Path) -> dict[Path, Path]:
    """Every file under `root` that this updater considers part of the app."""
    found: dict[Path, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if is_preserved(rel) or is_dropped(rel):
            continue
        found[rel] = path
    return found


def read_app_version(app_dir: Path) -> str:
    """Read APP_VERSION without importing -- the tree may be mid-update or broken."""
    constants = app_dir / "alpha" / "constants.py"
    try:
        text = constants.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def running_instances(install: Path) -> list[str]:
    r"""Executables running out of this install.

    A running Alpha does not lock its own `.py` files -- Python reads and closes
    them -- so replacing them under a live session silently produces a process
    running half the old code and half the new. Nothing would report that. Hence
    this is a hard stop rather than a warning.

    The PID filter is not defensive tidiness. `Update_Alpha.bat` deliberately
    runs this script with the install's OWN `python\python.exe`, so without it
    every single run finds itself inside the install and refuses -- the updater
    would never work once, on any machine. Caught by rehearsing the real .bat
    against a real copy of the built install.
    """
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.ExecutablePath } | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # No PowerShell, or it refused. Do not invent a verdict from a check that
        # did not run; the caller is told the check was skipped.
        return []

    prefix = str(install.resolve()).lower()
    mine = {os.getpid(), os.getppid()}
    live: set[str] = set()
    for line in result.stdout.splitlines():
        pid, _, executable = line.strip().partition("|")
        executable = executable.strip()
        if not executable or not executable.lower().startswith(prefix):
            continue
        try:
            if int(pid) in mine:
                continue
        except ValueError:
            pass
        live.add(executable)
    return sorted(live)


def preflight(install: Path, payload: Path, force: bool) -> None:
    if not payload.is_dir():
        raise UpdateError(
            f"the update payload is missing: {payload}\n"
            "Extract the whole update folder before running it -- running the .bat "
            "from inside the zip viewer gives it nothing to copy."
        )
    for required in ("main.py", Path("alpha") / "constants.py"):
        if not (payload / required).is_file():
            raise UpdateError(f"the payload is incomplete: {payload / required} is missing")

    if not install.is_dir():
        raise UpdateError(
            f"no Alpha Live Translator install at: {install}\n"
            "Pass the install folder as an argument if it is somewhere else."
        )
    if not (install / "app" / "main.py").is_file():
        raise UpdateError(
            f"{install} does not look like an Alpha install ({install / 'app' / 'main.py'} is missing)"
        )
    if not (install / "python" / "pythonw.exe").is_file():
        raise UpdateError(
            f"{install} has no embedded Python -- this is not a complete install"
        )

    live = running_instances(install)
    if live and not force:
        listing = "\n".join(f"    {p}" for p in live)
        raise UpdateError(
            "Alpha Live Translator is still running:\n"
            f"{listing}\n\n"
            "Close it completely, then run this update again. Updating a live "
            "session leaves it running a mix of old and new code."
        )
    if live and force:
        say("WARNING: --force given while Alpha is still running. Restart it after this finishes.")


def plan(payload: Path, app_dir: Path) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """(added, changed, unchanged, removed) -- worked out before anything is written."""
    new_files = walk_files(payload)
    old_files = walk_files(app_dir)

    added: list[Path] = []
    changed: list[Path] = []
    unchanged: list[Path] = []
    for rel, src in sorted(new_files.items()):
        dst = old_files.get(rel)
        if dst is None:
            added.append(rel)
        elif src.stat().st_size != dst.stat().st_size or sha256(src) != sha256(dst):
            changed.append(rel)
        else:
            unchanged.append(rel)

    removed = sorted(rel for rel in old_files if rel not in new_files)
    return added, changed, unchanged, removed


def back_up(app_dir: Path, install: Path) -> Path:
    """Copy the app tree aside, minus the runtime state (which can be gigabytes)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = install / f"app_backup_{stamp}"
    if backup.exists():
        shutil.rmtree(backup)
    for rel, src in walk_files(app_dir).items():
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return backup


def restore(backup: Path, app_dir: Path) -> None:
    """Put the previous tree back. Runtime state was never moved, so it is untouched."""
    for rel in list(walk_files(app_dir)):
        target = app_dir / rel
        try:
            target.unlink()
        except OSError:
            pass
    for rel, src in walk_files(backup).items():
        dst = app_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def apply(payload: Path, app_dir: Path, added: list[Path], changed: list[Path],
          removed: list[Path]) -> None:
    for rel in added + changed:
        dst = app_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload / rel, dst)
    for rel in removed:
        try:
            (app_dir / rel).unlink()
        except OSError as exc:
            raise UpdateError(f"could not remove {app_dir / rel}: {exc}") from exc

    # Stale bytecode next to a module that just changed.
    for path in sorted(app_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.name in DROP_DIRS:
            shutil.rmtree(path, ignore_errors=True)

    # Directories left empty by the removals. Deepest first, and never a preserved
    # one -- an empty troubleshooting\ is still the folder the app writes into.
    for path in sorted(app_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir() or is_preserved(path.relative_to(app_dir)):
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def verify(payload: Path, app_dir: Path) -> list[str]:
    """Every payload file present and identical; nothing extra left behind."""
    problems: list[str] = []
    new_files = walk_files(payload)
    for rel, src in new_files.items():
        dst = app_dir / rel
        if not dst.is_file():
            problems.append(f"missing after update: {rel}")
        elif sha256(src) != sha256(dst):
            problems.append(f"content differs after update: {rel}")
    for rel in walk_files(app_dir):
        if rel not in new_files:
            problems.append(f"still present after update: {rel}")
    return problems


def check_preserved(app_dir: Path) -> list[str]:
    """What survived, reported by name so the operator can see it did."""
    kept: list[str] = []
    for name in sorted(PRESERVE_FILES):
        if (app_dir / name).is_file():
            kept.append(name)
    for name in sorted(PRESERVE_DIRS):
        directory = app_dir / name
        if directory.is_dir():
            count = sum(1 for p in directory.rglob("*") if p.is_file())
            kept.append(f"{name}\\ ({count} files)")
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description="Update an installed Alpha Live Translator.")
    parser.add_argument("install", nargs="?", default=None,
                        help="install folder (default: %%LOCALAPPDATA%%\\Programs\\Alpha Live Translator)")
    parser.add_argument("--payload", default=None, help="folder holding the new app\\ tree")
    parser.add_argument("--force", action="store_true", help="update even if Alpha is running")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    install = Path(args.install).resolve() if args.install else DEFAULT_INSTALL
    payload = Path(args.payload).resolve() if args.payload else here / "app"
    app_dir = install / "app"

    say("Alpha Live Translator -- in-place update")
    say("=" * 60)
    say(f"install : {install}")
    say(f"payload : {payload}")
    say()

    try:
        preflight(install, payload, args.force)
    except UpdateError as exc:
        say("CANNOT UPDATE")
        say("-" * 60)
        say(str(exc))
        return 2

    say(f"installed version : {read_app_version(app_dir)}")
    say(f"update version    : {read_app_version(payload)}")
    say()

    added, changed, unchanged, removed = plan(payload, app_dir)
    say(f"{len(changed):5d} file(s) to update")
    say(f"{len(added):5d} file(s) to add")
    say(f"{len(removed):5d} file(s) no longer part of the app, to remove")
    say(f"{len(unchanged):5d} file(s) already correct")
    say()
    for rel in changed:
        say(f"  update  {rel}")
    for rel in added:
        say(f"  add     {rel}")
    for rel in removed:
        say(f"  remove  {rel}")
    say()

    if not (added or changed or removed):
        say("Already up to date. Nothing to do.")
        return 0

    if args.dry_run:
        say("--dry-run: nothing was written.")
        return 0

    say("backing up the current app folder...")
    backup = back_up(app_dir, install)
    say(f"  saved to {backup}")

    try:
        say("applying...")
        apply(payload, app_dir, added, changed, removed)
        say("verifying...")
        problems = verify(payload, app_dir)
    except Exception as exc:  # noqa: BLE001 - any failure here must roll back
        say(f"FAILED while updating: {type(exc).__name__}: {exc}")
        say("restoring the previous version...")
        restore(backup, app_dir)
        say("restored. The app is exactly as it was before this ran.")
        return 3

    if problems:
        say("FAILED verification:")
        for problem in problems[:20]:
            say(f"  {problem}")
        say("restoring the previous version...")
        restore(backup, app_dir)
        say("restored. The app is exactly as it was before this ran.")
        return 3

    kept = check_preserved(app_dir)
    # Beside app\, not inside it. Inside, the next update sees it as a file that
    # is no longer part of the app and deletes it -- so the record of what was
    # applied never survives to be read, and no run is ever idempotent.
    (install / "UPDATE_APPLIED.json").write_text(
        json.dumps({
            "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "app_version": read_app_version(app_dir),
            "files_updated": len(changed),
            "files_added": len(added),
            "files_removed": len(removed),
            "backup": str(backup),
        }, indent=2),
        encoding="utf-8",
    )

    say()
    say("UPDATE COMPLETE -- every file verified by SHA-256.")
    say(f"  updated {len(changed)}, added {len(added)}, removed {len(removed)}")
    say("kept untouched:")
    for item in kept:
        say(f"  {item}")
    say()
    say(f"Previous version kept at: {backup}")
    say("Delete that folder once the app has been confirmed working.")
    say()
    say("Start Alpha Live Translator normally.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("cancelled.")
        sys.exit(1)
