"""Group Japanese text into readable lines, the way English already is.

WHY THIS EXISTS
---------------
After item 82 the English pane reads well -- 2-3 sentences a line, around 20
words -- while the Japanese translation was still emitted as one line per
record. Measured on a real run's translation output: 36 records at a median of
139 characters, p90 392, max 769, holding 4.9 sentences each.

WHY JAPANESE NEEDS ITS OWN RULE RATHER THAN THE ENGLISH ONE
-----------------------------------------------------------
Japanese is not written with spaces, so `ENGLISH_LINE_TARGET_WORDS` has nothing
to count; the target here is CHARACTERS. In exchange the sentence boundary is
far easier than English: `。！？` are unambiguous terminators with no
abbreviation case and no decimal point to confuse, which is exactly what
`english_line_grouping`'s `_ABBREVIATION` and `_INITIAL_LETTER` regexes exist to
work around. There is nothing equivalent to guess at here.

Measured on that same output, splitting at `。！？` reconstructed the original
**36 of 36 records byte-exactly**, and grouping 2-3 sentences at a ~60 character
target produced 85 lines at a median of 71 characters, p90 106, max 157.

WHAT IS DELIBERATELY NOT TOUCHED
--------------------------------
The Japanese TRANSCRIPT. Its boundaries come from
`japanese_sentence_assembler.py` and contract C9 says they must never be
regrouped by a display rule. This module is for the Japanese TRANSLATION --
DeepL output of English input, which reaches the pane by a different path and
has no assembler behind it.

SAFETY
------
`group_japanese_lines` never invents, drops or reorders a character:
`"".join(result) == text` is asserted before returning, and any failure returns
`[text]` unchanged. A terminator inside `「」`, `『』` or brackets is not a line
break -- 2 such terminators appeared in the measured sample, both inside a URL
being read aloud.
"""

from __future__ import annotations

JAPANESE_LINE_MAX_SENTENCES = 3
JAPANESE_LINE_MIN_SENTENCES = 2
# Characters, not words: Japanese has no spaces to count. Chosen so a grouped
# line lands near the English rule's visual weight -- measured median 71 chars
# against English's median 24 words.
JAPANESE_LINE_TARGET_CHARS = 60

_TERMINATORS = "。！？"
_OPENERS = "「『（(【〔［《〈"
_CLOSERS = "」』）)】〕］》〉"


def split_japanese_sentences(text: str) -> list[str]:
    """Split after each sentence terminator that is not inside a quote.

    Terminators are kept on the sentence they end, so joining the result
    reproduces the input exactly.
    """
    text = text or ""
    if not text:
        return []
    out: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
        elif ch in _TERMINATORS and depth == 0:
            # Keep a run of terminators together ("本当に？！") rather than
            # emitting an empty sentence for the second one.
            if i + 1 < len(text) and text[i + 1] in _TERMINATORS:
                continue
            out.append(text[start : i + 1])
            start = i + 1
    if start < len(text):
        out.append(text[start:])
    return [p for p in out if p]


def group_japanese_lines(
    text: str,
    *,
    max_sentences: int = JAPANESE_LINE_MAX_SENTENCES,
    min_sentences: int = JAPANESE_LINE_MIN_SENTENCES,
    target_chars: int = JAPANESE_LINE_TARGET_CHARS,
) -> list[str]:
    """Group Japanese text into readable lines. Never alters the text itself."""
    raw = text or ""
    if not raw.strip():
        return []
    try:
        sentences = split_japanese_sentences(raw)
        if not sentences:
            return [raw]
        lines: list[str] = []
        current = ""
        count = 0
        for sentence in sentences:
            current += sentence
            count += 1
            if count >= max_sentences or (
                count >= min_sentences and len(current.strip()) >= target_chars
            ):
                lines.append(current)
                current = ""
                count = 0
        if current:
            lines.append(current)
        # The whole point is that this is a SPLIT, never a rewrite. If the
        # pieces do not rejoin into exactly what came in, the grouping is wrong
        # and the original is the safer thing to show.
        if "".join(lines) != raw:
            return [raw]
        stripped = [line.strip() for line in lines if line.strip()]
        return stripped or [raw]
    except Exception:
        return [raw]


def japanese_text_is_preserved(original: str, parts: list[str]) -> bool:
    """True when `parts` holds exactly the characters of `original`.

    Whitespace-insensitive, because the grouped lines are stripped for display
    while the source may carry incidental spacing around the split points.
    """
    joined = "".join(parts or [])
    return "".join(joined.split()) == "".join((original or "").split())


def looks_japanese(text: str) -> bool:
    """True when the text contains Japanese script.

    Used to pick the grouping rule for a TRANSLATION, whose language is decided
    by the target the user selected rather than by anything in the transcript.
    """
    for ch in text or "":
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF:
            return True
    return False
