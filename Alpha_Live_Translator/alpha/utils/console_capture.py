"""Give stdout and stderr somewhere durable to go (sprint item 49).

WHY THIS EXISTS
---------------
The packaged shortcut runs `pythonw.exe`, which has **no console**. Every
`print()` and every traceback raised outside the logging system is written to a
stream that goes nowhere. On the delivery machine there is no terminal to open,
no debugger to attach and no code to change -- so a failure to start would leave
no trace at all, which is the one failure we cannot afford there.

It is imported and started by `main.py` before anything else, so an ImportError
from the very next line still lands in a file.

IT MUST NEVER RAISE
-------------------
A logging facility that can stop the app from starting is worse than no logging
facility. Every operation here is wrapped; `start()` returns None on failure and
the app carries on exactly as before.
"""

from __future__ import annotations

import datetime
import os
import platform
import sys

# One file per launch. A long-lived install would otherwise accumulate one
# forever, and the operator is never going to clean them up.
KEEP_NEWEST = 20


class _Tee:
    """Write to the log file and, when one exists, to the real stream too.

    Under `pythonw.exe` `sys.stdout` is None and there is nothing to pass
    through to; in a development run there is, and losing the terminal output
    would make this change annoying to work with.
    """

    def __init__(self, handle, passthrough):
        self._handle = handle
        self._passthrough = passthrough

    def write(self, text):
        try:
            self._handle.write(text)
            self._handle.flush()
        except Exception:
            pass
        if self._passthrough is not None:
            try:
                self._passthrough.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self):
        for stream in (self._handle, self._passthrough):
            if stream is None:
                continue
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self._passthrough is not None and self._passthrough.isatty())
        except Exception:
            return False

    def fileno(self):
        # Some libraries probe for a real descriptor. The log file has one;
        # pythonw's missing stream does not.
        return self._handle.fileno()

    def writelines(self, lines):
        for line in lines:
            self.write(line)


def _prune(log_dir: str) -> None:
    try:
        existing = sorted(
            name
            for name in os.listdir(log_dir)
            if name.startswith("console-") and name.endswith(".log")
        )
        for stale in existing[:-KEEP_NEWEST]:
            try:
                os.remove(os.path.join(log_dir, stale))
            except Exception:
                pass
    except Exception:
        pass


def start(project_root: str) -> str | None:
    """Redirect stdout/stderr into `<project_root>/logs/console-<stamp>.log`.

    Returns the path, or None if capture could not be set up.
    """
    try:
        log_dir = os.path.join(project_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = os.path.join(log_dir, "console-" + stamp + ".log")
        handle = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)

        # A header that answers the first questions asked of any bug report,
        # without needing the reporter to know how to answer them.
        had_console = sys.stdout is not None
        for line in (
            "=== Alpha Live Translator console log ===",
            "started      " + datetime.datetime.now().isoformat(timespec="seconds"),
            "python       " + sys.version.split()[0] + " (" + platform.machine() + ")",
            "executable   " + str(sys.executable),
            "windows      " + platform.platform(),
            "had console  " + str(had_console),
            "=" * 41,
        ):
            handle.write(line + "\n")
        handle.flush()

        sys.stdout = _Tee(handle, sys.stdout)
        sys.stderr = _Tee(handle, sys.stderr)
        _prune(log_dir)
        return log_path
    except Exception:
        return None
