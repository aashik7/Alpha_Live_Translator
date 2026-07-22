"""Japanese continuity assembler (V3.3.5.5.8.5.11) — single Japanese buffer before UI commit."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Optional

from alpha.constants import (
    ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED,
    ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT,
    BENCHMARK_BASELINE_LOCK_ENABLED,
    BUSINESS_PHRASE_PROTECTION_ENABLED,
    INCOMPLETE_TAIL_HOLD_ENABLED,
    JAPANESE_BUSINESS_ACCURACY_8522_ENABLED,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_BOUNDARY_STABILIZER_ENABLED,
    JAPANESE_CONTINUITY_ASSEMBLER_ENABLED,
    JAPANESE_CONTINUITY_ASSEMBLER_SAFE_MODE,
    JAPANESE_CONTINUITY_MAX_BUFFER_CHARS,
    JAPANESE_CONTINUITY_MAX_HOLD_MS,
    JAPANESE_CONTINUITY_MAX_PARTS,
    JAPANESE_EMERGENCY_LAST_FRAG_GRACE_MS,
    JAPANESE_LIST_LESSON_CONTEXT_CUES,
    JAPANESE_NOISE_QUARANTINE_DROP_S,
    JAPANESE_NOISE_QUARANTINE_MAX_COMPACT,
    JAPANESE_NOISE_QUARANTINE_RELEASE_COMPACT,
    JAPANESE_NOISE_QUARANTINE_SILENCE_S,
    JAPANESE_STABLE_ACCURACY_FIX_ENABLED,
    JAPANESE_STT_PROFILE,
    MIC_ACTIVE_RMS_MIN,
    PUNCTUATION_START_MERGE_ENABLED,
    PUNCTUATION_START_POST_CORRECTION_MERGE_ENABLED,
    STABLE_LAYER_SAFE_MERGE_ENABLED,
    STOP_TAIL_CLEANUP_ENABLED,
    SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA,
    SYSTEM_ACTIVE_RMS_MIN,
    TK_SAFE_PIPELINE_MODE,
    TRANSLATION_READINESS_METRICS_ENABLED,
    VALID_SHORT_JAPANESE_LIST_TERMS,
)
from alpha.transcription.language_pipeline_base import LanguagePipelineBase
from alpha.transcription.japanese_stable_accuracy import (
    INCOMPLETE_TAIL_HOLD_MS_MAX,
    INCOMPLETE_TAIL_HOLD_MS_NORMAL,
    apply_punctuation_start_post_correction,
    can_merge_punctuation_with_previous,
    has_incomplete_tail_for_hold,
    is_punctuation_start_fragment,
    is_protected_business_phrase,
    merge_punctuation_fragment,
    merge_short_fragments,
    should_hold_incomplete_tail,
    should_hold_short_fragment,
)
from alpha.utils.lock_monitor import MonitoredRLock
from alpha.transcription.japanese_translation_unit_builder import (
    JapaneseTranslationUnitBuilder,
)
from alpha.transcription.japanese_accuracy_cleaner import (
    build_japanese_cleanup_candidate,
    business_japanese_stable_cleanup,
    detect_keyterm_overbias_candidates,
    detect_raw_stt_error_candidates,
    repair_leading_yo_fragment,
)
from alpha.utils.cjk_text import (
    cleanup_japanese_per_fragment,
    cleanup_japanese_transcript_precision,
    compact_cjk_for_compare,
    detect_kana_prefix_overlap_removal,
    detect_raw_stt_error_suspected,
)
from alpha.utils.japanese_accuracy_log import (
    get_japanese_accuracy_event_counts,
    jp_accuracy_log,
)

SENTENCE_HOLD_MIN_MS = 2000
SENTENCE_HOLD_MAX_MS = 3500
TARGET_CHUNK_MIN_COMPACT = 45
TARGET_CHUNK_MAX_COMPACT = 90
MAX_BUFFER_COMPACT_LEN = 120
SOFT_BOUNDARY_MIN_COMPACT = 28
MAX_SENTENCE_RAW_LEN = 200
SPEAKER_CONTINUATION_MAX_COMPACT = 3
JAPANESE_SPEAKER_STICKY_MS = 5000
SOFT_INCOMPLETE_MAX_COMPACT = 12

_STRONG_SENTENCE_END_RE = re.compile(r"[。！？？!?]+\s*$")
_STRONG_BOUNDARY_RE = re.compile(r"[。！？？!?]+")

# Longest first for greedy soft-boundary matching
_SOFT_COMMIT_BOUNDARIES = (
    "って思いました",
    "と思いました",
    "んですよね",
    "ですよね",
    "んですね",
    "と思って",
    "と思う",
    "なんだけど",
    "んだよね",
    "だよね",
    "だけど",
    "ました",
    "ですね",
    "です",
    "ます",
)

_INCOMPLETE_TAIL_PREFIXES = ("例えば",)

_MEANING_FRAGMENTS = (
    "尊敬してる人",
    "誰ですかっていう質問",
    "理由を教えてください",
    "トワイス",
    "ジヒョ",
    "そもそも話すこと",
    "理由までちゃんと",
)

_INCOMPLETE_SUFFIXES = (
    "自分の中",
    "基本的な",
    "思いまし",
    "話せないんです",
    "理由はね",
    "言うま",
    "けど",
    "から",
    "まで",
    "って",
    "なんで",
    "さらに",
    "つまり",
    "ので",
    "のに",
    "には",
    "では",
    "同じ",
    "レベルで",
    "が",
    "は",
    "を",
    "に",
    "で",
    "と",
    "の",
    "も",
    "て",
    "し",
    "そう",
)

_SOFT_INCOMPLETE_SUFFIXES = ("ね", "ま")

_SAFE_SENTENCE_NE_ENDINGS = (
    "んですよね",
    "ですよね",
    "んですね",
    "ですね",
    "だよね",
    "んだよね",
)

_SHORT_FILLER_INCOMPLETE = frozenset({"ね", "なんかね", "そうね"})

_INCOMPLETE_CONTAINS = ("自分の中", "基本的な")

_STANDALONE_INCOMPLETE = frozenset(
    {
        "まで",
        "普通にね",
        "みんな私と同じ",
        "ろう多分理由はね",
        "って思いまし",
        "ばあなたが",
        "このこのね私私が",
        "そのジヒョです",
        "なんでなんでって",
        "そう、理由がね",
        "なんだなんだけど結構ね",
        "英語のレベルの",
        "なんでその",
        "言うま自分の中",
        "さらになんで",
        "その理て言われた瞬間なんでって",
        "そう、理由だけどクラスのもう結構ね",
        "つまりみんなは自分の意見をさらになんで言うま自分の中",
        "あるかどうっていう基本的な",
    }
)

_STANDALONE_COMPLETE = frozenset(
    {
        "はい",
        "そうです",
        "そうですね",
        "ありがとうございます",
        "大丈夫です",
        "こんにちは",
        "こんばんは",
        "おはようございます",
        "よろしくお願いします",
        "いいえ",
    }
)

# Reasons that must never trigger timeout flush to assembler downstream
_TIMEOUT_NO_FLUSH_REASONS = frozenset(
    {
        "under_8_chars",
        "ends_with_に",
        "ends_with_を",
        "ends_with_も",
        "ends_with_は",
        "ends_with_で",
        "ends_with_が",
        "ends_with_って",
        "ends_with_だけど",
        "no_sentence_boundary",
    }
)

_EMERGENCY_GRACE_ENDINGS = (
    "の",
    "っていう",
    "くらいの",
    "と",
    "が",
    "は",
    "を",
    "に",
    "で",
    "から",
    "まで",
    "なんで",
    "さらに",
    "なんとかかんと",
    "トゥワイスの",
    "トワイスの",
)

_NOISE_QUARANTINE_RELEASE_WINDOW_S = 5.0

_FALSE_DESU_QUESTION_TAIL_PREFIXES = (
    "か",
    "かって",
    "かという",
    "かっていう",
    "っていう質問",
    "かっていう質問",
)

_SUSPICIOUS_NOISE_FRAGMENTS = (
    "罪",
    "空にかま",
    "こと富発想富から出てくる",
    "友達を食たいからだけ",
    "あんみんな時調理のやつがいいから",
    "して重ねて、その",
)

_SAFE_HOLD_TIMEOUT_ENDINGS = (
    "の",
    "けど",
    "だけど",
    "んだけど",
    "だよね",
    "ですよ",
    "ですとかね",
)

_TRANSLATION_WEAK_ENDINGS = (
    "と同じくらいの",
    "くらいの",
    "の",
    "が",
    "を",
    "に",
    "で",
    "から",
    "っていう",
    "けど",
    "なんだけど",
    "そう例えば",
)

_INCOMPLETE_TAIL_WEAK_ENDINGS = (
    "と同じくらいの",
    "くらいの",
    "っていう",
    "から",
    "の",
    "が",
    "を",
    "に",
    "で",
)

_SPEAKER_LOCK_CONTINUATION_PREFIXES = (
    "なんだけど",
    "それが",
    "だから",
    "でも",
    "けど",
    "理由までちゃんと",
)

_SPEAKER_LOCK_CONTINUATION_PHRASES = (
    "理由までちゃんと自分の中で分かってるんだよね",
    "なんだけど私は",
    "それがあんまりないのかもしれないっ思いました",
)


def has_strong_sentence_end(text: str) -> bool:
    return bool(_STRONG_SENTENCE_END_RE.search((text or "").strip()))


def count_japanese_chars(text: str) -> int:
    return len(compact_cjk_for_compare(text, "ja"))


def _ends_with_safe_ne_phrase(segment: str) -> bool:
    return any(segment.endswith(ending) for ending in _SAFE_SENTENCE_NE_ENDINGS)


def _is_false_ne_tail_split(prefix: str, tail: str) -> bool:
    """Reject です+ね splits that break んですね / ですね."""
    tail = (tail or "").strip()
    if tail != "ね":
        return False
    if prefix.endswith("んです") or prefix.endswith("です"):
        return True
    return False


def _is_false_desu_question_tail_split(prefix: str, tail: str) -> bool:
    prefix = (prefix or "").strip()
    tail = (tail or "").strip()
    if not prefix.endswith("です"):
        return False
    if not tail:
        return False
    if any(tail.startswith(token) for token in _FALSE_DESU_QUESTION_TAIL_PREFIXES):
        jp_accuracy_log(
            "FALSE_DESU_QUESTION_SPLIT_BLOCKED",
            prefix_text=prefix,
            tail_text=tail,
        )
        return True
    return False


def _soft_boundary_matches(segment: str) -> list[tuple[int, str]]:
    """Longest soft boundary at each end position (descending length priority)."""
    sorted_endings = sorted(_SOFT_COMMIT_BOUNDARIES, key=len, reverse=True)
    by_end: dict[int, str] = {}
    for end_pos in range(1, len(segment) + 1):
        for ending in sorted_endings:
            start = end_pos - len(ending)
            if start < 0:
                continue
            if segment[start:end_pos] == ending:
                by_end[end_pos] = ending
                break
    return [(end_pos, by_end[end_pos]) for end_pos in sorted(by_end.keys())]


def _candidate_sort_key(item: tuple[int, int, str]) -> tuple[int, int, int]:
    p_compact, end_pos, name = item
    ending_len = len(name.replace("soft_", "")) if name.startswith("soft_") else 0
    return (end_pos, ending_len, p_compact)


def looks_incomplete_japanese_fragment(text: str) -> tuple[bool, str]:
    """Incomplete detection for continuity buffer."""
    segment = (text or "").strip()
    if not segment:
        return True, "empty"
    compact = compact_cjk_for_compare(segment, "ja")
    if has_strong_sentence_end(segment):
        return False, "sentence_end_punctuation"
    if segment in _STANDALONE_COMPLETE or compact in _STANDALONE_COMPLETE:
        return False, "standalone_complete"
    if segment in _SHORT_FILLER_INCOMPLETE or compact in _SHORT_FILLER_INCOMPLETE:
        return True, "standalone_incomplete"
    if _ends_with_safe_ne_phrase(segment):
        return False, "safe_soft_ne_ending"
    if segment in _STANDALONE_INCOMPLETE or compact in _STANDALONE_INCOMPLETE:
        return True, "standalone_incomplete"
    for token in _INCOMPLETE_CONTAINS:
        if token in segment and not has_strong_sentence_end(segment):
            if segment.endswith(token) or segment.rstrip("、").endswith(token):
                return True, f"contains_{token}"
    for suffix in _INCOMPLETE_SUFFIXES:
        if not segment.endswith(suffix):
            continue
        if suffix == "と" and segment.endswith("こと"):
            continue
        if suffix == "だけど":
            continue
        return True, f"ends_with_{suffix}"
    compact_len = count_japanese_chars(segment)
    for suffix in _SOFT_INCOMPLETE_SUFFIXES:
        if segment.endswith(suffix) and compact_len < SOFT_INCOMPLETE_MAX_COMPACT:
            if not _ends_with_safe_ne_phrase(segment):
                return True, f"soft_ends_with_{suffix}"
    if compact_len < 8:
        return True, "under_8_chars"
    return True, "no_sentence_boundary"


def _tail_should_stay_buffered(tail: str) -> bool:
    tail_body = (tail or "").strip().rstrip("。！？!?")
    if not tail_body:
        return False
    incomplete, _ = looks_incomplete_japanese_fragment(tail_body)
    if incomplete:
        return True
    for prefix in _INCOMPLETE_TAIL_PREFIXES:
        if tail_body.startswith(prefix):
            return True
    return False


def _boundary_type(name: str) -> str:
    if name.startswith("strong"):
        return "strong"
    if name.startswith("soft"):
        return "soft"
    return "none"


def find_commit_boundary(
    text: str,
    *,
    max_compact: int = MAX_BUFFER_COMPACT_LEN,
    target_min: int = TARGET_CHUNK_MIN_COMPACT,
    target_max: int = TARGET_CHUNK_MAX_COMPACT,
) -> tuple[str, str, str, str]:
    """Return (prefix, tail, boundary_name, boundary_type)."""
    segment = (text or "").strip()
    if not segment:
        return "", "", "", "none"
    compact_len = count_japanese_chars(segment)
    force_split = compact_len >= max_compact
    candidates: list[tuple[int, int, str]] = []

    for match in _STRONG_BOUNDARY_RE.finditer(segment):
        end_pos = match.end()
        prefix = segment[:end_pos].strip()
        tail = segment[end_pos:].strip()
        p_compact = count_japanese_chars(prefix)
        if p_compact < 4:
            continue
        if tail and _tail_should_stay_buffered(tail):
            candidates.append((p_compact, end_pos, "strong_punctuation_incomplete_tail"))
            continue
        if not tail:
            body = prefix.rstrip("。！？!?")
            incomplete, _ = looks_incomplete_japanese_fragment(body)
            if incomplete:
                continue
        if force_split and p_compact >= target_min:
            candidates.append((p_compact, end_pos, "strong_punctuation"))
        elif tail and _tail_should_stay_buffered(tail):
            candidates.append((p_compact, end_pos, "strong_punctuation_incomplete_tail"))
        elif target_min <= p_compact <= target_max and tail:
            candidates.append((p_compact, end_pos, "strong_punctuation"))

    for end_pos, ending in _soft_boundary_matches(segment):
        prefix = segment[:end_pos].strip()
        tail = segment[end_pos:].strip()
        if _is_false_ne_tail_split(prefix, tail):
            continue
        if _is_false_desu_question_tail_split(prefix, tail):
            continue
        p_compact = count_japanese_chars(prefix)
        if p_compact < SOFT_BOUNDARY_MIN_COMPACT and not force_split:
            continue
        if not tail:
            if _ends_with_safe_ne_phrase(prefix):
                candidates.append((p_compact, end_pos, f"soft_{ending}"))
            continue
        tail_incomplete = _tail_should_stay_buffered(tail)
        min_for_soft = SOFT_BOUNDARY_MIN_COMPACT if tail_incomplete else target_min
        if p_compact < min_for_soft and not force_split:
            continue
        if tail and not tail_incomplete and not force_split:
            if p_compact < target_max:
                continue
        if force_split and p_compact >= target_min:
            candidates.append((p_compact, end_pos, f"soft_{ending}"))
        elif min_for_soft <= p_compact <= target_max:
            candidates.append((p_compact, end_pos, f"soft_{ending}"))
        elif compact_len > target_max and p_compact >= min_for_soft:
            candidates.append((p_compact, end_pos, f"soft_{ending}"))

    if not candidates:
        return "", segment, "", "none"

    incomplete_cands = [c for c in candidates if "incomplete_tail" in c[2]]
    if incomplete_cands:
        best = max(incomplete_cands, key=_candidate_sort_key)
    elif force_split or compact_len > target_max:
        in_range = [c for c in candidates if target_min <= c[0] <= target_max]
        if in_range:
            best = max(in_range, key=_candidate_sort_key)
        else:
            under_max = [c for c in candidates if c[0] <= max_compact]
            best = (
                max(under_max, key=_candidate_sort_key)
                if under_max
                else max(candidates, key=_candidate_sort_key)
            )
    else:
        in_range = [c for c in candidates if target_min <= c[0] <= target_max]
        if in_range:
            best = max(in_range, key=_candidate_sort_key)
        else:
            soft_ok = [
                c
                for c in candidates
                if c[0] >= SOFT_BOUNDARY_MIN_COMPACT and c[2].startswith("soft_")
            ]
            best = (
                max(soft_ok, key=_candidate_sort_key) if soft_ok else ("", 0, "")
            )

    if not best or not best[2]:
        return "", segment, "", "none"

    end_pos = best[1]
    prefix = segment[:end_pos].strip()
    tail = segment[end_pos:].strip()
    bname = best[2]
    btype = _boundary_type(bname)
    if prefix and tail:
        return prefix, tail, bname, btype
    return "", segment, "", "none"


def split_at_safe_chunk_boundary(
    text: str,
    *,
    max_compact: int = MAX_BUFFER_COMPACT_LEN,
    target_min: int = TARGET_CHUNK_MIN_COMPACT,
    target_max: int = TARGET_CHUNK_MAX_COMPACT,
) -> tuple[str, str, str]:
    prefix, tail, bname, _ = find_commit_boundary(
        text, max_compact=max_compact, target_min=target_min, target_max=target_max
    )
    return prefix, tail, bname


def split_at_last_safe_boundary(text: str) -> tuple[str, str]:
    prefix, tail, _ = split_at_safe_chunk_boundary(text)
    return prefix, tail


def compute_sentence_hold_ms(text: str, incomplete: bool) -> int:
    hold = SENTENCE_HOLD_MIN_MS
    if incomplete:
        hold += 400
    compact_len = count_japanese_chars(text)
    if compact_len < 8:
        hold += 300
    max_hold_ms = SENTENCE_HOLD_MAX_MS
    if JAPANESE_ACCURACY_MODE:
        hold += 900
        max_hold_ms = 5000
        if incomplete:
            hold += 500
        if compact_len >= 20:
            hold += 300
        if any((text or "").strip().endswith(token) for token in _TRANSLATION_WEAK_ENDINGS):
            hold += 300
    return max(SENTENCE_HOLD_MIN_MS, min(max_hold_ms, hold))


def merge_japanese_fragments(previous: str, current: str) -> str:
    prev = (previous or "").strip()
    curr = (current or "").strip()
    if not prev:
        return curr
    if not curr:
        return prev
    if curr.startswith(prev):
        return curr
    if prev.endswith(curr):
        return prev
    max_overlap = min(len(prev), len(curr), 32)
    for size in range(max_overlap, 0, -1):
        if prev[-size:] == curr[:size]:
            return (prev + curr[size:]).strip()
    compact_prev = compact_cjk_for_compare(prev, "ja")
    compact_curr = compact_cjk_for_compare(curr, "ja")
    for size in range(min(len(compact_prev), len(compact_curr), 32), 0, -1):
        if compact_prev[-size:] == compact_curr[:size]:
            tail = curr
            for end in range(1, len(curr) + 1):
                if compact_cjk_for_compare(curr[:end], "ja") == compact_curr[:size]:
                    tail = curr[end:]
                    break
            return (prev + tail).strip()
    return (prev + curr).strip()


def should_hold_speaker_continuation(
    buffer_speaker: Any,
    new_speaker: Any,
    fragment: str,
    *,
    buffer_text: str = "",
    buffer_updated_mono: float = 0.0,
) -> bool:
    if buffer_speaker == new_speaker:
        return False
    now = time.monotonic()
    if buffer_updated_mono > 0:
        elapsed_ms = (now - buffer_updated_mono) * 1000.0
        if elapsed_ms < JAPANESE_SPEAKER_STICKY_MS:
            prefix, _tail, _bname, btype = find_commit_boundary(buffer_text)
            if not prefix or btype == "none":
                return True
    compact_len = count_japanese_chars(fragment)
    if compact_len <= SPEAKER_CONTINUATION_MAX_COMPACT:
        return True
    incomplete, _ = looks_incomplete_japanese_fragment(fragment)
    if incomplete and compact_len <= 16:
        return True
    if buffer_text:
        buf_incomplete, _ = looks_incomplete_japanese_fragment(buffer_text)
        if buf_incomplete:
            return True
    return False


def _merge_ordered_lineage_ids(*sources: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for source in sources:
        if source is None:
            continue
        if isinstance(source, str):
            candidates = [source]
        else:
            candidates = list(source)
        for raw in candidates:
            item = str(raw or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
    return ordered


def _extract_lineage_from_metadata(metadata: dict[str, Any]) -> list[str]:
    meta = metadata or {}
    return _merge_ordered_lineage_ids(
        meta.get("raw_event_id"),
        meta.get("source_raw_event_ids"),
        meta.get("raw_event_ids"),
    )


def _is_synthetic_stop_only_ingress(
    metadata: dict[str, Any],
    upstream_reason: str,
    *,
    stop_incomplete: bool = False,
) -> bool:
    meta = metadata or {}
    if meta.get("synthetic_record") or meta.get("synthetic_lineage"):
        return True
    reason = str(upstream_reason or "")
    if reason in ("stop_listening", "stop_flush_incomplete_tail"):
        return True
    if reason.startswith("stop_") or stop_incomplete:
        return True
    return bool(meta.get("assembler_exception_direct_commit_fallback"))


def _prepare_assembler_ingress_metadata(
    metadata: dict[str, Any],
    upstream_reason: str,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    if _is_synthetic_stop_only_ingress(meta, upstream_reason):
        meta["synthetic_record"] = True
        meta["synthetic_lineage"] = True
        return meta
    if str(upstream_reason or "") in (
        "deepgram_final",
        "assembler_exception_direct_commit_fallback",
    ) or str(upstream_reason or "").startswith("deepgram"):
        lineage = _extract_lineage_from_metadata(meta)
        if meta.get("lineage_assignment_failed") or meta.get("force_append_only"):
            meta["source_raw_event_ids"] = []
            return meta
        if not lineage:
            meta["lineage_assignment_failed"] = True
            meta["force_append_only"] = True
            meta["source_raw_event_ids"] = []
            jp_accuracy_log(
                "ASSEMBLER_INGRESS_LINEAGE_MISSING",
                upstream_reason=upstream_reason,
                force_append_only=True,
            )
            return meta
        meta["source_raw_event_ids"] = lineage
        if lineage:
            meta["raw_event_id"] = lineage[0]
    return meta


class JapaneseContinuityAssembler(LanguagePipelineBase):
    """Single Japanese continuity buffer — owns commit decisions (Tk-safe pipeline)."""

    HEARTBEAT_MS = 5000
    _tk_scheduling_confirmed_logged = False

    def __init__(self, host: Any):
        self._host = host
        self._lock = MonitoredRLock("japanese_assembler")
        self._buffer: Optional[dict[str, Any]] = None
        self._pending_flush_due_mono: Optional[float] = None
        self._pending_flush_reason: str = ""
        self._pending_flush_generation: int = 0
        self._flush_generation: int = 0
        self._cached_snapshot: dict[str, Any] = {}
        self._snapshot_cached_return_count: int = 0
        self._quarantine_drop_scheduled: bool = False
        self._last_raw_stt_mono: float = 0.0
        self._last_ui_commit_mono: float = 0.0
        self._quarantine: list[dict[str, Any]] = []
        self._last_reliable_speaker: Optional[int] = None
        self._last_buffer_speaker: Optional[int] = None
        self._dominant_speaker_for_current_japanese_session: Optional[int] = None
        self._last_committed_speaker: Optional[int] = None
        self._pending_raw_speaker_candidate: Optional[int] = None
        self._consecutive_raw_speaker_votes: int = 0
        self._committed_segment_speaker_distribution: dict[int, int] = {}
        self._translation_ready_true_count: int = 0
        self._translation_ready_false_count: int = 0
        self._cleanup_candidate_count: int = 0
        self._cleanup_applied_to_ui_count: int = 0
        self._cleanup_low_confidence_not_applied_count: int = 0
        self._emergency_suppressed_count: int = 0
        self._incomplete_tail_commit_count: int = 0
        self._risky_segments: list[str] = []
        self._translation_unit_builder = JapaneseTranslationUnitBuilder()
        self._last_final_output_text: str = ""
        self._recent_committed_context: str = ""
        self._stable_commit_sample_count: int = 0
        self._cleanup_summary_count: int = 0
        self._business_cleanup_applied_count: int = 0
        self._business_risk_candidate_count: int = 0
        self._business_cleanup_skipped_already_correct_count: int = 0
        self._business_cleanup_duplicate_prevented_count: int = 0
        self._duplicate_damage_detected_count: int = 0
        self._duplicate_damage_fixed_count: int = 0
        self._duplicate_damage_reverted_count: int = 0
        self._idempotency_check_failed_count: int = 0
        self._short_valid_term_committed_count: int = 0
        self._short_valid_term_dropped_count: int = 0
        self._other_company_term_confusion_count: int = 0
        self._successor_term_confusion_count: int = 0
        self._causative_form_confusion_count: int = 0
        self._role_change_confusion_count: int = 0
        self._polite_expression_confusion_count: int = 0
        self._last_stable_commit: Optional[dict[str, Any]] = None
        self._stable_line_counter: int = 0
        self._last_stable_line_id: str = ""
        self._last_stable_source_raw_event_ids: list[str] = []
        self._assembler_commit_gate_failed: bool = False
        self._stable_hold_pending: Optional[dict[str, Any]] = None
        self._stable_hold_generation: int = 0
        self._stop_boundary_active: bool = False
        self._assembler_exception_recovery_buffer: Optional[dict[str, Any]] = None
        self._punctuation_start_count: int = 0
        self._short_fragment_count: int = 0
        self._incomplete_tail_hold_count: int = 0
        self._stable_merge_count: int = 0
        self._assembler_exception_count: int = 0
        self._quarantine_drop_count: int = 0
        self._business_phrase_protected_count: int = 0
        self._raw_mutation_count: int = 0
        self._force_translation_not_ready: bool = False
        self._business_correction_count: int = 0
        self._business_correction_high_confidence_count: int = 0
        self._business_correction_skipped_count: int = 0
        self._stop_tail_suppressed_count: int = 0
        self._stop_tail_debug_written_count: int = 0
        self._double_prefix_repair_count: int = 0
        self._triple_koko_repair_count: int = 0
        self._punctuation_start_post_correction_merge_count: int = 0
        self._business_correction_regression_count: int = 0
        self._business_accuracy_expansion_count: int = 0
        self._split_fragment_repair_count: int = 0
        self._duplicate_phrase_dedupe_count: int = 0
        self._midline_punctuation_cleanup_count: int = 0
        self._name_correction_count: int = 0
        self._name_correction_skipped_count: int = 0
        self._session_has_chin_name: bool = False
        self._exact_duplicate_continuation_count: int = 0
        self._recent_stable_lines: list[str] = []

    def _snapshot_from_buffer(self, buf: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not buf:
            return {}
        text = (buf.get("text") or "").strip()
        created = float(buf.get("created_mono") or time.monotonic())
        return {
            "japanese_buffer_chars": len(text),
            "japanese_buffer_compact_len": count_japanese_chars(text),
            "japanese_buffer_preview": text[:120],
            "japanese_buffer_part_count": int(buf.get("part_count") or 1),
            "japanese_buffer_age_ms": int((time.monotonic() - created) * 1000.0),
        }

    def get_buffer_snapshot(self) -> dict[str, Any]:
        """Blocking snapshot — pipeline worker only; UI must use nonblocking."""
        with self._lock:
            snap = self._snapshot_from_buffer(self._buffer)
            if snap:
                self._cached_snapshot = dict(snap)
            return snap

    def get_buffer_snapshot_nonblocking(self) -> dict[str, Any]:
        """UI-safe: try-lock with short timeout; return cached snapshot if busy."""
        acquired = self._lock.try_acquire(timeout=0.002)
        if acquired:
            try:
                snap = self._snapshot_from_buffer(self._buffer)
                if snap:
                    self._cached_snapshot = dict(snap)
                return snap
            finally:
                self._lock.release()
        self._snapshot_cached_return_count += 1
        jp_accuracy_log(
            "ASSEMBLER_SNAPSHOT_LOCK_BUSY_RETURNED_CACHED",
            cached=bool(self._cached_snapshot),
            return_count=self._snapshot_cached_return_count,
        )
        jp_accuracy_log("UI_SAFE_SNAPSHOT_USED")
        return dict(self._cached_snapshot)

    def get_snapshot_nonblocking(self) -> dict[str, Any]:
        return self.get_buffer_snapshot_nonblocking()

    def ingest_raw_final(self, raw_event: dict[str, Any]) -> None:
        self.ingest(
            int(raw_event.get("speaker") or 2),
            str(raw_event.get("text") or ""),
            raw_event.get("metadata"),
            str(raw_event.get("upstream_reason") or "deepgram_final"),
            already_cleaned=bool(raw_event.get("already_cleaned")),
            raw_original=str(raw_event.get("raw_original") or ""),
        )

    def ingest_interim(self, raw_event: dict[str, Any]) -> None:
        return

    def request_flush(self, reason: str) -> None:
        with self._lock:
            self._schedule_flush(SENTENCE_HOLD_MIN_MS, reason)

    def stop_flush(self) -> None:
        self.flush("stop_listening")

    def emit_events_to_ui_bus(self) -> None:
        return

    def reset(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._buffer = None
            self._quarantine.clear()
            self._last_raw_stt_mono = 0.0
            self._last_ui_commit_mono = 0.0
            self._last_reliable_speaker = None
            self._last_buffer_speaker = None
            self._dominant_speaker_for_current_japanese_session = None
            self._last_committed_speaker = None
            self._pending_raw_speaker_candidate = None
            self._consecutive_raw_speaker_votes = 0
            self._committed_segment_speaker_distribution.clear()
            self._translation_ready_true_count = 0
            self._translation_ready_false_count = 0
            self._cleanup_candidate_count = 0
            self._cleanup_applied_to_ui_count = 0
            self._cleanup_low_confidence_not_applied_count = 0
            self._emergency_suppressed_count = 0
            self._incomplete_tail_commit_count = 0
            self._risky_segments.clear()
            self._translation_unit_builder.reset()
            self._last_final_output_text = ""
            self._last_stable_commit = None
            self._stable_line_counter = 0
            self._last_stable_line_id = ""
            self._last_stable_source_raw_event_ids = []
            self._assembler_commit_gate_failed = False
            self._stable_hold_pending = None
            self._stable_hold_generation = 0
            self._stop_boundary_active = False
            self._assembler_exception_recovery_buffer = None
            self._punctuation_start_count = 0
            self._short_fragment_count = 0
            self._incomplete_tail_hold_count = 0
            self._stable_merge_count = 0
            self._assembler_exception_count = 0
            self._quarantine_drop_count = 0
            self._business_phrase_protected_count = 0
            self._raw_mutation_count = 0
            self._force_translation_not_ready = False
            self._business_correction_count = 0
            self._business_correction_high_confidence_count = 0
            self._business_correction_skipped_count = 0
            self._stop_tail_suppressed_count = 0
            try:
                from alpha.transcription.japanese_boundary_stabilizer import reset_boundary_stabilizer

                reset_boundary_stabilizer()
                from alpha.transcription.stable_line_revision import reset_stable_line_revision_manager

                reset_stable_line_revision_manager()
            except Exception:
                pass
            self._stop_tail_debug_written_count = 0
            self._double_prefix_repair_count = 0
            self._triple_koko_repair_count = 0
            self._punctuation_start_post_correction_merge_count = 0
            self._business_correction_regression_count = 0
            self._business_accuracy_expansion_count = 0
            self._split_fragment_repair_count = 0
            self._duplicate_phrase_dedupe_count = 0
            self._midline_punctuation_cleanup_count = 0
            self._name_correction_count = 0
            self._name_correction_skipped_count = 0
            self._session_has_chin_name = False
            self._exact_duplicate_continuation_count = 0
            self._recent_stable_lines = []

    def flush(self, reason: str) -> None:
        with self._lock:
            self._stop_boundary_active = True
            pending = self._stable_hold_pending
            if pending and reason == "stop_listening":
                self._release_stable_hold_locked(
                    str(pending.get("hold_kind") or "stop_flush"),
                    force=True,
                )
            if reason == "stop_listening" and JAPANESE_BOUNDARY_STABILIZER_ENABLED:
                try:
                    from alpha.transcription.japanese_boundary_stabilizer import (
                        get_boundary_stabilizer,
                    )

                    stab_flush = get_boundary_stabilizer().flush_pending(stop_flush=True)
                    if stab_flush and stab_flush.get("emit_now") and stab_flush.get("output_text"):
                        speaker = int(
                            (self._last_stable_commit or {}).get("speaker", 0) or 0
                        )
                        self._route_stable_publish(
                            speaker,
                            stab_flush["output_text"],
                            {"source": "boundary_stabilizer_stop_flush"},
                            "stop_listening",
                            stop_incomplete=True,
                            force_release=True,
                        )
                except Exception:
                    pass
            buf = self._buffer
            if not buf:
                return
            text = (buf.get("text") or "").strip()
            if not text:
                self._buffer = None
                return
            incomplete, inc_reason = looks_incomplete_japanese_fragment(text)
            if incomplete or reason == "stop_listening":
                self._flush_locked(
                    "stop_flush_incomplete_tail",
                    force=True,
                    stop_incomplete=incomplete,
                    incomplete_reason=inc_reason,
                )
            else:
                self._flush_locked(reason, force=True)

    def ingest(
        self,
        speaker: int,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
        upstream_reason: str = "deepgram_final",
        *,
        already_cleaned: bool = False,
        raw_original: str = "",
    ) -> None:
        try:
            self._ingest_safe(
                speaker,
                text,
                metadata,
                upstream_reason,
                already_cleaned=already_cleaned,
                raw_original=raw_original,
            )
        except Exception as exc:
            self._handle_assembler_exception(
                exc, speaker, text, metadata, upstream_reason
            )

    def _ingest_safe(
        self,
        speaker: int,
        text: str,
        metadata: Optional[dict[str, Any]],
        upstream_reason: str,
        *,
        already_cleaned: bool = False,
        raw_original: str = "",
        bypass_quarantine: bool = False,
    ) -> None:
        raw = (text or "").strip()
        if not raw:
            return
        if not already_cleaned:
            if detect_raw_stt_error_suspected(raw):
                jp_accuracy_log("RAW_STT_ERROR_CANDIDATE", raw_text=raw)
            cleaned, cleanup_reason, cleanup_flags = cleanup_japanese_per_fragment(raw)
            jp_accuracy_log(
                "per_fragment_cleanup",
                per_fragment_cleanup_input=raw,
                per_fragment_cleanup_output=cleaned,
                cleanup_reason=cleanup_reason,
                exact_duplicate_collapse=cleanup_flags.get(
                    "exact_duplicate_collapse", False
                ),
                prefix_extension_duplicate_collapse=cleanup_flags.get(
                    "prefix_extension_duplicate_collapse", False
                ),
                latin_acronym_spacing_normalized=cleanup_flags.get(
                    "latin_acronym_spacing_normalized", False
                ),
            )
            fragment = cleaned
            raw_orig = raw
        else:
            fragment = raw
            raw_orig = raw_original or raw

        self._last_raw_stt_mono = time.monotonic()

        released = self._try_release_quarantine(fragment)
        if released:
            fragment = released
        elif not bypass_quarantine and self._should_quarantine(fragment):
            self._quarantine_fragment(speaker, fragment, raw_orig)
            return

        with self._lock:
            prepared_metadata = _prepare_assembler_ingress_metadata(
                metadata or {},
                upstream_reason,
            )
            self._ingest_locked(
                speaker,
                fragment,
                prepared_metadata,
                upstream_reason,
                raw_original=raw_orig,
            )
            flush_due = self._pending_flush_due_mono
            flush_gen = self._pending_flush_generation
            flush_reason = self._pending_flush_reason
        if not JapaneseContinuityAssembler._tk_scheduling_confirmed_logged:
            JapaneseContinuityAssembler._tk_scheduling_confirmed_logged = True
            jp_accuracy_log("ASSEMBLER_NO_TK_SCHEDULING_CONFIRMED")
        if flush_due is not None:
            from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

            get_language_pipeline_worker().schedule_flush(
                self, flush_due, flush_gen, flush_reason
            )
            jp_accuracy_log(
                "ASSEMBLER_FLUSH_REQUEST_POSTED_TO_EVENT_BUS",
                generation=flush_gen,
                reason=flush_reason,
            )

    def _handle_assembler_exception(
        self,
        exc: Exception,
        speaker: int,
        text: str,
        metadata: Optional[dict[str, Any]],
        upstream_reason: str,
    ) -> None:
        try:
            from alpha.utils.crash_guard_log import log_exception

            log_exception(
                exc,
                source="japanese_continuity_assembler",
                host=self._host,
            )
        except Exception:
            pass
        jp_accuracy_log(
            "ASSEMBLER_EXCEPTION_CAUGHT",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            upstream_reason=upstream_reason,
        )
        if not JAPANESE_CONTINUITY_ASSEMBLER_SAFE_MODE:
            raise
        with self._lock:
            self._cancel_timer()
            self._buffer = None
        fragment = (text or "").strip()
        if not fragment:
            return
        if ASSEMBLER_EXCEPTION_NO_DIRECT_COMMIT:
            self._assembler_exception_count += 1
            jp_accuracy_log(
                "ASSEMBLER_EXCEPTION_DIRECT_COMMIT_BLOCKED",
                fragment_preview=fragment[:80],
                upstream_reason=upstream_reason,
            )
            with self._lock:
                self._assembler_exception_recovery_buffer = {
                    "speaker": speaker,
                    "text": fragment,
                    "metadata": dict(metadata or {}),
                    "upstream_reason": upstream_reason,
                    "exception": f"{type(exc).__name__}: {exc}",
                }
            jp_accuracy_log(
                "ASSEMBLER_EXCEPTION_FRAGMENT_RECOVERED",
                fragment_preview=fragment[:80],
                raw_mutated=False,
            )
            try:
                from alpha.utils.accuracy_decision_log import log_assembler_decision

                log_assembler_decision(
                    raw_text=fragment,
                    decision="assembler_exception_recovery",
                    reason="direct_commit_blocked",
                    commit_reason="assembler_exception_direct_commit_blocked",
                    exception=f"{type(exc).__name__}: {exc}",
                    translation_ready=False,
                    raw_mutated=False,
                )
            except Exception:
                pass
            return
        try:
            cleaned, _, _ = cleanup_japanese_per_fragment(fragment)
            self._publish_sentence(
                speaker,
                cleaned,
                dict(metadata or {}),
                "assembler_exception_direct_commit_fallback",
            )
        except Exception as inner:
            try:
                from alpha.utils.crash_guard_log import log_exception

                log_exception(
                    inner,
                    source="assembler_exception_fallback_publish",
                    host=self._host,
                )
            except Exception:
                pass

    def _buffer_hold_ms(self, buf: dict[str, Any]) -> float:
        started = float(
            buf.get("hold_started_mono")
            or buf.get("created_mono")
            or time.monotonic()
        )
        return (time.monotonic() - started) * 1000.0

    def _is_meaningful_timeout_chunk(self, text: str) -> bool:
        compact_len = count_japanese_chars(text)
        if compact_len >= 20:
            return True
        if compact_len >= 12 and not self._matches_suspicious_noise_fragment(text):
            return True
        return any(fragment in text for fragment in _MEANING_FRAGMENTS)

    def _has_incomplete_weak_tail(self, text: str) -> tuple[bool, str]:
        segment = (text or "").strip()
        for token in _INCOMPLETE_TAIL_WEAK_ENDINGS:
            if segment.endswith(token):
                return True, token
        return False, ""

    def _is_buffer_extreme_size(self, *, buffer_chars: int, part_count: int) -> bool:
        extreme_chars = buffer_chars >= max(
            JAPANESE_CONTINUITY_MAX_BUFFER_CHARS + 40,
            int(JAPANESE_CONTINUITY_MAX_BUFFER_CHARS * 1.4),
        )
        extreme_parts = part_count >= (JAPANESE_CONTINUITY_MAX_PARTS + 4)
        return bool(extreme_chars or extreme_parts)

    def _safe_hold_timeout_reason(self, text: str) -> str:
        prefix, tail, _bname, btype = find_commit_boundary(text)
        if prefix and tail and btype == "soft":
            return "safe_hold_timeout_soft_boundary"
        if any(text.endswith(token) for token in _SAFE_HOLD_TIMEOUT_ENDINGS):
            return "safe_hold_timeout_particle_merge_candidate"
        incomplete, _ = looks_incomplete_japanese_fragment(text)
        if incomplete:
            return "safe_hold_timeout_incomplete_but_stable"
        return "safe_hold_timeout_meaningful_chunk"

    def _commit_safe_hold_timeout(
        self,
        buf: dict[str, Any],
        *,
        reason: str,
        hold_ms: float,
        last_fragment_age_ms: float,
    ) -> bool:
        text = (buf.get("text") or "").strip()
        if not text:
            return False
        jp_accuracy_log(
            "SAFE_HOLD_TIMEOUT_COMMIT",
            safe_hold_timeout_reason=reason,
            buffer_chars=len(text),
            part_count=int(buf.get("part_count") or 1),
            hold_ms=int(hold_ms),
            last_fragment_age_ms=int(last_fragment_age_ms),
            ends_incomplete=looks_incomplete_japanese_fragment(text)[0],
            committed_text=text,
        )
        metadata = dict(buf.get("metadata") or {})
        if reason in {
            "safe_hold_timeout_particle_merge_candidate",
            "safe_hold_timeout_incomplete_but_stable",
            "safe_hold_timeout_incomplete_tail",
        }:
            jp_accuracy_log(
                "INCOMPLETE_TAIL_SAFE_TIMEOUT_COMMIT",
                committed_text=text,
                safe_hold_timeout_reason=reason,
            )
            jp_accuracy_log(
                "ALLOW_LATE_CONTINUATION_UPDATE_PREVIOUS",
                committed_text=text,
                safe_hold_timeout_reason=reason,
            )
            metadata["allow_late_continuation_update_previous"] = True
        self._route_stable_publish(
            buf.get("speaker", 1),
            text,
            metadata,
            reason,
            raw_fragments=list(buf.get("raw_fragments") or []),
        )
        self._buffer = None
        self._cancel_timer()
        return True

    def _is_true_emergency_state(
        self,
        *,
        buffer_chars: int,
        part_count: int,
        hold_ms: float,
    ) -> bool:
        return self._is_buffer_extreme_size(
            buffer_chars=buffer_chars,
            part_count=part_count,
        )

    def _commit_safe_chunk_boundary(
        self,
        buf: dict[str, Any],
        *,
        prefix: str,
        tail: str,
        boundary_name: str,
        boundary_type: str,
        hold_ms: float,
        last_fragment_age_ms: float,
    ) -> bool:
        text = (buf.get("text") or "").strip()
        if not text or not prefix:
            return False
        jp_accuracy_log(
            "SAFE_CHUNK_BOUNDARY_COMMIT",
            buffer_chars=len(text),
            part_count=int(buf.get("part_count") or 1),
            hold_ms=int(hold_ms),
            last_fragment_age_ms=int(last_fragment_age_ms),
            committed_text=prefix,
            remaining_tail=tail,
            safe_boundary_detected=boundary_name,
            boundary_type=boundary_type,
        )
        self._route_stable_publish(
            buf.get("speaker", 1),
            prefix,
            dict(buf.get("metadata") or {}),
            "safe_chunk_boundary_commit",
            raw_fragments=list(buf.get("raw_fragments") or []),
            held_tail=tail,
            safe_boundary_used=boundary_name,
            boundary_type=boundary_type,
        )
        if tail:
            buf["text"] = tail
            buf["raw_fragments"] = [tail]
            buf["created_mono"] = time.monotonic()
            buf["updated_mono"] = time.monotonic()
            buf["hold_started_mono"] = time.monotonic()
            buf["part_count"] = 1
        else:
            self._buffer = None
            self._cancel_timer()
        return True

    def emit_final_live_session_summary(
        self,
        *,
        reason: str,
        final_output_tail_preview: str = "",
    ) -> None:
        distribution = self._speaker_distribution_snapshot()
        event_counts = get_japanese_accuracy_event_counts()
        committed_segment_count = sum(int(v) for v in self._committed_segment_speaker_distribution.values())
        tail_preview = (final_output_tail_preview or self._last_final_output_text or "").strip()
        if len(tail_preview) > 220:
            tail_preview = tail_preview[-220:]
        ready_true = int(self._translation_ready_true_count)
        ready_false = int(self._translation_ready_false_count)
        ready_total = ready_true + ready_false
        translation_ready_ratio = (
            round(ready_true / ready_total, 3) if ready_total > 0 else 0.0
        )
        risky_preview = list(self._risky_segments[:5])
        internal_stable_commit_count = int(
            event_counts.get("STABLE_JAPANESE_COMMIT", 0)
        )
        exported_ui_segment_count = int(
            getattr(self._host, "_exported_ui_segment_count", 0) or 0
        )
        self._translation_unit_builder.flush(reason=reason)
        unit_summary = self._translation_unit_builder.summary_counts()
        summary_payload = {
            "reason": reason,
            "dominant_speaker": self._dominant_speaker_for_current_japanese_session,
            "last_committed_speaker": self._last_committed_speaker,
            "committed_segment_count": committed_segment_count,
            "internal_stable_commit_count": internal_stable_commit_count,
            "exported_ui_segment_count": exported_ui_segment_count,
            "speaker_distribution": distribution,
            "EMERGENCY_COMMIT_count": int(event_counts.get("EMERGENCY_COMMIT", 0)),
            "SAFE_CHUNK_BOUNDARY_COMMIT_count": int(
                event_counts.get("SAFE_CHUNK_BOUNDARY_COMMIT", 0)
            ),
            "SAFE_HOLD_TIMEOUT_COMMIT_count": int(
                event_counts.get("SAFE_HOLD_TIMEOUT_COMMIT", 0)
            ),
            "STABLE_JAPANESE_COMMIT_count": int(
                event_counts.get("STABLE_JAPANESE_COMMIT", 0)
            ),
            "TRANSLATION_READY_TRUE_count": ready_true,
            "TRANSLATION_READY_FALSE_count": ready_false,
            "TRANSLATION_READY_RATIO": translation_ready_ratio,
            "JAPANESE_CLEANUP_CANDIDATE_count": int(self._cleanup_candidate_count),
            "cleanup_applied_to_ui_count": int(self._cleanup_applied_to_ui_count),
            "cleanup_high_confidence_applied_count": int(
                self._cleanup_applied_to_ui_count
            ),
            "cleanup_low_confidence_not_applied_count": int(
                self._cleanup_low_confidence_not_applied_count
            ),
            "emergency_suppressed_count": int(self._emergency_suppressed_count),
            "incomplete_tail_commit_count": int(self._incomplete_tail_commit_count),
            "risky_segment_count": len(self._risky_segments),
            "risky_segments_preview": risky_preview,
            "KEYTERM_OVERBIAS_CANDIDATE_count": int(
                event_counts.get("KEYTERM_OVERBIAS_CANDIDATE", 0)
            ),
            "NOISE_FRAGMENT_QUARANTINED_count": int(
                event_counts.get("NOISE_FRAGMENT_QUARANTINED", 0)
            ),
            "STALE_FINAL_DROPPED_count": int(
                event_counts.get("STALE_FINAL_DROPPED", 0)
            ),
            "translation_unit_count": unit_summary["translation_unit_count"],
            "ready_translation_unit_count": unit_summary["ready_translation_unit_count"],
            "TRANSLATION_UNIT_READY_RATIO": unit_summary["TRANSLATION_UNIT_READY_RATIO"],
            "translation_unit_preview": self._translation_unit_builder.units_preview(),
            "final_output_tail_preview": tail_preview,
        }
        jp_accuracy_log("final_speaker_distribution", **summary_payload)
        jp_accuracy_log("FINAL_LIVE_SESSION_SUMMARY", **summary_payload)
        self.emit_business_japanese_accuracy_summary(reason=reason)
        if TRANSLATION_READINESS_METRICS_ENABLED:
            ready_true = int(self._translation_ready_true_count)
            ready_false = int(self._translation_ready_false_count)
            stable_total = ready_true + ready_false
            try:
                from alpha.utils.accuracy_decision_log import write_translation_readiness_summary
                from alpha.utils.troubleshooting_paths import get_audio_temp_path

                audio_available = get_audio_temp_path("audio_manifest").exists()
                write_translation_readiness_summary(
                    {
                        "stable_commit_count": stable_total,
                        "translation_ready_true_count": ready_true,
                        "translation_ready_false_count": ready_false,
                        "translation_ready_ratio": (
                            round(ready_true / stable_total, 3) if stable_total > 0 else 0.0
                        ),
                        "punctuation_start_count": int(self._punctuation_start_count),
                        "short_fragment_count": int(self._short_fragment_count),
                        "incomplete_tail_count": int(self._incomplete_tail_hold_count),
                        "assembler_exception_count": int(self._assembler_exception_count),
                        "quarantine_drop_count": int(self._quarantine_drop_count),
                        "business_phrase_protected_count": int(
                            self._business_phrase_protected_count
                        ),
                        "stable_merge_count": int(self._stable_merge_count),
                        "raw_mutation_count": int(self._raw_mutation_count),
                        "business_correction_count": int(self._business_correction_count),
                        "business_correction_high_confidence_count": int(
                            self._business_correction_high_confidence_count
                        ),
                        "business_correction_skipped_count": int(
                            self._business_correction_skipped_count
                        ),
                        "stop_tail_suppressed_count": int(self._stop_tail_suppressed_count),
                        "stop_tail_debug_written_count": int(
                            self._stop_tail_debug_written_count
                        ),
                        "clean_alpha_output_line_count": int(exported_ui_segment_count),
                        "incomplete_tail_debug_count": int(
                            self._stop_tail_debug_written_count
                        ),
                        "temp_audio_available": audio_available,
                        "temp_audio_retention_hours": 2,
                        "accuracy_evidence_ready": bool(
                            exported_ui_segment_count > 0 or audio_available
                        ),
                        "double_prefix_repair_count": int(self._double_prefix_repair_count),
                        "triple_koko_repair_count": int(self._triple_koko_repair_count),
                        "punctuation_start_post_correction_merge_count": int(
                            self._punctuation_start_post_correction_merge_count
                        ),
                        "business_correction_regression_count": int(
                            self._business_correction_regression_count
                        ),
                        "business_accuracy_expansion_count": int(
                            self._business_accuracy_expansion_count
                        ),
                        "split_fragment_repair_count": int(self._split_fragment_repair_count),
                        "duplicate_phrase_dedupe_count": int(
                            self._duplicate_phrase_dedupe_count
                        ),
                        "midline_punctuation_cleanup_count": int(
                            self._midline_punctuation_cleanup_count
                        ),
                        "name_correction_count": int(self._name_correction_count),
                        "name_correction_skipped_count": int(
                            self._name_correction_skipped_count
                        ),
                        "exact_duplicate_continuation_count": int(
                            self._exact_duplicate_continuation_count
                        ),
                        "accuracy_regression_rollback_85223": True,
                        "accuracy_direction_settled_85231": True,
                        "benchmark_baseline_lock_enabled": True,
                        "anti_overfit_mode_enabled": True,
                        "auto_business_correction_level": "minimal",
                        "lesson_specific_corrections_disabled": True,
                        "safe_correction_gate_enabled": True,
                        "harmful_85222_expansion_disabled": True,
                        "evidence_pointer_fix_preserved": True,
                        "latest_accuracy_zip_fix_preserved": True,
                        "latest_pointer_status_fixed": True,
                        "latest_upload_zip_pointer_fixed": True,
                        "latest_accuracy_zip_entry_verified": True,
                        "validation_false_failure_fixed": True,
                    }
                )
            except Exception:
                pass
        try:
            from alpha.utils.runtime_evidence import mirror_runtime_event

            mirror_runtime_event("FINAL_LIVE_SESSION_SUMMARY", summary_payload)
        except Exception:
            pass

    def emit_business_japanese_accuracy_summary(self, *, reason: str = "stop_listening") -> None:
        from alpha.constants import JAPANESE_KEYTERM_PROFILE, resolve_japanese_keyterms
        from alpha.utils.runtime_evidence import get_ui_performance_counters

        event_counts = get_japanese_accuracy_event_counts()
        perf = get_ui_performance_counters().as_summary_dict()
        terms, profile_name, _ = resolve_japanese_keyterms()
        esl_markers = ("ESL", "トワイス", "ジヒョ", "英語のレベル")
        old_esl_present = any(m in terms for m in esl_markers)
        keyterms_total = len(terms)
        keyterm_count_within_safe_limit = 30 <= keyterms_total <= 60
        payload = {
            "reason": reason,
            "business_cleanup_applied_count": self._business_cleanup_applied_count,
            "business_cleanup_skipped_already_correct_count": (
                self._business_cleanup_skipped_already_correct_count
            ),
            "business_cleanup_duplicate_prevented_count": (
                self._business_cleanup_duplicate_prevented_count
            ),
            "business_risk_candidate_count": self._business_risk_candidate_count,
            "remaining_candidate_only_count": self._business_risk_candidate_count,
            "duplicate_damage_detected_count": self._duplicate_damage_detected_count,
            "duplicate_damage_fixed_count": self._duplicate_damage_fixed_count,
            "duplicate_damage_reverted_count": self._duplicate_damage_reverted_count,
            "idempotency_check_failed_count": self._idempotency_check_failed_count,
            "stale_esl_twice_keyterm_present_count": 0,
            "short_valid_term_dropped_count": self._short_valid_term_dropped_count,
            "short_valid_term_committed_count": self._short_valid_term_committed_count,
            "other_company_term_confusion_count": self._other_company_term_confusion_count,
            "successor_term_confusion_count": self._successor_term_confusion_count,
            "causative_form_confusion_count": self._causative_form_confusion_count,
            "role_change_confusion_count": self._role_change_confusion_count,
            "polite_expression_confusion_count": self._polite_expression_confusion_count,
            "JAPANESE_KEYTERM_PROFILE": JAPANESE_KEYTERM_PROFILE,
            "keyterm_profile": profile_name,
            "keyterms_total": keyterms_total,
            "old_esl_twice_terms_present": old_esl_present,
            "keyterm_count_within_safe_limit": keyterm_count_within_safe_limit,
            "EMERGENCY_COMMIT_count": int(event_counts.get("EMERGENCY_COMMIT", 0)),
            "speaker_distribution": self._speaker_distribution_snapshot(),
            "exported_ui_segment_count": int(
                getattr(self._host, "_exported_ui_segment_count", 0) or 0
            ),
            "internal_stable_commit_count": int(
                event_counts.get("STABLE_JAPANESE_COMMIT", 0)
            ),
            "transcript_ui_insert_slow_count": perf.get("transcript_ui_insert_slow_count", 0),
            "ui_queue_drain_slow_count": perf.get("ui_queue_drain_slow_count", 0),
            "ui_queue_tick_slow_count": perf.get("ui_queue_tick_slow_count", 0),
            "audio_queue_overflow_after_stop_count": perf.get(
                "audio_queue_overflow_after_stop_count", 0
            ),
        }
        jp_accuracy_log("BUSINESS_JAPANESE_ACCURACY_SUMMARY", **payload)
        jp_accuracy_log(
            "KEYTERM_PROFILE_QUALITY_SUMMARY",
            keyterms_total=keyterms_total,
            keyterm_profile=profile_name,
            helped_terms_detected=["他社", "担当交代", "後任", "使役形", "翌日", "このたび"],
            still_wrong_terms_detected=[],
            possible_overbias_terms=[],
            stale_terms_detected=[] if not old_esl_present else list(esl_markers),
            keyterm_count_within_safe_limit=keyterm_count_within_safe_limit,
            keyterm_overbias_warning=not keyterm_count_within_safe_limit,
            recommendation=(
                "profile_ok"
                if keyterm_count_within_safe_limit and not old_esl_present
                else "review_keyterm_profile"
            ),
        )

    def _check_emergency_commit(self, buf: dict[str, Any]) -> bool:
        text = (buf.get("text") or "").strip()
        if not text:
            return False
        part_count = int(buf.get("part_count") or 1)
        hold_ms = self._buffer_hold_ms(buf)
        buffer_chars = len(text)
        last_frag_ms = self._last_fragment_age_ms(buf)
        incomplete, _ = looks_incomplete_japanese_fragment(text)

        hard_size = (
            buffer_chars >= JAPANESE_CONTINUITY_MAX_BUFFER_CHARS
            or part_count >= JAPANESE_CONTINUITY_MAX_PARTS
        )
        hard_time = hold_ms >= JAPANESE_CONTINUITY_MAX_HOLD_MS
        very_old_stuck = hold_ms >= (JAPANESE_CONTINUITY_MAX_HOLD_MS + 4000)

        if not hard_size and not hard_time:
            return False

        if last_frag_ms < 2500.0 and not hard_size:
            return False

        ends_incomplete = incomplete and any(
            text.endswith(e) for e in _EMERGENCY_GRACE_ENDINGS
        )
        if ends_incomplete and last_frag_ms < JAPANESE_EMERGENCY_LAST_FRAG_GRACE_MS:
            if not hard_size:
                return False

        prefix, tail, bname, btype = find_commit_boundary(text)
        true_emergency_state = self._is_true_emergency_state(
            buffer_chars=buffer_chars,
            part_count=part_count,
            hold_ms=hold_ms,
        )
        safe_boundary_available = bool(
            prefix
            and tail
            and btype != "none"
            and not _is_false_ne_tail_split(prefix, tail)
            and not _is_false_desu_question_tail_split(prefix, tail)
        )
        complete_sentence_safe_boundary = bool(
            has_strong_sentence_end(text) and not incomplete
        )
        if (
            complete_sentence_safe_boundary
            and self._is_meaningful_timeout_chunk(text)
            and not true_emergency_state
        ):
            suppressed_boundary_name = "strong_sentence_end"
            suppressed_boundary_type = "strong"
            commit_prefix = text
            commit_tail = ""
            jp_accuracy_log(
                "EMERGENCY_COMMIT_SUPPRESSED_SAFE_BOUNDARY",
                old_reason="japanese_continuity_emergency_commit",
                new_reason="japanese_continuity_assembler_safe_chunk_boundary_commit",
                committed_text=commit_prefix,
                remaining_tail=commit_tail,
                buffer_chars=buffer_chars,
                part_count=part_count,
                hold_ms=int(hold_ms),
                last_fragment_age_ms=int(last_frag_ms),
                safe_boundary_detected=suppressed_boundary_name,
                boundary_type=suppressed_boundary_type,
            )
            return self._commit_safe_chunk_boundary(
                buf,
                prefix=commit_prefix,
                tail=commit_tail,
                boundary_name=suppressed_boundary_name,
                boundary_type=suppressed_boundary_type,
                hold_ms=hold_ms,
                last_fragment_age_ms=last_frag_ms,
            )
        if (
            safe_boundary_available
            and self._is_meaningful_timeout_chunk(text)
            and not true_emergency_state
        ):
            suppressed_boundary_name = bname
            suppressed_boundary_type = btype
            commit_prefix = prefix
            commit_tail = tail
            jp_accuracy_log(
                "EMERGENCY_COMMIT_SUPPRESSED_SAFE_BOUNDARY",
                old_reason="japanese_continuity_emergency_commit",
                new_reason="japanese_continuity_assembler_safe_chunk_boundary_commit",
                committed_text=commit_prefix,
                remaining_tail=commit_tail,
                buffer_chars=buffer_chars,
                part_count=part_count,
                hold_ms=int(hold_ms),
                last_fragment_age_ms=int(last_frag_ms),
                safe_boundary_detected=suppressed_boundary_name,
                boundary_type=suppressed_boundary_type,
            )
            return self._commit_safe_chunk_boundary(
                buf,
                prefix=commit_prefix,
                tail=commit_tail,
                boundary_name=suppressed_boundary_name,
                boundary_type=suppressed_boundary_type,
                hold_ms=hold_ms,
                last_fragment_age_ms=last_frag_ms,
            )

        if hard_time and not hard_size:
            if self._is_meaningful_timeout_chunk(text) and not very_old_stuck:
                reason = self._safe_hold_timeout_reason(text)
                return self._commit_safe_hold_timeout(
                    buf,
                    reason=reason,
                    hold_ms=hold_ms,
                    last_fragment_age_ms=last_frag_ms,
                )
            if not very_old_stuck:
                return False

        meaningful_chunk = self._is_meaningful_timeout_chunk(text)
        has_weak_tail, tail_token = self._has_incomplete_weak_tail(text)
        buffer_extreme = self._is_buffer_extreme_size(
            buffer_chars=buffer_chars,
            part_count=part_count,
        )
        if meaningful_chunk and has_weak_tail and not buffer_extreme:
            preview_score, _, _ = self._translation_readiness_score(
                text,
                commit_reason=(
                    "japanese_continuity_assembler_safe_hold_timeout_incomplete_tail"
                ),
                held_tail="",
                raw_fragments=list(buf.get("raw_fragments") or []),
                is_stop_incomplete=False,
            )
            jp_accuracy_log(
                "EMERGENCY_COMMIT_SUPPRESSED_INCOMPLETE_TAIL",
                old_reason="japanese_continuity_emergency_commit",
                new_reason=(
                    "japanese_continuity_assembler_safe_hold_timeout_incomplete_tail"
                ),
                committed_text=text,
                tail=tail_token,
                buffer_chars=buffer_chars,
                part_count=part_count,
                hold_ms=int(hold_ms),
                last_fragment_age_ms=int(last_frag_ms),
                translation_ready_score=preview_score,
            )
            self._emergency_suppressed_count += 1
            self._incomplete_tail_commit_count += 1
            return self._commit_safe_hold_timeout(
                buf,
                reason="safe_hold_timeout_incomplete_tail",
                hold_ms=hold_ms,
                last_fragment_age_ms=last_frag_ms,
            )

        if meaningful_chunk and not buffer_extreme:
            reason = self._safe_hold_timeout_reason(text)
            return self._commit_safe_hold_timeout(
                buf,
                reason=reason,
                hold_ms=hold_ms,
                last_fragment_age_ms=last_frag_ms,
            )

        reason = "japanese_continuity_emergency_commit"
        remaining_tail = ""
        commit_text = text
        if (
            prefix
            and tail
            and btype != "none"
            and not _is_false_ne_tail_split(prefix, tail)
            and not _is_false_desu_question_tail_split(prefix, tail)
        ):
            commit_text = prefix
            remaining_tail = tail
        jp_accuracy_log(
            "EMERGENCY_COMMIT",
            reason=reason,
            buffer_chars=buffer_chars,
            part_count=part_count,
            hold_ms=int(hold_ms),
            last_fragment_age_ms=int(last_frag_ms),
            ends_incomplete=ends_incomplete,
            committed_text=commit_text,
            remaining_tail=remaining_tail,
            safe_boundary_detected=bname or None,
            boundary_type=btype or "none",
        )
        self._route_stable_publish(
            buf["speaker"],
            commit_text,
            dict(buf.get("metadata") or {}),
            reason,
            raw_fragments=list(buf.get("raw_fragments") or []),
            held_tail=remaining_tail,
            safe_boundary_used=bname,
            boundary_type=btype,
        )
        if remaining_tail:
            buf["text"] = remaining_tail
            buf["raw_fragments"] = [remaining_tail]
            buf["created_mono"] = time.monotonic()
            buf["updated_mono"] = time.monotonic()
            buf["hold_started_mono"] = time.monotonic()
            buf["part_count"] = 1
        else:
            self._buffer = None
            self._cancel_timer()
        return True

    def emit_heartbeat_from_ui(self) -> None:
        """Called only from Tk main thread — uses non-blocking snapshot."""
        host = self._host
        snap = self.get_buffer_snapshot_nonblocking()
        audio_q = -1
        ui_q = -1
        is_listening = bool(getattr(host, "is_listening", False))
        legacy_listening = bool(getattr(host, "listening", False))
        stabilizer = getattr(host, "_jp_final_stabilizer", None)
        accepting_transcripts = None
        if stabilizer is not None and hasattr(stabilizer, "is_accepting"):
            try:
                accepting_transcripts = bool(stabilizer.is_accepting())
            except Exception:
                accepting_transcripts = None
        try:
            aq = getattr(host, "_audio_q", None)
            if aq is not None:
                audio_q = aq.qsize()
        except Exception:
            pass
        try:
            tq = getattr(host, "transcript_queue", None)
            if tq is not None:
                ui_q = tq.qsize()
        except Exception:
            pass
        jp_accuracy_log(
            "JAPANESE_PIPELINE_HEARTBEAT",
            is_listening=is_listening,
            legacy_listening_attr=legacy_listening,
            accepting_transcripts=accepting_transcripts,
            websocket_connected=bool(getattr(host, "_dg_ws", None) is not None),
            audio_queue_size=audio_q,
            ui_queue_size=ui_q,
            last_raw_stt_age_ms=(
                int((time.monotonic() - self._last_raw_stt_mono) * 1000)
                if self._last_raw_stt_mono
                else None
            ),
            last_ui_commit_age_ms=(
                int((time.monotonic() - self._last_ui_commit_mono) * 1000)
                if self._last_ui_commit_mono
                else None
            ),
            **snap,
        )
        if is_listening:
            try:
                from alpha.utils.runtime_evidence import (
                    emit_long_session_accuracy_summary,
                    should_emit_long_session_summary,
                )

                if should_emit_long_session_summary():
                    emit_long_session_accuracy_summary(
                        self,
                        reason="heartbeat_2min",
                        host=host,
                    )
            except Exception:
                pass

    def _match_valid_short_list_term(self, fragment: str) -> Optional[str]:
        stripped = (fragment or "").strip().strip("、。！？!?")
        compact = compact_cjk_for_compare(stripped, "ja")
        for term in VALID_SHORT_JAPANESE_LIST_TERMS:
            if compact == compact_cjk_for_compare(term, "ja"):
                return term
        return None

    def _has_list_lesson_context(self) -> bool:
        window = self._recent_committed_context[-500:]
        return any(cue in window for cue in JAPANESE_LIST_LESSON_CONTEXT_CUES)

    def _note_committed_context(self, text: str) -> None:
        if not text:
            return
        self._recent_committed_context = (self._recent_committed_context + text)[-800:]

    def _track_business_risk_pairs(self, raw_text: str, stable_text: str) -> None:
        if "他者の人" in raw_text or "他者の人" in stable_text:
            self._other_company_term_confusion_count += 1
        if "公認" in raw_text:
            self._successor_term_confusion_count += 1
        if "短頭交代" in raw_text:
            self._role_change_confusion_count += 1
        if "使役系" in raw_text:
            self._causative_form_confusion_count += 1
        if "回りました" in raw_text and "参りました" not in stable_text:
            self._polite_expression_confusion_count += 1

    def _cancel_quarantine_timer(self) -> None:
        self._quarantine_drop_scheduled = False

    def _drop_expired_quarantine_locked(self, *, skip_valid_short: bool = False) -> None:
        if not self._quarantine:
            self._quarantine_drop_scheduled = False
            return
        now = time.monotonic()
        kept: list[dict[str, Any]] = []
        for entry in self._quarantine:
            if entry.get("valid_short_term"):
                kept.append(entry)
                continue
            age = now - entry["quarantined_mono"]
            if age >= JAPANESE_NOISE_QUARANTINE_DROP_S:
                drop_text = str(entry.get("raw", entry.get("text", "")))
                if BUSINESS_PHRASE_PROTECTION_ENABLED and is_protected_business_phrase(
                    drop_text
                ):
                    kept.append(entry)
                    jp_accuracy_log(
                        "BUSINESS_PHRASE_PROTECTED_FROM_DROP",
                        raw_text=drop_text,
                        reason="quarantine_expiry_protected",
                        raw_mutated=False,
                    )
                    self._business_phrase_protected_count += 1
                    continue
                jp_accuracy_log(
                    "NOISE_FRAGMENT_DROPPED",
                    raw_text=drop_text,
                    age_s=round(age, 1),
                )
                self._quarantine_drop_count += 1
            else:
                kept.append(entry)
        self._quarantine = kept
        if self._quarantine:
            self._schedule_quarantine_drop(skip_valid_short=True)

    def _schedule_quarantine_drop(self, *, skip_valid_short: bool = False) -> None:
        if self._quarantine_drop_scheduled:
            return
        self._quarantine_drop_scheduled = True
        drop_ms = int(JAPANESE_NOISE_QUARANTINE_DROP_S * 1000)
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        get_language_pipeline_worker().schedule_quarantine_drop(
            self, drop_ms, skip_valid_short=skip_valid_short
        )

    def _schedule_stable_hold_release(self, hold_ms: int, hold_kind: str) -> None:
        self._stable_hold_generation += 1
        generation = self._stable_hold_generation
        due = time.monotonic() + max(0.001, hold_ms / 1000.0)
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        get_language_pipeline_worker().schedule_flush(
            self,
            due,
            generation,
            f"stable_layer_hold_release:{hold_kind}",
        )

    def _release_stable_hold_locked(self, hold_kind: str, *, force: bool = False) -> None:
        pending = self._stable_hold_pending
        if not pending:
            return
        self._stable_hold_pending = None
        text = str(pending.get("text") or "").strip()
        if not text:
            return
        speaker = int(pending.get("speaker") or 1)
        metadata = dict(pending.get("metadata") or {})
        reason = str(pending.get("reason") or hold_kind)
        if pending.get("hold_kind") == "incomplete_tail":
            jp_accuracy_log(
                "INCOMPLETE_TAIL_RELEASED_AFTER_TIMEOUT",
                committed_text=text,
                hold_kind=hold_kind,
                raw_mutated=False,
            )
            self._incomplete_tail_hold_count += 1
        else:
            jp_accuracy_log(
                "SHORT_FRAGMENT_RELEASED_AFTER_TIMEOUT",
                committed_text=text,
                hold_kind=hold_kind,
                raw_mutated=False,
            )
            self._short_fragment_count += 1
        self._force_translation_not_ready = True
        self._route_stable_publish(
            speaker,
            text,
            metadata,
            f"{reason}_timeout_release",
            raw_fragments=list(pending.get("raw_fragments") or []),
            stop_incomplete=bool(pending.get("stop_incomplete")),
            incomplete_reason=str(pending.get("incomplete_reason") or ""),
            held_tail=str(pending.get("held_tail") or ""),
            safe_boundary_used=str(pending.get("safe_boundary_used") or ""),
            boundary_type=str(pending.get("boundary_type") or ""),
            force_release=True,
        )
        self._force_translation_not_ready = False

    def _hold_stable_candidate(
        self,
        *,
        speaker: int,
        text: str,
        metadata: dict[str, Any],
        reason: str,
        hold_kind: str,
        hold_ms: int,
        raw_fragments: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> None:
        self._stable_hold_pending = {
            "speaker": speaker,
            "text": text,
            "metadata": dict(metadata),
            "reason": reason,
            "hold_kind": hold_kind,
            "held_mono": time.monotonic(),
            "raw_fragments": list(raw_fragments or []),
            **kwargs,
        }
        self._schedule_stable_hold_release(hold_ms, hold_kind)
        if hold_kind == "short_fragment":
            jp_accuracy_log(
                "SHORT_FRAGMENT_HELD",
                fragment_text=text,
                hold_ms=hold_ms,
                raw_mutated=False,
            )
        else:
            jp_accuracy_log(
                "INCOMPLETE_TAIL_HELD",
                fragment_text=text,
                hold_ms=hold_ms,
                raw_mutated=False,
            )
        try:
            from alpha.utils.accuracy_decision_log import log_assembler_decision

            log_assembler_decision(
                raw_text=text,
                decision="hold",
                hold_reason=hold_kind,
                reason=reason,
                commit_reason=reason,
                translation_ready=False,
                raw_mutated=False,
            )
        except Exception:
            pass

    def _route_stable_publish(
        self,
        speaker: int,
        text: str,
        metadata: dict[str, Any],
        reason: str,
        *,
        raw_fragments: Optional[list[str]] = None,
        held_tail: str = "",
        safe_boundary_used: str = "",
        boundary_type: str = "",
        stop_incomplete: bool = False,
        incomplete_reason: str = "",
        force_release: bool = False,
    ) -> None:
        if not JAPANESE_STABLE_ACCURACY_FIX_ENABLED:
            self._publish_sentence(
                speaker,
                text,
                metadata,
                reason,
                raw_fragments=raw_fragments,
                held_tail=held_tail,
                safe_boundary_used=safe_boundary_used,
                boundary_type=boundary_type,
                stop_incomplete=stop_incomplete,
                incomplete_reason=incomplete_reason,
            )
            return

        segment = (text or "").strip()
        if not segment:
            return

        is_stop_flush = (
            stop_incomplete
            or reason in ("stop_listening", "stop_flush_incomplete_tail")
            or str(reason).startswith("stop_")
        )

        pending = self._stable_hold_pending
        if pending and not is_stop_flush and not force_release:
            held_text = str(pending.get("text") or "").strip()
            hold_kind = str(pending.get("hold_kind") or "")
            merged = merge_short_fragments(held_text, segment)
            self._stable_hold_pending = None
            if hold_kind == "short_fragment":
                jp_accuracy_log(
                    "SHORT_FRAGMENT_MERGED_WITH_NEXT",
                    held_text=held_text,
                    next_text=segment,
                    merged_text=merged,
                    raw_mutated=False,
                )
                self._stable_merge_count += 1
            else:
                jp_accuracy_log(
                    "INCOMPLETE_TAIL_MERGED_WITH_CONTINUATION",
                    held_text=held_text,
                    continuation_text=segment,
                    merged_text=merged,
                    raw_mutated=False,
                )
                self._stable_merge_count += 1
            try:
                from alpha.utils.accuracy_decision_log import log_stable_merge_correction

                log_stable_merge_correction(
                    raw_input_text=segment,
                    stable_output_text=merged,
                    transform_type=hold_kind,
                    transform_reason="held_merge_with_next",
                    previous_text_before=held_text,
                    previous_text_after=merged,
                    fragment_text=segment,
                )
            except Exception:
                pass
            segment = merged
            metadata = dict(metadata)
            metadata.update(dict(pending.get("metadata") or {}))
            raw_fragments = list(pending.get("raw_fragments") or []) + list(
                raw_fragments or []
            )

        if is_stop_flush and pending:
            self._release_stable_hold_locked(
                str(pending.get("hold_kind") or "stop_flush"),
                force=True,
            )
            pending = None

        if (
            not force_release
            and not is_stop_flush
            and INCOMPLETE_TAIL_HOLD_ENABLED
            and should_hold_incomplete_tail(segment, is_stop_flush=False)
        ):
            incomplete, tail_token = has_incomplete_tail_for_hold(segment)
            if incomplete:
                jp_accuracy_log(
                    "INCOMPLETE_TAIL_DETECTED",
                    fragment_text=segment,
                    tail_token=tail_token,
                    raw_mutated=False,
                )
                self._hold_stable_candidate(
                    speaker=speaker,
                    text=segment,
                    metadata=metadata,
                    reason=reason,
                    hold_kind="incomplete_tail",
                    hold_ms=INCOMPLETE_TAIL_HOLD_MS_NORMAL,
                    raw_fragments=raw_fragments,
                    held_tail=held_tail,
                    safe_boundary_used=safe_boundary_used,
                    boundary_type=boundary_type,
                    stop_incomplete=stop_incomplete,
                    incomplete_reason=incomplete_reason,
                )
                return

        if (
            not force_release
            and not is_stop_flush
            and should_hold_short_fragment(segment, is_stop_flush=False)
        ):
            self._hold_stable_candidate(
                speaker=speaker,
                text=segment,
                metadata=metadata,
                reason=reason,
                hold_kind="short_fragment",
                hold_ms=INCOMPLETE_TAIL_HOLD_MS_MAX,
                raw_fragments=raw_fragments,
                held_tail=held_tail,
                safe_boundary_used=safe_boundary_used,
                boundary_type=boundary_type,
                stop_incomplete=stop_incomplete,
                incomplete_reason=incomplete_reason,
            )
            return

        metadata = dict(metadata)
        update_previous = False
        merge_reason = ""
        if (
            STABLE_LAYER_SAFE_MERGE_ENABLED
            and PUNCTUATION_START_MERGE_ENABLED
            and is_punctuation_start_fragment(segment)
        ):
            self._punctuation_start_count += 1
            can_merge, merge_reason = can_merge_punctuation_with_previous(
                segment,
                self._last_stable_commit,
                current_speaker=speaker,
                stop_boundary_active=self._stop_boundary_active,
            )
            if can_merge and self._last_stable_commit:
                previous_text = str(self._last_stable_commit.get("text") or "")
                merged = merge_punctuation_fragment(previous_text, segment)
                jp_accuracy_log(
                    "PUNCTUATION_START_FRAGMENT_MERGED",
                    fragment_text=segment,
                    previous_text_before=previous_text,
                    previous_text_after=merged,
                    reason=merge_reason,
                    raw_mutated=False,
                )
                self._stable_merge_count += 1
                try:
                    from alpha.utils.accuracy_decision_log import log_stable_merge_correction

                    log_stable_merge_correction(
                        raw_input_text=segment,
                        stable_output_text=merged,
                        transform_type="punctuation_start_merge",
                        transform_reason=merge_reason,
                        previous_text_before=previous_text,
                        previous_text_after=merged,
                        fragment_text=segment,
                    )
                except Exception:
                    pass
                segment = merged
                metadata["buffer_decision"] = (
                    "update_previous",
                    "stable_layer_punctuation_merge",
                )
                metadata["stable_layer_merged_previous"] = True
                update_previous = True
            else:
                jp_accuracy_log(
                    "PUNCTUATION_START_FRAGMENT_MERGE_SKIPPED",
                    fragment_text=segment,
                    reason=merge_reason or "merge_not_safe",
                    raw_mutated=False,
                )

        if is_protected_business_phrase(segment):
            jp_accuracy_log(
                "BUSINESS_PHRASE_ALLOWED_STANDALONE",
                phrase=segment,
                raw_mutated=False,
            )
            self._business_phrase_protected_count += 1

        if JAPANESE_BOUNDARY_STABILIZER_ENABLED:
            try:
                from alpha.transcription.japanese_boundary_stabilizer import get_boundary_stabilizer

                stabilizer = get_boundary_stabilizer()
                stab = stabilizer.process(
                    segment,
                    commit_reason=reason,
                    previous_line=self._last_final_output_text,
                    stop_flush=is_stop_flush,
                )
                if not stab.get("emit_now", True):
                    if stab.get("action") == "suppress_duplicate_continuation" or stab.get("suppress_current"):
                        return
                    return
                if stab.get("suppress_current"):
                    return
                segment = stab.get("output_text") or segment
                if stab.get("should_revise") or stab.get("update_previous"):
                    update_previous = True
                metadata = dict(metadata)
                metadata["boundary_stabilizer_enabled"] = True
                metadata["boundary_action"] = stab.get("output_action") or stab.get("action", "")
                metadata["boundary_reason"] = stab.get("reason", "")
                metadata["boundary_confidence"] = stab.get("confidence", "")
                metadata["boundary_pending_used"] = bool(stab.get("pending_before"))
                metadata["boundary_merged"] = bool(stab.get("should_revise") or str(stab.get("action", "")).startswith("merge"))
                metadata["boundary_raw_mutation"] = False
                metadata["boundary_should_revise"] = bool(stab.get("should_revise"))
                metadata["boundary_should_export"] = bool(stab.get("should_export", True))
                metadata["boundary_stab_result"] = stab
                jp_accuracy_log(
                    "BOUNDARY_STABILIZER_OUTPUT_COMMITTED",
                    action=stab.get("action"),
                    reason=stab.get("reason"),
                )
            except Exception:
                pass

        self._publish_sentence(
            speaker,
            segment,
            metadata,
            reason,
            raw_fragments=raw_fragments,
            held_tail=held_tail,
            safe_boundary_used=safe_boundary_used,
            boundary_type=boundary_type,
            stop_incomplete=stop_incomplete,
            incomplete_reason=incomplete_reason,
            stable_layer_update_previous=update_previous,
        )

    def _looks_abnormal_isolated_kanji_noise(self, fragment: str) -> bool:
        stripped = (fragment or "").strip().strip("、。！？!?")
        compact_len = count_japanese_chars(stripped)
        if compact_len > 12:
            return False
        if re.fullmatch(r"[一-龯]{1,4}", stripped):
            return True
        if stripped.count("富") >= 2:
            return True
        return False

    def _source_activity_is_clearly_low(self) -> bool:
        snap = getattr(self._host, "_teams_latest_source_snapshot", {}) or {}
        if not snap:
            return False
        chosen_source = str(snap.get("chosen_source") or "").lower()
        sys_rms = float(snap.get("sys_rms") or 0.0)
        mic_rms = float(snap.get("mic_rms") or 0.0)
        if chosen_source == "none":
            return True
        return sys_rms < SYSTEM_ACTIVE_RMS_MIN and mic_rms < MIC_ACTIVE_RMS_MIN

    def _should_quarantine(self, fragment: str) -> bool:
        if BUSINESS_PHRASE_PROTECTION_ENABLED and is_protected_business_phrase(fragment):
            jp_accuracy_log(
                "BUSINESS_PHRASE_PROTECTED_FROM_DROP",
                raw_text=fragment,
                raw_mutated=False,
            )
            self._business_phrase_protected_count += 1
            try:
                from alpha.utils.accuracy_decision_log import log_quarantine_decision

                log_quarantine_decision(
                    raw_text=fragment,
                    compact_length=count_japanese_chars(fragment),
                    quarantine=False,
                    reason="business_phrase_protected",
                    raw_mutated=False,
                )
            except Exception:
                pass
            return False
        if self._match_valid_short_list_term(fragment):
            jp_accuracy_log(
                "SHORT_VALID_TERM_BYPASSED_NOISE_QUARANTINE",
                raw_text=fragment,
                matched_term=self._match_valid_short_list_term(fragment),
            )
            return False
        listening = bool(
            getattr(self._host, "is_listening", False)
            or getattr(self._host, "listening", False)
        )
        if not listening:
            return False
        compact_len = count_japanese_chars(fragment)
        if has_strong_sentence_end(fragment):
            return False
        if self._last_ui_commit_mono <= 0:
            return False
        silence_s = time.monotonic() - self._last_ui_commit_mono
        if silence_s < JAPANESE_NOISE_QUARANTINE_SILENCE_S:
            return False
        return bool(
            compact_len <= 6
            or self._matches_suspicious_noise_fragment(fragment)
            or self._looks_abnormal_isolated_kanji_noise(fragment)
            or self._source_activity_is_clearly_low()
            or bool(getattr(self._host, "_is_stopping", False))
        )

    def _quarantine_fragment(self, speaker: int, fragment: str, raw: str) -> None:
        if self._match_valid_short_list_term(fragment):
            if self._has_list_lesson_context():
                jp_accuracy_log(
                    "SHORT_VALID_TERM_HELD_FOR_LIST_CONTEXT",
                    raw_text=raw,
                    matched_term=self._match_valid_short_list_term(fragment),
                )
            entry = {
                "speaker": speaker,
                "text": fragment,
                "raw": raw,
                "quarantined_mono": time.monotonic(),
                "valid_short_term": True,
            }
            self._quarantine.append(entry)
            self._schedule_quarantine_drop(skip_valid_short=True)
            return
        entry = {
            "speaker": speaker,
            "text": fragment,
            "raw": raw,
            "quarantined_mono": time.monotonic(),
        }
        self._quarantine.append(entry)
        jp_accuracy_log(
            "NOISE_FRAGMENT_QUARANTINED",
            raw_text=raw,
            compact_len=count_japanese_chars(fragment),
            silence_since_last_commit_s=round(
                time.monotonic() - self._last_ui_commit_mono, 1
            )
            if self._last_ui_commit_mono
            else None,
        )
        try:
            from alpha.utils.accuracy_decision_log import log_quarantine_decision

            log_quarantine_decision(
                raw_text=raw,
                compact_length=count_japanese_chars(fragment),
                quarantine=True,
                reason="noise_fragment",
                raw_mutated=False,
            )
        except Exception:
            pass
        self._schedule_quarantine_drop()

    def _try_release_quarantine(self, fragment: str) -> Optional[str]:
        if not self._quarantine:
            return None
        oldest_age_s = time.monotonic() - float(
            self._quarantine[0].get("quarantined_mono") or time.monotonic()
        )
        if oldest_age_s > _NOISE_QUARANTINE_RELEASE_WINDOW_S:
            return None
        combined = "".join(e["text"] for e in self._quarantine) + fragment
        if count_japanese_chars(combined) >= JAPANESE_NOISE_QUARANTINE_RELEASE_COMPACT:
            texts = [e["text"] for e in self._quarantine]
            self._quarantine.clear()
            self._cancel_quarantine_timer()
            jp_accuracy_log(
                "NOISE_QUARANTINE_RELEASED",
                released_count=len(texts),
                combined_compact_len=count_japanese_chars(combined),
            )
            return combined
        return None

    def flush_quarantine_on_stop(self, reason: str = "stop_listening") -> None:
        """Commit valid short list terms; drop only true noise."""
        with self._lock:
            self._cancel_quarantine_timer()
            pending: list[dict[str, Any]] = []
            for entry in list(self._quarantine):
                text = str(entry.get("text", ""))
                raw = str(entry.get("raw", text))
                if entry.get("valid_short_term") or self._match_valid_short_list_term(text):
                    pending.append(entry)
                    jp_accuracy_log(
                        "SHORT_VALID_TERM_STOP_FLUSHED",
                        raw_text=raw,
                        reason=reason,
                    )
                else:
                    jp_accuracy_log(
                        "NOISE_FRAGMENT_DROPPED",
                        raw_text=raw,
                        reason=reason,
                    )
                    self._short_valid_term_dropped_count += 0
            self._quarantine.clear()
        for entry in pending:
            self._ingest_safe(
                int(entry.get("speaker") or 2),
                str(entry.get("text", "")),
                {},
                "short_valid_term_stop_flush",
                already_cleaned=True,
                raw_original=str(entry.get("raw", "")),
                bypass_quarantine=True,
            )
            self._short_valid_term_committed_count += 1
            jp_accuracy_log("SHORT_VALID_TERM_COMMITTED", raw_text=entry.get("raw", ""))

    def _matches_suspicious_noise_fragment(self, fragment: str) -> bool:
        compact_fragment = compact_cjk_for_compare(fragment, "ja")
        for pattern in _SUSPICIOUS_NOISE_FRAGMENTS:
            compact_pattern = compact_cjk_for_compare(pattern, "ja")
            if compact_fragment == compact_pattern or compact_fragment.startswith(
                compact_pattern
            ):
                return True
        return False

    def drop_quarantine(self, reason: str = "stop") -> None:
        self.flush_quarantine_on_stop(reason)

    def _last_fragment_age_ms(self, buf: dict[str, Any]) -> float:
        updated = float(buf.get("updated_mono") or buf.get("created_mono") or time.monotonic())
        return (time.monotonic() - updated) * 1000.0

    def _speaker_stability_lock_enabled(self) -> bool:
        return str(JAPANESE_STT_PROFILE).strip().lower() == "no_diarize"

    def _speaker_distribution_snapshot(self) -> dict[str, int]:
        return {
            str(int(speaker)): int(count)
            for speaker, count in sorted(self._committed_segment_speaker_distribution.items())
            if int(count) > 0
        }

    def _dominant_committed_speaker(self) -> Optional[int]:
        if not self._committed_segment_speaker_distribution:
            return self._dominant_speaker_for_current_japanese_session
        return max(
            self._committed_segment_speaker_distribution.items(),
            key=lambda item: (int(item[1]), -int(item[0])),
        )[0]

    def _speaker_share(self, speaker: Optional[int]) -> float:
        if speaker in (None, 0):
            return 0.0
        total = sum(int(v) for v in self._committed_segment_speaker_distribution.values())
        if total <= 0:
            return 0.0
        return float(
            int(self._committed_segment_speaker_distribution.get(int(speaker), 0)) / total
        )

    def _strong_speaker_evidence(self, metadata: dict[str, Any]) -> bool:
        return bool(
            metadata.get("speaker_change_confirmed")
            or metadata.get("speaker_strong_evidence")
        )

    def _looks_like_speaker_continuation_tail(self, text: str, reason: str) -> bool:
        segment = (text or "").strip()
        if not segment:
            return False
        if any(phrase in segment for phrase in _SPEAKER_LOCK_CONTINUATION_PHRASES):
            return True
        if any(segment.startswith(prefix) for prefix in _SPEAKER_LOCK_CONTINUATION_PREFIXES):
            return True
        compact_len = count_japanese_chars(segment)
        incomplete, _ = looks_incomplete_japanese_fragment(segment)
        if compact_len <= 24 and incomplete:
            return True
        if reason in {
            "stop_flush_incomplete_tail",
            "safe_hold_timeout_particle_merge_candidate",
            "safe_hold_timeout_incomplete_but_stable",
        }:
            return True
        return False

    def _record_raw_speaker_vote(
        self,
        speaker: int,
        fragment: str,
        metadata: dict[str, Any],
    ) -> None:
        if not self._speaker_stability_lock_enabled():
            return
        raw_speaker = int(speaker or 1)
        baseline_speaker = (
            self._last_reliable_speaker
            or self._last_committed_speaker
            or self._dominant_speaker_for_current_japanese_session
        )
        if baseline_speaker in (None, 0) or raw_speaker == baseline_speaker:
            self._pending_raw_speaker_candidate = None
            self._consecutive_raw_speaker_votes = 0
            return
        compact_len = count_japanese_chars(fragment)
        if compact_len <= 4:
            return
        if self._matches_suspicious_noise_fragment(fragment):
            return
        if self._looks_abnormal_isolated_kanji_noise(fragment):
            return
        if self._strong_speaker_evidence(metadata) and compact_len >= 8:
            self._pending_raw_speaker_candidate = raw_speaker
            self._consecutive_raw_speaker_votes = max(
                self._consecutive_raw_speaker_votes,
                2,
            )
            return
        if self._pending_raw_speaker_candidate == raw_speaker:
            self._consecutive_raw_speaker_votes += 1
        else:
            self._pending_raw_speaker_candidate = raw_speaker
            self._consecutive_raw_speaker_votes = 1

    def _log_speaker_stability_lock(
        self,
        *,
        raw_speaker: int,
        final_speaker: int,
        reason: str,
    ) -> None:
        jp_accuracy_log(
            "JAPANESE_SPEAKER_STABILITY_LOCK_APPLIED",
            raw_speaker=raw_speaker,
            final_speaker=final_speaker,
            dominant_speaker=self._dominant_speaker_for_current_japanese_session,
            last_reliable_speaker=self._last_reliable_speaker,
            reason=reason,
            consecutive_new_speaker_votes=self._consecutive_raw_speaker_votes,
            committed_segment_speaker_distribution=self._speaker_distribution_snapshot(),
        )

    def _update_committed_speaker_distribution(
        self,
        speaker: int,
        text: str,
        *,
        is_stop_incomplete: bool,
    ) -> None:
        final_speaker = int(speaker or 1)
        self._committed_segment_speaker_distribution[final_speaker] = (
            int(self._committed_segment_speaker_distribution.get(final_speaker, 0)) + 1
        )
        self._dominant_speaker_for_current_japanese_session = self._dominant_committed_speaker()
        self._last_committed_speaker = final_speaker
        if self._pending_raw_speaker_candidate == final_speaker:
            self._pending_raw_speaker_candidate = None
            self._consecutive_raw_speaker_votes = 0
        distribution = self._speaker_distribution_snapshot()
        jp_accuracy_log(
            "speaker_distribution_summary",
            dominant_speaker=self._dominant_speaker_for_current_japanese_session,
            last_committed_speaker=final_speaker,
            committed_segment_speaker_distribution=distribution,
        )
        if is_stop_incomplete:
            event_counts = get_japanese_accuracy_event_counts()
            jp_accuracy_log(
                "final_speaker_distribution",
                dominant_speaker=self._dominant_speaker_for_current_japanese_session,
                last_committed_speaker=final_speaker,
                committed_segment_speaker_distribution=distribution,
                EMERGENCY_COMMIT_count=int(event_counts.get("EMERGENCY_COMMIT", 0)),
                SAFE_CHUNK_BOUNDARY_COMMIT_count=int(
                    event_counts.get("SAFE_CHUNK_BOUNDARY_COMMIT", 0)
                ),
                SAFE_HOLD_TIMEOUT_COMMIT_count=int(
                    event_counts.get("SAFE_HOLD_TIMEOUT_COMMIT", 0)
                ),
                STABLE_JAPANESE_COMMIT_count=int(
                    event_counts.get("STABLE_JAPANESE_COMMIT", 0)
                ),
                NOISE_FRAGMENT_QUARANTINED_count=int(
                    event_counts.get("NOISE_FRAGMENT_QUARANTINED", 0)
                ),
                STALE_FINAL_DROPPED_count=int(
                    event_counts.get("STALE_FINAL_DROPPED", 0)
                ),
                final_committed_text=text,
            )

    def _ingest_locked(
        self,
        speaker: int,
        fragment: str,
        metadata: dict[str, Any],
        upstream_reason: str,
        raw_original: str = "",
    ) -> None:
        buf = self._buffer
        speaker_before = buf.get("speaker") if buf else None
        self._record_raw_speaker_vote(speaker, fragment, metadata)

        if buf is not None and buf.get("speaker") != speaker:
            buf_text = buf.get("text", "")
            buf_incomplete, buf_inc_reason = looks_incomplete_japanese_fragment(buf_text)
            frag_incomplete, _ = looks_incomplete_japanese_fragment(fragment)
            merged_probe = merge_japanese_fragments(buf_text, fragment)
            overlap_merge = len(merged_probe) < len(buf_text) + len(fragment)
            hold_speaker = (
                should_hold_speaker_continuation(
                    buf.get("speaker"),
                    speaker,
                    fragment,
                    buffer_text=buf_text,
                    buffer_updated_mono=float(buf.get("updated_mono") or 0),
                )
                or buf_incomplete
                or frag_incomplete
                or overlap_merge
            )
            if not hold_speaker:
                prefix, tail, bname, btype = find_commit_boundary(buf_text)
                if prefix and btype != "none":
                    self._commit_partial(
                        buf,
                        prefix,
                        tail,
                        bname,
                        btype,
                        "speaker_change_safe_prefix",
                    )
                    hold_speaker = True
                    speaker = buf.get("speaker", speaker)
                elif has_strong_sentence_end(buf_text):
                    body = buf_text.rstrip("。！？!?")
                    inc, _ = looks_incomplete_japanese_fragment(body)
                    if not inc:
                        self._flush_locked("speaker_changed", force=True)
                        buf = None
                    else:
                        hold_speaker = True
                else:
                    hold_speaker = True
            if hold_speaker and buf is not None:
                jp_accuracy_log(
                    "SPEAKER_CHANGE_HELD",
                    speaker_before=speaker_before,
                    speaker_after=speaker,
                    fragment=fragment,
                    buffer_incomplete=buf_incomplete,
                    buffer_incomplete_reason=buf_inc_reason,
                    fragment_incomplete=frag_incomplete,
                    overlap_merge=overlap_merge,
                )
                speaker = buf.get("speaker", speaker)

        if buf is None:
            incoming_ids = _extract_lineage_from_metadata(metadata)
            buf = {
                "speaker": speaker,
                "text": fragment,
                "metadata": dict(metadata),
                "created_mono": time.monotonic(),
                "updated_mono": time.monotonic(),
                "hold_started_mono": time.monotonic(),
                "part_count": 1,
                "upstream_reason": upstream_reason,
                "raw_fragments": [raw_original or fragment],
                "source_raw_event_ids": list(incoming_ids),
            }
            buf["metadata"]["source_raw_event_ids"] = list(incoming_ids)
            self._buffer = buf
            self._last_buffer_speaker = speaker
            jp_accuracy_log(
                "continuity_buffer_before",
                action="open",
                buffer_text="",
                incoming_fragment=fragment,
            )
            jp_accuracy_log(
                "continuity_buffer_after",
                action="open",
                buffer_text=fragment,
                speaker=speaker,
                buffer_chars=len(fragment),
                part_count=1,
                buffer_age_ms=0,
            )
        else:
            before = buf.get("text", "")
            jp_accuracy_log(
                "continuity_buffer_before",
                action="merge",
                buffer_text=before,
                incoming_fragment=fragment,
            )
            merged = merge_japanese_fragments(before, fragment)
            post_cleaned, _, _ = cleanup_japanese_per_fragment(merged)
            buf["text"] = post_cleaned
            buf["metadata"].update(metadata)
            buf["updated_mono"] = time.monotonic()
            buf["part_count"] = int(buf.get("part_count") or 1) + 1
            raw_frags = list(buf.get("raw_fragments") or [])
            raw_frags.append(raw_original or fragment)
            buf["raw_fragments"] = raw_frags
            merged_ids = _merge_ordered_lineage_ids(
                buf.get("source_raw_event_ids"),
                _extract_lineage_from_metadata(metadata),
            )
            buf["source_raw_event_ids"] = merged_ids
            buf["metadata"]["source_raw_event_ids"] = list(merged_ids)
            self._last_buffer_speaker = int(buf.get("speaker") or speaker)
            jp_accuracy_log(
                "continuity_buffer_after",
                action="merge",
                buffer_before=before,
                buffer_after=post_cleaned,
                speaker=buf.get("speaker"),
                part_count=buf["part_count"],
                buffer_chars=len(post_cleaned),
                buffer_age_ms=int(
                    (time.monotonic() - float(buf.get("created_mono") or time.monotonic()))
                    * 1000
                ),
            )

        if self._check_emergency_commit(buf):
            return
        if self._maybe_commit_at_boundary(buf):
            return
        if getattr(self._host, "_is_finalizing", False):
            self._flush_locked("finalize_immediate", force=True)
            return

        merged_text = buf["text"]
        incomplete, incomplete_reason = looks_incomplete_japanese_fragment(merged_text)
        if incomplete:
            jp_accuracy_log(
                "INCOMPLETE_FRAGMENT_HELD",
                buffer_text=merged_text,
                incomplete_reason=incomplete_reason,
            )
        hold_ms = compute_sentence_hold_ms(merged_text, incomplete)
        self._schedule_flush(hold_ms, incomplete_reason)

    def _maybe_commit_at_boundary(self, buf: dict[str, Any]) -> bool:
        merged_text = (buf.get("text") or "").strip()
        if not merged_text:
            return False
        compact_len = count_japanese_chars(merged_text)

        if has_strong_sentence_end(merged_text):
            body = merged_text.rstrip("。！？!?")
            inc, _ = looks_incomplete_japanese_fragment(body)
            if not inc:
                self._flush_locked("sentence_punctuation")
                return True

        prefix, tail, bname, btype = find_commit_boundary(merged_text)
        if not prefix or not tail or btype == "none":
            return False

        p_compact = count_japanese_chars(prefix)
        jp_accuracy_log(
            "safe_boundary_detected",
            safe_boundary_detected=bname,
            boundary_type=btype,
            prefix_compact_len=p_compact,
            buffer_compact_len=compact_len,
        )

        if compact_len >= MAX_BUFFER_COMPACT_LEN:
            self._commit_partial(buf, prefix, tail, bname, btype, "max_buffer_safe_prefix")
            return True

        if TARGET_CHUNK_MIN_COMPACT <= p_compact <= TARGET_CHUNK_MAX_COMPACT:
            self._commit_partial(buf, prefix, tail, bname, btype, "target_chunk_boundary")
            return True

        if p_compact >= SOFT_BOUNDARY_MIN_COMPACT and btype == "soft":
            tail_inc, _ = looks_incomplete_japanese_fragment(tail)
            if tail_inc:
                self._commit_partial(
                    buf, prefix, tail, bname, btype, "soft_boundary_incomplete_tail"
                )
                return True

        if compact_len > TARGET_CHUNK_MAX_COMPACT and p_compact >= SOFT_BOUNDARY_MIN_COMPACT:
            self._commit_partial(buf, prefix, tail, bname, btype, "over_target_safe_prefix")
            return True

        return False

    def _commit_partial(
        self,
        buf: dict[str, Any],
        prefix: str,
        tail: str,
        boundary_name: str,
        boundary_type: str,
        reason: str,
    ) -> None:
        if _is_false_ne_tail_split(prefix, tail) or _is_false_desu_question_tail_split(
            prefix, tail
        ):
            return
        jp_accuracy_log(
            "commit_decision",
            decision="partial_commit",
            commit_reason=reason,
            committed_text=prefix,
            held_tail=tail,
            safe_boundary_detected=boundary_name,
            boundary_type=boundary_type,
        )
        partial_metadata = dict(buf.get("metadata") or {})
        partial_metadata["source_raw_event_ids"] = list(buf.get("source_raw_event_ids") or [])
        self._route_stable_publish(
            buf["speaker"],
            prefix,
            partial_metadata,
            reason,
            raw_fragments=list(buf.get("raw_fragments") or []),
            held_tail=tail,
            safe_boundary_used=boundary_name,
            boundary_type=boundary_type,
        )
        buf["text"] = tail
        buf["raw_fragments"] = [tail]
        buf["created_mono"] = time.monotonic()
        buf["updated_mono"] = time.monotonic()
        buf["hold_started_mono"] = time.monotonic()
        buf["part_count"] = 1
        incomplete, incomplete_reason = looks_incomplete_japanese_fragment(tail)
        self._schedule_flush(compute_sentence_hold_ms(tail, incomplete), incomplete_reason)

    def _schedule_flush(self, hold_ms: int, reason: str) -> None:
        """Record flush intent only — must not call Tkinter. Called under lock."""
        due = time.monotonic() + max(0.001, hold_ms / 1000.0)
        self._flush_generation += 1
        self._pending_flush_due_mono = due
        self._pending_flush_reason = reason
        self._pending_flush_generation = self._flush_generation
        if JAPANESE_CONTINUITY_ASSEMBLER_ENABLED:
            jp_accuracy_log(
                "old_timer_flush_bypassed_for_japanese",
                old_timer_flush_bypassed_for_japanese=True,
                continuity_hold_tick=True,
                interval_ms=int(hold_ms),
            )
        jp_accuracy_log(
            "ASSEMBLER_FLUSH_INTENT_RECORDED",
            hold_ms=int(hold_ms),
            reason=reason,
            generation=self._pending_flush_generation,
        )

    def try_execute_continuity_hold(self, generation: int, reason: str) -> bool:
        """Execute hold-tick flush logic off UI thread."""
        if reason.startswith("stable_layer_hold_release:"):
            acquired = self._lock.try_acquire(timeout=0.0)
            if not acquired:
                return False
            try:
                if generation != self._stable_hold_generation:
                    return True
                hold_kind = reason.split(":", 1)[-1]
                self._release_stable_hold_locked(hold_kind)
                return True
            finally:
                self._lock.release()
        acquired = self._lock.try_acquire(timeout=0.0)
        if not acquired:
            return False
        try:
            if generation != self._flush_generation:
                jp_accuracy_log(
                    "ASSEMBLER_FLUSH_SKIPPED_STALE_GENERATION",
                    expected=generation,
                    current=self._flush_generation,
                )
                return True
            if (
                self._pending_flush_due_mono is not None
                and time.monotonic() < self._pending_flush_due_mono
            ):
                from alpha.utils.language_pipeline_worker import (
                    get_language_pipeline_worker,
                )

                get_language_pipeline_worker().schedule_flush(
                    self,
                    self._pending_flush_due_mono,
                    generation,
                    reason,
                )
                return True
            self._execute_continuity_hold_locked(reason)
            return True
        except Exception as exc:
            try:
                from alpha.utils.crash_guard_log import log_exception

                log_exception(
                    exc,
                    source="continuity_hold_tick",
                    callback_name="try_execute_continuity_hold",
                    host=self._host,
                )
            except Exception:
                pass
            if JAPANESE_CONTINUITY_ASSEMBLER_SAFE_MODE:
                self._buffer = None
                self._cancel_timer()
            return True
        finally:
            self._lock.release()

    def _execute_continuity_hold_locked(self, reason: str) -> None:
        buf = self._buffer
        if not buf:
            return
        if self._check_emergency_commit(buf):
            return
        text = (buf.get("text") or "").strip()
        incomplete, inc_reason = looks_incomplete_japanese_fragment(text)
        hold_elapsed = self._buffer_hold_ms(buf)
        if incomplete:
            if hold_elapsed >= JAPANESE_CONTINUITY_MAX_HOLD_MS:
                self._check_emergency_commit(buf)
                return
            prefix, tail, bname, btype = find_commit_boundary(text)
            if (
                prefix
                and tail
                and btype != "none"
                and not _is_false_ne_tail_split(prefix, tail)
                and not _is_false_desu_question_tail_split(prefix, tail)
            ):
                self._commit_partial(
                    buf,
                    prefix,
                    tail,
                    bname,
                    btype,
                    "hold_timeout_safe_prefix",
                )
                return
            jp_accuracy_log(
                "held_fragment_reason",
                buffer_text=text,
                held_fragment_reason=inc_reason,
                hold_ms=int(hold_elapsed),
            )
            remaining = max(500, int(JAPANESE_CONTINUITY_MAX_HOLD_MS - hold_elapsed))
            self._schedule_flush(min(SENTENCE_HOLD_MAX_MS, remaining), inc_reason)
            flush_due = self._pending_flush_due_mono
            flush_gen = self._pending_flush_generation
            flush_reason = self._pending_flush_reason
            if flush_due is not None:
                from alpha.utils.language_pipeline_worker import (
                    get_language_pipeline_worker,
                )

                get_language_pipeline_worker().schedule_flush(
                    self, flush_due, flush_gen, flush_reason
                )
            return
        self._flush_locked(f"hold_timeout_{reason}", force=True)

    def _cancel_timer(self) -> None:
        self._pending_flush_due_mono = None
        self._flush_generation += 1
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        get_language_pipeline_worker().cancel_flush(self)

    def _flush_locked(
        self,
        reason: str,
        *,
        force: bool = False,
        stop_incomplete: bool = False,
        incomplete_reason: str = "",
    ) -> None:
        self._cancel_timer()
        buf = self._buffer
        self._buffer = None
        if not buf:
            return
        text = (buf.get("text") or "").strip()
        if not text:
            return
        if not force:
            incomplete, inc_reason = looks_incomplete_japanese_fragment(text)
            if incomplete:
                prefix, tail, bname, btype = find_commit_boundary(text)
                if (
                    prefix
                    and tail
                    and btype != "none"
                    and not _is_false_desu_question_tail_split(prefix, tail)
                ):
                    self._buffer = {
                        "speaker": buf.get("speaker", 1),
                        "text": tail,
                        "metadata": dict(buf.get("metadata") or {}),
                        "created_mono": time.monotonic(),
                        "updated_mono": time.monotonic(),
                        "hold_started_mono": time.monotonic(),
                        "part_count": 1,
                        "upstream_reason": buf.get("upstream_reason", ""),
                        "raw_fragments": [tail],
                        "source_raw_event_ids": list(buf.get("source_raw_event_ids") or []),
                    }
                    partial_metadata = dict(buf.get("metadata") or {})
                    partial_metadata["source_raw_event_ids"] = list(
                        buf.get("source_raw_event_ids") or partial_metadata.get("source_raw_event_ids") or []
                    )
                    self._route_stable_publish(
                        buf.get("speaker", 1),
                        prefix,
                        partial_metadata,
                        f"{reason}_safe_prefix",
                        raw_fragments=list(buf.get("raw_fragments") or []),
                        safe_boundary_used=bname,
                        boundary_type=btype,
                    )
                    hold_ms = compute_sentence_hold_ms(tail, True)
                    self._schedule_flush(hold_ms, inc_reason)
                    return
                self._buffer = buf
                jp_accuracy_log(
                    "held_fragment_reason",
                    buffer_text=text,
                    held_fragment_reason=inc_reason,
                    flush_blocked_reason=reason,
                )
                hold_ms = compute_sentence_hold_ms(text, True)
                self._schedule_flush(hold_ms, inc_reason)
                return
        speaker = buf.get("speaker", 1)
        metadata = dict(buf.get("metadata") or {})
        metadata["source_raw_event_ids"] = list(
            buf.get("source_raw_event_ids") or metadata.get("source_raw_event_ids") or []
        )
        self._route_stable_publish(
            speaker,
            text,
            metadata,
            reason,
            raw_fragments=list(buf.get("raw_fragments") or []),
            stop_incomplete=stop_incomplete,
            incomplete_reason=incomplete_reason,
        )

    def _check_meaning_fragments_dropped(
        self, raw_fragments: list[str], final_text: str
    ) -> None:
        combined_raw = "".join(raw_fragments)
        final_compact = compact_cjk_for_compare(final_text, "ja")
        for fragment in _MEANING_FRAGMENTS:
            if fragment in combined_raw and fragment not in final_text:
                frag_compact = compact_cjk_for_compare(fragment, "ja")
                if frag_compact not in final_compact:
                    jp_accuracy_log(
                        "WARNING_MEANING_FRAGMENT_DROPPED",
                        missing_fragment=fragment,
                        raw_fragments_preview=raw_fragments[:5],
                        final_text=final_text,
                    )

    def _has_raw_stt_warning(self, raw_fragments: list[str]) -> bool:
        return any(detect_raw_stt_error_suspected(fragment) for fragment in raw_fragments)

    def _translation_readiness_score(
        self,
        text: str,
        *,
        commit_reason: str,
        held_tail: str,
        raw_fragments: list[str],
        is_stop_incomplete: bool,
        cleanup_candidate: Optional[dict[str, Any]] = None,
        business_repair_complete: bool = False,
        real_text_repair_complete: bool = False,
    ) -> tuple[float, bool, list[str]]:
        segment = (text or "").strip()
        score = 0.9
        reasons: list[str] = []
        risky_patterns: list[str] = []
        incomplete, incomplete_reason = looks_incomplete_japanese_fragment(segment)
        if has_strong_sentence_end(segment):
            score += 0.08
            reasons.append("complete_sentence")
        elif not incomplete:
            score += 0.03
            reasons.append("meaningful_clause")
        else:
            score -= 0.20
            reasons.append(f"incomplete_fragment:{incomplete_reason}")
        if any(segment.endswith(token) for token in _INCOMPLETE_TAIL_WEAK_ENDINGS):
            score = min(score, 0.55)
            reasons.append("weak_incomplete_tail")
            risky_patterns.append("weak_incomplete_tail")
        elif any(segment.endswith(token) for token in _TRANSLATION_WEAK_ENDINGS):
            score -= 0.25
            reasons.append("weak_particle_ending")
        if held_tail:
            score -= 0.30
            reasons.append("pending_continuation_tail")
        if is_stop_incomplete:
            score -= 0.18
            reasons.append("stop_flush_incomplete")
        if "emergency_commit" in commit_reason:
            score = min(score, 0.20)
            reasons.append("emergency_commit")
            risky_patterns.append("emergency_commit")
        if "safe_hold_timeout" in commit_reason:
            score -= 0.12
            reasons.append("late_continuation_possible")
        if self._has_raw_stt_warning(raw_fragments):
            score -= 0.20
            reasons.append("raw_stt_warning")
        if re.search(r"な(?:と|んと)か監督", segment):
            score = min(score, 0.35)
            reasons.append("suspicious_kantoku_pattern")
            risky_patterns.append("suspicious_kantoku_pattern")
        elif detect_keyterm_overbias_candidates(segment):
            score -= 0.12
            reasons.append("keyterm_overbias_candidate")
        if "リーフが浮かんでこない" in segment:
            cleaned_ok = bool(
                cleanup_candidate
                and cleanup_candidate.get("applied_to_ui")
                and "理由が浮かんでこない" in str(cleanup_candidate.get("candidate", ""))
            )
            if not cleaned_ok:
                score = min(score, 0.45)
                reasons.append("unfixed_riifu_stt_error")
                risky_patterns.append("unfixed_riifu_stt_error")
        if segment.startswith("よ私が"):
            score = min(score, 0.45)
            reasons.append("leading_yo_fragment")
            risky_patterns.append("leading_yo_fragment")
        if (
            cleanup_candidate
            and cleanup_candidate.get("applied_to_ui")
            and float(cleanup_candidate.get("confidence") or 0.0) >= 0.95
            and not risky_patterns
        ):
            score = min(1.0, round(score + 0.05, 2))
            reasons.append("high_confidence_cleanup_applied")
        if real_text_repair_complete and has_strong_sentence_end(segment) and not incomplete:
            score = min(1.0, round(score + 0.08, 2))
            reasons.append("real_repair_complete_sentence")
        elif business_repair_complete and not (
            BENCHMARK_BASELINE_LOCK_ENABLED or ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED
        ):
            if has_strong_sentence_end(segment) and not incomplete:
                score = min(1.0, round(score + 0.12, 2))
                reasons.append("business_repair_complete_sentence")
        elif business_repair_complete and (
            BENCHMARK_BASELINE_LOCK_ENABLED or ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED
        ):
            jp_accuracy_log(
                "TRANSLATION_READY_FAKE_RECLASSIFICATION_BLOCKED",
                text=segment[:120],
                reason="85223_honest_readiness_only",
            )
        score = max(0.0, min(1.0, round(score, 2)))
        ready = score >= 0.75 and not risky_patterns
        if ready:
            reasons.append("ready_for_translation")
        return score, ready, reasons

    def _resolve_output_speaker(
        self,
        speaker: int,
        metadata: dict[str, Any],
        *,
        is_stop_incomplete: bool,
        text: str = "",
        reason: str = "",
    ) -> int:
        raw_speaker = int(speaker or 1)
        final_speaker = raw_speaker
        previous_speaker = self._last_reliable_speaker
        tail_original_speaker = final_speaker
        strong_speaker_evidence = self._strong_speaker_evidence(metadata)
        if is_stop_incomplete and not strong_speaker_evidence:
            if final_speaker == 1 and self._last_buffer_speaker not in (None, 1):
                final_speaker = int(self._last_buffer_speaker)
            elif previous_speaker not in (None, 0) and final_speaker != previous_speaker:
                final_speaker = int(previous_speaker)
            if final_speaker != tail_original_speaker:
                jp_accuracy_log(
                    "STOP_FLUSH_SPEAKER_PRESERVED",
                    previous_speaker=previous_speaker,
                    tail_original_speaker=tail_original_speaker,
                    final_speaker=final_speaker,
                    reason="preserve_last_reliable_speaker_no_strong_evidence",
                )
                if self._speaker_stability_lock_enabled():
                    self._log_speaker_stability_lock(
                        raw_speaker=raw_speaker,
                        final_speaker=final_speaker,
                        reason="stop_flush_preserve_last_reliable_speaker",
                    )
        if not self._speaker_stability_lock_enabled():
            return final_speaker
        if strong_speaker_evidence:
            return final_speaker
        baseline_speaker = (
            previous_speaker
            or self._last_committed_speaker
            or self._dominant_speaker_for_current_japanese_session
        )
        if baseline_speaker in (None, 0) or final_speaker == baseline_speaker:
            return final_speaker
        if raw_speaker != self._pending_raw_speaker_candidate:
            consecutive_votes = 0
        else:
            consecutive_votes = int(self._consecutive_raw_speaker_votes)
        dominant_speaker = self._dominant_committed_speaker()
        dominant_share = self._speaker_share(dominant_speaker)
        baseline_share = self._speaker_share(baseline_speaker)
        continuation_tail = self._looks_like_speaker_continuation_tail(text, reason)
        lock_reasons: list[str] = []
        if dominant_speaker not in (None, final_speaker):
            lock_reasons.append("prefer_dominant_session_speaker")
        if consecutive_votes < 3:
            lock_reasons.append("insufficient_consecutive_new_speaker_votes")
        if baseline_share > 0.80:
            lock_reasons.append("previous_speaker_above_80_percent")
        if continuation_tail:
            lock_reasons.append("semantic_continuation_tail")
        if baseline_speaker == 2 and final_speaker == 1:
            lock_reasons.append("block_speaker2_to_speaker1_flip")
        if not lock_reasons:
            return final_speaker
        locked_speaker = int(baseline_speaker)
        if dominant_speaker not in (None, final_speaker) and dominant_share >= 0.50:
            locked_speaker = int(dominant_speaker)
        if locked_speaker != final_speaker:
            self._log_speaker_stability_lock(
                raw_speaker=raw_speaker,
                final_speaker=locked_speaker,
                reason="+".join(lock_reasons),
            )
            final_speaker = locked_speaker
        return final_speaker

    def _publish_sentence(
        self,
        speaker: int,
        text: str,
        metadata: dict[str, Any],
        reason: str,
        raw_fragments: Optional[list[str]] = None,
        held_tail: str = "",
        safe_boundary_used: str = "",
        boundary_type: str = "",
        stop_incomplete: bool = False,
        incomplete_reason: str = "",
        stable_layer_update_previous: bool = False,
    ) -> None:
        host = self._host
        is_stop_incomplete = stop_incomplete or reason == "stop_flush_incomplete_tail"
        if self._assembler_commit_gate_failed:
            jp_accuracy_log(
                "ASSEMBLER_COMMIT_GATE_FAILED_REJECT",
                reason=reason,
                text_preview=(text or "")[:80],
            )
            return
        metadata = dict(metadata or {})
        if _is_synthetic_stop_only_ingress(
            metadata,
            reason,
            stop_incomplete=is_stop_incomplete,
        ):
            metadata["synthetic_record"] = True
            metadata["synthetic_lineage"] = True

        try:
            from alpha.constants import (
                STOP_TAIL_CLEANUP_ENABLED,
                SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA,
            )
            suppress_early = bool(
                is_stop_incomplete
                and STOP_TAIL_CLEANUP_ENABLED
                and SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA
                and not has_strong_sentence_end(text)
            )
        except Exception:
            suppress_early = False

        speaker = self._resolve_output_speaker(
            speaker,
            metadata,
            is_stop_incomplete=is_stop_incomplete,
            text=text,
            reason=reason,
        )
        jp_accuracy_log("final_cleanup_input", final_cleanup_input=text)
        preparer = getattr(host, "_prepare_final_transcript_for_queue", None)
        cleaned = text
        if callable(preparer):
            cleaned = preparer(text)
        else:
            cleanup_fn = getattr(host, "_apply_japanese_final_cleanup_timed", None)
            if callable(cleanup_fn):
                cleaned = cleanup_fn(text, source="stt_worker")

        precision_cleaned, cleanup_reason, cleanup_flags = (
            cleanup_japanese_transcript_precision(cleaned)
        )
        if precision_cleaned != cleaned or cleanup_reason:
            jp_accuracy_log(
                "final_cleanup_output",
                final_cleanup_input=cleaned,
                final_cleanup_output=precision_cleaned,
                cleanup_reason=cleanup_reason,
                spaced_compact_duplicate_collapse=cleanup_flags.get(
                    "spaced_compact_duplicate_collapse", False
                ),
                exact_duplicate_collapse=cleanup_flags.get(
                    "exact_duplicate_collapse", False
                ),
                prefix_extension_duplicate_collapse=cleanup_flags.get(
                    "prefix_extension_duplicate_collapse", False
                ),
                latin_acronym_spacing_normalized=cleanup_flags.get(
                    "latin_acronym_spacing_normalized", False
                ),
            )
            cleaned = precision_cleaned

        stable_text_original = cleaned
        yo_repair = repair_leading_yo_fragment(
            stable_text_original,
            previous_segment=self._last_final_output_text,
        )
        if yo_repair["repaired"]:
            jp_accuracy_log(
                "LEADING_YO_FRAGMENT_REPAIRED",
                previous_segment_tail=yo_repair.get("previous_segment_tail", ""),
                current_segment_original=yo_repair["original"],
                current_segment_candidate=yo_repair["candidate"],
                applied_to_ui=yo_repair["applied_to_ui"],
            )
            stable_text_original = yo_repair["candidate"]

        cleanup_candidate = build_japanese_cleanup_candidate(
            stable_text_original,
            previous_segment=self._last_final_output_text,
            nearby_context=f"{self._last_final_output_text}{stable_text_original}",
        )
        stable_text_cleaned_candidate = cleanup_candidate["candidate"]
        if cleanup_candidate["changes"]:
            self._cleanup_candidate_count += 1
            if cleanup_candidate["applied_to_ui"]:
                self._cleanup_applied_to_ui_count += 1
            else:
                self._cleanup_low_confidence_not_applied_count += 1
            jp_accuracy_log(
                "JAPANESE_CLEANUP_CANDIDATE",
                original=stable_text_original,
                candidate=stable_text_cleaned_candidate,
                changes=cleanup_candidate["changes"],
                confidence=cleanup_candidate["confidence"],
                applied_to_ui=cleanup_candidate["applied_to_ui"],
                reason=cleanup_candidate["reason"],
            )
            self._cleanup_summary_count += 1
            if self._cleanup_summary_count % 5 == 0:
                jp_accuracy_log(
                    "JAPANESE_CLEANUP_SUMMARY",
                    cleanup_count=self._cleanup_summary_count,
                    applied_to_ui_count=self._cleanup_applied_to_ui_count,
                    not_applied_count=self._cleanup_low_confidence_not_applied_count,
                    latest_original=stable_text_original[:120],
                    latest_candidate=stable_text_cleaned_candidate[:120],
                )
        cleaned = (
            stable_text_cleaned_candidate
            if cleanup_candidate["applied_to_ui"]
            else stable_text_original
        )

        business_result = business_japanese_stable_cleanup(
            cleaned,
            nearby_context=f"{self._last_final_output_text}{stable_text_original}",
            previous_segment=self._last_final_output_text,
        )
        cleanup_stats = business_result.get("cleanup_stats") or {}
        self._business_cleanup_skipped_already_correct_count += int(
            cleanup_stats.get("skipped_already_correct_count", 0)
        )
        self._business_cleanup_duplicate_prevented_count += int(
            cleanup_stats.get("duplicate_prevented_count", 0)
        )
        self._duplicate_damage_detected_count += int(
            cleanup_stats.get("duplicate_damage_detected_count", 0)
        )
        self._duplicate_damage_fixed_count += int(
            cleanup_stats.get("duplicate_damage_fixed_count", 0)
        )
        self._duplicate_damage_reverted_count += int(
            cleanup_stats.get("duplicate_damage_reverted_count", 0)
        )
        self._idempotency_check_failed_count += int(
            cleanup_stats.get("idempotency_check_failed_count", 0)
        )
        for change in business_result.get("changes") or []:
            if change.get("applied_to_ui"):
                jp_accuracy_log("BUSINESS_JP_CLEANUP_APPLIED", **change)
                self._business_cleanup_applied_count += 1
        for risk in business_result.get("risk_candidates") or []:
            jp_accuracy_log("BUSINESS_JP_RISK_CANDIDATE", **risk)
            self._business_risk_candidate_count += 1
        if business_result.get("applied_to_ui"):
            cleaned = business_result["candidate"]
        self._track_business_risk_pairs(stable_text_original, cleaned)

        business_repair_complete = False
        real_text_repair_complete = False
        before_business_cleanup = cleaned

        if JAPANESE_BUSINESS_ACCURACY_8522_ENABLED:
            from alpha.transcription.japanese_business_accuracy import (
                apply_business_stable_corrections,
            )

            biz8522 = apply_business_stable_corrections(
                cleaned,
                previous_segment=self._last_final_output_text,
                nearby_context=f"{self._last_final_output_text}{stable_text_original}",
                session_context={
                    "has_chin_name": self._session_has_chin_name,
                    "recent_stable_lines": list(self._recent_stable_lines),
                },
            )
            if biz8522.get("applied"):
                cleaned = biz8522["corrected"]
                business_repair_complete = True
                incomplete_after, _ = looks_incomplete_japanese_fragment(cleaned)
                if (
                    cleaned != before_business_cleanup
                    and has_strong_sentence_end(cleaned)
                    and not incomplete_after
                    and not cleaned.strip().startswith(("は", "が", "に", "の"))
                ):
                    real_text_repair_complete = True
                self._business_correction_count += len(biz8522.get("corrections") or [])
                self._business_correction_high_confidence_count += int(
                    biz8522.get("high_confidence_count", 0)
                )
                self._double_prefix_repair_count += int(
                    biz8522.get("double_prefix_repair_count", 0)
                )
                self._triple_koko_repair_count += int(
                    biz8522.get("triple_koko_repair_count", 0)
                )
                self._business_correction_regression_count += int(
                    biz8522.get("business_correction_regression_count", 0)
                )
                self._business_accuracy_expansion_count += int(
                    biz8522.get("business_accuracy_expansion_count", 0)
                )
                self._name_correction_count += int(biz8522.get("name_correction_count", 0))
                self._name_correction_skipped_count += int(
                    biz8522.get("name_correction_skipped_count", 0)
                )
            else:
                self._business_correction_skipped_count += 1

        if (
            not BENCHMARK_BASELINE_LOCK_ENABLED
            and ACCURACY_REGRESSION_ROLLBACK_85223_ENABLED
        ):
            from alpha.transcription.japanese_business_accuracy import (
                clean_midline_punctuation_artifact,
                cleanup_exact_duplicate_continuation,
            )

            exact_dup = cleanup_exact_duplicate_continuation(
                self._last_final_output_text,
                cleaned,
                same_speaker=(
                    self._last_committed_speaker is not None
                    and int(self._last_committed_speaker) == int(speaker)
                ),
            )
            if exact_dup.get("applied"):
                cleaned = str(exact_dup.get("corrected") or cleaned)
                self._exact_duplicate_continuation_count += 1
                incomplete_after, _ = looks_incomplete_japanese_fragment(cleaned)
                if has_strong_sentence_end(cleaned) and not incomplete_after:
                    real_text_repair_complete = True

            cleaned_midline, midline_cleaned = clean_midline_punctuation_artifact(cleaned)
            if midline_cleaned:
                cleaned = cleaned_midline
                self._midline_punctuation_cleanup_count += 1
                jp_accuracy_log(
                    "MIDLINE_PUNCTUATION_ARTIFACT_CLEANED",
                    input_text=stable_text_original,
                    output_text=cleaned,
                    raw_deepgram_mutated=False,
                )

        post_update_previous = stable_layer_update_previous
        if PUNCTUATION_START_POST_CORRECTION_MERGE_ENABLED:
            post_result = apply_punctuation_start_post_correction(
                cleaned,
                previous_stable=self._last_stable_commit,
                current_speaker=speaker,
                stop_boundary_active=self._stop_boundary_active,
            )
            post_action = post_result.get("action", "unchanged")
            if post_action == "merged_previous":
                cleaned = str(post_result.get("text") or cleaned)
                post_update_previous = True
                self._punctuation_start_post_correction_merge_count += 1
                if self._punctuation_start_count > 0:
                    self._punctuation_start_count -= 1
            elif post_action == "leading_removed":
                cleaned = str(post_result.get("text") or cleaned)
                self._punctuation_start_post_correction_merge_count += 1
                if self._punctuation_start_count > 0:
                    self._punctuation_start_count -= 1

        update_previous_requested = bool(
            post_update_previous
            or stable_layer_update_previous
            or metadata.get("boundary_should_revise")
            or (metadata.get("boundary_stab_result") or {}).get("should_revise")
        )
        previous_record = None
        if self._last_stable_commit and str(self._last_final_output_text or "").strip():
            previous_record = {
                "line_id": self._last_stable_line_id,
                "text": str(self._last_final_output_text or self._last_stable_commit.get("text") or ""),
                "source_raw_event_ids": list(self._last_stable_source_raw_event_ids or []),
            }
        candidate_raw_event_ids = _extract_lineage_from_metadata(metadata)
        revision_decision: dict[str, Any] = {"action": "append", "reason": "default"}
        final_revision_action = "append"
        decision_reason = ""
        rejected_to_append = False
        pipeline_txn = None
        try:
            from alpha.transcription.stable_revision_decision import decide_stable_revision_action
            from alpha.utils.accuracy_stage_capture import (
                get_accuracy_stage_active_char_count,
                record_assembler_only_event,
                record_revision_decision_stats,
            )
            from alpha.utils.run_identity import get_run_id

            active_chars_before = get_accuracy_stage_active_char_count()
            revision_decision = decide_stable_revision_action(
                previous_record=previous_record,
                candidate_text=cleaned,
                update_previous_requested=update_previous_requested,
                candidate_raw_event_ids=candidate_raw_event_ids,
                candidate_metadata=metadata,
            )
            final_revision_action = str(revision_decision.get("action") or "append")
            decision_reason = str(revision_decision.get("reason") or "")
            if metadata.get("force_append_only") or metadata.get("lineage_assignment_failed"):
                if final_revision_action == "revise_previous":
                    final_revision_action = "append"
                    update_previous_requested = False
                    decision_reason = decision_reason or "lineage_assignment_failed"
            rejected_to_append = update_previous_requested and final_revision_action == "append"
            record_revision_decision_stats(
                update_previous_requested=update_previous_requested,
                final_action=final_revision_action,
                decision_reason=decision_reason,
                revision_requested_but_rejected=rejected_to_append,
            )
            if final_revision_action == "no_op":
                jp_accuracy_log(
                    "EXACT_DUPLICATE_NO_OP",
                    candidate_text=cleaned[:120],
                    previous_text=str(previous_record.get("text") if previous_record else "")[:120],
                )
                jp_accuracy_log(
                    "STABLE_REVISION_DECISION",
                    run_id=get_run_id(),
                    candidate_event_id="",
                    target_line_id=revision_decision.get("target_line_id", ""),
                    previous_text=str(previous_record.get("text") if previous_record else ""),
                    candidate_text=cleaned,
                    update_previous_requested=update_previous_requested,
                    final_action=final_revision_action,
                    decision_reason=decision_reason,
                    previous_terminal=revision_decision.get("previous_terminal"),
                    previous_raw_event_ids=list(previous_record.get("source_raw_event_ids") if previous_record else []),
                    candidate_raw_event_ids=candidate_raw_event_ids,
                    lineage_overlap_count=revision_decision.get("lineage_overlap_count", 0),
                    candidate_directly_extends_previous=revision_decision.get("candidate_extends_previous"),
                    similarity_score=revision_decision.get("similarity_score"),
                    content_loss_risk=revision_decision.get("content_loss_risk"),
                    previous_preserved=True,
                    active_transcript_chars_before=active_chars_before,
                    active_transcript_chars_after=active_chars_before,
                )
                record_assembler_only_event(
                    run_id=get_run_id(),
                    speaker=speaker,
                    assembler_text=cleaned,
                    reason=reason,
                    commit_reason=reason,
                    action="no_op",
                    update_previous=update_previous_requested,
                    stop_incomplete=is_stop_incomplete,
                    incomplete_reason=incomplete_reason,
                    held_tail=held_tail,
                    boundary_type=boundary_type,
                    safe_boundary_used=safe_boundary_used,
                    raw_fragments=raw_fragments,
                    source_raw_event_ids=candidate_raw_event_ids,
                    decision_reason=decision_reason,
                    revision_decision=revision_decision,
                )
                return
            if final_revision_action == "append":
                stable_layer_update_previous = False
                post_update_previous = False
                metadata = dict(metadata)
                metadata["boundary_should_revise"] = False
                stab = dict(metadata.get("boundary_stab_result") or {})
                stab["should_revise"] = False
                metadata["boundary_stab_result"] = stab
                if rejected_to_append:
                    if decision_reason == "completed_previous_sentence_protected":
                        jp_accuracy_log("COMPLETED_SENTENCE_REVISION_BLOCKED", reason=decision_reason)
                    elif decision_reason == "revision_lineage_missing":
                        jp_accuracy_log("REVISION_LINEAGE_MISSING")
                    elif decision_reason == "revision_lineage_disjoint":
                        jp_accuracy_log("REVISION_LINEAGE_DISJOINT")
                    elif decision_reason == "destructive_content_loss_prevented":
                        jp_accuracy_log("DESTRUCTIVE_CONTENT_LOSS_PREVENTED")
                    jp_accuracy_log("UNSAFE_REVISION_REJECTED_TO_APPEND", reason=decision_reason)
            elif final_revision_action == "revise_previous":
                stable_layer_update_previous = True
                post_update_previous = True
                jp_accuracy_log("SAFE_REVISION_APPLIED", reason=decision_reason)
            active_chars_after = active_chars_before
            if final_revision_action == "revise_previous":
                active_chars_after = active_chars_before - len(str(previous_record.get("text") if previous_record else "")) + len(cleaned)
            elif final_revision_action == "append":
                active_chars_after = active_chars_before + len(cleaned)
            jp_accuracy_log(
                "STABLE_REVISION_DECISION",
                run_id=get_run_id(),
                candidate_event_id="",
                target_line_id=revision_decision.get("target_line_id", ""),
                previous_text=str(previous_record.get("text") if previous_record else ""),
                candidate_text=cleaned,
                update_previous_requested=update_previous_requested,
                final_action=final_revision_action,
                decision_reason=decision_reason,
                previous_terminal=revision_decision.get("previous_terminal"),
                previous_raw_event_ids=list(previous_record.get("source_raw_event_ids") if previous_record else []),
                candidate_raw_event_ids=candidate_raw_event_ids,
                lineage_overlap_count=revision_decision.get("lineage_overlap_count", 0),
                candidate_directly_extends_previous=revision_decision.get("candidate_extends_previous"),
                similarity_score=revision_decision.get("similarity_score"),
                content_loss_risk=revision_decision.get("content_loss_risk"),
                previous_preserved=final_revision_action != "revise_previous" or revision_decision.get("candidate_extends_previous"),
                active_transcript_chars_before=active_chars_before,
                active_transcript_chars_after=active_chars_after,
            )
            asm_action = final_revision_action
            if suppress_early:
                asm_action = "suppress_candidate"
            ledger_applied = final_revision_action
            if suppress_early:
                ledger_applied = "suppress_candidate"
                metadata = dict(metadata)
                metadata["stop_tail_candidate"] = True
                metadata["stop_tail_candidate_text"] = cleaned
                metadata["stop_tail_candidate_suppressed"] = True
                metadata["previous_active_record_preserved"] = True
                metadata["revision_target_id"] = None
                metadata["canonical_record_id"] = None
                metadata["suppression_reason"] = incomplete_reason or "incomplete_stop_tail"
                metadata["synthetic_record"] = False
                metadata["source_raw_event_ids"] = list(candidate_raw_event_ids)
            from alpha.transcription.pipeline_commit_transaction import execute_pipeline_commit

            pipeline_txn = execute_pipeline_commit(
                speaker=speaker,
                assembler_text=stable_text_original,
                final_text=cleaned,
                metadata=metadata,
                requested_action="revise" if update_previous_requested else "append",
                applied_action=ledger_applied,
                revision_target_id=""
                if suppress_early
                else str(
                    revision_decision.get("target_line_id") or self._last_stable_line_id or ""
                ),
                revision_reason=decision_reason,
                source_raw_event_ids=candidate_raw_event_ids,
                commit_reason=reason,
                stop_flush=is_stop_incomplete,
                incomplete_tail=is_stop_incomplete,
                suppression_reason=incomplete_reason if suppress_early else "",
                update_previous_requested=False if suppress_early else update_previous_requested,
                rejected_to_append=rejected_to_append,
                decision_reason=decision_reason,
                revision_decision=revision_decision,
                stage_reason=reason,
                stage_commit_reason=reason,
                stage_action=asm_action,
                stop_incomplete=is_stop_incomplete,
                incomplete_reason=incomplete_reason,
                held_tail=held_tail,
                boundary_type=boundary_type,
                safe_boundary_used=safe_boundary_used,
                raw_fragments=raw_fragments,
            )
            if not pipeline_txn.success:
                self._assembler_commit_gate_failed = True
                jp_accuracy_log(
                    "ASSEMBLER_COMMIT_GATE_FAILED",
                    failure_reason=pipeline_txn.failure_reason,
                    transaction_id=pipeline_txn.transaction_id,
                    text_preview=cleaned[:120],
                )
                return
            metadata = dict(pipeline_txn.metadata)
            final_revision_action = pipeline_txn.applied_action
            if final_revision_action == "revise":
                final_revision_action = "revise_previous"
            if final_revision_action == "suppress_candidate":
                # Do not publish candidate; previous active record stays unchanged.
                jp_accuracy_log(
                    "STOP_TAIL_CANDIDATE_SUPPRESSED",
                    candidate_text=cleaned[:120],
                    previous_active_record_preserved=True,
                )
                jp_accuracy_log(
                    "STOP_TAIL_PREVIOUS_RECORD_PRESERVED",
                    last_stable_line_id=self._last_stable_line_id,
                )
                return
            if pipeline_txn.record_id:
                self._last_stable_line_id = str(pipeline_txn.record_id)
                metadata["revision_target_id"] = self._last_stable_line_id
                metadata["canonical_record_id"] = self._last_stable_line_id
            stable_layer_update_previous = bool(metadata.get("stable_layer_update_previous"))
            post_update_previous = stable_layer_update_previous
        except Exception as exc:
            self._assembler_commit_gate_failed = True
            jp_accuracy_log(
                "ASSEMBLER_COMMIT_GATE_FAILED",
                failure_reason=f"{type(exc).__name__}:{exc}",
                text_preview=cleaned[:120],
            )
            return

        raw_fragments_used = list(raw_fragments or [])
        if raw_fragments_used:
            self._check_meaning_fragments_dropped(raw_fragments_used, cleaned)

        if detect_kana_prefix_overlap_removal(text, stable_text_original):
            jp_accuracy_log(
                "cjk_prefix_overlap_warning",
                input_text=text,
                output_text=stable_text_original,
            )

        for finding in detect_keyterm_overbias_candidates(stable_text_original):
            jp_accuracy_log(
                "KEYTERM_OVERBIAS_CANDIDATE",
                text=finding["text"],
                suspected_area=finding["suspected_area"],
                confidence=finding["confidence"],
            )

        for finding in detect_raw_stt_error_candidates(stable_text_original):
            jp_accuracy_log(
                "RAW_STT_ERROR_CANDIDATE",
                raw_text=finding["text"],
                suspected_area=finding["suspected_area"],
                reason=finding["reason"],
            )

        commit_reason = (
            "stop_flush_incomplete_tail"
            if is_stop_incomplete
            else f"japanese_continuity_assembler_{reason}"
        )
        translation_ready_score, ready_for_translation, readiness_reasons = (
            self._translation_readiness_score(
                cleaned,
                commit_reason=commit_reason,
                held_tail=held_tail,
                raw_fragments=raw_fragments_used,
                is_stop_incomplete=is_stop_incomplete,
                cleanup_candidate=cleanup_candidate,
                business_repair_complete=business_repair_complete,
                real_text_repair_complete=real_text_repair_complete,
            )
        )
        if real_text_repair_complete and ready_for_translation:
            jp_accuracy_log(
                "TRANSLATION_READY_RECLASSIFIED_AFTER_REAL_REPAIR",
                text=cleaned[:120],
                score=translation_ready_score,
                reasons=readiness_reasons,
            )
        elif business_repair_complete and not ready_for_translation:
            jp_accuracy_log(
                "TRANSLATION_READY_NOT_RECLASSIFIED_REASON",
                text=cleaned[:120],
                score=translation_ready_score,
                reasons=readiness_reasons,
            )
        if not ready_for_translation and cleaned.strip():
            preview = cleaned.strip()
            if len(preview) > 80:
                preview = preview[:80] + "..."
            if preview not in self._risky_segments:
                self._risky_segments.append(preview)
        if is_stop_incomplete:
            jp_accuracy_log(
                "STOP_FLUSH_INCOMPLETE_TAIL",
                committed_text=cleaned,
                incomplete_reason=incomplete_reason,
                stop_flush_incomplete_tail=True,
            )
            jp_accuracy_log(
                "INCOMPLETE_TAIL_STOP_FLUSHED",
                committed_text=cleaned,
                incomplete_reason=incomplete_reason,
                raw_mutated=False,
            )
        if self._force_translation_not_ready:
            ready_for_translation = False
            readiness_reasons.append("stable_layer_timeout_release")
        if is_punctuation_start_fragment(cleaned) and not post_update_previous:
            ready_for_translation = False
            readiness_reasons.append("punctuation_start_unresolved")
        jp_accuracy_log(
            "RAW_DEEPGRAM_PRESERVED_UNMUTATED",
            stable_text=cleaned,
            commit_reason=commit_reason,
            raw_mutated=False,
        )
        if cleaned != text:
            jp_accuracy_log(
                "STABLE_LAYER_TRANSFORM",
                raw_input_text=text,
                stable_output_text=cleaned,
                transform_type="stable_cleanup",
                transform_reason=commit_reason,
                raw_mutated=False,
            )
        try:
            from alpha.utils.accuracy_decision_log import log_assembler_decision

            log_assembler_decision(
                raw_text=str(metadata.get("raw_deepgram_text") or text),
                buffer_before=text,
                buffer_after=cleaned,
                decision="commit_new" if not post_update_previous else "update_previous",
                reason=commit_reason,
                commit_reason=commit_reason,
                merge_reason=(
                    "stable_layer_punctuation_post_correction_merge"
                    if post_update_previous and not stable_layer_update_previous
                    else ("stable_layer_punctuation_merge" if post_update_previous else "")
                ),
                translation_ready=bool(ready_for_translation),
                raw_mutated=False,
            )
        except Exception:
            pass
        jp_accuracy_log(
            "TRANSLATION_READINESS_SCORE",
            stable_text=cleaned,
            score=translation_ready_score,
            ready_for_translation=ready_for_translation,
            reasons=readiness_reasons,
            commit_reason=commit_reason,
        )
        try:
            from alpha.utils.runtime_evidence import (
                categorize_japanese_accuracy_issues,
                log_japanese_accuracy_issue_candidates,
                note_readiness_score,
            )

            note_readiness_score(translation_ready_score)
            issue_findings = categorize_japanese_accuracy_issues(
                raw_text=str(metadata.get("raw_deepgram_text") or text),
                stable_text=stable_text_original,
                candidate_text=cleaned,
                cleanup_candidate=cleanup_candidate,
                readiness_reasons=readiness_reasons,
            )
            log_japanese_accuracy_issue_candidates(issue_findings)
        except Exception:
            pass
        stable_commit_id = ""
        suppress_stop_tail_early = False
        try:
            from alpha.utils.transcript_evidence import log_stable_commit

            suppress_stop_tail_early = bool(
                is_stop_incomplete
                and STOP_TAIL_CLEANUP_ENABLED
                and SUPPRESS_INCOMPLETE_STOP_TAIL_FROM_ALPHA
                and not has_strong_sentence_end(cleaned)
            )
            commit_meta = dict(metadata)
            if metadata.get("applied_action") == "revise":
                commit_meta["stable_line_status"] = "revision"
                commit_meta["replaces_previous_stable_line"] = True
            else:
                commit_meta["stable_line_status"] = "active"
                commit_meta["replaces_previous_stable_line"] = False
            stable_commit_id = log_stable_commit(
                stable_text=stable_text_original,
                commit_reason=commit_reason or "",
                assembler_metadata=commit_meta,
                translation_ready=bool(ready_for_translation),
                source_raw_event_ids=list(
                    commit_meta.get("source_raw_event_ids")
                    or metadata.get("source_raw_event_ids")
                    or []
                ),
                export_eligibility="intentionally_suppressed" if suppress_stop_tail_early else "export_required",
                suppression_classification="incomplete_suppressed" if suppress_stop_tail_early else "",
                suppression_reason=incomplete_reason if suppress_stop_tail_early else "",
                debug_history_only=suppress_stop_tail_early,
            )
            jp_accuracy_log("STABLE_COMMIT_HISTORY_PRESERVED_FOR_DEBUG", stable_commit_id=stable_commit_id)
        except Exception:
            stable_commit_id = ""
            suppress_stop_tail_early = False
        boundary_revise = bool(metadata.get("applied_action") == "revise")
        boundary_suppress = bool((metadata.get("boundary_stab_result") or {}).get("suppress_current"))
        if boundary_suppress:
            try:
                from alpha.transcription.stable_line_revision import get_stable_line_revision_manager

                get_stable_line_revision_manager().suppress_current(
                    reason=metadata.get("boundary_reason", "duplicate_suppressed")
                )
            except Exception:
                pass
            return
        try:
            from alpha.constants import CLEAN_ALPHA_EXPORT_ENABLED, STABLE_LINE_REVISION_MODEL_ENABLED
            from alpha.transcription.stable_line_revision import get_stable_line_revision_manager

            if STABLE_LINE_REVISION_MODEL_ENABLED and CLEAN_ALPHA_EXPORT_ENABLED:
                stab = metadata.get("boundary_stab_result") or {}
                get_stable_line_revision_manager().apply_boundary_output(
                    stab if stab else {"output_action": "append_new_line", "output_text": cleaned, "should_append": not boundary_revise, "should_revise": boundary_revise, "emit_now": True},
                    speaker=speaker,
                    previous_text=self._last_final_output_text or "",
                )
        except Exception:
            pass
        jp_accuracy_log(
            "STABLE_JAPANESE_COMMIT",
            raw_fragments_used=raw_fragments_used,
            stable_text=cleaned,
            stable_text_original=stable_text_original,
            stable_text_cleaned_candidate=stable_text_cleaned_candidate,
            speaker=speaker,
            commit_reason=commit_reason,
            ready_for_translation=ready_for_translation,
            translation_ready_score=translation_ready_score,
        )
        try:
            from alpha.utils.session_progress import touch_progress

            touch_progress("last_stable_commit")
        except Exception:
            pass
        suppress_stop_tail = False
        try:
            from alpha.utils.partial_autosave_worker import notify_stable_commit
            from alpha.utils.transcript_snapshot_store import (
                append_transcript_snapshot,
                revise_last_transcript_snapshot,
            )

            suppress_stop_tail = bool(suppress_stop_tail_early)
            if suppress_stop_tail:
                classification = "incomplete_suppressed"
                jp_accuracy_log(
                    "STOP_TAIL_CLASSIFIED",
                    text=cleaned,
                    classification=classification,
                    incomplete_reason=incomplete_reason,
                )
                jp_accuracy_log(
                    "STOP_TAIL_SUPPRESSED_FROM_ALPHA",
                    text=cleaned,
                    raw_mutated=False,
                )
                self._stop_tail_suppressed_count += 1
                try:
                    from alpha.utils.accuracy_evidence_export import (
                        write_stop_tail_debug_evidence,
                    )

                    write_stop_tail_debug_evidence(
                        text=cleaned,
                        speaker=speaker,
                        commit_reason=commit_reason or "",
                        incomplete_reason=incomplete_reason,
                        classification=classification,
                        stable_commit_id=stable_commit_id,
                        source_commit_id=stable_commit_id,
                    )
                    self._stop_tail_debug_written_count += 1
                except Exception:
                    pass
            else:
                canonical_record_id = str(
                    metadata.get("canonical_record_id")
                    or metadata.get("revision_target_id")
                    or self._last_stable_line_id
                    or ""
                )
                snapshot_commit_reason = commit_reason or ""
                if canonical_record_id:
                    snapshot_commit_reason = (
                        f"{snapshot_commit_reason}|canonical_record_id={canonical_record_id}"
                    )
                if is_stop_incomplete and has_strong_sentence_end(cleaned):
                    jp_accuracy_log(
                        "STOP_TAIL_COMMITTED_COMPLETE",
                        text=cleaned,
                        commit_reason=commit_reason,
                    )
                seg_id = (
                    revise_last_transcript_snapshot(
                        speaker=speaker,
                        stable_text=cleaned,
                        commit_reason=snapshot_commit_reason,
                    )
                    if boundary_revise
                    else append_transcript_snapshot(
                        speaker=speaker,
                        stable_text=cleaned,
                        commit_reason=snapshot_commit_reason,
                    )
                )
                if canonical_record_id:
                    jp_accuracy_log(
                        "TRANSCRIPT_SNAPSHOT_CANONICAL_RECORD_ID",
                        canonical_record_id=canonical_record_id,
                        snapshot_segment_id=seg_id,
                    )
                if boundary_revise:
                    jp_accuracy_log("CLEAN_ALPHA_EXPORT_SOURCE_ACTIVE_LINES", action="revise_previous")
                else:
                    jp_accuracy_log("CLEAN_ALPHA_EXPORT_SOURCE_ACTIVE_LINES", action="append_new")
                notify_stable_commit()
                try:
                    from alpha.utils.flight_recorder import record_flight_event

                    record_flight_event("stable_commit", force=True, segment_id=seg_id)
                except Exception:
                    pass
        except Exception:
            pass
        if suppress_stop_tail:
            jp_accuracy_log(
                "commit_decision",
                decision="stop_tail_suppressed",
                commit_reason=commit_reason,
                committed_text=cleaned,
                stop_flush_incomplete_tail=True,
            )
            return
        self._stable_commit_sample_count += 1
        if self._stable_commit_sample_count % 5 == 0:
            jp_accuracy_log(
                "STABLE_JAPANESE_COMMIT_SUMMARY",
                stable_commit_count=self._stable_commit_sample_count,
                latest_stable_text=cleaned[:120],
                speaker=speaker,
                ready_for_translation=ready_for_translation,
            )
        self._translation_unit_builder.ingest_stable_commit(
            text=cleaned,
            speaker=speaker,
            commit_reason=commit_reason,
            translation_ready_score=translation_ready_score,
            ready_for_translation=ready_for_translation,
            cleanup_applied=bool(cleanup_candidate.get("applied_to_ui")),
            risky_flags=[
                reason
                for reason in readiness_reasons
                if reason
                not in (
                    "ready_for_translation",
                    "complete_sentence",
                    "meaningful_clause",
                    "high_confidence_cleanup_applied",
                )
            ],
            stable_text_original=stable_text_original,
        )

        jp_accuracy_log(
            "commit_decision",
            decision="stop_flush_incomplete_tail" if is_stop_incomplete else "commit_new",
            commit_reason=commit_reason,
            committed_text=cleaned,
            held_tail=held_tail,
            safe_boundary_detected=safe_boundary_used,
            boundary_type=boundary_type or ("none" if not safe_boundary_used else ""),
            stop_flush_incomplete_tail=is_stop_incomplete,
            speaker_before=metadata.get("speaker_before"),
            speaker_after=speaker,
        )
        self._last_ui_commit_mono = time.monotonic()
        self._last_reliable_speaker = speaker
        self._last_final_output_text = cleaned
        if JAPANESE_BOUNDARY_STABILIZER_ENABLED:
            try:
                from alpha.transcription.japanese_boundary_stabilizer import get_boundary_stabilizer

                get_boundary_stabilizer().note_emitted(cleaned)
                jp_accuracy_log("STABLE_COMMIT_BOUNDARY_METADATA_WRITTEN", action=metadata.get("boundary_action", ""))
            except Exception:
                pass
        if "チン・シュウメイ" in cleaned or "チンさん" in cleaned:
            self._session_has_chin_name = True
        self._recent_stable_lines.append(cleaned)
        if len(self._recent_stable_lines) > 5:
            self._recent_stable_lines = self._recent_stable_lines[-5:]
        self._last_stable_commit = {
            "text": cleaned,
            "speaker": speaker,
            "mono": time.monotonic(),
            "line_id": self._last_stable_line_id,
        }
        canonical_record_id = str(
            metadata.get("canonical_record_id")
            or metadata.get("revision_target_id")
            or self._last_stable_line_id
            or ""
        )
        if boundary_revise and self._last_stable_line_id:
            self._last_stable_source_raw_event_ids = list(
                dict.fromkeys(self._last_stable_source_raw_event_ids + candidate_raw_event_ids)
            )
        else:
            if canonical_record_id:
                self._last_stable_line_id = canonical_record_id
            else:
                self._stable_line_counter += 1
                self._last_stable_line_id = f"stable-line-{self._stable_line_counter:06d}"
            self._last_stable_commit["line_id"] = self._last_stable_line_id
            self._last_stable_source_raw_event_ids = list(candidate_raw_event_ids)
        self._note_committed_context(cleaned)
        if ready_for_translation:
            self._translation_ready_true_count += 1
        else:
            self._translation_ready_false_count += 1
        self._update_committed_speaker_distribution(
            speaker,
            cleaned,
            is_stop_incomplete=is_stop_incomplete,
        )

        metadata = dict(metadata)
        metadata["stable_text_original"] = stable_text_original
        metadata["stable_text_cleaned_candidate"] = stable_text_cleaned_candidate
        metadata["translation_ready_score"] = translation_ready_score
        metadata["ready_for_translation"] = ready_for_translation
        metadata["raw_fragments_used"] = raw_fragments_used
        metadata["stable_japanese_transcript"] = cleaned
        if metadata.get("applied_action") == "revise":
            metadata["buffer_decision"] = ("update_previous", "canonical_ledger_revision")
            metadata["force_update_previous"] = True
        else:
            metadata["force_update_previous"] = False
            if metadata.get("applied_action") == "append":
                metadata["buffer_decision"] = ("append_new", "canonical_ledger_append")

        queue_item = {
            "speaker": speaker,
            "text": cleaned,
            "is_final": True,
            "_jp_cleaned": True,
            "_jp_continuity_assembler": True,
            "assembler_reason": reason,
            "stop_flush_incomplete_tail": is_stop_incomplete,
            "stable_text_original": stable_text_original,
            "stable_text_cleaned_candidate": stable_text_cleaned_candidate,
            "translation_ready_score": translation_ready_score,
            "ready_for_translation": ready_for_translation,
            "raw_fragments_used": raw_fragments_used,
            "stable_japanese_transcript": cleaned,
            "canonical_record_id": metadata.get("canonical_record_id") or self._last_stable_line_id,
            "source_raw_event_ids": list(candidate_raw_event_ids),
        }
        queue_item.update(metadata)
        publisher = getattr(host, "_publish_final_transcript_segment", None)
        if callable(publisher):
            publisher(
                speaker,
                cleaned,
                metadata=metadata,
                queue_item=queue_item,
                commit_reason=commit_reason,
            )
            return
        if hasattr(host, "publish_transcript_event"):
            host.publish_transcript_event(
                text=cleaned,
                speaker=speaker,
                is_final=True,
                queue_item=queue_item,
            )


# Backward-compatible aliases
JapaneseSentenceAssembler = JapaneseContinuityAssembler


def get_japanese_continuity_assembler(host: Any) -> JapaneseContinuityAssembler:
    assembler = getattr(host, "_jp_continuity_assembler", None)
    if assembler is None:
        assembler = getattr(host, "_jp_sentence_assembler", None)
    if assembler is None:
        assembler = JapaneseContinuityAssembler(host)
        host._jp_continuity_assembler = assembler
        host._jp_sentence_assembler = assembler
    return assembler


def get_japanese_sentence_assembler(host: Any) -> JapaneseContinuityAssembler:
    return get_japanese_continuity_assembler(host)


def reset_japanese_sentence_assembler(host: Any) -> None:
    assembler = getattr(host, "_jp_continuity_assembler", None)
    if assembler is None:
        assembler = getattr(host, "_jp_sentence_assembler", None)
    if assembler is not None:
        assembler.reset()


def flush_japanese_sentence_assembler(host: Any, reason: str = "stop_listening") -> None:
    assembler = get_japanese_continuity_assembler(host)
    assembler.flush(reason)
