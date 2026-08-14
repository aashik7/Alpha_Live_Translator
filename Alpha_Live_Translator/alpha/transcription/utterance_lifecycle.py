# -*- coding: utf-8 -*-
"""Session-scoped utterance revision lifecycle for production Deepgram finals.

English / non-Japanese path: buffer incomplete finals (is_final=true,
speech_final=false), replace one active UI record, and commit once on
speech_final / UtteranceEnd / bounded inactivity timeout.

Japanese finals still use their own boundary/timing strategy in
japanese_final_chunk_stabilizer.py / japanese_sentence_assembler.py (this
is explicitly allowed by REPAIR_PLAN.md Phase 2: "Japanese and English may
use different boundary strategies"). What is no longer separate: identity
registration and the actual canonical-ledger commit. Per
TASK_5_FINAL_CLEANUP_REPORT.md Fix 1, the Japanese assembler proposes its
already-decided HOLD/EXTEND/COMMIT-shaped action to this module via
`accept_boundary_proposal` instead of calling execute_pipeline_commit and
canonical_identity_registry independently -- this is now the single place
identity is observed/assigned and commits happen, for both languages.
"""

from __future__ import annotations

import json
import re
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from alpha.transcription.speaker_boundary_guard import speakers_confirmed_same
from pathlib import Path
from typing import Any, Callable, Optional

# States
IDLE = "IDLE"
ACTIVE_INTERIM = "ACTIVE_INTERIM"
ACTIVE_FINAL_CHUNK = "ACTIVE_FINAL_CHUNK"
READY_TO_COMMIT = "READY_TO_COMMIT"
COMMITTED = "COMMITTED"
SUPERSEDED = "SUPERSEDED"
CANCELLED = "CANCELLED"

# Decisions
CREATE_ACTIVE = "CREATE_ACTIVE"
REPLACE_ACTIVE = "REPLACE_ACTIVE"
EXTEND_ACTIVE = "EXTEND_ACTIVE"
HOLD_FINAL_CHUNK = "HOLD_FINAL_CHUNK"
COMMIT_ACTIVE = "COMMIT_ACTIVE"
CREATE_NEW_UTTERANCE = "CREATE_NEW_UTTERANCE"
SUPERSEDE_PREVIOUS = "SUPERSEDE_PREVIOUS"
IGNORE_DUPLICATE = "IGNORE_DUPLICATE"
CANCEL_ACTIVE = "CANCEL_ACTIVE"

REPLACE_PROVISIONAL = "REPLACE_PROVISIONAL"
SUPERSEDE = "SUPERSEDE"
TERMINAL_COMMIT = "TERMINAL_COMMIT"
CREATE_NEW = "CREATE_NEW"

# Timing proximity for same-utterance merge (seconds). Short — not a minute wait.
_TIMING_GAP_MAX_S = 2.5
_TIMING_START_MATCH_S = 0.35
_TIMING_OVERLAP_MIN_S = 0.05

# Commit reasons that represent the app's own uncertain guess that an
# utterance was finished, rather than a confident signal (from Deepgram's
# speech_final/UtteranceEnd, or an explicit incompatible-utterance
# boundary) that it actually was. "extend_then_commit" is included so a
# chain of several premature fragments (A, then B extends A, then C
# extends A+B, ...) keeps working — each extension is itself only as
# confident as the fragment before it, until a real boundary signal ends
# the chain.
_PREMATURE_COMMIT_REASONS = frozenset(
    {"inactivity_timeout_fallback", "extend_then_commit"}
)

# Commit reasons after which a re-sent tail must NOT be stripped from the next
# utterance. A provider disconnect is the case that matters: the words after
# the hole are not a continuation of the words before it, so an apparent
# overlap there is coincidence, not the provider repeating itself.
_HARD_BOUNDARY_COMMIT_REASONS = frozenset({"provider_disconnected"})

# A sentence terminator, allowing for a closing quote or bracket after it.
_SENTENCE_TERMINATED = re.compile(r'[.!?]["\')\]]*\s*$')


def _ends_a_sentence(text: str) -> bool:
    return bool(_SENTENCE_TERMINATED.search((text or "").rstrip()))



# Fallback inactivity commit (ms). Uses existing meeting-buffer scale; does not
# change Deepgram endpointing configuration.
DEFAULT_COMMIT_FALLBACK_MS = 2000


