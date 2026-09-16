"""Ask the operator for the API keys, once, on a build that ships without them.

WHY THIS EXISTS
---------------
The installer and the portable zip normally carry the delivery keys, written
into `app/.env` at build or install time. A build made with
`build_installer.py --no-keys` carries none, so it can be handed to anyone: they
paste their own Deepgram and DeepL keys the first time they start it, and from
then on the app behaves exactly as a keyed build does.

WHY IT RUNS FROM `alpha/config.py`
----------------------------------
Every consumer reads the keys with `from alpha.config import DEEPGRAM_API_KEY`,
which binds the value at import time. So the keys have to be in the environment
BEFORE that module finishes importing -- config is the one choke point that is
early enough, and asking there means no other module needs to change.

It is deliberately plain tkinter, not customtkinter: this runs before the app
window exists, and it must not drag the UI stack (or its theme, fonts and DPI
work) into a process that may be about to exit.

The prompt only appears when a `.needs-api-keys` marker sits beside the app, so
a developer tree and the test suite -- neither of which has that marker -- can
never block on a dialog.
"""

from __future__ import annotations

import os
from pathlib import Path

MARKER_NAME = ".needs-api-keys"

TITLE = "Alpha Live Translator — API keys"
INTRO = (
    "This copy ships without API keys.\n"
    "Paste your own keys once; they are saved next to the app and reused."
)
DEEPGRAM_LABEL = "Deepgram API key (speech recognition, required)"
DEEPL_LABEL = "DeepL auth key (translation, required)"
HINT = "Deepgram: console.deepgram.com    DeepL: deepl.com/pro-api"


def keys_are_missing(deepgram: str | None, deepl: str | None) -> bool:
    """True when either key is absent or is one of the documented placeholders."""
    from alpha.config import PLACEHOLDER_API_KEYS

    for value in (deepgram, deepl):
        cleaned = (value or "").strip()
        if not cleaned or cleaned.lower() in PLACEHOLDER_API_KEYS:
            return True
    return False


def should_prompt(project_root: Path, deepgram: str | None, deepl: str | None) -> bool:
    """Only on a keyless build, only when a key is actually missing.

    `ALPHA_NO_KEY_PROMPT=1` turns it off for automated runs (the build's own
    smoke check sets it, and so does anything headless).
    """
    if (os.environ.get("ALPHA_NO_KEY_PROMPT") or "").strip():
        return False
    if not (project_root / MARKER_NAME).is_file():
        return False
    return keys_are_missing(deepgram, deepl)


def write_env(env_path: Path, deepgram: str, deepl: str) -> None:
    """Write the two keys the same way the installer does, and export them now.

    Any other settings already in the file are kept: only the two key lines are
    replaced, so a user who added `DEEPL_API_PLAN` does not lose it.
    """
    values = {"DEEPGRAM_API_KEY": deepgram.strip(), "DEEPL_AUTH_KEY": deepl.strip()}
    lines = []
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            name = line.split("=", 1)[0].strip()
            if name in values or name == "DEEPL_API_KEY":
                continue  # replaced below; the legacy alias is dropped
            lines.append(line)
    else:
        lines.append("# Written by the app's first-run key setup.")
    lines.extend(f"{name}={value}" for name, value in values.items())
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name, value in values.items():
        os.environ[name] = value


def ask_for_keys(env_path: Path, *, deepgram: str = "", deepl: str = "") -> bool:
    """Show the dialog. Returns True when keys were saved.

    Never raises: on a machine with no display the app should start and report a
    missing key the way it always has, not crash before its first window.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return False

    saved = {"ok": False}
    try:
        root = tk.Tk()
    except Exception:
        return False
    try:
        root.title(TITLE)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=16, pady=14)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=INTRO, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")

        entries = {}
        for row, (label, initial, show) in enumerate(
            ((DEEPGRAM_LABEL, deepgram, "*"), (DEEPL_LABEL, deepl, "*")), start=1
        ):
            tk.Label(frame, text=label, anchor="w").grid(
                row=row * 2 - 1, column=0, columnspan=2, sticky="w", pady=(12, 2)
            )
            entry = tk.Entry(frame, width=52, show=show)
            entry.insert(0, initial or "")
            entry.grid(row=row * 2, column=0, columnspan=2, sticky="we")
            entries[label] = entry
        entries[DEEPGRAM_LABEL].focus_set()

        tk.Label(frame, text=HINT, fg="#555555").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        def save(_event=None):
            values = [entries[DEEPGRAM_LABEL].get(), entries[DEEPL_LABEL].get()]
            if keys_are_missing(*values):
                messagebox.showerror(
                    TITLE, "Both keys are required, and neither may be an example value.", parent=root
                )
                return
            try:
                write_env(env_path, *values)
            except Exception as exc:
                messagebox.showerror(TITLE, f"Could not save the keys:\n{exc}", parent=root)
                return
            saved["ok"] = True
            root.destroy()

        buttons = tk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
        tk.Button(buttons, text="Quit", width=10, command=root.destroy).pack(side="right", padx=(8, 0))
        tk.Button(buttons, text="Save and start", width=16, command=save, default="active").pack(side="right")
        root.bind("<Return>", save)
        root.bind("<Escape>", lambda _e: root.destroy())

        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 3
        root.geometry(f"+{max(0, x)}+{max(0, y)}")
        root.lift()
        root.attributes("-topmost", True)
        root.after(200, lambda: root.attributes("-topmost", False))
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return saved["ok"]
