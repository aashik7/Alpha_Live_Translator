"""UI/export-only speaker label helpers.

Never inject into Raw/Stable lexical scoring or DeepL source text.
"""

from __future__ import annotations

import re

from alpha.constants import UI_SPEAKER_LABEL

_NUMBERED_BRACKET = re.compile(r"^\[Speaker\s+\d+\]\s*", re.IGNORECASE)
_NUMBERED_COLON = re.compile(r"^Speaker\s+\d+\s*:\s*", re.IGNORECASE)
_GENERIC_COLON = re.compile(r"^Speaker\s*:\s*", re.IGNORECASE)


def ui_speaker_prefix() -> str:
    label = str(UI_SPEAKER_LABEL or "Speaker:").strip()
    if not label.endswith(":"):
        label = f"{label}:"
    return f"{label} "


def strip_speaker_prefix(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    body = _NUMBERED_BRACKET.sub("", body)
    body = _NUMBERED_COLON.sub("", body)
    body = _GENERIC_COLON.sub("", body)
    return body.strip()


def format_ui_speaker_line(text: str) -> str:
    body = strip_speaker_prefix(text)
    if not body:
        return ""
    return f"{ui_speaker_prefix()}{body}"


def count_numbered_speaker_labels(text: str) -> int:
    count = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _NUMBERED_BRACKET.match(line) or _NUMBERED_COLON.match(line):
            count += 1
    return count