def _as_float(value: Any, default: float = -1.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _norm_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _compare_tokens(text: str) -> list[str]:
    """Whitespace tokens with edge punctuation stripped, for COMPARISON ONLY.

    fixes CLIENT_DELIVERY_SPRINT_v5.md problem F (item 51). `_norm_text`
    only lowercases and collapses whitespace, so punctuation stays glued to
    the word: `"olympia."` and `"olympia,"` compare as different tokens.
    Deepgram routinely re-sends the same growing utterance with different
    formatting (`"50 percent"` -> `"50%"`, `"mister"` -> `"Mr."`,
    punctuation moving as the sentence resolves), and that glued
    punctuation dropped the word-overlap score below its threshold on
    exactly the pairs the threshold exists to catch -- so `_merge_lexical`
    fell through to its concatenation branch and glued a reformatted
    re-send onto the text it was meant to replace, compounding on every
    tick. Measured on run `...20260811-182940`: 5 of 54 exported lines
    carried 85.9% of the export's characters, the worst one 5039
    characters from ~112 glued fragments.

    Only edges are stripped, so `didn't` / `I'm` keep their apostrophes.
    The returned tokens are never used to build output text -- callers
    always emit the caller's original strings.
    """
    return [w for w in (t.strip(string.punctuation) for t in _norm_text(text).split()) if w]


def _overlap_join(prev: str, curr: str, prev_tokens: list[str], curr_tokens: list[str]) -> Optional[str]:
    """Join two adjacent chunks that share a boundary, without duplicating it.

    fixes problem F, second half. The concatenation branch below is
    documented as being for "non-overlapping lexical spans", but Deepgram
    also emits a *sliding window* over continuous speech, where the next
    chunk repeats the tail of the previous one:

        "...the best bodybuilder in" + "bodybuilder in the world, and I didn't"

    Concatenating those duplicates "bodybuilder in". This finds the longest
    exact run of `k` consecutive tokens that is both a suffix of `prev` and
    a prefix of `curr`, and appends only what follows it.

    `k >= 2` is deliberate, not tuning: a single shared boundary word
    ("the", "and") happens constantly between unrelated sentences, whereas
    two *consecutive* matching tokens at the exact boundary do not. Returns
    None when there is no such run, leaving the caller's existing
    concatenation untouched -- so genuinely disjoint chunks are unaffected.
    """
    max_k = min(len(prev_tokens), len(curr_tokens))
    for k in range(max_k, 1, -1):
        if prev_tokens[-k:] != curr_tokens[:k]:
            continue
        if k == len(curr_tokens):
            # curr adds nothing past the shared run.
            return prev
        # Re-split curr's ORIGINAL text so the appended remainder keeps its
        # own formatting and punctuation; the token lists are comparison
        # forms and must never be emitted.
        remainder = " ".join(curr.split()[k:]).strip()
        return f"{prev} {remainder}" if remainder else prev
    return None


def _tail_resend_splice(
    prev: str,
    curr: str,
    prev_tokens: list[str],
    curr_tokens: list[str],
    *,
    min_run: int = 3,
    max_orphan: int = 4,
) -> Optional[str]:
    """Splice a chunk that re-sends and revises the TAIL of a long accumulator.

    fixes problem F, third shape. Once an utterance has accumulated, Deepgram
    keeps re-sending only its most recent span, revised and extended:

        accumulated: "...You lower it. So even though you failed, positively, we're"
        next chunk:  "So even though you failed positively, we can do a couple of extra reps"

    Whole-against-whole comparison cannot see this -- the shared run is a
    small fraction of the accumulator, so the similarity gate scores under
    threshold -- and `_overlap_join` cannot either, because the run is not a
    clean suffix of `prev`: `prev` ends in a partial word (`"we're"`) that
    `curr` revises away. Both fell through to concatenation, which is why
    long lines still duplicated after the first two fixes.

    So: find the longest run of `curr`'s leading tokens that appears
    contiguously in `prev`, preferring the latest such position, and keep
    everything in `prev` before it. `max_orphan` is the safety bound -- the
    run must reach within that many tokens of `prev`'s end, so this can
    never discard more than `max_orphan` tokens no matter how long the
    accumulator is. `min_run` is stricter than `_overlap_join`'s because
    this one can drop text, and a coincidental two-word echo must not.

    Returns None when no qualifying run exists, leaving the caller's
    concatenation untouched.
    """
    limit = min(len(curr_tokens), len(prev_tokens))
    for k in range(limit, min_run - 1, -1):
        head = curr_tokens[:k]
        for i in range(len(prev_tokens) - k, -1, -1):  # latest occurrence first
            if prev_tokens[i : i + k] != head:
                continue
            if len(prev_tokens) - (i + k) > max_orphan:
                break  # this run sits too early in prev to be a tail re-send
            kept = " ".join(prev.split()[:i]).strip()
            return f"{kept} {curr}".strip() if kept else curr
    return None


def _audio_spans_overlap(
    prev_start: float,
    prev_end: float,
    cand_start: float,
    cand_end: float,
) -> bool:
    """True when the candidate re-sends audio the active utterance already covers.

    This is the evidence `_boundary_token_collapse` needs and the reason it is
    safe. Deepgram emits both *continuations* (the next span of speech) and
    *re-sends* (a window that slides back over audio it already transcribed),
    and from text alone the two are indistinguishable at a one-token seam. The
    audio clock is not ambiguous: a re-send starts before the active utterance
    ended, a continuation does not.

    Requires a real overlap of at least `_TIMING_OVERLAP_MIN_S` rather than any
    `cand_start < prev_end`, so float noise on two spans that merely abut
    cannot read as a re-send. Missing timing (either side negative) returns
    False -- fail closed, because the caller uses this to authorise dropping a
    token.
    """
    if prev_end < 0 or cand_start < 0:
        return False
    return (prev_end - cand_start) >= _TIMING_OVERLAP_MIN_S


def _boundary_token_collapse(
    prev: str,
    curr: str,
    prev_tokens: list[str],
    curr_tokens: list[str],
) -> Optional[str]:
    """Join a seam whose overlap is a single token, without repeating it.

    fixes CLIENT_DELIVERY_SPRINT_v5.md item 64. `_overlap_join` deliberately
    requires `k >= 2`, and `_tail_resend_splice` requires `min_run=3`, so a
    re-send that overlaps by exactly ONE token matches neither and falls
    through to the concatenation branch below -- which, when `prev` does not
    end in punctuation, joins with `f"{prev}, {curr}"`. That comma is the
    fingerprint: measured against the human reference transcript for run
    `...20260812-095935`, the source says "other schools of Muslim", "heretics
    have attributed" and "objectively claiming the falsehood", while the export
    says "schools, schools", "have, have" and "Claiming, Claiming". All three
    reproduce exactly by calling `_merge_lexical` on the two spans.

    The k=1 case is NOT symmetric with k>=2 and must not simply relax that
    threshold: a single shared boundary token is genuinely ambiguous, because
    English really does double words across a clause boundary ("He said that"
    + "that was fine", "the food I had" + "had gone cold"). Collapsing those
    silently deletes a spoken word, which this module treats as strictly worse
    than the duplication it is fixing (see `_merge_lexical`'s note on the
    ordering of `_overlap_join`). So the caller only passes
    `audio_overlaps=True` when the two spans overlap on the audio clock, which
    a re-send does and a continuation does not. With no timing, this never
    runs.

    Returns None unless the seam token matches on both sides *and* `curr`'s
    leading whitespace token corresponds to that comparison token -- token
    lists are punctuation-stripped and drop tokens that empty out, so the
    alignment is checked rather than assumed. Emits the callers' original
    strings; comparison forms are never output.
    """
    if not prev_tokens or not curr_tokens:
        return None
    if prev_tokens[-1] != curr_tokens[0]:
        return None
    curr_parts = curr.split()
    if not curr_parts:
        return None
    if _compare_tokens(curr_parts[0])[:1] != curr_tokens[:1]:
        return None  # leading whitespace token is not the first comparison token
    remainder = " ".join(curr_parts[1:]).strip()
    return f"{prev} {remainder}" if remainder else prev


def _strip_committed_tail_prefix(
    committed_text: str,
    lexical: str,
    *,
    min_run: int = 3,
) -> Optional[str]:
    """Drop the head of a new utterance that repeats the tail already committed.

    fixes CLIENT_DELIVERY_SPRINT_v5.md item 66. When a commit lands mid-sentence
    -- which `utterance_end` and `speech_final` both do, because they mean
    *speech* paused, not that a *sentence* finished -- the provider's next
    window re-sends the span it just sent. `_merge_lexical`'s overlap machinery
    (`_overlap_join`, `_tail_resend_splice`) only ever runs WITHIN one active
    utterance, so once the previous one is committed nothing dedupes across the
    boundary and the tail is stored twice, in two records.

    Measured on run `...20260812-161651`, 5 of 7 exported records:

        [5] "...in Duterte, he writes openly, I never considered"
        [6] "he writes openly, I never considered him an impostor at all"

    This removes only the leading run that is provably a suffix of the text
    already committed, so nothing is lost -- those words are in the previous
    record, verbatim. `min_run=3` matches `_tail_resend_splice`'s bound: this
    can discard text, and a coincidental one- or two-word echo across a genuine
    utterance boundary ("...thank you." / "You are welcome") must not trigger
    it.

    Deliberately NOT solved by extending the committed record instead. That was
    tried and measured: routing these through `_extend_committed_locked`
    produces the correct merged text in the lifecycle and the store, but the
    canonical write is skipped as `already_committed`, so the LEDGER keeps the
    truncated record -- and the export reads the ledger. Truncating the export
    is worse than the duplication being fixed. Stripping the new utterance's
    prefix revises nothing and cannot reach the ledger's existing records.

    Returns the trimmed text, or None when there is no qualifying overlap.
    """
    prev_tokens = _compare_tokens(committed_text)
    curr_parts = (lexical or "").split()
    curr_tokens = _compare_tokens(lexical)
    if len(prev_tokens) < min_run or len(curr_tokens) < min_run:
        return None
    limit = min(len(prev_tokens), len(curr_tokens))
    for k in range(limit, min_run - 1, -1):
        if prev_tokens[-k:] != curr_tokens[:k]:
            continue
        # Align the comparison tokens back onto the original whitespace tokens
        # before cutting, rather than assuming they index the same -- edge
        # punctuation is stripped for comparison and empty tokens are dropped.
        if _compare_tokens(" ".join(curr_parts[:k])) != curr_tokens[:k]:
            return None
        remainder = " ".join(curr_parts[k:]).strip()
        return remainder or None
    return None


def _known_speaker(value: Any) -> Optional[int]:
    """Normalise a speaker label to `None` when it is not actually known.

    fixes CLIENT_DELIVERY_SPRINT_v5.md problem C (item 22). Callers pass `0`,
    `None` and `""` interchangeably for "no speaker identified", and the old
    comparison coerced every one of them to `1`, so two *unidentified* speakers
    compared equal and their turns merged into a single line.

    `speakers_confirmed_same` is fail-closed on `None` but would still read
    `0 == 0` as a confirmed match, so the unknown forms have to collapse to
    `None` here before it is called -- swapping the call in without this would
    leave the `0` case fail-open.
    """
    if value is None:
        return None
    try:
        speaker = int(value)
    except (TypeError, ValueError):
        return None
    return speaker if speaker else None


def _merge_lexical(previous: str, current: str, *, audio_overlaps: bool = False) -> str:
    """Merge two lexical chunks of the same utterance without hardcoding phrases.

    `audio_overlaps` says the two spans overlap on the audio clock, i.e. the
    caller has evidence that `current` re-sends audio `previous` already
    covers. It only unlocks `_boundary_token_collapse` (item 64); every other
    branch behaves identically either way. Defaults False so any caller
    without timing keeps the old behaviour rather than silently gaining the
    ability to drop a token.
    """
    prev = (previous or "").strip()
    curr = (current or "").strip()
    if not prev:
        return curr
    if not curr:
        return prev
    prev_n = _norm_text(prev)
    curr_n = _norm_text(curr)
    if prev_n == curr_n:
        return curr if len(curr) >= len(prev) else prev
    if prev_n and curr_n.startswith(prev_n):
        return curr
    if curr_n and prev_n.startswith(curr_n):
        return prev
    if prev_n and prev_n in curr_n:
        return curr
    if curr_n and curr_n in prev_n:
        return prev
    # Compared with edge punctuation stripped -- see _compare_tokens.
    prev_word_list = _compare_tokens(prev)
    curr_word_list = _compare_tokens(curr)

    # Adjacent chunks that overlap at the boundary -- join on the shared run
    # rather than repeating it.
    #
    # Deliberately BEFORE the similarity gate below: an exact run of
    # consecutive tokens shared between prev's tail and curr's head is
    # literal evidence, and it must outrank a fuzzy score. Ordered the other
    # way, a window that had slid forward carrying new tail content scored
    # as "the same utterance re-said" and the gate returned whichever side
    # was *longer* -- which is prev, because prev still holds the earlier
    # text. That silently discarded curr's new tail (measured: it dropped
    # "lifting weights. Come on." from the run `...182940` sequence). Silent
    # content loss is strictly worse than the duplication of problem F, so
    # the literal check runs first.
    joined = _overlap_join(prev, curr, prev_word_list, curr_word_list)
    if joined is not None:
        return joined

    prev_words = set(prev_word_list)
    curr_words = set(curr_word_list)
    if prev_words and curr_words:
        overlap = len(prev_words & curr_words) / min(len(prev_words), len(curr_words))
        if overlap >= 0.6:
            # Bare set overlap is order-blind: "cat sat mat" vs "mat sat cat"
            # score the same 100% overlap despite being different content.
            # Require the shared words to also appear in a compatible
            # relative order before treating this as the same utterance
            # re-said, not just an anagram of word choices.
            #
            # Measured as in-order matched tokens over the SHORTER side, the
            # same asymmetric convention the set-overlap gate directly above
            # already uses. This replaces `SequenceMatcher.ratio()`, which is
            # symmetric (2*M/total) and so penalises length difference: a
            # 4-token chunk against the 8-token growing version of itself
            # scored 0.50 and was rejected, even though 3 of its 4 tokens
            # matched in order. That rejection is the second half of problem
            # F -- it sent "So I'm Mr. Olympia," + "So I'm mister Olympia,
            # the best bodybuilder in" to the concatenation branch, which
            # glued a re-send onto the text it was replacing. The 0.6
            # threshold itself is unchanged; only the denominator is, so
            # this stays as strict for equal-length pairs as it always was.
            matched = sum(
                b.size for b in SequenceMatcher(None, prev_word_list, curr_word_list).get_matching_blocks()
            )
            order_ratio = matched / min(len(prev_word_list), len(curr_word_list))
            if order_ratio >= 0.6:
                return curr if len(curr_word_list) >= len(prev_word_list) else prev
    # A chunk that re-sends and revises only the accumulator's tail. Placed
    # after the similarity gate because it is the one step here that can
    # discard text -- bounded to max_orphan tokens -- so the non-destructive
    # checks get first refusal.
    spliced = _tail_resend_splice(prev, curr, prev_word_list, curr_word_list)
    if spliced is not None:
        return spliced
    # A re-send overlapping by exactly one token -- below both thresholds
    # above, so without this it reaches the concatenation branch and the
    # shared token is emitted twice. Gated on audio-clock evidence; see
    # _boundary_token_collapse.
    if audio_overlaps:
        collapsed = _boundary_token_collapse(prev, curr, prev_word_list, curr_word_list)
        if collapsed is not None:
            return collapsed
    # Adjacent finalised chunks of one utterance (non-overlapping lexical spans).
    if prev.endswith((",", ";", ":")):
        return f"{prev} {curr}"
    if prev[-1:] in ".!?":
        return f"{prev} {curr}"
    return f"{prev}, {curr}"


def _channel_matches_exactly(expected: Any, observed: Any) -> bool:
    if expected is None or observed is None:
        return expected is None and observed is None
    try:
        return int(expected) == int(observed)
    except (TypeError, ValueError):
        return str(expected) == str(observed)


def _timing_compatible(
    prev_start: float,
    prev_end: float,
    cand_start: float,
    cand_end: float,
) -> bool:
    if cand_start < 0 and prev_start < 0:
        return True  # no timing → rely on other gates
    if prev_start >= 0 and cand_start >= 0 and abs(prev_start - cand_start) <= _TIMING_START_MATCH_S:
        return True
    if prev_start >= 0 and prev_end > prev_start and cand_start >= 0 and cand_end > cand_start:
        latest_start = max(prev_start, cand_start)
        earliest_end = min(prev_end, cand_end)
        if earliest_end - latest_start >= _TIMING_OVERLAP_MIN_S:
            return True
    if prev_end >= 0 and cand_start >= 0:
        gap = cand_start - prev_end
        if -0.25 <= gap <= _TIMING_GAP_MAX_S:
            return True
    if prev_start >= 0 and cand_start >= 0:
        gap = abs(cand_start - prev_start)
        if gap <= _TIMING_GAP_MAX_S:
            return True
    return False


def _text_related(previous: str, current: str) -> bool:
    prev_n = _norm_text(previous)
    curr_n = _norm_text(current)
    if not prev_n or not curr_n:
        return False
    if prev_n == curr_n:
        return True
    if prev_n in curr_n or curr_n in prev_n:
        return True
    if curr_n.startswith(prev_n) or prev_n.startswith(curr_n):
        return True
    # High prefix overlap for near-revisions (e.g. Terry -> Tariqul).
    # Floor raised 8->12 and overlap fraction 0.5->0.65: an 8-char/50%
    # match let two independent same-speaker/same-channel utterances that
    # merely opened with the same common phrase (e.g. "the meeting...")
    # get treated as related. 12 chars minimum plus a majority (65%) of
    # the shorter text keeps the near-revision case working while cutting
    # this false-positive class.
    min_len = min(len(prev_n), len(curr_n))
    prefix = min(min_len, max(12, int(0.65 * min_len)))
    if prefix >= 12 and prev_n[:prefix] == curr_n[:prefix]:
        return True
    return False


def _provider_utterance_id(
    metadata: dict[str, Any],
    *,
    event_id: str = "",
    deepgram_request_id: str = "",
) -> str:
    return str(
        metadata.get("provider_utterance_id")
        or metadata.get("request_id")
        or metadata.get("event_id")
        or deepgram_request_id
        or event_id
        or ""
    )


def _canonical_decision_name(decision: str) -> str:
    normalized = str(decision or "").upper()
    if normalized in (IGNORE_DUPLICATE, "IGNORE"):
        return "IGNORE"
    if normalized in (REPLACE_ACTIVE, EXTEND_ACTIVE, HOLD_FINAL_CHUNK, REPLACE_PROVISIONAL):
        return REPLACE_PROVISIONAL
    if normalized in (SUPERSEDE_PREVIOUS, SUPERSEDE):
        return SUPERSEDE
    if normalized in (COMMIT_ACTIVE, TERMINAL_COMMIT):
        return TERMINAL_COMMIT
    if normalized in (CREATE_ACTIVE, CREATE_NEW_UTTERANCE, CREATE_NEW):
        return CREATE_NEW
    return normalized or CREATE_NEW


@dataclass
class ActiveUtterance:
    utterance_id: str
    session_id: str
    state: str = IDLE
    speaker: int = 1
    channel: Any = None
    text: str = ""
    version: int = 0
    start_time: float = -1.0
    end_time: float = -1.0
    deepgram_request_id: str = ""
    lineage_ids: list[str] = field(default_factory=list)
    committed_record_id: str = ""
    committed: bool = False
    commit_reason: str = ""
    last_event_mono: float = field(default_factory=time.monotonic)
    created_mono: float = field(default_factory=time.monotonic)


@dataclass
class LifecycleDecision:
    decision: str
    reason: str
    utterance_id: str = ""
    text: str = ""
    previous_text: str = ""
    should_update_interim: bool = False
    should_commit: bool = False
    should_supersede_committed: bool = False
    superseded_record_id: str = ""
    version: int = 0
    session_id: str = ""
    event_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class UtteranceLifecycleOwner:
    """Owns one active utterance per session for the English/generic final path."""

    def __init__(
        self,
        *,
        host: Any = None,
        commit_fallback_ms: int = DEFAULT_COMMIT_FALLBACK_MS,
        event_log_path: Optional[Path] = None,
        on_commit: Optional[Callable[[LifecycleDecision], None]] = None,
        on_interim_update: Optional[Callable[[LifecycleDecision], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._host = host
        self._lock = threading.RLock()
        self._commit_fallback_ms = max(250, int(commit_fallback_ms))
        self._event_log_path = event_log_path
        self._on_commit = on_commit
        self._on_interim_update = on_interim_update
        self._clock = clock or time.monotonic
        self._session_id = ""
        self._seq = 0
        self._active: Optional[ActiveUtterance] = None
        self._last_committed: Optional[ActiveUtterance] = None
        self._timeout_token = 0
        self._timeout_after_id: Any = None
        self._events: list[dict[str, Any]] = []
        # fixes BUG-F: commit/interim decisions get queued here while
        # self._lock is held, and are only actually published (which can
        # call into host/Tkinter code) after the lock is released -- see
        # _drain_pending_emits_unlocked.
        self._pending_emits: list[tuple[str, "LifecycleDecision"]] = []
        self._committed_utterance_ids: set[str] = set()
        self._stats = {
            "canonical_commits": 0,
            "translation_jobs_hint": 0,
            "hold_final_chunks": 0,
            "replace_active": 0,
            "extend_active": 0,
            "utterance_end_dedup": 0,
            "timeout_commits": 0,
            "supersessions": 0,
            "sentence_boundary_flushes": 0,
            "in_flight_commits": 0,
            "resent_tails_trimmed": 0,
        }

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    def reset_for_session(self, session_id: str) -> None:
        with self._lock:
            self._cancel_timeout_locked()
            self._session_id = str(session_id or "")
            self._seq = 0
            self._active = None
            self._last_committed = None
            self._committed_utterance_ids.clear()
            self._timeout_token = 0
            self._stats = {k: 0 for k in self._stats}
            self._log_event(
                {
                    "decision": CANCEL_ACTIVE,
                    "decision_reason": "session_reset",
                    "session_id": self._session_id,
                }
            )
        try:
            from alpha.transcription.canonical_identity_registry import reset_for_session

            reset_for_session(self._session_id)
        except Exception as exc:
            # fixes BUG_FIX_ROADMAP.md Batch 2 item 6: logging only -- a
            # failure here previously left the identity registry
            # unreset with zero trace, risking a new session inheriting
            # stale identity entries from the previous one.
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "IDENTITY_REGISTRY_RESET_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    session_id=self._session_id,
                )
            except Exception:
                pass

    def bind_host(self, host: Any) -> None:
        self._host = host

    def set_event_log_path(self, path: Optional[Path]) -> None:
        self._event_log_path = path

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def active(self) -> Optional[ActiveUtterance]:
        return self._active

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stats)

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def _observe_identity(
        self,
        *,
        utterance_id: str,
        channel: Any,
        version: int,
        decision: str,
        text: str,
        lifecycle_state: str,
        translation_eligible: bool,
        metadata: dict[str, Any],
        deepgram_request_id: str = "",
        event_id: str = "",
    ) -> tuple[bool, str, dict[str, Any]]:
        try:
            from alpha.transcription.canonical_identity_registry import observe_identity

            result = observe_identity(
                session_id=self._session_id,
                channel_index=channel,
                canonical_utterance_id=utterance_id,
                provider_utterance_id=_provider_utterance_id(
                    metadata,
                    event_id=event_id,
                    deepgram_request_id=deepgram_request_id,
                ),
                source_version=version,
                decision=_canonical_decision_name(decision),
                text=text,
                lifecycle_state=lifecycle_state,
                translation_eligible=translation_eligible,
            )
            return bool(result.accepted), str(result.reason or ""), dict(result.entry or {})
        except Exception as exc:
            # fixes BUG_FIX_ROADMAP.md Batch 2 item 8: logging only. This
            # is a fail-OPEN path in a file whose whole design is
            # fail-closed -- on any exception here, the one gate meant to
            # prevent duplicate/cross-utterance mutation is silently
            # bypassed. NOT changing fail-open to fail-closed in this
            # batch (that's a real behavior change needing evidence of
            # how often this actually fires -- see item 27); this only
            # makes the bypass visible.
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "OBSERVE_IDENTITY_FAILED_OPEN",
                    reason=f"{type(exc).__name__}:{exc}",
                    session_id=self._session_id,
                    channel_index=channel,
                    canonical_utterance_id=utterance_id,
                    source_version=version,
                )
            except Exception:
                pass
            return True, "unavailable", {}

    def _resolve_correction_target_locked(
        self,
        *,
        channel: Any,
        metadata: dict[str, Any],
        fallback_utterance_id: str = "",
    ) -> tuple[str, str]:
        target_record_id = str(metadata.get("revision_target_id") or "").strip()
        target_utterance_id = str(metadata.get("canonical_utterance_id") or "").strip()
        if not target_utterance_id:
            # No upstream system supplied an explicit identity link — this is
            # the normal case for raw Deepgram English/generic finals, which
            # have no such concept. Fall back to the id we already track
            # internally for the previously committed utterance and let the
            # canonical identity registry (already populated by
            # duplicate_protection.py's commit path) resolve it, instead of
            # giving up before even trying.
            target_utterance_id = str(fallback_utterance_id or "").strip()
        if not target_record_id and not target_utterance_id:
            return "", ""
        try:
            from alpha.transcription.canonical_identity_registry import resolve_canonical_record_id

            if target_utterance_id:
                # The real ledger commit + assign_canonical_record_id for
                # this utterance (duplicate_protection.py's
                # _display_transcript_item) runs later than this in-memory
                # decision -- it's dispatched via transcript_queue and only
                # drained on the Tk main thread's ~100ms (UI_QUEUE_POLL_MS)
                # poll tick, not synchronously with the commit above. A
                # correction/extend candidate for the same utterance that
                # arrives before that tick lands finds nothing registered
                # yet and would otherwise be wrongly rejected. Bounded
                # retry across that window (3 lookups, ~60ms apart, ~120ms
                # total) closes most of the gap; self._lock is released
                # during each sleep (same thread, RLock, balanced) so this
                # never blocks other channels/utterances on this owner.
                # If every attempt still comes up empty, behavior is
                # unchanged from before: fall through to the existing
                # graceful "no exact target" rejection below.
                exact_record_id = ""
                for attempt in range(3):
                    exact_record_id = str(
                        resolve_canonical_record_id(
                            session_id=self._session_id,
                            channel_index=channel,
                            canonical_utterance_id=target_utterance_id,
                        )
                        or ""
                    )
                    if exact_record_id or attempt == 2:
                        break
                    self._lock.release()
                    try:
                        time.sleep(0.06)
                    finally:
                        self._lock.acquire()
                if not exact_record_id:
                    return "", target_utterance_id
                if target_record_id and target_record_id != exact_record_id:
                    return "", target_utterance_id
                return exact_record_id, target_utterance_id
        except Exception as exc:
            # fixes BUG_FIX_ROADMAP.md Batch 2 item 6: logging only -- on
            # any exception here, this function falls through to
            # returning the raw, UNVERIFIED target_record_id/
            # target_utterance_id below instead of the registry-resolved
            # exact match. Behavior is unchanged (still fails open to the
            # raw values); this only makes that fallback visible.
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "CORRECTION_TARGET_RESOLUTION_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    session_id=self._session_id,
                    channel_index=channel,
                    canonical_utterance_id=target_utterance_id,
                    revision_target_id=target_record_id,
                )
            except Exception:
                pass
        return target_record_id, target_utterance_id

    # ------------------------------------------------------------------
    # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 1 / REPAIR_PLAN.md Phase 2
    # ("only the canonical utterance controller may decide create/extend/
    # replace/commit/supersede/ignore; other modules may only recommend"):
    # a single entry point for a caller that has already decided its own
    # HOLD/EXTEND/COMMIT-shaped boundary action (the Japanese assembler's
    # existing, already-correct boundary/revision-decision chain from Tasks
    # 2B-2D) to PROPOSE that action here instead of calling
    # execute_pipeline_commit and the identity registry independently.
    # This does not re-decide the boundary/timing strategy itself --
    # REPAIR_PLAN.md explicitly allows Japanese and English to use
    # different boundary strategies, only requiring one shared commit/
    # identity system, which this method now is (mirroring exactly what
    # duplicate_protection.py already does for the English/manual-mode
    # path, so there is one identity-registration + commit implementation,
    # not two).
    # ------------------------------------------------------------------
    def accept_boundary_proposal(
        self,
        *,
        action: str,
        text: str,
        speaker: int = 1,
        channel: Any = None,
        canonical_utterance_id: str = "",
        source_version: int = 1,
        revision_target_id: str = "",
        provider_utterance_id: str = "",
        source_raw_event_ids: Optional[list[str]] = None,
        commit_reason: str = "",
        lifecycle_state: str = "",
        translation_eligible: bool = True,
        extra_commit_kwargs: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Accept a HOLD/EXTEND/COMMIT-shaped proposal from a boundary
        strategy (e.g. the Japanese assembler). `action` is one of
        "hold" (no commit), "commit_new" (append), "revise_previous".
        Returns {"accepted", "success", "record_id", "reason", ...} --
        never raises; the caller must not commit on any falsy `success`.
        """
        result: dict[str, Any] = {
            "accepted": False,
            "success": False,
            "record_id": "",
            "reason": "",
            "applied_action": "",
            "transaction_id": "",
            "metadata": {},
            "canonical_utterance_id": str(canonical_utterance_id or ""),
            "source_version": int(source_version or 1),
        }
        if action == "hold":
            result["reason"] = "hold_no_commit_requested"
            return result

        cleaned = (text or "").strip()
        if not cleaned:
            result["reason"] = "empty_text"
            return result

        utterance_id = str(canonical_utterance_id or "").strip()
        if not utterance_id:
            # fixes TASK_1A_FINDINGS.md Pattern 1 lineage: no canonical
            # identity to register against -- fail closed, never guess.
            result["reason"] = "missing_canonical_utterance_id"
            return result

        with self._lock:
            session_id = self._session_id

        decision_name = "SUPERSEDE" if action == "revise_previous" else "CREATE_NEW"

        try:
            from alpha.transcription.canonical_identity_registry import (
                assign_canonical_record_id,
                observe_identity,
            )
            from alpha.transcription.pipeline_commit_transaction import (
                execute_pipeline_commit,
            )
        except Exception as exc:
            result["reason"] = f"import_failed:{type(exc).__name__}:{exc}"
            return result

        try:
            identity = observe_identity(
                session_id=session_id,
                channel_index=channel,
                canonical_utterance_id=utterance_id,
                provider_utterance_id=str(provider_utterance_id or ""),
                source_version=int(source_version or 1),
                decision=decision_name,
                text=cleaned,
                lifecycle_state=lifecycle_state or "COMMITTED",
                translation_eligible=bool(translation_eligible),
            )
        except Exception as exc:
            result["reason"] = f"observe_identity_failed:{type(exc).__name__}:{exc}"
            return result

        if not identity.accepted:
            result["reason"] = identity.reason or "identity_rejected"
            return result
        if identity.duplicate:
            result.update(
                {
                    "accepted": True,
                    "success": False,
                    "reason": identity.reason or "duplicate",
                    "record_id": str((identity.entry or {}).get("canonical_record_id", "")),
                }
            )
            return result

        applied = "revise" if action == "revise_previous" else "append"
        resolved_target = str(revision_target_id or "")
        if applied == "revise":
            with self._lock:
                exact_target, _ = self._resolve_correction_target_locked(
                    channel=channel,
                    metadata={
                        "revision_target_id": resolved_target,
                        "canonical_utterance_id": utterance_id,
                    },
                )
            if not exact_target:
                # fixes TASK_1A_FINDINGS.md Pattern 1: no exact target found
                # -- reject the revision rather than guess/fall back.
                result["reason"] = "missing_exact_revision_target"
                return result
            resolved_target = exact_target

        try:
            kwargs = dict(extra_commit_kwargs or {})
            txn = execute_pipeline_commit(
                speaker=int(speaker or 1),
                assembler_text=cleaned,
                final_text=cleaned,
                requested_action=applied,
                applied_action=applied,
                revision_target_id=resolved_target,
                source_raw_event_ids=list(source_raw_event_ids or []),
                commit_reason=commit_reason or "japanese_boundary_proposal",
                metadata={
                    "source": "utterance_lifecycle_accept_boundary_proposal",
                    "session_id": session_id,
                    "channel_index": channel,
                    "canonical_utterance_id": utterance_id,
                    "source_version": int(source_version or 1),
                    "canonical_decision": decision_name,
                    "idempotency_decision": decision_name,
                    "translation_eligible": bool(translation_eligible),
                    "synthetic_record": not bool(source_raw_event_ids),
                },
                **kwargs,
            )
        except Exception as exc:
            result["reason"] = f"commit_failed:{type(exc).__name__}:{exc}"
            return result

        if not txn.success:
            result.update(
                {"accepted": True, "success": False, "reason": txn.failure_reason or "commit_not_successful"}
            )
            return result

        try:
            assign_result = assign_canonical_record_id(
                session_id=session_id,
                channel_index=channel,
                canonical_utterance_id=utterance_id,
                canonical_record_id=str(txn.record_id or ""),
            )
        except Exception as exc:
            assign_result = None
            result["reason"] = f"assign_canonical_record_id_failed:{type(exc).__name__}:{exc}"

        identity_assigned = bool(assign_result.accepted) if assign_result is not None else False

        # fixes TASK_6_REPORT.md P0 (ALPHA_ARCHITECTURE_DEBUG_REPORT.md
        # "Canonical commit reports success when identity assignment
        # fails"): identity binding and ledger commit must be atomic. The
        # ledger transaction above already applied a real mutation
        # (txn.success is True at this point) -- if identity binding fails
        # or rejects afterward, success must NOT be True, and the ledger
        # record must be explicitly quarantined (suppressed, not left as a
        # silent orphan a later revision/translation could address wrongly)
        # rather than just returning success=False with no downstream
        # signal.
        if not identity_assigned:
            quarantine_reason = str(result.get("reason") or "identity_assignment_failed")
            try:
                from alpha.transcription.canonical_transcript_ledger import (
                    suppress_record,
                )

                suppress_record(
                    record_id=str(txn.record_id or ""),
                    suppression_reason=f"identity_incomplete:{quarantine_reason}",
                    commit_reason=commit_reason or "japanese_boundary_proposal",
                    transaction_id=str(txn.transaction_id or ""),
                )
            except Exception as exc:
                # Quarantine itself failing must not be swallowed into a
                # false success either -- record it and still fail closed.
                quarantine_reason = f"{quarantine_reason};quarantine_failed:{type(exc).__name__}:{exc}"
            result.update(
                {
                    "accepted": True,
                    "success": False,
                    "reason": quarantine_reason,
                    "record_id": str(txn.record_id or ""),
                    "identity_assigned": False,
                    "quarantined": True,
                    "evidence_write_failed": bool(txn.evidence_write_failed),
                    "metrics_write_failed": bool(txn.metrics_write_failed),
                    "applied_action": applied,
                    "transaction_id": txn.transaction_id,
                    "metadata": dict(txn.metadata or {}),
                    "canonical_utterance_id": utterance_id,
                    "source_version": int(source_version or 1),
                }
            )
            return result

        result.update(
            {
                "accepted": True,
                "success": True,
                "record_id": str(txn.record_id or ""),
                "identity_assigned": True,
                "quarantined": False,
                "evidence_write_failed": bool(txn.evidence_write_failed),
                "metrics_write_failed": bool(txn.metrics_write_failed),
                "applied_action": applied,
                "transaction_id": txn.transaction_id,
                "metadata": dict(txn.metadata or {}),
                "canonical_utterance_id": utterance_id,
                "source_version": int(source_version or 1),
            }
        )
        return result

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def on_interim(
        self,
        *,
        text: str,
        speaker: int = 1,
        channel: Any = None,
        start: Any = None,
        end: Any = None,
        event_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleDecision:
        try:
            return self._ingest(
                text=text,
                speaker=speaker,
                channel=channel,
                start=start,
                end=end,
                is_final=False,
                speech_final=False,
                event_id=event_id or f"interim-{time.time_ns()}",
                metadata=metadata or {},
                source="interim",
            )
        finally:
            self._drain_pending_emits_unlocked()

    def on_final_chunk(
        self,
        *,
        text: str,
        speaker: int = 1,
        channel: Any = None,
        start: Any = None,
        end: Any = None,
        is_final: bool = True,
        speech_final: Any = None,
        event_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
        deepgram_request_id: str = "",
    ) -> LifecycleDecision:
        try:
            return self._ingest(
                text=text,
                speaker=speaker,
                channel=channel,
                start=start,
                end=end,
                is_final=bool(is_final),
                speech_final=speech_final,
                event_id=event_id or f"final-{time.time_ns()}",
                metadata=metadata or {},
                source="final",
                deepgram_request_id=deepgram_request_id,
            )
        finally:
            # fixes BUG-F: publish only after self._lock (acquired inside
            # _ingest) has been released.
            self._drain_pending_emits_unlocked()

    def commit_in_flight(
        self, *, reason: str = "provider_disconnected"
    ) -> Optional[LifecycleDecision]:
        """Commit the utterance that was still open when the provider dropped.

        Item 44's third requirement -- "commit in-flight" -- which was never
        implemented. Measured by driving this class: an utterance in progress
        when the socket dies is neither merged into the post-reconnect text nor
        committed. The next final arrives with the provider's restarted clock,
        `_timing_compatible` correctly rejects it as a continuation, and
        `_apply_active_update_locked`'s `force_new` branch then REPLACES
        `self._active` outright. The old text is dropped with no commit, no log
        and no trace -- every reconnect silently loses whatever was spoken but
        not yet committed before the drop.

        Committing here also puts it in the right place: this runs on the
        unexpected close, so the pre-drop speech lands BEFORE the gap marker
        that `_deepgram_on_open` emits, and post-reconnect speech starts its own
        utterance after it. That is the order item 44's own title asks for --
        commit in-flight, then mark the gap.

        Returns the commit decision, or None when there was nothing in flight
        (no active utterance, already committed, or empty text) -- all of which
        are normal and silent.

        The reason is deliberately absent from `_PREMATURE_COMMIT_REASONS`: a
        provider disconnect is a hard boundary, and letting the first
        post-reconnect chunk extend this record would glue speech from either
        side of the hole into one line.
        """
        try:
            with self._lock:
                active = self._active
                if active is None or active.committed or not (active.text or "").strip():
                    return None
                decision = self._commit_locked(
                    reason=reason,
                    event_id=f"disconnect-{time.time_ns()}",
                    metadata={"commit_in_flight": True, "disconnect_reason": reason},
                    decision_name=COMMIT_ACTIVE,
                )
            if decision.should_commit:
                self._stats["in_flight_commits"] = (
                    self._stats.get("in_flight_commits", 0) + 1
                )
            return decision
        finally:
            self._drain_pending_emits_unlocked()

    def on_utterance_end(
        self,
        *,
        channel: Any = None,
        event_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleDecision:
        try:
            with self._lock:
                active = self._active
                if active is None or not (active.text or "").strip():
                    d = LifecycleDecision(
                        decision=IGNORE_DUPLICATE,
                        reason="utterance_end_no_active",
                        session_id=self._session_id,
                        event_id=event_id or f"ue-{time.time_ns()}",
                    )
                    self._record_decision(d, is_final=True, speech_final=None, channel=channel)
                    return d
                if active.committed or active.utterance_id in self._committed_utterance_ids:
                    self._stats["utterance_end_dedup"] += 1
                    d = LifecycleDecision(
                        decision=IGNORE_DUPLICATE,
                        reason="utterance_end_already_committed",
                        utterance_id=active.utterance_id,
                        text=active.text,
                        session_id=self._session_id,
                        event_id=event_id or f"ue-{time.time_ns()}",
                        version=active.version,
                    )
                    self._record_decision(d, is_final=True, speech_final=True, channel=channel)
                    return d
                if not _channel_matches_exactly(active.channel, channel):
                    try:
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                        jp_accuracy_log(
                            "CROSS_CHANNEL_END_IGNORED",
                            session_id=self._session_id,
                            active_channel=active.channel,
                            observed_channel=channel,
                            canonical_utterance_id=active.utterance_id,
                        )
                    except Exception:
                        pass
                    d = LifecycleDecision(
                        decision=IGNORE_DUPLICATE,
                        reason="cross_channel_utterance_end_ignored",
                        utterance_id=active.utterance_id,
                        text=active.text,
                        session_id=self._session_id,
                        event_id=event_id or f"ue-{time.time_ns()}",
                        version=active.version,
                        metadata={
                            "channel": active.channel,
                            "canonical_utterance_id": active.utterance_id,
                            "source_version": active.version,
                            "canonical_decision": "IGNORE",
                            "translation_eligible": False,
                        },
                    )
                    self._record_decision(d, is_final=True, speech_final=None, channel=channel)
                    return d
                return self._commit_locked(
                    reason="utterance_end",
                    event_id=event_id or f"ue-{time.time_ns()}",
                    metadata=dict(metadata or {}),
                    decision_name=COMMIT_ACTIVE,
                )
        finally:
            self._drain_pending_emits_unlocked()

    def on_timeout(self, *, token: int) -> Optional[LifecycleDecision]:
        try:
            with self._lock:
                if token != self._timeout_token:
                    return None
                active = self._active
                if active is None or not (active.text or "").strip():
                    return None
                if active.committed or active.utterance_id in self._committed_utterance_ids:
                    return None
                if active.state not in (ACTIVE_FINAL_CHUNK, ACTIVE_INTERIM, READY_TO_COMMIT):
                    return None
                self._stats["timeout_commits"] += 1
                return self._commit_locked(
                    reason="inactivity_timeout_fallback",
                    event_id=f"timeout-{time.time_ns()}",
                    metadata={"timeout_ms": self._commit_fallback_ms},
                    decision_name=COMMIT_ACTIVE,
                )
        finally:
            # fixes BUG-F: this is the exact call site from the confirmed
            # thread dump (main thread stuck here trying to re-acquire
            # self._lock while the WS thread held it inside a publish
            # call). Publishing must happen after release, not before.
            self._drain_pending_emits_unlocked()

    def force_cancel_active(self, reason: str = "cancelled") -> LifecycleDecision:
        with self._lock:
            self._cancel_timeout_locked()
            active = self._active
            text = active.text if active else ""
            uid = active.utterance_id if active else ""
            if active:
                active.state = CANCELLED
            self._active = None
            d = LifecycleDecision(
                decision=CANCEL_ACTIVE,
                reason=reason,
                utterance_id=uid,
                text=text,
                session_id=self._session_id,
            )
            self._record_decision(d, is_final=False, speech_final=None, channel=None)
            return d

    # ------------------------------------------------------------------
    # Core ingest
    # ------------------------------------------------------------------
    def _ingest(
        self,
        *,
        text: str,
        speaker: int,
        channel: Any,
        start: Any,
        end: Any,
        is_final: bool,
        speech_final: Any,
        event_id: str,
        metadata: dict[str, Any],
        source: str,
        deepgram_request_id: str = "",
    ) -> LifecycleDecision:
        lexical = (text or "").strip()
        if not lexical:
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="empty_text",
                session_id=self._session_id,
                event_id=event_id,
            )
            self._record_decision(
                d, is_final=is_final, speech_final=speech_final, channel=channel
            )
            return d

        cand_start = _as_float(start, _as_float(metadata.get("start_time"), -1.0))
        cand_end = _as_float(end, _as_float(metadata.get("end_time"), -1.0))
        sf = speech_final
        if isinstance(sf, str):
            sf = sf.strip().lower() in ("1", "true", "yes")
        elif sf is not None:
            sf = bool(sf)

        with self._lock:
            session_id = self._session_id or str(
                getattr(self._host, "_live_session_id", "") or ""
            )
            if session_id and self._session_id and session_id != self._session_id:
                d = LifecycleDecision(
                    decision=IGNORE_DUPLICATE,
                    reason="session_mismatch",
                    session_id=self._session_id,
                    event_id=event_id,
                    text=lexical,
                )
                self._record_decision(
                    d, is_final=is_final, speech_final=sf, channel=channel
                )
                return d
            if not self._session_id:
                self._session_id = session_id or f"sess-local-{uuid.uuid4().hex[:8]}"

            active = self._active
            previous_text = active.text if active else ""

            # Case A — interim only
            if not is_final:
                if (
                    active is not None
                    and not active.committed
                    and not self._compatible_with_active_locked(
                        speaker=speaker,
                        channel=channel,
                        cand_start=cand_start,
                        cand_end=cand_end,
                        lexical=lexical,
                    )
                ):
                    # fixes: interim updates previously bypassed all
                    # speaker/channel/timing compatibility checks and could
                    # silently merge into (and corrupt) an unrelated held
                    # utterance. An incompatible interim is now ignored so
                    # the held utterance is left untouched, instead of being
                    # merged or discarded.
                    d = LifecycleDecision(
                        decision=IGNORE_DUPLICATE,
                        reason="interim_incompatible_with_active_utterance",
                        utterance_id=active.utterance_id,
                        text=active.text,
                        previous_text=active.text,
                        session_id=self._session_id,
                        event_id=event_id,
                        version=active.version,
                    )
                    self._record_decision(
                        d, is_final=False, speech_final=False, channel=channel
                    )
                    return d
                d = self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=ACTIVE_INTERIM,
                    hold=False,
                    speech_final=False,
                    source=source,
                )
                # Pure-interim-only utterances previously never armed a
                # fallback timer, so if Deepgram never promotes this
                # utterance to is_final=True (e.g. the stream simply stops
                # producing further results), it could never commit and the
                # interim marker would stay on screen indefinitely.
                # on_timeout() already explicitly supports committing from
                # ACTIVE_INTERIM state — arming here just makes that reachable.
                self._arm_timeout_locked()
                return d

            # Duplicate of already-committed active
            if (
                active
                and active.committed
                and _norm_text(active.text) == _norm_text(lexical)
            ):
                d = LifecycleDecision(
                    decision=IGNORE_DUPLICATE,
                    reason="duplicate_of_committed",
                    utterance_id=active.utterance_id,
                    text=lexical,
                    previous_text=previous_text,
                    session_id=self._session_id,
                    event_id=event_id,
                    version=active.version,
                )
                self._record_decision(d, is_final=True, speech_final=sf, channel=channel)
                return d

            same_active = self._compatible_with_active_locked(
                speaker=speaker,
                channel=channel,
                cand_start=cand_start,
                cand_end=cand_end,
                lexical=lexical,
            )

            # Authoritative correction of last committed (same timing/lineage).
            # Must run even when active is None after a prior COMMIT.
            if (
                self._last_committed is not None
                and (active is None or not same_active)
                and self._is_correction_of_committed_locked(
                    lexical=lexical,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    metadata=metadata,
                )
            ):
                return self._supersede_committed_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    speech_final=sf if sf is not None else True,
                )

            # fixes BUG-E: not a same-text correction, but may still be the
            # next part of a sentence whose previous chunk we committed
            # early ourselves (uncertain fallback, not a confident
            # boundary). Append rather than starting an unrelated new line.
            if (
                self._last_committed is not None
                and (active is None or not same_active)
                and self._is_premature_continuation_locked(
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                )
            ):
                extended = self._extend_committed_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    speech_final=sf if sf is not None else True,
                )
                # fixes BUG-G1: None means the extend couldn't be resolved --
                # fall through to Case B/C below exactly as if this branch
                # had not matched, instead of returning a lost-text decision.
                if extended is not None:
                    return extended

            # Case B — final chunk, utterance incomplete
            if is_final and sf is False:
                d = self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=ACTIVE_FINAL_CHUNK,
                    hold=True,
                    speech_final=False,
                    source=source,
                    # fixes problem C (item 23). This was
                    # `not same_active and active is not None and active.committed`,
                    # so a candidate that was NOT compatible -- a different
                    # speaker or a different channel -- still merged into the
                    # active utterance whenever that utterance happened to be
                    # uncommitted, which is the normal state for a held final
                    # chunk. Case C directly below has always gated correctly
                    # (it merges only when `same_active or active is None or
                    # not active.text.strip()`); Case B is now aligned to it, so
                    # an incompatible candidate starts its own utterance
                    # regardless of whether the active one has committed yet.
                    force_new=active is not None and (active.committed or not same_active),
                )
                self._stats["hold_final_chunks"] += 1
                self._arm_timeout_locked()
                return d

            # Case C — speech_final true (or unknown final treated carefully)
            if is_final and (sf is True or sf is None):
                # If sf is None on English finals, prefer hold when active incomplete
                # chunk exists and candidate is compatible; otherwise commit.
                if sf is None and active and active.state == ACTIVE_FINAL_CHUNK and same_active:
                    d = self._apply_active_update_locked(
                        lexical=lexical,
                        speaker=speaker,
                        channel=channel,
                        cand_start=cand_start,
                        cand_end=cand_end,
                        event_id=event_id,
                        metadata=metadata,
                        deepgram_request_id=deepgram_request_id,
                        state=ACTIVE_FINAL_CHUNK,
                        hold=True,
                        speech_final=None,
                        source=source,
                    )
                    self._arm_timeout_locked()
                    return d

                if same_active or active is None or not (active.text or "").strip():
                    self._apply_active_update_locked(
                        lexical=lexical,
                        speaker=speaker,
                        channel=channel,
                        cand_start=cand_start,
                        cand_end=cand_end,
                        event_id=event_id,
                        metadata=metadata,
                        deepgram_request_id=deepgram_request_id,
                        state=READY_TO_COMMIT,
                        hold=False,
                        speech_final=True,
                        source=source,
                        force_new=active is None,
                        emit_interim=False,
                    )
                    return self._commit_locked(
                        reason="speech_final",
                        event_id=event_id,
                        metadata=metadata,
                        decision_name=COMMIT_ACTIVE,
                    )

                # Incompatible with active → commit previous if held, then new
                if active and not active.committed and (active.text or "").strip():
                    self._commit_locked(
                        reason="boundary_before_new_utterance",
                        event_id=f"{event_id}-flush",
                        metadata=metadata,
                        decision_name=COMMIT_ACTIVE,
                    )
                self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=READY_TO_COMMIT,
                    hold=False,
                    speech_final=True,
                    source=source,
                    force_new=True,
                    emit_interim=False,
                )
                return self._commit_locked(
                    reason="speech_final_new_utterance",
                    event_id=event_id,
                    metadata=metadata,
                    decision_name=CREATE_NEW_UTTERANCE,
                )

            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="unhandled_flags",
                text=lexical,
                session_id=self._session_id,
                event_id=event_id,
            )
            self._record_decision(d, is_final=is_final, speech_final=sf, channel=channel)
            return d

    def _compatible_with_active_locked(
        self,
        *,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        lexical: str,
    ) -> bool:
        active = self._active
        if active is None or not (active.text or "").strip():
            return True
        if active.committed:
            return False
        if active.session_id and self._session_id and active.session_id != self._session_id:
            return False
        # fixes problem C (item 22). This was
        # `int(active.speaker or 1) != int(speaker or 1)`, which coerced every
        # unknown speaker to 1 -- so two *unidentified* speakers compared equal
        # and one speaker's turn merged into another's line. Fail-closed now:
        # an unknown speaker on either side is never a confirmed match, which
        # is the same primitive already used by the boundary stabilizer, the
        # transcript store and the stable-revision decision.
        if not speakers_confirmed_same(
            _known_speaker(active.speaker), _known_speaker(speaker)
        ):
            return False
        if not _channel_matches_exactly(active.channel, channel):
            # fixes TASK_1A_FINDINGS.md Pattern 2: _channels_compatible() treated
            # a None channel on either side as an automatic match, which allowed
            # a candidate on a known channel to merge into an active utterance
            # whose channel was unset (or vice versa). Exact match only.
            return False
        timing_ok = _timing_compatible(
            active.start_time, active.end_time, cand_start, cand_end
        )
        text_ok = _text_related(active.text, lexical)
        # Prefer lineage/timing; text overlap only as bounded fallback.
        if timing_ok:
            return True
        if text_ok and active.state in (ACTIVE_INTERIM, ACTIVE_FINAL_CHUNK, READY_TO_COMMIT):
            # Fallback requires non-terminal + session already matched + channel ok.
            return True
        # fixes TASK_2A_FINDINGS.md Item 4/5: a held final chunk with no
        # timing data on the candidate was previously treated as compatible
        # unconditionally, letting an unrelated utterance merge into the
        # held one. Missing timing data is no longer an automatic match --
        # timing_ok/text_ok above already cover every case that should merge.
        return False

    def _log_identity_mismatch(self, reason: str, *, prev: "ActiveUtterance", channel: Any) -> None:
        # fixes TASK_1A_FINDINGS.md Pattern 1: "no exact match -> reject revision,
        # log identity mismatch" was previously silent for correction-target
        # rejections in _is_correction_of_committed_locked.
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "IDENTITY_REJECTION",
                reason=reason,
                session_id=self._session_id,
                active_channel=prev.channel,
                observed_channel=channel,
                canonical_utterance_id=prev.utterance_id,
                committed_record_id=prev.committed_record_id,
            )
        except Exception:
            pass

    def _is_correction_of_committed_locked(
        self,
        *,
        lexical: str,
        channel: Any,
        cand_start: float,
        cand_end: float,
        metadata: dict[str, Any],
    ) -> bool:
        prev = self._last_committed
        if prev is None or not prev.committed:
            return False
        if not _channel_matches_exactly(prev.channel, channel):
            self._log_identity_mismatch("channel_mismatch", prev=prev, channel=channel)
            return False
        target_record_id, target_utterance_id = self._resolve_correction_target_locked(
            channel=channel,
            metadata=metadata,
            fallback_utterance_id=prev.utterance_id,
        )
        if not target_record_id:
            self._log_identity_mismatch("no_exact_revision_target", prev=prev, channel=channel)
            return False
        # NOTE: prev.committed_record_id is not currently populated anywhere
        # in this class (it stays at its "" default for the lifetime of the
        # process) — only enforce this cross-check when it actually holds a
        # value, so a genuinely resolved registry hit above isn't rejected
        # against an always-empty field. If a future change starts populating
        # committed_record_id, this check becomes fully active again as-is.
        if prev.committed_record_id and target_record_id != str(prev.committed_record_id):
            self._log_identity_mismatch("revision_target_record_mismatch", prev=prev, channel=channel)
            return False
        if target_utterance_id and target_utterance_id != str(prev.utterance_id or ""):
            self._log_identity_mismatch("revision_target_utterance_mismatch", prev=prev, channel=channel)
            return False
        # fixes DIAGNOSTIC-H: this check and _text_related below previously
        # failed completely silently -- no way to tell, from any log, which
        # of the two was rejecting a real continuation/correction, or with
        # what actual values. Both branches below are diagnostic-only additions.
        if not _timing_compatible(prev.start_time, prev.end_time, cand_start, cand_end):
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "CORRECTION_GATE_TIMING_MISMATCH",
                    session_id=self._session_id,
                    canonical_utterance_id=prev.utterance_id,
                    prev_start_time=prev.start_time,
                    prev_end_time=prev.end_time,
                    cand_start=cand_start,
                    cand_end=cand_end,
                )
            except Exception:
                pass
            return False
        related = _text_related(prev.text, lexical)
        if not related:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "CORRECTION_GATE_TEXT_MISMATCH",
                    session_id=self._session_id,
                    canonical_utterance_id=prev.utterance_id,
                    prev_text_preview=(prev.text or "")[:80],
                    cand_text_preview=(lexical or "")[:80],
                )
            except Exception:
                pass
        return related

    def _trim_resent_tail_locked(
        self,
        lexical: str,
        *,
        channel: Any,
        speaker: Any,
        cand_start: float = -1.0,
        cand_end: float = -1.0,
    ) -> str:
        """Item 66: drop a head that repeats the previously committed tail.

        Only fires when the previous record is genuinely the thing this text
        continues: same channel, same confirmed speaker, and not committed at a
        hard boundary. A provider disconnect is excluded because the words on
        either side of the hole are unrelated, so an apparent overlap there is
        coincidence rather than the provider repeating itself.

        Returns `lexical` unchanged whenever anything is uncertain -- this can
        remove text, so every gate fails closed.
        """
        prev = self._last_committed
        if prev is None or not prev.committed or not (lexical or "").strip():
            return lexical
        if str(prev.commit_reason or "") in _HARD_BOUNDARY_COMMIT_REASONS:
            return lexical
        if not _channel_matches_exactly(prev.channel, channel):
            return lexical
        # Same speaker, OR the two spans overlap on the audio clock. The second
        # is what live run `...20260814-101813` needed: records [4] and [5]
        # share the re-sent run "and the number 1 thing", but the provider
        # labelled them speaker 2 and speaker 1, so the speaker gate alone
        # refused and the duplicate survived into the export. Their audio spans
        # were 131.56-138.24 and 136.32-160.48 -- overlapping by ~1.9s, which
        # is the same audio arriving twice, so the label disagreement is a
        # diarization artifact rather than two people talking.
        #
        # Deliberately an OR, not a replacement: overlapping audio is stronger
        # evidence than a speaker label here, and it is the same discriminator
        # item 64 already relies on. A genuine speaker change does NOT produce
        # overlapping spans, so this cannot reopen problem C's cross-speaker
        # merging -- items 22/23/24's guard stays the sole rule whenever timing
        # is absent, which is when it fails closed.
        same_speaker = speakers_confirmed_same(
            _known_speaker(prev.speaker), _known_speaker(speaker)
        )
        overlapping_audio = _audio_spans_overlap(
            prev.start_time, prev.end_time, cand_start, cand_end
        )
        if not same_speaker and not overlapping_audio:
            return lexical
        trimmed = _strip_committed_tail_prefix(prev.text, lexical)
        if trimmed is None or trimmed == lexical:
            return lexical
        self._stats["resent_tails_trimmed"] += 1
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "RESENT_TAIL_TRIMMED",
                reason="head_duplicated_previously_committed_tail",
                session_id=self._session_id,
                canonical_utterance_id=prev.utterance_id,
                prev_commit_reason=str(prev.commit_reason or ""),
                removed_preview=(lexical or "")[: max(0, len(lexical) - len(trimmed))][:120],
            )
        except Exception:
            pass
        return trimmed

    def _is_premature_continuation_locked(
        self,
        *,
        channel: Any,
        cand_start: float,
        cand_end: float,
    ) -> bool:
        """Is this candidate plausibly the *next part* of an utterance whose
        previous chunk was committed early by our own uncertain fallback,
        rather than a confident Deepgram/boundary signal? Deliberately does
        NOT check text similarity (unlike _is_correction_of_committed_locked)
        -- a genuine continuation typically has completely different words
        from what came before it; that's expected, not a red flag. The
        safety gate here is instead: the previous commit must have been an
        uncertain, app-driven guess, plus the same tight-timing/matching-
        channel signals already used elsewhere in this file for "plausibly
        still part of the same utterance."
        """
        prev = self._last_committed
        if prev is None or not prev.committed:
            return False
        # fixes DIAGNOSTIC-H: all three checks below previously failed
        # completely silently. Diagnostic-only additions, no logic changed.
        if prev.commit_reason not in _PREMATURE_COMMIT_REASONS:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "CONTINUATION_GATE_REASON_MISMATCH",
                    session_id=self._session_id,
                    canonical_utterance_id=prev.utterance_id,
                    prev_commit_reason=prev.commit_reason,
                )
            except Exception:
                pass
            return False
        if not _channel_matches_exactly(prev.channel, channel):
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "CONTINUATION_GATE_CHANNEL_MISMATCH",
                    session_id=self._session_id,
                    canonical_utterance_id=prev.utterance_id,
                    prev_channel=str(prev.channel),
                    cand_channel=str(channel),
                )
            except Exception:
                pass
            return False
        compatible = _timing_compatible(prev.start_time, prev.end_time, cand_start, cand_end)
        if not compatible:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "CONTINUATION_GATE_TIMING_MISMATCH",
                    session_id=self._session_id,
                    canonical_utterance_id=prev.utterance_id,
                    prev_start_time=prev.start_time,
                    prev_end_time=prev.end_time,
                    cand_start=cand_start,
                    cand_end=cand_end,
                )
            except Exception:
                pass
        return compatible

    def _apply_active_update_locked(
        self,
        *,
        lexical: str,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        event_id: str,
        metadata: dict[str, Any],
        deepgram_request_id: str,
        state: str,
        hold: bool,
        speech_final: Any,
        source: str,
        force_new: bool = False,
        emit_interim: bool = True,
    ) -> LifecycleDecision:
        active = self._active
        previous_text = active.text if active else ""

        if force_new or active is None or active.committed:
            # Item 66: a commit that landed mid-sentence is followed by the
            # provider re-sending that span. The previous utterance is already
            # committed, so `_merge_lexical`'s overlap machinery never sees it
            # and the tail ends up stored in two records. Trim it here, at the
            # one point a new utterance inherits text straight from the wire.
            lexical = self._trim_resent_tail_locked(
                lexical,
                channel=channel,
                speaker=speaker,
                cand_start=cand_start,
                cand_end=cand_end,
            )
            self._seq += 1
            uid = f"U-{self._seq}"
            active = ActiveUtterance(
                utterance_id=uid,
                session_id=self._session_id,
                state=state,
                speaker=int(speaker or 1),
                channel=channel,
                text=lexical,
                version=1,
                start_time=cand_start,
                end_time=cand_end,
                deepgram_request_id=str(deepgram_request_id or ""),
                lineage_ids=[event_id] if event_id else [],
            )
            self._active = active
            decision = CREATE_ACTIVE if not force_new else CREATE_NEW_UTTERANCE
            reason = "create_active_utterance" if not force_new else "create_new_utterance"
            if hold:
                decision = HOLD_FINAL_CHUNK
                reason = "hold_incomplete_final_chunk"
        else:
            prev_n = _norm_text(previous_text)
            curr_n = _norm_text(lexical)
            if prev_n == curr_n:
                decision = IGNORE_DUPLICATE
                reason = "exact_duplicate_active"
                active.last_event_mono = self._clock()
                d = LifecycleDecision(
                    decision=decision,
                    reason=reason,
                    utterance_id=active.utterance_id,
                    text=active.text,
                    previous_text=previous_text,
                    session_id=self._session_id,
                    event_id=event_id,
                    version=active.version,
                    should_update_interim=False,
                )
                d.metadata = {
                    "source": source,
                    "start_time": active.start_time,
                    "end_time": active.end_time,
                    "channel": active.channel,
                    "channel_index": active.channel,
                    "speaker": active.speaker,
                    "canonical_utterance_id": active.utterance_id,
                    "provider_utterance_id": _provider_utterance_id(
                        metadata,
                        event_id=event_id,
                        deepgram_request_id=deepgram_request_id,
                    ),
                    "source_version": active.version,
                    "canonical_decision": "IGNORE",
                    "translation_eligible": False,
                    "lifecycle_state": active.state,
                }
                self._record_decision(
                    d, is_final=source == "final", speech_final=speech_final, channel=channel
                )
                return d

            merged = _merge_lexical(
                previous_text,
                lexical,
                audio_overlaps=_audio_spans_overlap(
                    active.start_time, active.end_time, cand_start, cand_end
                ),
            )
            # item 65: a sentence that ended is a boundary, not a place to keep
            # appending. Only fires when the merge above was a *pure* append
            # across a sentence terminator, so every revision, overlap-join and
            # tail-splice path is untouched -- and it publishes the finished
            # text rather than discarding it, so nothing is lost either way.
            if self._flush_sentence_boundary_locked(
                previous_text=previous_text,
                lexical=lexical,
                merged=merged,
                event_id=event_id,
                metadata=metadata,
            ):
                return self._apply_active_update_locked(
                    lexical=lexical,
                    speaker=speaker,
                    channel=channel,
                    cand_start=cand_start,
                    cand_end=cand_end,
                    event_id=event_id,
                    metadata=metadata,
                    deepgram_request_id=deepgram_request_id,
                    state=state,
                    hold=hold,
                    speech_final=speech_final,
                    source=source,
                    force_new=True,
                    emit_interim=emit_interim,
                )
            if _norm_text(merged) != curr_n and _norm_text(merged) != prev_n:
                decision = EXTEND_ACTIVE
                reason = "extend_active_adjacent_chunk"
                self._stats["extend_active"] += 1
            elif curr_n.startswith(prev_n) or prev_n in curr_n:
                decision = REPLACE_ACTIVE
                reason = "replace_active_cumulative_revision"
                self._stats["replace_active"] += 1
            else:
                decision = REPLACE_ACTIVE
                reason = "replace_active_same_utterance_revision"
                self._stats["replace_active"] += 1
            if hold:
                # Preserve HOLD as the logged decision while still replace/extend.
                decision = HOLD_FINAL_CHUNK if decision != EXTEND_ACTIVE else EXTEND_ACTIVE
                if decision == HOLD_FINAL_CHUNK:
                    reason = "hold_incomplete_final_chunk"
                else:
                    reason = "extend_active_held_final_chunk"

            active.text = merged
            active.version += 1
            active.state = state
            active.channel = channel if channel is not None else active.channel
            active.speaker = int(speaker or active.speaker or 1)
            if cand_start >= 0:
                if active.start_time < 0 or cand_start < active.start_time:
                    active.start_time = cand_start
            if cand_end >= 0:
                active.end_time = max(active.end_time, cand_end)
            if event_id:
                active.lineage_ids.append(event_id)
            if deepgram_request_id:
                active.deepgram_request_id = str(deepgram_request_id)
            active.last_event_mono = self._clock()

        accepted, identity_reason, identity_meta = self._observe_identity(
            utterance_id=active.utterance_id,
            channel=active.channel,
            version=active.version,
            decision=decision,
            text=active.text,
            lifecycle_state=active.state,
            translation_eligible=False,
            metadata=metadata,
            deepgram_request_id=deepgram_request_id,
            event_id=event_id,
        )
        if not accepted:
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason=f"identity_rejected:{identity_reason}",
                utterance_id=active.utterance_id,
                text=active.text,
                previous_text=previous_text,
                should_update_interim=False,
                should_commit=False,
                version=active.version,
                session_id=self._session_id,
                event_id=event_id,
                metadata={
                    "channel": active.channel,
                    "channel_index": active.channel,
                    "canonical_utterance_id": active.utterance_id,
                    "source_version": active.version,
                    "canonical_decision": "IGNORE",
                    "translation_eligible": False,
                    "lifecycle_state": active.state,
                },
            )
            self._record_decision(
                d, is_final=source == "final", speech_final=speech_final, channel=channel
            )
            return d

        d = LifecycleDecision(
            decision=decision,
            reason=reason,
            utterance_id=active.utterance_id,
            text=active.text,
            previous_text=previous_text,
            should_update_interim=True,
            should_commit=False,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "source": source,
                "start_time": active.start_time,
                "end_time": active.end_time,
                "channel": active.channel,
                "channel_index": active.channel,
                "speaker": active.speaker,
                "canonical_utterance_id": active.utterance_id,
                "provider_utterance_id": _provider_utterance_id(
                    metadata,
                    event_id=event_id,
                    deepgram_request_id=deepgram_request_id,
                ),
                "source_version": active.version,
                "lineage_ids": list(active.lineage_ids),
                "canonical_decision": _canonical_decision_name(decision),
                "translation_eligible": False,
                "lifecycle_state": active.state,
                **identity_meta,
                **{k: v for k, v in metadata.items() if k not in ("text",)},
            },
        )
        self._record_decision(
            d, is_final=source == "final", speech_final=speech_final, channel=channel
        )
        # emit_interim=False is passed by the two Case C call sites that fold
        # this final fragment into active.text and then commit it in the very
        # same _ingest call. Emitting an interim there published the utterance's
        # *final* text through the live-preview channel microseconds before it
        # was committed permanently -- and because the preview
        # (_pending_interim, INTERIM_UI_THROTTLE_MS) and the commit
        # (transcript_queue, TRANSCRIPT_UI_BATCH_FLUSH_MS) reach the UI on two
        # independent timers, the preview could land *after* the commit had
        # already run its final/interim comparison, repainting the
        # just-committed sentence as a stale "in progress" line until the ghost
        # watchdog reaped it ~1.5s later. Nothing consumes this emit except the
        # UI preview, so suppressing it on the commit path removes the
        # redundant delivery at its source.
        if emit_interim:
            self._emit_interim(d)
        return d

    def _flush_sentence_boundary_locked(
        self,
        *,
        previous_text: str,
        lexical: str,
        merged: str,
        event_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Commit the active utterance when a finished sentence meets new speech.

        fixes CLIENT_DELIVERY_SPRINT_v5.md item 65. English has no boundary
        stabilizer -- Japanese gets its sentence boundaries from
        japanese_sentence_assembler.py, English relies entirely on Deepgram's
        `speech_final`, which for continuous speech never arrives. Deepgram was
        configured correctly for run `...20260812-095935` (endpointing=1200,
        utterance_end_ms=1500); a podcast speaker simply never paused 1.2s, so
        one utterance absorbed 45 seconds of audio across 187 revisions and
        exported as a single 2445-character line holding 25 sentences. 16 of
        that run's 41 exported lines were over 400 characters.

        The trigger is deliberately the narrowest one that describes the
        accumulation: `merged` is EXACTLY `previous_text` + " " + `lexical`,
        which only `_merge_lexical`'s `prev[-1:] in ".!?"` branch produces. Any
        revision, `_overlap_join`, `_tail_resend_splice` or similarity result
        differs from that string and is left alone -- so this cannot split an
        utterance mid-revision, and it cannot fire where the text is still
        being corrected.

        Returns True only when the active utterance was actually committed and
        published. If `_commit_locked` refuses (no active, already committed,
        identity rejected) this returns False and the caller extends as before:
        the alternative -- starting a new utterance while the old one is still
        live and uncommitted -- would abandon its text.

        The commit reason is intentionally absent from `_PREMATURE_COMMIT_REASONS`.
        A sentence terminator from `smart_format` is a confident boundary, not
        this module guessing an utterance ended; treating it as premature would
        let the very next chunk extend the flushed record and glue the line
        back together.
        """
        # Gated OFF by default -- see ENGLISH_SENTENCE_FLUSH_ENABLED in
        # constants.py. The flush fires correctly, but on run
        # ...20260812-142447 only 1 of the 9 utterances it committed reached
        # the export, and the survivor was the one commit that did NOT come
        # from here. Until that is explained, the long-line problem is the
        # lesser evil.
        try:
            from alpha.constants import ENGLISH_SENTENCE_FLUSH_ENABLED

            if not ENGLISH_SENTENCE_FLUSH_ENABLED:
                return False
        except Exception:
            return False  # fail closed: no flag, no flush
        active = self._active
        if active is None or active.committed:
            return False
        prev = (previous_text or "").rstrip()
        if not prev or not (lexical or "").strip():
            return False
        if prev[-1] not in ".!?":
            return False
        if merged != f"{prev} {(lexical or '').strip()}":
            return False
        commit = self._commit_locked(
            reason="sentence_boundary_flush",
            event_id=event_id,
            metadata=metadata,
            decision_name=COMMIT_ACTIVE,
        )
        if not commit.should_commit:
            return False
        self._stats["sentence_boundary_flushes"] += 1
        return True

    def _commit_locked(
        self,
        *,
        reason: str,
        event_id: str,
        metadata: dict[str, Any],
        decision_name: str,
    ) -> LifecycleDecision:
        active = self._active
        if active is None or not (active.text or "").strip():
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="commit_without_active",
                session_id=self._session_id,
                event_id=event_id,
            )
            self._record_decision(d, is_final=True, speech_final=True, channel=None)
            return d
        if active.committed or active.utterance_id in self._committed_utterance_ids:
            self._stats["utterance_end_dedup"] += 1
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="already_committed",
                utterance_id=active.utterance_id,
                text=active.text,
                session_id=self._session_id,
                event_id=event_id,
                version=active.version,
            )
            self._record_decision(d, is_final=True, speech_final=True, channel=active.channel)
            return d

        accepted, identity_reason, identity_meta = self._observe_identity(
            utterance_id=active.utterance_id,
            channel=active.channel,
            version=active.version,
            decision=decision_name,
            text=active.text,
            lifecycle_state=COMMITTED,
            translation_eligible=True,
            metadata=metadata,
            deepgram_request_id=active.deepgram_request_id,
            event_id=event_id,
        )
        if not accepted:
            d = LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason=f"identity_rejected:{identity_reason}",
                utterance_id=active.utterance_id,
                text=active.text,
                session_id=self._session_id,
                event_id=event_id,
                version=active.version,
                metadata={
                    "channel": active.channel,
                    "channel_index": active.channel,
                    "canonical_utterance_id": active.utterance_id,
                    "source_version": active.version,
                    "canonical_decision": "IGNORE",
                    "translation_eligible": False,
                    "lifecycle_state": active.state,
                },
            )
            self._record_decision(d, is_final=True, speech_final=True, channel=active.channel)
            return d

        self._cancel_timeout_locked()
        active.state = COMMITTED
        active.committed = True
        active.commit_reason = reason
        self._committed_utterance_ids.add(active.utterance_id)
        self._stats["canonical_commits"] += 1
        self._stats["translation_jobs_hint"] += 1
        self._last_committed = active
        self._active = None

        d = LifecycleDecision(
            decision=decision_name,
            reason=reason,
            utterance_id=active.utterance_id,
            text=active.text,
            previous_text="",
            should_update_interim=False,
            should_commit=True,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "speech_final": True,
                "start_time": active.start_time,
                "end_time": active.end_time,
                "channel": active.channel,
                "channel_index": active.channel,
                "speaker": active.speaker,
                "canonical_utterance_id": active.utterance_id,
                "provider_utterance_id": _provider_utterance_id(
                    metadata,
                    event_id=event_id,
                    deepgram_request_id=active.deepgram_request_id,
                ),
                "source_version": active.version,
                "source_raw_event_ids": list(active.lineage_ids),
                "deepgram_request_id": active.deepgram_request_id,
                "lifecycle_commit_reason": reason,
                "canonical_decision": _canonical_decision_name(decision_name),
                "translation_eligible": True,
                "lifecycle_state": active.state,
                **identity_meta,
                **{k: v for k, v in metadata.items() if k not in ("text",)},
            },
        )
        self._record_decision(
            d, is_final=True, speech_final=True, channel=active.channel, commit=True
        )
        self._emit_commit(d)
        return d

    def _supersede_committed_locked(
        self,
        *,
        lexical: str,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        event_id: str,
        metadata: dict[str, Any],
        deepgram_request_id: str,
        speech_final: Any,
    ) -> LifecycleDecision:
        prev = self._last_committed
        assert prev is not None
        original_id, target_utterance_id = self._resolve_correction_target_locked(
            channel=channel,
            metadata=metadata,
            fallback_utterance_id=prev.utterance_id,
        )
        if not original_id:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "IDENTITY_REJECTION",
                    reason="missing_exact_supersede_target",
                    session_id=self._session_id,
                    channel_index=channel,
                    canonical_utterance_id=str(
                        metadata.get("canonical_utterance_id") or prev.utterance_id or ""
                    ),
                )
            except Exception:
                pass
            return LifecycleDecision(
                decision=IGNORE_DUPLICATE,
                reason="missing_exact_supersede_target",
                utterance_id=str(target_utterance_id or prev.utterance_id or ""),
                text=lexical,
                previous_text=prev.text,
                version=int(prev.version or 0),
                session_id=self._session_id,
                event_id=event_id,
                metadata={
                    "channel": channel,
                    "canonical_utterance_id": str(
                        target_utterance_id or prev.utterance_id or ""
                    ),
                    "source_version": int(prev.version or 0),
                    "canonical_decision": "IGNORE",
                    "translation_eligible": False,
                },
            )
        self._seq += 1
        uid = str(target_utterance_id or prev.utterance_id)  # keep same canonical identity
        active = ActiveUtterance(
            utterance_id=uid,
            session_id=self._session_id,
            state=READY_TO_COMMIT,
            speaker=int(speaker or prev.speaker or 1),
            channel=channel if channel is not None else prev.channel,
            text=lexical,
            version=int(prev.version) + 1,
            start_time=cand_start if cand_start >= 0 else prev.start_time,
            end_time=cand_end if cand_end >= 0 else prev.end_time,
            deepgram_request_id=str(deepgram_request_id or prev.deepgram_request_id),
            lineage_ids=list(prev.lineage_ids) + ([event_id] if event_id else []),
        )
        # Mark previous committed snapshot superseded in audit trail.
        prev.state = SUPERSEDED
        self._active = active
        self._committed_utterance_ids.discard(uid)
        self._stats["supersessions"] += 1

        d_super = LifecycleDecision(
            decision=SUPERSEDE_PREVIOUS,
            reason="authoritative_same_utterance_correction",
            utterance_id=uid,
            text=lexical,
            previous_text=prev.text,
            should_supersede_committed=True,
            superseded_record_id=original_id,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "original_record_id": original_id,
                "revision_target_id": original_id,
                "replacement_utterance_id": uid,
                "session_id": self._session_id,
                "channel": active.channel,
                "channel_index": active.channel,
                "canonical_utterance_id": uid,
                "provider_utterance_id": _provider_utterance_id(
                    metadata,
                    event_id=event_id,
                    deepgram_request_id=deepgram_request_id,
                ),
                "source_version": active.version,
                "canonical_decision": SUPERSEDE,
                "translation_eligible": speech_final is not False,
                "lifecycle_state": active.state,
                "start_time": active.start_time,
                "end_time": active.end_time,
            },
        )
        self._record_decision(
            d_super, is_final=True, speech_final=speech_final, channel=channel
        )

        if speech_final is False:
            active.state = ACTIVE_FINAL_CHUNK
            self._arm_timeout_locked()
            d_super.should_update_interim = True
            d_super.decision = HOLD_FINAL_CHUNK
            d_super.metadata["canonical_decision"] = REPLACE_PROVISIONAL
            d_super.metadata["translation_eligible"] = False
            d_super.metadata["lifecycle_state"] = active.state
            self._emit_interim(d_super)
            return d_super

        commit = self._commit_locked(
            reason="supersede_then_commit",
            event_id=event_id,
            metadata={
                **metadata,
                "superseded_record_id": original_id,
                "original_record_id": original_id,
            },
            decision_name=SUPERSEDE_PREVIOUS,
        )
        commit.should_supersede_committed = True
        commit.superseded_record_id = original_id
        commit.previous_text = prev.text
        return commit

    def _extend_committed_locked(
        self,
        *,
        lexical: str,
        speaker: int,
        channel: Any,
        cand_start: float,
        cand_end: float,
        event_id: str,
        metadata: dict[str, Any],
        deepgram_request_id: str,
        speech_final: Any,
    ) -> Optional[LifecycleDecision]:
        prev = self._last_committed
        assert prev is not None
        original_id, target_utterance_id = self._resolve_correction_target_locked(
            channel=channel,
            metadata=metadata,
            fallback_utterance_id=prev.utterance_id,
        )
        if not original_id:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "IDENTITY_REJECTION",
                    reason="missing_exact_extend_target_falling_back_to_new",
                    session_id=self._session_id,
                    channel_index=channel,
                    canonical_utterance_id=str(
                        metadata.get("canonical_utterance_id") or prev.utterance_id or ""
                    ),
                )
            except Exception:
                pass
            # fixes BUG-G1: previously returned an IGNORE_DUPLICATE decision
            # here, which silently discarded `lexical` -- the caller in
            # _ingest returned it unconditionally, so the spoken text was
            # lost with no trace. Returning None instead tells the caller to
            # fall through to normal Case B/C handling, which preserves the
            # text as its own (unmerged, but not lost) utterance.
            return None
        merged_text = _merge_lexical(
            prev.text,
            lexical,
            audio_overlaps=_audio_spans_overlap(
                prev.start_time, prev.end_time, cand_start, cand_end
            ),
        )
        self._seq += 1
        uid = str(target_utterance_id or prev.utterance_id)  # keep same canonical identity
        active = ActiveUtterance(
            utterance_id=uid,
            session_id=self._session_id,
            state=READY_TO_COMMIT,
            speaker=int(speaker or prev.speaker or 1),
            channel=channel if channel is not None else prev.channel,
            text=merged_text,
            version=int(prev.version) + 1,
            start_time=prev.start_time if prev.start_time >= 0 else cand_start,
            end_time=cand_end if cand_end >= 0 else prev.end_time,
            deepgram_request_id=str(deepgram_request_id or prev.deepgram_request_id),
            lineage_ids=list(prev.lineage_ids) + ([event_id] if event_id else []),
        )
        # Mark previous committed snapshot superseded in audit trail (it's
        # being absorbed into the extended utterance, same as a correction).
        prev.state = SUPERSEDED
        self._active = active
        self._committed_utterance_ids.discard(uid)
        self._stats["supersessions"] += 1

        d_ext = LifecycleDecision(
            decision=SUPERSEDE_PREVIOUS,
            reason="premature_continuation_extend",
            utterance_id=uid,
            text=merged_text,
            previous_text=prev.text,
            should_supersede_committed=True,
            superseded_record_id=original_id,
            version=active.version,
            session_id=self._session_id,
            event_id=event_id,
            metadata={
                "original_record_id": original_id,
                "revision_target_id": original_id,
                "replacement_utterance_id": uid,
                "session_id": self._session_id,
                "channel": active.channel,
                "channel_index": active.channel,
                "canonical_utterance_id": uid,
                "provider_utterance_id": _provider_utterance_id(
                    metadata,
                    event_id=event_id,
                    deepgram_request_id=deepgram_request_id,
                ),
                "source_version": active.version,
                "canonical_decision": SUPERSEDE,
                "translation_eligible": speech_final is not False,
                "lifecycle_state": active.state,
                "start_time": active.start_time,
                "end_time": active.end_time,
            },
        )
        self._record_decision(
            d_ext, is_final=True, speech_final=speech_final, channel=channel
        )

        if speech_final is False:
            active.state = ACTIVE_FINAL_CHUNK
            self._arm_timeout_locked()
            d_ext.should_update_interim = True
            d_ext.decision = HOLD_FINAL_CHUNK
            d_ext.metadata["canonical_decision"] = REPLACE_PROVISIONAL
            d_ext.metadata["translation_eligible"] = False
            d_ext.metadata["lifecycle_state"] = active.state
            self._emit_interim(d_ext)
            return d_ext

        commit = self._commit_locked(
            reason="extend_then_commit",
            event_id=event_id,
            metadata={
                **metadata,
                "superseded_record_id": original_id,
                "original_record_id": original_id,
            },
            decision_name=SUPERSEDE_PREVIOUS,
        )
        commit.should_supersede_committed = True
        commit.superseded_record_id = original_id
        commit.previous_text = prev.text
        return commit

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------
    def _arm_timeout_locked(self) -> None:
        self._timeout_token += 1
        token = self._timeout_token
        self._cancel_timeout_job_only_locked()
        host = self._host
        ms = self._commit_fallback_ms

        def _fire() -> None:
            decision = self.on_timeout(token=token)
            if decision is None:
                return
            # Commit callback already emitted via _emit_commit when should_commit.

        if host is not None and callable(getattr(host, "after", None)):
            try:
                self._timeout_after_id = host.after(ms, _fire)
                return
            except Exception:
                pass
        timer = threading.Timer(ms / 1000.0, _fire)
        timer.daemon = True
        timer.start()
        self._timeout_after_id = timer

    def _cancel_timeout_job_only_locked(self) -> None:
        job = self._timeout_after_id
        self._timeout_after_id = None
        if job is None:
            return
        host = self._host
        if host is not None and callable(getattr(host, "after_cancel", None)):
            try:
                if not isinstance(job, threading.Timer):
                    host.after_cancel(job)
                    return
            except Exception:
                pass
        if isinstance(job, threading.Timer):
            try:
                job.cancel()
            except Exception:
                pass

    def _cancel_timeout_locked(self) -> None:
        self._timeout_token += 1
        self._cancel_timeout_job_only_locked()

    # ------------------------------------------------------------------
    # Emit / log
    # ------------------------------------------------------------------
    def _emit_interim(self, decision: LifecycleDecision) -> None:
        # fixes BUG-F: do not publish from here -- this can run while
        # self._lock is held (called from deep inside _ingest/_commit_locked/
        # _supersede_committed_locked/_extend_committed_locked). Queue it;
        # the actual publish happens in _dispatch_interim, only once
        # _drain_pending_emits_unlocked runs with the lock released.
        if decision.should_update_interim:
            self._pending_emits.append(("interim", decision))

    def _dispatch_interim(self, decision: LifecycleDecision) -> None:
        cb = self._on_interim_update
        if cb is None and self._host is not None:
            handler = getattr(self._host, "on_interim_transcript", None)
            if callable(handler):

                def _default(dec: LifecycleDecision) -> None:
                    meta = dict(dec.metadata or {})
                    meta["lifecycle_decision"] = dec.decision
                    meta["canonical_utterance_id"] = dec.utterance_id
                    meta["source_version"] = dec.version
                    meta["is_final"] = False
                    handler(
                        int(meta.get("speaker") or 1),
                        dec.text,
                        metadata=meta,
                    )

                cb = _default
        if cb:
            try:
                cb(decision)
            except Exception:
                pass

    def _emit_commit(self, decision: LifecycleDecision) -> None:
        # fixes BUG-F: see _emit_interim above -- same reasoning. This is
        # the specific path that produced the confirmed deadlock (it's the
        # one that reaches main_window.py's Tkinter calls).
        if decision.should_commit:
            self._pending_emits.append(("commit", decision))

    def _dispatch_commit(self, decision: LifecycleDecision) -> None:
        cb = self._on_commit
        if cb is None and self._host is not None:
            publisher = getattr(self._host, "_publish_final_transcript_segment", None)
            if callable(publisher):

                def _default(dec: LifecycleDecision) -> None:
                    meta = dict(dec.metadata or {})
                    meta["speech_final"] = True
                    # A committed utterance IS final -- say so explicitly.
                    #
                    # This is item 65-flush's root cause. `_commit_locked`
                    # builds its metadata by spreading the TRIGGERING event's
                    # metadata last, so a commit raised while handling a chunk
                    # whose metadata carried `is_final: False` inherits that
                    # False. It then reaches
                    # `_publish_final_transcript_segment`, whose
                    # `queue_item.update(metadata)` overwrites the `is_final:
                    # True` it had just set, and `_display_transcript_item`
                    # opens with `if item.get("is_final") is False: return` --
                    # a silent drop, before the canonical commit, with nothing
                    # logged anywhere.
                    #
                    # Measured on run `...20260812-142447`: all 8
                    # `sentence_boundary_flush` commits carried
                    # `is_final: False` and were dropped; the single survivor
                    # was the `inactivity_timeout_fallback` commit, whose
                    # metadata had no `is_final` key at all, so the `is False`
                    # identity check let `None` through. 10 publishes,
                    # `PIPELINE_COMMIT_TRANSACTION_STARTED: 1`.
                    #
                    # Not specific to the flush: ANY commit triggered while
                    # handling an event with `is_final: False` was silently
                    # discarded. The flush only made it frequent enough to see.
                    meta["is_final"] = True
                    meta["canonical_utterance_id"] = dec.utterance_id
                    meta["source_version"] = dec.version
                    meta["lifecycle_decision"] = dec.decision
                    meta["source_raw_event_ids"] = list(
                        meta.get("source_raw_event_ids") or []
                    )
                    if dec.superseded_record_id:
                        meta["superseded_record_id"] = dec.superseded_record_id
                    publisher(
                        int(meta.get("speaker") or 1),
                        dec.text,
                        metadata=meta,
                        commit_reason=str(
                            meta.get("lifecycle_commit_reason") or "utterance_lifecycle"
                        ),
                    )

                cb = _default
        if cb:
            try:
                cb(decision)
            except Exception as exc:
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "DISPATCH_COMMIT_CALLBACK_FAILED",
                        reason=f"{type(exc).__name__}:{exc}",
                        session_id=self._session_id,
                        canonical_utterance_id=decision.utterance_id,
                        channel_index=int((decision.metadata or {}).get("channel_index") or 0),
                    )
                except Exception:
                    pass

    def _drain_pending_emits_unlocked(self) -> None:
        """Dispatch any commit/interim callbacks that were queued during a
        just-released self._lock section. The caller MUST NOT be holding
        self._lock when this runs -- these callbacks can end up calling
        into host/Tkinter code (confirmed via a real thread dump for the
        commit path), and Tkinter must never be touched while holding a
        lock the main thread's own event loop might separately need (see
        BUG-F). Only the brief swap below is lock-protected; the actual
        dispatch loop runs fully unlocked."""
        with self._lock:
            pending, self._pending_emits = self._pending_emits, []
        for kind, dec in pending:
            if kind == "commit":
                self._dispatch_commit(dec)
            else:
                self._dispatch_interim(dec)

    def _record_decision(
        self,
        decision: LifecycleDecision,
        *,
        is_final: bool,
        speech_final: Any,
        channel: Any,
        commit: bool = False,
    ) -> None:
        row = {
            "ts": time.time(),
            "session_id": decision.session_id or self._session_id,
            "event_id": decision.event_id,
            "channel": channel,
            "start": (decision.metadata or {}).get("start_time"),
            "duration": None,
            "is_final": bool(is_final),
            "speech_final": speech_final,
            "utterance_id": decision.utterance_id,
            "lexical_text": decision.text,
            "active_utterance_id": decision.utterance_id,
            "previous_active_text": decision.previous_text,
            "decision": decision.decision,
            "decision_reason": decision.reason,
            "canonical_commit_created": bool(commit or decision.should_commit),
            "canonical_record_id": "",
            "superseded_record_id": decision.superseded_record_id,
            "translation_job_created": bool(commit or decision.should_commit),
            "source_version": decision.version,
        }
        start = _as_float((decision.metadata or {}).get("start_time"), -1.0)
        end = _as_float((decision.metadata or {}).get("end_time"), -1.0)
        if start >= 0 and end >= start:
            row["duration"] = round(end - start, 3)
        self._events.append(row)
        self._log_event(row)

    def _log_event(self, row: dict[str, Any]) -> None:
        path = self._event_log_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass


