# -*- coding: utf-8 -*-
"""Group English transcript text into readable 2-3 sentence lines.

CLIENT_DELIVERY_SPRINT_v5.md item 65, third approach. The English path has no
sentence boundary of its own -- it depends on Deepgram `speech_final`, which
continuous speech never sends -- so one committed utterance can hold an entire
monologue. Measured on live run `...20260812-154956`: one exported line of
**2342 characters, 424 words, 27 sentences**. The user's words for it: "like a
composition which is not suitable for reading".

The requested shape, in the user's own example:

    Speaker: My name is Tariqul. I am from Bangladesh. I am a software developer.
    Speaker: Currently I am working on Wicresoft Japan as a System Engineer. I
             have a dream to chase so I work so hard night and day.
    Speaker: I live in Tokyo Japan right now. I use Bus and Train for come to
             office and it takes more than an hour to reach office.

Three sentences when they are short, two when they are long -- and explicitly
NOT "count the letters", which is what a human cannot do while reading. So the
rule is a word budget: always break at `max_sentences`, and break early at
`min_sentences` once the line has already reached `target_words`. Against that
example the first line is 8 words after two sentences (under budget, so it takes
a third) and the second and third are 26 and 30 words after two (over budget, so
they stop there) -- which reproduces his grouping exactly. That is the test.

This module is deliberately **pure text formatting**: same characters in, same
characters out, only regrouped. It creates no records, changes no identity, and
touches neither the canonical ledger nor the lifecycle. That is a direct
response to this item's second attempt (`73ae8b2`), where committing at sentence
boundaries inside `UtteranceLifecycleOwner` published 9 utterances and only 1
reached the export -- an unexplained loss that is still open and is why
`ENGLISH_SENTENCE_FLUSH_ENABLED` remains False. Regrouping text cannot lose a
word, and a test asserts that byte-for-byte.
"""

from __future__ import annotations

import re

# Break at three sentences at the latest -- past that a line reads as a wall.
ENGLISH_LINE_MAX_SENTENCES = 3
# Never break before two, so a line is a thought rather than a fragment.
ENGLISH_LINE_MIN_SENTENCES = 2
# Word budget that decides between two and three. Derived from the user's own
# example, which brackets it: two sentences totalling 8 words must still take a
# third, and two totalling 26 must not. 20 sits inside that gap with room on
# both sides rather than hugging either bound.
ENGLISH_LINE_TARGET_WORDS = 20

# Sentence end = terminator, optional closing quote/bracket, then whitespace.
# Requiring the whitespace is what keeps "3.5" and "google.com" intact.
_SENTENCE_END = re.compile(r'(?<=[.!?])(?=["\')\]]*\s)')

# Terminators that do NOT end a sentence: an initial ("J. R. R.") or a common
# abbreviation. Kept to the short list that actually appears in meeting speech;
# a wrong split here only mis-groups a line, it never loses text.
_NOT_A_SENTENCE_END = re.compile(
    r"(?:\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|Prof|St|Jr|Sr|vs|etc|e\.g|i\.e|approx|No)\.?)\s*$",
    re.IGNORECASE,
)


def split_sentences(text: str) -> list[str]:
    """Split into sentences, preserving every character including spacing runs.

    Joining the result with "" must return the input unchanged -- callers rely
    on that, and `text_is_preserved` asserts it.
    """
    raw = text or ""
    if not raw.strip():
        return [raw] if raw else []
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(raw):
        cut = match.start()
        candidate = raw[start:cut]
        if _NOT_A_SENTENCE_END.search(candidate):
            continue  # an initial or abbreviation, not a boundary
        pieces.append(raw[start:cut])
        start = cut
    tail = raw[start:]
    if tail:
        pieces.append(tail)
    return pieces or [raw]


def group_sentences_into_lines(
    text: str,
    *,
    target_words: int = ENGLISH_LINE_TARGET_WORDS,
    max_sentences: int = ENGLISH_LINE_MAX_SENTENCES,
    min_sentences: int = ENGLISH_LINE_MIN_SENTENCES,
) -> list[str]:
    """Regroup one utterance's text into 2-3 sentence lines.

    Returns the lines in order, each stripped of edge whitespace. Empty or
    whitespace-only input returns []. No word is added, dropped or reordered.
    """
    if not (text or "").strip():
        return []
    sentences = split_sentences(text)
    lines: list[str] = []
    buf: list[str] = []
    words = 0
    for sentence in sentences:
        buf.append(sentence)
        words += len(sentence.split())
        count = len(buf)
        long_enough = count >= max(1, min_sentences) and words >= max(1, target_words)
        if count >= max(1, max_sentences) or long_enough:
            lines.append("".join(buf).strip())
            buf = []
            words = 0
    if buf:
        remainder = "".join(buf).strip()
        if remainder:
            lines.append(remainder)
    return [line for line in lines if line]


def text_is_preserved(original: str, lines: list[str]) -> bool:
    """True when regrouping changed only whitespace, never the words.

    The whole safety claim of this module. Compared on the word sequence rather
    than raw characters, because grouping legitimately drops the run of spaces
    at each break point and nothing else.
    """
    return (original or "").split() == " ".join(lines).split()