# Module-level owner bound to the live host (session-scoped via reset_for_session).
_owner_lock = threading.RLock()
_owner: Optional[UtteranceLifecycleOwner] = None


def get_utterance_lifecycle(host: Any = None) -> UtteranceLifecycleOwner:
    global _owner
    with _owner_lock:
        if _owner is None:
            try:
                from alpha.constants import UTTERANCE_COMMIT_FALLBACK_MS

                ms = int(UTTERANCE_COMMIT_FALLBACK_MS)
            except Exception:
                ms = DEFAULT_COMMIT_FALLBACK_MS
            _owner = UtteranceLifecycleOwner(host=host, commit_fallback_ms=ms)
        elif host is not None:
            _owner.bind_host(host)
        return _owner


def reset_utterance_lifecycle(host: Any = None, session_id: str = "") -> UtteranceLifecycleOwner:
    owner = get_utterance_lifecycle(host)
    sid = session_id or str(getattr(host, "_live_session_id", "") or "")
    owner.reset_for_session(sid)
    return owner


def should_use_utterance_lifecycle(host: Any) -> bool:
    """English / generic finals only — Japanese keeps its stabilizer path."""
    try:
        from alpha.transcription.japanese_final_chunk_stabilizer import (
            should_use_japanese_final_stabilizer,
        )

        if should_use_japanese_final_stabilizer(host):
            return False
    except Exception:
        pass
    lang = str(getattr(host, "_listen_language", "") or "").lower()
    if lang.startswith("ja"):
        return False
    return True
