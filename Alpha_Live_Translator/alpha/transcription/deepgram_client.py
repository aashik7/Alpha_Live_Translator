"""Deepgram Nova-3 WebSocket client, reconnect, and health monitoring."""

import json
from pathlib import Path
import queue
import re
import threading
import time
import traceback
from typing import Any
from urllib.parse import quote

from tkinter import messagebox

from alpha.config import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_JA_ENDPOINTING_MS,
    DEEPGRAM_JA_UTTERANCE_END_MS,
    DEEPGRAM_MODEL,
    DEEPGRAM_SAMPLE_RATE,
    DEEPGRAM_UTTERANCE_END_MS,
    clamp_deepgram_utterance_end_ms,
    DG_KEEPALIVE_INTERVAL_S,
    DG_RECONNECT_BACKOFF_MAX_S,
    HEALTH_MONITOR_INTERVAL_MS,
    LANGUAGE_CONFIG,
)
from alpha.audio.processing import ensure_deepgram_pcm_bytes, pcm_duration_ms
from alpha.constants import (
    DG_GAP_MARKER_MIN_S,
    DG_WS_PING_INTERVAL_S,
    DG_WS_PING_TIMEOUT_S,
    DG_GAP_MARKER_TEMPLATE,
    APP_CODENAME,
    APP_VERSION,
    AUTO_LANGUAGE_ENABLED,
    DEBUG_DIAGNOSTICS,
    DEBUG_TEAMS_DIAGNOSTICS,
    DEEPGRAM_BYTES_PER_SECOND,
    DEEPGRAM_EXPECTED_KBPS,
    DEEPGRAM_KBPS_MAX,
    DEEPGRAM_KBPS_MIN,
    ENGLISH_DIARIZATION_ENABLED,
    LANGUAGE_GATE_ENABLED,
    LANGUAGE_GATE_WARNING_ONLY,
    FORCE_DEEPGRAM_LANGUAGE,
    JAPANESE_MODE_ENABLED,
    JAPANESE_KEYTERMS_ENABLED,
    JAPANESE_KEYTERM_MAX,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_KEYTERM_CLASSES,
    resolve_japanese_keyterms,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_WEAK_PHRASE_HINTS_ENABLED,
    JAPANESE_STT_PROFILE,
    DEEPGRAM_REQUEST_SNAPSHOT_ENABLED,
    LOG_PREVIEW_MAX_CHARS,
    PERFORMANCE_SAFE_LOGGING,
)
from alpha.utils.logging_utils import sanitize_log_data

# V26.5.1: bounded Stop drain budget — long enough for a queued backlog
# (e.g. 20+ chunks) without discarding pending audio to finish early.
GRACEFUL_DRAIN_MAX_S = 25.0
GRACEFUL_FINALIZE_WAIT_S = 4.0
GRACEFUL_CLOSE_WAIT_S = 1.5
GRACEFUL_STOP_DEFAULT_TIMEOUT_S = 12.0
STOP_QUEUE_FLUSH_MAX_S = 5.0
STOP_CAPTURE_OPEN_FLUSH_MAX_S = 2.0
STOP_CAPTURE_DEFERRED_MAX_S = 1.5
STOP_SETTLE_DELAY_S = 0.3
STOP_FINALIZE_WAIT_MAX_S = 5.0
STOP_CLOSE_WAIT_MAX_S = 1.5
STOP_MAX_TIMEOUT_S = 12.0


def _websocket():
    """Lazy websocket import — not required until Start connects to Deepgram."""
    import websocket

    return websocket


def _keepalive_websocket_app_class():
    """`WebSocketApp` whose ping thread cannot crash the app on disconnect.

    Item 44, fourth correction, and this one was self-inflicted. Passing
    `ping_interval` (the keepalive that finally made dropped sockets
    detectable) makes websocket-client start a `_send_ping` thread. In 1.6.0
    that thread's whole body is

        if self.stop_ping.wait(self.ping_interval) or ...
        while not self.stop_ping.wait(self.ping_interval) and ...

    with no guard, while `stop_ping` is `None` until `_start_ping_thread`
    assigns it and is torn down around it. On live run
    `...20260812-150116` that raced during the WiFi drop and raised
    `AttributeError: 'NoneType' object has no attribute 'wait'` in
    `Thread-6 (_send_ping)`. Nothing catches it -- it is a bare thread -- so it
    reached the app's thread excepthook, fired `CRASH_HOOK_TRIGGERED` /
    `UNHANDLED_EXCEPTION_CAPTURED` at 15:03:50, and the run was recorded
    `status: crashed` with a partial output written.

    There was no ping thread before the keepalive, so this failure mode
    arrived with it. The override is deliberately narrow: it swallows the
    AttributeError **only** when `stop_ping` is actually gone, which is
    precisely the teardown race, and re-raises anything else so a genuine bug
    in this thread still surfaces. A ping thread that exits during teardown is
    correct behaviour -- there is nothing left to ping.
    """
    websocket = _websocket()
    base = websocket.WebSocketApp

    class _KeepaliveWebSocketApp(base):
        def _send_ping(self, *args, **kwargs):
            try:
                return super()._send_ping(*args, **kwargs)
            except AttributeError:
                if getattr(self, "stop_ping", None) is not None:
                    raise  # a real AttributeError, not the teardown race
                return None

    return _KeepaliveWebSocketApp


def _diag_text_preview(text: str, max_len: int = 160) -> str:
    limit = LOG_PREVIEW_MAX_CHARS if PERFORMANCE_SAFE_LOGGING else max_len
    return (text or "")[:limit]


DEBUG_SESSION_ID = "46ae0c"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEBUG_LOG_DIR = _PROJECT_ROOT / "debug"


def _ensure_debug_log_dir() -> Path:
    DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return DEBUG_LOG_DIR


DEBUG_LOG_PATH = _ensure_debug_log_dir() / (
    f"debug-{DEBUG_SESSION_ID}-v{APP_VERSION}-{time.strftime('%Y%m%d-%H%M%S')}.log"
)
DEBUG_LOG_FILENAME = str(DEBUG_LOG_PATH)
_SESSION_LOG_DEDUP: tuple[str, str] | None = None


def _write_ndjson_log(
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data=None,
):
    try:
        from alpha.utils.async_debug_log import enqueue_ndjson_log

        enqueue_ndjson_log(
            run_id=run_id,
            hypothesis_id=hypothesis_id,
            location=location,
            message=message,
            data=data or {},
        )
    except Exception:
        pass


def get_debug_log_path() -> Path:
    try:
        from alpha.utils.async_debug_log import get_async_debug_log_path

        return get_async_debug_log_path()
    except Exception:
        return DEBUG_LOG_PATH


def _classify_keyterms(keyterms: list[str]) -> tuple[list[dict[str, str]], dict[str, int]]:
    classified: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for term in keyterms:
        keyterm_class = str(JAPANESE_KEYTERM_CLASSES.get(term, "phrase_hint"))
        classified.append({"term": term, "class": keyterm_class})
        counts[keyterm_class] = int(counts.get(keyterm_class, 0)) + 1
    return classified, counts


def _split_keyterms_for_deepgram(keyterms: list[str]) -> tuple[list[str], list[str], list[dict[str, str]], dict[str, int]]:
    allowed_classes = {"proper_noun", "meeting_term", "domain_term"}
    if bool(JAPANESE_WEAK_PHRASE_HINTS_ENABLED) and not bool(JAPANESE_ACCURACY_MODE):
        allowed_classes.add("weak_phrase_hint")
    sent_terms: list[str] = []
    suppressed_terms: list[str] = []
    classified, counts = _classify_keyterms(keyterms)
    for item in classified:
        term = item["term"]
        keyterm_class = item["class"]
        if keyterm_class in allowed_classes:
            sent_terms.append(term)
        else:
            suppressed_terms.append(term)
    return sent_terms, suppressed_terms, classified, counts


def _resolve_active_japanese_keyterms() -> dict[str, Any]:
    """Load keyterms for active profile and log business profile activation."""
    import os

    try:
        from alpha.utils.multidomain_gate_evidence import (
            ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
            is_domain_agnostic_no_hints_active,
        )

        if is_domain_agnostic_no_hints_active():
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "DOMAIN_AGNOSTIC_NO_HINTS_ACTIVE",
                    profile=ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
                    keyterm_count=0,
                    keyword_count=0,
                    reference_terms_loaded=0,
                    meeting_glossary_loaded=False,
                    business_japanese_profile_active=False,
                    test01_profile_active=False,
                )
            except Exception:
                pass
            return {
                "keyterms": [],
                "sent_keyterms": [],
                "suppressed_keyterms": [],
                "keyterm_classes": [],
                "keyterm_class_counts": {},
                "profile_name": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
                "keyterm_count": 0,
                "accuracy_benchmark_mode": True,
                "applied_keyterm_profile": ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
                "keyterms_sent": [],
                "meeting_glossary_loaded": False,
                "business_japanese_profile_active": False,
                "test01_profile_active": False,
                "reference_terms_loaded": 0,
            }
    except Exception:
        pass

    try:
        from alpha.utils.issue12_stage1_runtime import is_target_85_meeting_context_active
    except Exception:
        def is_target_85_meeting_context_active() -> bool:  # type: ignore
            return False

    # Stage 1 meeting-context must not be neutralized by ALPHA_ACCURACY_BENCHMARK.
    if (
        os.environ.get("ALPHA_ACCURACY_BENCHMARK", "").strip() in ("1", "true", "yes")
        and not is_target_85_meeting_context_active()
    ):
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "ACCURACY_BENCHMARK_MODE_ACTIVE",
                accuracy_benchmark_mode=True,
                applied_keyterm_profile="neutral",
                keyterm_count=0,
            )
        except Exception:
            pass
        return {
            "keyterms": [],
            "sent_keyterms": [],
            "suppressed_keyterms": [],
            "keyterm_classes": [],
            "keyterm_class_counts": {},
            "profile_name": "neutral_benchmark",
            "keyterm_count": 0,
            "accuracy_benchmark_mode": True,
            "applied_keyterm_profile": "neutral",
            "keyterms_sent": [],
        }
    keyterms, profile_name, stale_removed = resolve_japanese_keyterms()
    keyterms = [
        str(term).strip() for term in keyterms if str(term).strip()
    ]
    glossary_added = 0
    try:
        from alpha.constants import CORPORATE_IR_GLOSSARY_ENABLED, GLOSSARY_KEYTERM_BOOST_ENABLED

        if CORPORATE_IR_GLOSSARY_ENABLED and GLOSSARY_KEYTERM_BOOST_ENABLED:
            from alpha.transcription.corporate_ir_glossary import load_corporate_ir_glossary, merge_keyterms_with_glossary

            glossary = load_corporate_ir_glossary()
            if glossary:
                keyterms, glossary_added = merge_keyterms_with_glossary(keyterms, glossary)
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("DEEPGRAM_GLOSSARY_KEYTERMS_APPLIED", added=glossary_added)
                    jp_accuracy_log("DEEPGRAM_GLOSSARY_KEYTERM_COUNT", count=len(keyterms))
                except Exception:
                    pass
            else:
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("DEEPGRAM_GLOSSARY_KEYTERMS_SKIPPED_NO_GLOSSARY")
                except Exception:
                    pass
    except Exception:
        pass
    keyterms = keyterms[: max(JAPANESE_KEYTERM_MAX, 150)]
    sent_keyterms, suppressed_keyterms, keyterm_classes, keyterm_class_counts = (
        _split_keyterms_for_deepgram(keyterms)
    )
    esl_markers = ("ESL", "トワイス", "ジヒョ", "英語のレベル")
    old_esl_present = any(m in sent_keyterms for m in esl_markers)
    keyterms_total = len(keyterms)
    keyterm_count_within_safe_limit = 30 <= keyterms_total <= 60
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "BUSINESS_KEYTERM_PROFILE_ACTIVE",
            profile_name=profile_name,
            keyterm_profile=profile_name,
            keyterms_total=keyterms_total,
            keyterms_sent_to_deepgram=sent_keyterms,
            keyterms_suppressed=suppressed_keyterms,
            stale_profile_terms_removed=stale_removed,
            old_esl_twice_terms_present=old_esl_present,
            keyterm_count_within_safe_limit=keyterm_count_within_safe_limit,
            keyterm_overbias_warning=not keyterm_count_within_safe_limit,
            keyterms_preview=sent_keyterms[:12],
        )
    except Exception:
        pass
    return {
        "keyterms": keyterms,
        "sent_keyterms": sent_keyterms,
        "suppressed_keyterms": suppressed_keyterms,
        "keyterm_classes": keyterm_classes,
        "keyterm_class_counts": keyterm_class_counts,
        "profile_name": profile_name,
        "stale_removed": stale_removed,
        "old_esl_twice_terms_present": old_esl_present,
        "keyterm_count_within_safe_limit": keyterm_count_within_safe_limit,
    }


def _agent_debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data=None):
    # region agent log
    if not DEBUG_DIAGNOSTICS:
        return
    _write_ndjson_log(run_id, hypothesis_id, location, message, data)
    # endregion


def _latency_ndjson_log(location: str, message: str, data=None):
    """Write a [LATENCY] line to console and the NDJSON debug log."""
    if not DEBUG_DIAGNOSTICS:
        return
    print(message)
    _write_ndjson_log(
        run_id=f"latency-v{APP_VERSION}",
        hypothesis_id="LATENCY",
        location=location,
        message=message,
        data=data or {},
    )


def _latency_text_preview(text: str, max_len: int = 120) -> str:
    return (text or "")[:max_len]


def _diag_ndjson_log(location: str, message: str, data=None):
    """Write a [DIAG] line to console and the NDJSON debug log."""
    if not DEBUG_DIAGNOSTICS:
        return
    print(message)
    _write_ndjson_log(
        run_id=f"diag-v{APP_VERSION}",
        hypothesis_id="DIAG",
        location=location,
        message=message,
        data=data or {},
    )


def _session_ndjson_log(location: str, message: str, data=None):
    """Write a [SESSION] line to the NDJSON debug log (console only when not perf-safe)."""
    global _SESSION_LOG_DEDUP
    safe_data = sanitize_log_data(data or {})
    dedup_key = (message, str(safe_data))
    if PERFORMANCE_SAFE_LOGGING and dedup_key == _SESSION_LOG_DEDUP:
        return
    _SESSION_LOG_DEDUP = dedup_key
    if not PERFORMANCE_SAFE_LOGGING:
        print(message)
    normalized = message
    for prefix in ("[SESSION] ", "[DIAG] ", "[JAPANESE] ", "[LATENCY] "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    _write_ndjson_log(
        run_id=f"session-v{APP_VERSION}",
        hypothesis_id="SESSION",
        location=location,
        message=normalized,
        data=safe_data,
    )
    try:
        from alpha.utils.runtime_evidence import mirror_runtime_event

        mirror_runtime_event(normalized, safe_data)
    except Exception:
        pass


def _interim_ndjson_log(location: str, message: str, data=None):
    """Write an [INTERIM] line to console and the NDJSON debug log."""
    if not DEBUG_DIAGNOSTICS:
        return
    print(message)
    _write_ndjson_log(
        run_id=f"interim-v{APP_VERSION}",
        hypothesis_id="INTERIM",
        location=location,
        message=message,
        data=data or {},
    )


def _audio_format_ndjson_log(location: str, message: str, data=None):
    """Write an [AUDIO_FORMAT] line to console and the NDJSON debug log."""
    if not DEBUG_DIAGNOSTICS:
        return
    print(message)
    _write_ndjson_log(
        run_id=f"audio-format-v{APP_VERSION}",
        hypothesis_id="AUDIO_FORMAT",
        location=location,
        message=message,
        data=data or {},
    )


def _speaker_ndjson_log(location: str, message: str, data=None):
    """Write a [SPEAKER] line to console and the NDJSON debug log."""
    if not DEBUG_DIAGNOSTICS:
        return
    print(message)
    _write_ndjson_log(
        run_id=f"audio-format-v{APP_VERSION}",
        hypothesis_id="SPEAKER",
        location=location,
        message=message,
        data=data or {},
    )


def _teams_diag_ndjson_log(location: str, message: str, data=None):
    """Write a [TEAMS_DIAG] line when Teams diagnostics are enabled."""
    if not DEBUG_TEAMS_DIAGNOSTICS:
        return
    _write_ndjson_log(
        run_id=f"teams-v{APP_VERSION}",
        hypothesis_id="TEAMS_DIAG",
        location=location,
        message=message,
        data=data or {},
    )


def _segment_buffer_ndjson_log(location: str, message: str, data=None):
    """Write a [SEGMENT_BUFFER] line when Teams diagnostics are enabled."""
    if not DEBUG_TEAMS_DIAGNOSTICS:
        return
    _write_ndjson_log(
        run_id=f"segment-buffer-v{APP_VERSION}",
        hypothesis_id="SEGMENT_BUFFER",
        location=location,
        message=message,
        data=data or {},
    )


def _segment_repair_ndjson_log(location: str, message: str, data=None):
    """Write a [SEGMENT_REPAIR] line when Teams diagnostics are enabled."""
    if not DEBUG_TEAMS_DIAGNOSTICS:
        return
    _write_ndjson_log(
        run_id=f"segment-repair-v{APP_VERSION}",
        hypothesis_id="SEGMENT_REPAIR",
        location=location,
        message=message,
        data=data or {},
    )


def _language_ndjson_log(location: str, message: str, data=None):
    """Write a [LANGUAGE] line when Teams diagnostics are enabled."""
    if not DEBUG_TEAMS_DIAGNOSTICS:
        return
    _write_ndjson_log(
        run_id=f"language-v{APP_VERSION}",
        hypothesis_id="LANGUAGE",
        location=location,
        message=message,
        data=data or {},
    )


_TEAMS_INCOMPLETE_CONNECTORS = frozenset(
    {
        "and",
        "but",
        "the",
        "in",
        "when",
        "because",
        "there",
        "some",
        "we'll",
        "well",
        "a",
        "an",
        "or",
        "so",
        "if",
        "that",
        "to",
        "for",
        "with",
        "your",
        "my",
        "it",
        "is",
        "are",
        "was",
        "were",
        "of",
        "on",
        "at",
        "as",
        "i",
        "you",
        "we",
        "they",
        "he",
        "she",
        "have",
        "has",
        "had",
        "be",
        "been",
        "do",
        "does",
        "can",
        "could",
        "would",
        "should",
        "will",
        "about",
        "into",
        "from",
        "by",
    }
)


def teams_guess_repetition(text: str):
    """Diagnostic-only repeated phrase guess inside one segment."""
    cleaned = re.sub(r"[^\w\s']", " ", (text or "").lower())
    words = [w for w in cleaned.split() if w]
    if len(words) < 4:
        return None, None

    best_phrase = None
    best_conf = None
    for size in range(6, 1, -1):
        if len(words) < size * 2:
            continue
        for i in range(0, len(words) - size * 2 + 1):
            phrase = words[i : i + size]
            window_end = min(len(words), i + size * 2 + 4)
            for j in range(i + size, window_end - size + 1):
                if words[j : j + size] == phrase:
                    gap = j - (i + size)
                    if gap == 0:
                        conf = "high"
                    elif gap <= 2:
                        conf = "high"
                    else:
                        conf = "medium"
                    candidate = " ".join(phrase)
                    if best_conf is None or {"high": 3, "medium": 2, "low": 1}[conf] > {
                        "high": 3,
                        "medium": 2,
                        "low": 1,
                    }[best_conf]:
                        best_phrase = candidate
                        best_conf = conf
    if best_phrase:
        return best_phrase, best_conf

    for i in range(len(words) - 3):
        if words[i] == words[i + 1] and len(words[i]) > 2:
            return f"{words[i]} {words[i]}", "low"
    return None, None


def teams_guess_incomplete_reason(text: str, speech_final=None):
    """Diagnostic-only incomplete sentence reason for one segment."""
    segment = (text or "").strip()
    if not segment:
        return None
    reasons = []
    if len(segment) < 20 or len(segment.split()) < 4:
        reasons.append("very_short_fragment")
    if segment[-1] not in ".!?。…":
        reasons.append("no_sentence_end_punctuation")
    tail = re.sub(r"[^\w']", "", segment.split()[-1]).lower() if segment.split() else ""
    if tail in _TEAMS_INCOMPLETE_CONNECTORS:
        reasons.append("ends_with_connector")
    if speech_final is False:
        reasons.append("speech_final_false")
    return reasons[0] if reasons else None


def teams_log_quality_signals(
    location: str,
    elapsed_sec,
    speaker_label,
    text: str,
    speech_final=None,
):
    """Log repetition / incomplete diagnostics for candidate or committed text."""
    if not DEBUG_TEAMS_DIAGNOSTICS or not text:
        return
    phrase, confidence = teams_guess_repetition(text)
    if phrase:
        _teams_diag_ndjson_log(
            location=location,
            message="[TEAMS_DIAG] possible repetition",
            data={
                "elapsed_sec": elapsed_sec,
                "speaker_label": speaker_label,
                "text_preview": _diag_text_preview(text, 160),
                "repeated_phrase_guess": phrase,
                "confidence": confidence,
            },
        )
    incomplete_reason = teams_guess_incomplete_reason(text, speech_final=speech_final)
    if incomplete_reason:
        _teams_diag_ndjson_log(
            location=location,
            message="[TEAMS_DIAG] possible incomplete sentence",
            data={
                "elapsed_sec": elapsed_sec,
                "speaker_label": speaker_label,
                "text_preview": _diag_text_preview(text, 160),
                "reason": incomplete_reason,
            },
        )


def teams_commit_decision_from_dup_action_diagnostic_only(
    action: str, previous_text: str, current_text: str
):
    """Map duplicate-protection action to Teams commit decision labels.

    DIAGNOSTIC-ONLY (BUG_FIX_ROADMAP.md Batch 3 item 16): the real commit
    decision is made by decide_transcript_action() and observed via the
    transcript store's before/after segment count. This function's return
    value must only ever feed logging (both call sites currently do
    exactly that -- verified by full control-flow trace). The name is
    suffixed so a future change cannot silently wire it into a live commit
    branch without the rename standing out at every call site.
    """
    from alpha.transcription.duplicate_protection import normalize_for_compare

    current = (current_text or "").strip()
    previous = (previous_text or "").strip()
    if action == "add":
        return "commit_new", "new_segment"
    if action == "update":
        prev_n = normalize_for_compare(previous)
        curr_n = normalize_for_compare(current)
        if prev_n and prev_n in curr_n and curr_n != prev_n:
            return "merge_with_previous", "current_extends_previous"
        return "update_previous", "current_prefix_or_extension"
    if not current:
        return "skip_too_short", "empty_text"
    prev_n = normalize_for_compare(previous)
    curr_n = normalize_for_compare(current)
    if curr_n == prev_n:
        return "skip_duplicate", "normalized_equal"
    if curr_n in prev_n:
        return "skip_contained", "current_contained_in_previous"
    if prev_n.startswith(curr_n):
        return "skip_too_short", "previous_starts_with_current"
    return "skip_duplicate", "duplicate_protection_skip"


class DeepgramClientMixin:
    """Mixin providing Deepgram streaming STT WebSocket lifecycle."""

    def reset_latency_session_state(self):
            """Reset monotonic latency counters for a new listen session."""
            now = time.monotonic()
            self._session_start_monotonic = now
            self._last_audio_send_monotonic = None
            self._last_deepgram_message_monotonic = None
            self._last_final_result_monotonic = None
            self._last_commit_monotonic = None
            self._latency_previous_final_monotonic = None
            self._latency_audio_chunks_sent = 0
            self._latency_bytes_sent_total = 0
            self._latency_final_results_received = 0
            self._latency_interim_results_received = 0
            self._latency_sender_loop_alive = False
            self._latency_last_rate_log_monotonic = now
            self._latency_chunks_at_rate_log = 0
            self._latency_bytes_at_rate_log = 0
            self.reset_audio_format_state()
            self._teams_last_interim_sample_monotonic = None
            self._teams_latest_source_snapshot = {}
            self._teams_last_source_energy_log_monotonic = now

    def reset_audio_format_state(self):
            """Reset audio-format diagnostic counters for a new listen session."""
            now = time.monotonic()
            self._audio_format_first_chunk_logged = False
            self._audio_format_last_rate_log_monotonic = now
            self._audio_format_bytes_at_rate_log = 0
            self._audio_format_config_logged = False

    def _latency_elapsed_sec(self):
            start = getattr(self, "_session_start_monotonic", None)
            if start is None:
                return None
            return round(time.monotonic() - start, 3)

    def _latency_seconds_since(self, monotonic_ts):
            if monotonic_ts is None:
                return None
            return round(time.monotonic() - monotonic_ts, 3)

    def _latency_safe_queue_size(self, queue_obj):
            try:
                if queue_obj is None:
                    return None
                return int(queue_obj.qsize())
            except Exception:
                return None

    def _record_audio_chunk_sent(self, chunk_bytes: int):
            """Track outbound audio send timing and volume (diagnostic only)."""
            now = time.monotonic()
            self._latency_audio_chunks_sent = (
                int(getattr(self, "_latency_audio_chunks_sent", 0)) + 1
            )
            sent = max(0, int(chunk_bytes))
            self._latency_bytes_sent_total = int(
                getattr(self, "_latency_bytes_sent_total", 0)
            ) + sent
            self._last_audio_send_monotonic = now
            try:
                from alpha.constants import RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED
                from alpha.utils.runtime_audio_counters import note_audio_chunk_sent

                if RUNTIME_AUDIO_DELIVERY_COUNTERS_ENABLED:
                    note_audio_chunk_sent(sent)
            except Exception:
                pass
            try:
                from alpha.utils.session_progress import touch_progress

                touch_progress("last_audio_frame_sent_to_deepgram")
            except Exception:
                pass
            self._maybe_log_audio_format_rate_check(now, sent)

    def get_authoritative_send_accounting(self) -> dict[str, int]:
        return {
            "audio_chunks_sent": int(getattr(self, "_latency_audio_chunks_sent", 0) or 0),
            "audio_bytes_sent": int(getattr(self, "_latency_bytes_sent_total", 0) or 0),
        }

    def log_deepgram_language_config(self):
            """Log Deepgram language selection at connection startup."""
            lang_code = getattr(self, "_listen_language", None) or FORCE_DEEPGRAM_LANGUAGE or "en"
            ui_label = None
            if hasattr(self, "source_language"):
                try:
                    ui_label = self.source_language.get()
                except Exception:
                    ui_label = None
            if hasattr(self, "_strip_language_flag") and ui_label:
                try:
                    ui_label = self._strip_language_flag(ui_label)
                except Exception:
                    pass
            keyterm_bundle: dict[str, Any] = {}
            if (
                bool(JAPANESE_MODE_ENABLED)
                and bool(JAPANESE_KEYTERMS_ENABLED)
                and str(lang_code).lower() == "ja"
            ):
                keyterm_bundle = _resolve_active_japanese_keyterms()
            keyterms = keyterm_bundle.get("keyterms", [])
            sent_keyterms = keyterm_bundle.get("sent_keyterms", [])
            suppressed_keyterms = keyterm_bundle.get("suppressed_keyterms", [])
            keyterm_classes = keyterm_bundle.get("keyterm_classes", [])
            keyterm_class_counts = keyterm_bundle.get("keyterm_class_counts", {})
            _language_ndjson_log(
                location="deepgram_client.py:log_deepgram_language_config",
                message="[JAPANESE] deepgram config",
                data={
                    "selected_source_language_ui": ui_label or "Japanese",
                    "deepgram_language_param": str(lang_code),
                    "model": str(DEEPGRAM_MODEL),
                    "encoding": "linear16",
                    "sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                    "channels": 1,
                    "keyterms_enabled": bool(sent_keyterms),
                    "keyterm_count": int(len(keyterms)),
                    "keyterms_sent_to_deepgram": sent_keyterms,
                    "keyterms_suppressed": suppressed_keyterms,
                    "keyterm_classes": keyterm_classes,
                    "keyterm_class_counts": keyterm_class_counts,
                    "japanese_mode_enabled": bool(JAPANESE_MODE_ENABLED),
                    "japanese_accuracy_mode": bool(JAPANESE_ACCURACY_MODE),
                    "auto_language_enabled": bool(AUTO_LANGUAGE_ENABLED),
                    "language_gate_enabled": bool(LANGUAGE_GATE_ENABLED),
                },
            )

    def _log_detected_language(self, data, alt, segment_text, speaker_num):
            """Log detected language metadata from a final Deepgram result."""
            detected = None
            language_confidence = None
            transcript_confidence = None
            if isinstance(data, dict):
                detected = data.get("language")
                transcript_confidence = data.get("confidence")
            if isinstance(alt, dict):
                detected = detected or alt.get("language")
                transcript_confidence = alt.get("confidence") or transcript_confidence
                languages = alt.get("languages")
                if not detected and isinstance(languages, list) and languages:
                    first = languages[0]
                    if isinstance(first, dict):
                        detected = first.get("language") or first.get("lang")
                        language_confidence = first.get("confidence")
                        transcript_confidence = (
                            transcript_confidence or first.get("confidence")
                        )
                    elif isinstance(first, str):
                        detected = first
                if language_confidence is None:
                    language_confidence = (
                        alt.get("language_confidence")
                        or alt.get("detected_language_confidence")
                    )
            if language_confidence is None and isinstance(data, dict):
                language_confidence = (
                    data.get("language_confidence")
                    or data.get("detected_language_confidence")
                )
            _language_ndjson_log(
                location="deepgram_client.py:_log_detected_language",
                message="[LANGUAGE] detected",
                data={
                    "detected_language": detected,
                    "language_confidence": language_confidence,
                    "transcript_confidence": transcript_confidence,
                    "allowed_languages": getattr(self, "_allowed_languages", None),
                    "selected_source_language_ui": getattr(
                        self, "_selected_source_language_ui_label", None
                    ),
                    "text_preview": _diag_text_preview(segment_text, 160),
                    "speaker_label": speaker_num,
                },
            )

    def log_deepgram_stream_config(self):
            """Log declared Deepgram PCM stream parameters once per session."""
            if getattr(self, "_audio_format_config_logged", False):
                return
            self._audio_format_config_logged = True
            _audio_format_ndjson_log(
                location="deepgram_client.py:log_deepgram_stream_config",
                message="[AUDIO_FORMAT] deepgram stream config",
                data={
                    "declared_sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                    "declared_channels": 1,
                    "declared_encoding": "linear16",
                    "target_bytes_per_second": int(DEEPGRAM_BYTES_PER_SECOND),
                    "expected_kbps": int(DEEPGRAM_EXPECTED_KBPS),
                },
            )

    def _normalize_and_send_pcm(self, ws, raw_chunk, *, input_type="timeline_mixer"):
            """Normalize at Deepgram boundary: mono int16 LE 16 kHz PCM bytes."""
            pcm_bytes, sample_count = ensure_deepgram_pcm_bytes(raw_chunk)
            if not pcm_bytes:
                # fixes BUG_FIX_ROADMAP.md Batch 2 item 7: logging only --
                # this was a silent no-op drop of one audio chunk with no
                # trace anywhere. jp_accuracy_log throttles repeats of the
                # same event internally, so this is safe even if normal
                # audio processing hits it often.
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "PCM_NORMALIZE_EMPTY_OUTPUT",
                        input_type=input_type,
                        input_bytes=len(raw_chunk) if raw_chunk else 0,
                    )
                except Exception:
                    pass
                return 0
            if not getattr(self, "_audio_format_first_chunk_logged", False):
                self._audio_format_first_chunk_logged = True
                duration_ms = pcm_duration_ms(sample_count, DEEPGRAM_SAMPLE_RATE)
                kbps_est = (
                    round((len(pcm_bytes) * 8 / 1000.0) / (duration_ms / 1000.0), 2)
                    if duration_ms > 0
                    else 0.0
                )
                _audio_format_ndjson_log(
                    location="deepgram_client.py:_normalize_and_send_pcm",
                    message="[AUDIO_FORMAT] first converted chunk",
                    data={
                        "input_type": input_type,
                        "input_sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                        "input_channels": 1,
                        "input_bytes": len(raw_chunk) if raw_chunk else 0,
                        "output_sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                        "output_channels": 1,
                        "output_bytes": len(pcm_bytes),
                        "output_duration_ms": duration_ms,
                        "output_dtype": "int16",
                        "output_kbps_estimate": kbps_est,
                    },
                )
            delivery_chunk_id = None
            try:
                from alpha.utils.multidomain_gate_evidence import (
                    is_multidomain_benchmark_mode,
                    take_pending_delivery_id,
                )

                if is_multidomain_benchmark_mode():
                    delivery_chunk_id = take_pending_delivery_id()
            except Exception:
                delivery_chunk_id = None
            try:
                ws.send(pcm_bytes, opcode=_websocket().ABNF.OPCODE_BINARY)
            except Exception as exc:
                try:
                    from alpha.utils.runtime_audio_counters import note_deepgram_send_error

                    note_deepgram_send_error()
                except Exception:
                    pass
                try:
                    from alpha.utils.multidomain_gate_evidence import (
                        note_normalized_chunk_send_failed,
                    )

                    note_normalized_chunk_send_failed(
                        delivery_chunk_id,
                        error_class=type(exc).__name__,
                        error_message_sanitized=str(exc)[:200],
                    )
                except Exception:
                    pass
                raise
            try:
                from alpha.utils.multidomain_gate_evidence import note_normalized_chunk_sent

                note_normalized_chunk_sent(
                    delivery_chunk_id,
                    frame_count=int(sample_count),
                    byte_count=len(pcm_bytes),
                    sample_rate=int(DEEPGRAM_SAMPLE_RATE),
                    channels=1,
                    send_result="success",
                )
            except Exception:
                pass
            return len(pcm_bytes)

    def _maybe_log_audio_format_rate_check(self, now, last_chunk_bytes=0):
            last_log = getattr(self, "_audio_format_last_rate_log_monotonic", None)
            if last_log is None:
                self._audio_format_last_rate_log_monotonic = now
                self._audio_format_bytes_at_rate_log = int(
                    getattr(self, "_latency_bytes_sent_total", 0)
                )
                return
            duration = now - last_log
            if duration < 10.0:
                return
            bytes_sent = int(getattr(self, "_latency_bytes_sent_total", 0))
            bytes_at = int(getattr(self, "_audio_format_bytes_at_rate_log", 0))
            bytes_delta = max(0, bytes_sent - bytes_at)
            kbps = (
                round((bytes_delta * 8 / 1000.0) / duration, 2) if duration > 0 else 0.0
            )
            status = (
                "ok"
                if DEEPGRAM_KBPS_MIN <= kbps <= DEEPGRAM_KBPS_MAX
                else "mismatch"
            )
            _audio_format_ndjson_log(
                location="deepgram_client.py:_maybe_log_audio_format_rate_check",
                message="[AUDIO_FORMAT] stream rate check",
                data={
                    "elapsed_sec": self._latency_elapsed_sec(),
                    "bytes_sent_last_10_sec": bytes_delta,
                    "kbps_last_10_sec": kbps,
                    "expected_kbps": int(DEEPGRAM_EXPECTED_KBPS),
                    "status": status,
                },
            )
            if status == "mismatch":
                reason = "unknown"
                if kbps > DEEPGRAM_KBPS_MAX:
                    if kbps > DEEPGRAM_EXPECTED_KBPS * 1.5:
                        reason = "possible_stereo_or_concatenated_streams"
                    else:
                        reason = "send_rate_above_real_time"
                elif kbps < DEEPGRAM_KBPS_MIN:
                    reason = "send_rate_below_real_time"
                _audio_format_ndjson_log(
                    location="deepgram_client.py:_maybe_log_audio_format_rate_check",
                    message="[AUDIO_FORMAT] rate mismatch warning",
                    data={
                        "kbps_last_10_sec": kbps,
                        "expected_kbps": int(DEEPGRAM_EXPECTED_KBPS),
                        "possible_reason": reason,
                    },
                )
            self._audio_format_last_rate_log_monotonic = now
            self._audio_format_bytes_at_rate_log = bytes_sent

    def record_latency_commit(self):
            """Record UI/TranscriptStore commit timing (callable from UI thread)."""
            self._last_commit_monotonic = time.monotonic()

    def _build_latency_snapshot(self, **extra):
            outgoing_qsize = None
            if hasattr(self, "get_outgoing_audio_queue_size"):
                try:
                    outgoing_qsize = int(self.get_outgoing_audio_queue_size())
                except Exception:
                    outgoing_qsize = None
            data = {
                "elapsed_sec": self._latency_elapsed_sec(),
                "audio_q": self._latency_safe_queue_size(
                    getattr(self, "_audio_q", None)
                ),
                "sys_q": self._latency_safe_queue_size(
                    getattr(self, "sys_audio_queue", None)
                ),
                "mic_q": self._latency_safe_queue_size(
                    getattr(self, "mic_audio_queue", None)
                ),
                "outgoing_queue_size": outgoing_qsize,
                "audio_chunks_sent": int(
                    getattr(self, "_latency_audio_chunks_sent", 0)
                ),
                "bytes_sent_total": int(
                    getattr(self, "_latency_bytes_sent_total", 0)
                ),
                "final_results_received": int(
                    getattr(self, "_latency_final_results_received", 0)
                ),
                "interim_results_received": int(
                    getattr(self, "_latency_interim_results_received", 0)
                ),
                "seconds_since_last_audio_send": self._latency_seconds_since(
                    getattr(self, "_last_audio_send_monotonic", None)
                ),
                "seconds_since_last_final_result": self._latency_seconds_since(
                    getattr(self, "_last_final_result_monotonic", None)
                ),
            }
            data.update(extra)
            return data

    def log_latency_stop_clicked_snapshot(self):
            _latency_ndjson_log(
                location="deepgram_client.py:log_latency_stop_clicked_snapshot",
                message="[LATENCY] stop clicked snapshot",
                data=self._build_latency_snapshot(),
            )

    def log_latency_stop_completed_snapshot(
            self, finalized=False, closed=False, timed_out=False
    ):
            _latency_ndjson_log(
                location="deepgram_client.py:log_latency_stop_completed_snapshot",
                message="[LATENCY] stop completed snapshot",
                data=self._build_latency_snapshot(
                    finalized=bool(finalized),
                    closed=bool(closed),
                    timed_out=bool(timed_out),
                ),
            )

    def log_latency_transcript_committed(
            self, text, is_finalizing=False, store_segment_count=None
    ):
            preview = _latency_text_preview(text)
            data = {
                "elapsed_sec": self._latency_elapsed_sec(),
                "text_len": len(text) if text else 0,
                "text_preview": preview,
                "is_finalizing": bool(is_finalizing),
            }
            if store_segment_count is not None:
                data["store_segment_count"] = int(store_segment_count)
            _latency_ndjson_log(
                location="main_window.py:log_latency_transcript_committed",
                message="[LATENCY] transcript committed",
                data=data,
            )

    def _log_latency_pipeline_health(self):
            _latency_ndjson_log(
                location="deepgram_client.py:_log_latency_pipeline_health",
                message="[LATENCY] pipeline health",
                data={
                    "elapsed_sec": self._latency_elapsed_sec(),
                    "audio_q": self._latency_safe_queue_size(
                        getattr(self, "_audio_q", None)
                    ),
                    "sys_q": self._latency_safe_queue_size(
                        getattr(self, "sys_audio_queue", None)
                    ),
                    "mic_q": self._latency_safe_queue_size(
                        getattr(self, "mic_audio_queue", None)
                    ),
                    "outgoing_queue_size": (
                        int(self.get_outgoing_audio_queue_size())
                        if hasattr(self, "get_outgoing_audio_queue_size")
                        else None
                    ),
                    "audio_chunks_sent": int(
                        getattr(self, "_latency_audio_chunks_sent", 0)
                    ),
                    "bytes_sent_total": int(
                        getattr(self, "_latency_bytes_sent_total", 0)
                    ),
                    "final_results_received": int(
                        getattr(self, "_latency_final_results_received", 0)
                    ),
                    "interim_results_received": int(
                        getattr(self, "_latency_interim_results_received", 0)
                    ),
                    "seconds_since_last_audio_send": self._latency_seconds_since(
                        getattr(self, "_last_audio_send_monotonic", None)
                    ),
                    "seconds_since_last_deepgram_message": self._latency_seconds_since(
                        getattr(self, "_last_deepgram_message_monotonic", None)
                    ),
                    "seconds_since_last_final_result": self._latency_seconds_since(
                        getattr(self, "_last_final_result_monotonic", None)
                    ),
                },
            )

    def _maybe_log_latency_audio_send_rate(self):
            now = time.monotonic()
            last = float(getattr(self, "_latency_last_rate_log_monotonic", now))
            duration = now - last
            if duration < 10.0:
                return
            chunks_sent = int(getattr(self, "_latency_audio_chunks_sent", 0))
            bytes_sent = int(getattr(self, "_latency_bytes_sent_total", 0))
            chunks_at = int(getattr(self, "_latency_chunks_at_rate_log", 0))
            bytes_at = int(getattr(self, "_latency_bytes_at_rate_log", 0))
            chunks_delta = max(0, chunks_sent - chunks_at)
            bytes_delta = max(0, bytes_sent - bytes_at)
            avg_chunk_bytes = (
                round(bytes_delta / chunks_delta, 1) if chunks_delta else 0.0
            )
            approx_kbps = (
                round((bytes_delta * 8 / 1000.0) / duration, 2)
                if duration > 0
                else 0.0
            )
            _latency_ndjson_log(
                location="deepgram_client.py:_maybe_log_latency_audio_send_rate",
                message="[LATENCY] audio send rate",
                data={
                    "elapsed_sec": self._latency_elapsed_sec(),
                    "chunks_sent_last_10_sec": chunks_delta,
                    "bytes_sent_last_10_sec": bytes_delta,
                    "approx_kbps_last_10_sec": approx_kbps,
                    "avg_chunk_bytes": avg_chunk_bytes,
                    "sender_loop_alive": bool(
                        getattr(self, "_latency_sender_loop_alive", False)
                    ),
                    "ws_open": bool(getattr(self, "_dg_ws", None) is not None),
                },
            )
            self._latency_last_rate_log_monotonic = now
            self._latency_chunks_at_rate_log = chunks_sent
            self._latency_bytes_at_rate_log = bytes_sent

    def _resolve_deepgram_stream_options(self, lang_code: str) -> dict:
            """Resolve endpointing/utterance timing for the active language."""
            code = str(lang_code or "").lower()
            if code == "ja" or code.startswith("ja-"):
                raw_utterance_end_ms = int(DEEPGRAM_JA_UTTERANCE_END_MS)
                endpointing_ms = int(DEEPGRAM_JA_ENDPOINTING_MS)
            else:
                raw_utterance_end_ms = int(DEEPGRAM_UTTERANCE_END_MS)
                endpointing_ms = int(DEEPGRAM_ENDPOINTING_MS)
            utterance_end_ms, was_clamped = clamp_deepgram_utterance_end_ms(
                raw_utterance_end_ms
            )
            if was_clamped:
                print(
                    f"[Deepgram] utterance_end_ms out of supported range "
                    f"({raw_utterance_end_ms}); using {utterance_end_ms}"
                )
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "deepgram_utterance_end_clamped",
                        requested_ms=raw_utterance_end_ms,
                        applied_ms=utterance_end_ms,
                    )
                except Exception:
                    pass
            return {
                "endpointing_ms": endpointing_ms,
                "utterance_end_ms": utterance_end_ms,
            }

    def _resolve_japanese_stt_profile(self, lang_code: str) -> dict:
            """Resolve Japanese STT diagnostic profile (query params only)."""
            code = str(lang_code or "").lower()
            profile = str(JAPANESE_STT_PROFILE or "current").strip().lower()
            use_diarize = True
            sample_rate = int(DEEPGRAM_SAMPLE_RATE)
            effective_profile = profile
            fallback_reason = ""

            if not (bool(JAPANESE_MODE_ENABLED) and (code == "ja" or code.startswith("ja-"))):
                effective_profile = "current"
            elif profile == "no_diarize":
                use_diarize = False
            elif profile == "high_sample_rate_if_supported":
                # Pipeline resamples to 16 kHz before Deepgram — cannot safely declare 48 kHz
                effective_profile = "current"
                fallback_reason = "pipeline_16k_only"
            elif profile not in ("current", "no_diarize", "high_sample_rate_if_supported"):
                effective_profile = "current"
                fallback_reason = f"unknown_profile_{profile}"

            return {
                "profile": effective_profile,
                "requested_profile": profile,
                "use_diarize": use_diarize,
                "sample_rate": sample_rate,
                "fallback_reason": fallback_reason,
            }

    def _build_deepgram_url(self):
            """Build the Deepgram live-listen WebSocket URL with Nova-3 accuracy options."""
            # Finalized run language is authoritative; FORCE must not override it.
            lang = getattr(self, "_listen_language", None) or FORCE_DEEPGRAM_LANGUAGE
            if not lang:
                raise ValueError(
                    "Deepgram language not finalized before connect "
                    "(no _listen_language / FORCE_DEEPGRAM_LANGUAGE)"
                )
            lang = str(lang)
            stream_opts = self._resolve_deepgram_stream_options(lang)
            endpointing_ms = stream_opts["endpointing_ms"]
            utterance_end_ms = stream_opts["utterance_end_ms"]
            stt_profile = self._resolve_japanese_stt_profile(lang)
            profile_name = stt_profile["profile"]
            use_diarize = stt_profile["use_diarize"]
            # English-only: never send diarize / diarize_model when disabled.
            # Japanese path remains governed solely by JAPANESE_STT_PROFILE.
            if str(lang).lower().startswith("en") and not bool(ENGLISH_DIARIZATION_ENABLED):
                use_diarize = False
            wire_sample_rate = stt_profile["sample_rate"]
            keyterm_params = ""
            runtime_keyterm_fallback = bool(getattr(self, "_jp_keyterms_fallback_used", False))
            keyterms: list[str] = []
            keyterms_applied = False
            sent_keyterms: list[str] = []
            suppressed_keyterms: list[str] = []
            keyterm_classes: list[dict[str, str]] = []
            keyterm_class_counts: dict[str, int] = {}
            keyterm_profile_name = JAPANESE_KEYTERM_PROFILE
            if (
                bool(JAPANESE_MODE_ENABLED)
                and bool(JAPANESE_KEYTERMS_ENABLED)
                and str(lang).lower() == "ja"
                and not runtime_keyterm_fallback
            ):
                bundle = _resolve_active_japanese_keyterms()
                keyterms = bundle["keyterms"]
                sent_keyterms = bundle["sent_keyterms"]
                suppressed_keyterms = bundle["suppressed_keyterms"]
                keyterm_classes = bundle["keyterm_classes"]
                keyterm_class_counts = bundle["keyterm_class_counts"]
                keyterm_profile_name = bundle["profile_name"]
                keyterms_applied = bool(sent_keyterms)
                for term in sent_keyterms:
                    keyterm_params += f"&keyterm={quote(term)}"
            else:
                keyterm_classes, keyterm_class_counts = _classify_keyterms(keyterms)
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                query_keys = [
                    "model",
                    "language",
                    "punctuate",
                    "smart_format",
                    "numerals",
                    "profanity_filter",
                    "redact",
                    "endpointing",
                    "utterance_end_ms",
                    "encoding",
                    "sample_rate",
                    "channels",
                    "interim_results",
                ]
                if use_diarize:
                    query_keys.append("diarize_model")
                if keyterms_applied:
                    query_keys.append("keyterm")
                jp_accuracy_log(
                    "KEYTERM_CLASSIFICATION_SUMMARY",
                    keyterms_total=len(keyterms),
                    keyterms_sent_to_deepgram=sent_keyterms,
                    keyterms_suppressed=suppressed_keyterms,
                    suppressed_terms=suppressed_keyterms,
                    reason=(
                        "accuracy_mode_suppressed_weak_and_test_hints"
                        if JAPANESE_ACCURACY_MODE
                        else "default_keyterm_class_filter"
                    ),
                )
                if stt_profile.get("fallback_reason"):
                    jp_accuracy_log(
                        "stt_profile_fallback",
                        JAPANESE_STT_PROFILE=stt_profile.get("requested_profile"),
                        effective_profile=profile_name,
                        fallback_reason=stt_profile.get("fallback_reason"),
                    )
                jp_accuracy_log(
                    "deepgram_options_snapshot",
                    JAPANESE_STT_PROFILE=profile_name,
                    stt_profile_active=profile_name,
                    diarize_model_present=use_diarize,
                    diarize_model_absent=not use_diarize,
                    keyterm_applied=keyterms_applied,
                    keyterm_profile=keyterm_profile_name,
                    keyterms_applied_to_deepgram_request=keyterms_applied,
                    keyterm_count=len(keyterms),
                    keyterms_preview=sent_keyterms[:9],
                    keyterm_list=keyterms,
                    keyterms_sent_to_deepgram=sent_keyterms,
                    keyterms_suppressed=suppressed_keyterms,
                    keyterm_classes=keyterm_classes,
                    keyterm_class_counts=keyterm_class_counts,
                    model=str(DEEPGRAM_MODEL),
                    language=str(lang),
                    punctuate=True,
                    smart_format=True,
                    interim_results=True,
                    endpointing=endpointing_ms,
                    endpointing_ms=endpointing_ms,
                    utterance_end_ms=utterance_end_ms,
                    vad_events=False,
                    query_keys=query_keys,
                    sample_rate=wire_sample_rate,
                    channels=1,
                    diarize_model="latest" if use_diarize else None,
                    diarize_enabled=use_diarize,
                    japanese_accuracy_mode=bool(JAPANESE_ACCURACY_MODE),
                )
            except Exception:
                pass
            _language_ndjson_log(
                location="deepgram_client.py:_build_deepgram_url",
                message="[JAPANESE] keyterms enabled",
                data={
                    "keyterm_count": int(len(keyterms)),
                    "keyterms_preview": sent_keyterms[:5],
                    "keyterm_class_counts": keyterm_class_counts,
                    "suppressed_terms": suppressed_keyterms[:6],
                    "fallback_used": runtime_keyterm_fallback,
                },
            )
            if str(lang).lower() == "ja":
                _language_ndjson_log(
                    location="deepgram_client.py:_build_deepgram_url",
                    message="[CJK] keyterms hint only",
                    data={
                        "keyterm_count": int(len(sent_keyterms)),
                        "suppressed_terms": suppressed_keyterms[:6],
                        "post_processing_replacement_enabled": False,
                    },
                )
            diarize_param = "&diarize_model=latest" if use_diarize else ""
            params = (
                f"model={DEEPGRAM_MODEL}"
                f"&language={lang}"
                f"&punctuate=true"
                f"&smart_format=true"
                f"{diarize_param}"
                f"&numerals=true"
                f"&profanity_filter=false"
                f"&redact=false"
                f"&endpointing={endpointing_ms}"
                f"&utterance_end_ms={utterance_end_ms}"
                f"&encoding=linear16"
                f"&sample_rate={wire_sample_rate}"
                f"&channels=1"
                # utterance_end_ms requires interim_results=true on Deepgram's API;
                # interim payloads are still ignored in _deepgram_on_message.
                f"&interim_results=true"
                f"{keyterm_params}"
            )
            try:
                from alpha.utils.accuracy_stage_capture import (
                    write_deepgram_request_snapshot,
                )
                from alpha.utils.issue12_stage1_runtime import (
                    get_active_japanese_accuracy_profile,
                )
                from alpha.utils.run_identity import get_run_id
                from urllib.parse import parse_qs

                if DEEPGRAM_REQUEST_SNAPSHOT_ENABLED:
                    parsed_q = parse_qs(params, keep_blank_values=True)
                    sanitized_params = {k: (v[0] if len(v) == 1 else v) for k, v in parsed_q.items()}
                    write_deepgram_request_snapshot(
                        {
                            "run_id": get_run_id(),
                            "model": str(DEEPGRAM_MODEL),
                            "language": str(lang),
                            "punctuate": True,
                            "smart_format": True,
                            "numerals": True,
                            "profanity_filter": False,
                            "redact": False,
                            "endpointing": endpointing_ms,
                            "utterance_end_ms": utterance_end_ms,
                            "encoding": "linear16",
                            "sample_rate": wire_sample_rate,
                            "channels": 1,
                            "interim_results": True,
                            "vad_events": False,
                            "JAPANESE_STT_PROFILE": profile_name,
                            "JAPANESE_KEYTERM_PROFILE": keyterm_profile_name,
                            "JAPANESE_ACCURACY_PROFILE": get_active_japanese_accuracy_profile(),
                            "diarize_enabled": use_diarize,
                            "diarize_model_present": use_diarize,
                            "diarize_model": "latest" if use_diarize else None,
                            "keyterm_applied": keyterms_applied,
                            "keyterm_count": len(sent_keyterms),
                            "keyterms_sent_to_deepgram": sent_keyterms,
                            "keyterms_suppressed": suppressed_keyterms,
                            "query_keys": query_keys,
                            "sanitized_query_parameters": sanitized_params,
                        }
                    )
                    # Cache last-built query so _deepgram_worker can prove actual request
                    # immediately before connect (canonical Stage 1 evidence).
                    self._last_deepgram_listen_params = params
                    self._last_deepgram_sent_keyterms = list(sent_keyterms)
                    self._last_deepgram_keyterm_profile = keyterm_profile_name
                    self._last_deepgram_diarize = bool(use_diarize)
                    self._last_deepgram_endpointing_ms = int(endpointing_ms)
                    self._last_deepgram_utterance_end_ms = int(utterance_end_ms)
                    self._last_deepgram_sample_rate = int(wire_sample_rate)
                    self._last_deepgram_language = str(lang)
            except Exception:
                pass
            # English-only request conflict guard (Japanese query path unchanged).
            if str(lang).lower().startswith("en"):
                try:
                    from alpha.utils.english_deepgram_request import (
                        validate_english_query_string,
                    )

                    validate_english_query_string(params)
                except ValueError as exc:
                    raise ValueError(
                        f"English Deepgram request validation failed: {exc}"
                    ) from exc
            return f"wss://api.deepgram.com/v1/listen?{params}"

    def _get_language_name(self, lang_code):
            """Return display name for a Deepgram language code."""
            return LANGUAGE_CONFIG.get(lang_code, {}).get("name", lang_code)

    def _print_accuracy_startup(self, lang_code):
            """Print expected accuracy target for the selected language."""
            cfg = LANGUAGE_CONFIG.get(lang_code, {})
            lang_name = cfg.get("name", lang_code)
            expected_wer = cfg.get("expected_wer", 0.03)
            expected_accuracy = (1 - expected_wer) * 100
            stt_profile = self._resolve_japanese_stt_profile(lang_code)
            print(f"[Nova-3] Model activated - Enhanced accuracy mode")
            print(f"[Language] Set to: {lang_name} ({lang_code})")
            if stt_profile["use_diarize"]:
                print("[Diarization] diarize_model=latest enabled")
            else:
                print("[Diarization] disabled (no_diarize profile)")
            print(
                f"[Accuracy] Target: {expected_accuracy:.1f}% "
                f"(WER <= {expected_wer:.2f}) for {lang_name}"
            )

    def _allow_final_transcript_commit(self) -> bool:
            """True when final transcript messages should be committed."""
            return bool(getattr(self, "is_listening", False)) or bool(
                getattr(self, "_is_finalizing", False)
            )

    def _commit_final_transcript_segment(
            self, speaker_num: int, segment_text: str, metadata=None
    ) -> bool:
            """Publish one final transcript segment through the normal callback path."""
            if not self._allow_final_transcript_commit():
                if getattr(self, "_is_finalizing", False):
                    print(
                        "[STOP][ERROR] final transcript received during finalize "
                        "but commit was skipped"
                    )
                    _diag_ndjson_log(
                        location="deepgram_client.py:_commit_final_transcript_segment",
                        message="[DIAG] transcript commit skipped",
                        data={
                            "reason": "allow_final_transcript_commit_false",
                            "is_finalizing": bool(
                                getattr(self, "_is_finalizing", False)
                            ),
                            "is_listening": bool(getattr(self, "is_listening", False)),
                            "speaker": speaker_num,
                            "text_len": len(segment_text),
                            "text_preview": _diag_text_preview(segment_text),
                        },
                    )
                return False

            # fixes BUG_FIX_ROADMAP.md Batch 2 item 7b (audit §2.10): the
            # language-path decision and the Japanese stabilizer work used
            # to share one try/except, so ANY failure inside -- most
            # importantly stabilizer.ingest() raising -- was printed and
            # then fell through into the English/generic block below.
            # After this fix, NO failure in either step can reach that
            # block: both now publish the final directly instead, so a
            # Japanese final can never be handed to the English-only
            # utterance lifecycle controller.
            use_japanese = False
            try:
                from alpha.transcription.japanese_final_chunk_stabilizer import (
                    should_use_japanese_final_stabilizer,
                )

                use_japanese = bool(should_use_japanese_final_stabilizer(self))
            except Exception as exc:
                # Language path is undeterminable. Do NOT fall through to
                # the English/generic block: should_use_utterance_lifecycle()
                # re-derives the same decision from the same (now broken)
                # helper, and when that inner call also fails it falls back
                # to a lang check that defaults to "" -- which does not
                # start with "ja", so it returns True and would feed a
                # Japanese final into the English-only controller. That is
                # exactly the contamination audit §2.10 describes. Publish
                # directly instead: the spoken text is preserved either way,
                # and no wrong-language controller can see it. The cost is
                # that an English final also skips the lifecycle here, but
                # this branch only runs when the language module is already
                # broken, and bypassing assembly is strictly safer than
                # routing to a possibly-wrong controller.
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "JAPANESE_PATH_DETECTION_FAILED",
                        reason=f"{type(exc).__name__}:{exc}",
                        text_preview=str(segment_text or "")[:120],
                        fallback="published_directly_no_controller",
                    )
                except Exception:
                    pass
                return self._publish_final_transcript_segment(
                    speaker_num, segment_text, metadata=metadata
                )

            if use_japanese:
                try:
                    from alpha.transcription.japanese_final_chunk_stabilizer import (
                        get_japanese_final_stabilizer,
                        is_accepting_japanese_transcripts,
                    )
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    stabilizer = get_japanese_final_stabilizer(self)
                    if not is_accepting_japanese_transcripts(self):
                        # fixes TASK_6_REPORT.md P1 (ALPHA_ARCHITECTURE_DEBUG_REPORT.md
                        # "Late final transcript can be acknowledged but
                        # silently dropped"): _allow_final_transcript_commit()
                        # already confirmed above that we are still
                        # is_listening or _is_finalizing -- i.e. the overall
                        # session/utterance boundary has not closed yet, so
                        # this final legitimately belongs to the current
                        # utterance even though the Japanese-specific
                        # acceptance gate was independently closed already
                        # (e.g. a WS-close race against the finalize
                        # sequence). Reopen the gate for this one late final
                        # instead of returning True on a silent drop -- a
                        # spoken final must not vanish with no observable
                        # trace and no commit.
                        jp_accuracy_log(
                            "STALE_FINAL_GATE_REOPENED_FOR_LATE_FINAL",
                            raw_text=segment_text,
                            reason="allow_final_transcript_commit_true_but_gate_closed",
                            is_listening=bool(getattr(self, "is_listening", False)),
                            is_finalizing=bool(getattr(self, "_is_finalizing", False)),
                            is_stopping=bool(getattr(self, "_is_stopping", False)),
                        )
                        stabilizer.set_accepting(True)
                    return stabilizer.ingest(speaker_num, segment_text, metadata)
                except Exception as exc:
                    print(f"[JAPANESE] stabilizer ingest error: {exc}")
                    try:
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                        jp_accuracy_log(
                            "JAPANESE_STABILIZER_INGEST_FAILED",
                            reason=f"{type(exc).__name__}:{exc}",
                            text_preview=str(segment_text or "")[:120],
                            fallback="published_directly_without_assembler",
                        )
                    except Exception:
                        pass
                    # Confirmed Japanese session: publish this final
                    # directly rather than letting it fall through to the
                    # English/generic block below. Publishing preserves the
                    # spoken text -- and this is also what already happened
                    # in practice before the fix, since
                    # should_use_utterance_lifecycle() independently rejects
                    # Japanese, so the pre-fix fall-through skipped the
                    # lifecycle and landed on the same publish call at the
                    # bottom. Making it explicit means the routing no longer
                    # depends on that second, unrelated guard staying correct.
                    return self._publish_final_transcript_segment(
                        speaker_num, segment_text, metadata=metadata
                    )

            # English / generic: utterance lifecycle owns incomplete finals and
            # cumulative revisions. Japanese path returned above.
            try:
                from alpha.constants import UTTERANCE_LIFECYCLE_ENABLED
                from alpha.transcription.utterance_lifecycle import (
                    get_utterance_lifecycle,
                    should_use_utterance_lifecycle,
                )

                if UTTERANCE_LIFECYCLE_ENABLED and should_use_utterance_lifecycle(self):
                    meta = dict(metadata or {})
                    decision = get_utterance_lifecycle(self).on_final_chunk(
                        text=segment_text,
                        speaker=int(speaker_num or 1),
                        channel=meta.get("channel_index", meta.get("channel")),
                        start=meta.get("start_time"),
                        end=meta.get("end_time"),
                        is_final=True,
                        speech_final=meta.get("speech_final"),
                        # fixes BUG_FIX_ROADMAP.md Batch 3 item 20 (audit
                        # §1.3): this used to be
                        #     meta.get("event_id")
                        #     or meta.get("request_id")
                        #     or f"dg-final-{time.time_ns()}"
                        # `segment_metadata` never carries an "event_id"
                        # key, so the first term was always None and
                        # `request_id` -- Deepgram's **connection-level** id,
                        # identical for every utterance in the session --
                        # always won, leaving the unique fallback dead code.
                        #
                        # event_id feeds active.lineage_ids, which becomes
                        # source_raw_event_ids on the ledger record, which
                        # stable_revision_decision._same_revision_chain uses
                        # via _lineage_overlap(). A session constant makes
                        # that overlap non-zero for EVERY pair of utterances,
                        # so the lineage half of the same-segment test was a
                        # constant-true check. Measured live: in run
                        # ...155842 one such id appears in 13 of 14 canonical
                        # records; in ...133236, in 30. (The comment in
                        # stable_revision_decision.py about lineage overlap
                        # being "sticky and false-positive across adjacent
                        # utterances" is that symptom -- this is its cause.)
                        #
                        # The connection id is NOT lost: it is passed
                        # separately as deepgram_request_id just below and
                        # stored on its own field. Japanese was never
                        # affected -- that path supplies per-event
                        # `raw-NNNNNN` ids that genuinely vary.
                        event_id=str(
                            meta.get("event_id")
                            or f"dg-final-{time.time_ns()}"
                        ),
                        metadata=meta,
                        deepgram_request_id=str(
                            meta.get("request_id")
                            or meta.get("deepgram_request_id")
                            or ""
                        ),
                    )
                    # Held / replaced active → interim UI only (no permanent publish).
                    if not decision.should_commit:
                        return True
                    # Commit path publishes via lifecycle on_commit → _publish_...
                    return True
            except Exception as exc:
                print(f"[UTTERANCE] lifecycle ingest error: {exc}")

            return self._publish_final_transcript_segment(
                speaker_num, segment_text, metadata=metadata
            )

    def _publish_final_transcript_segment(
            self,
            speaker_num: int,
            segment_text: str,
            metadata=None,
            queue_item=None,
            commit_reason=None,
    ) -> bool:
            """Publish a prepared final transcript segment to the UI queue."""
            if getattr(self, "_is_finalizing", False):
                print("[STOP] committing final transcript during finalize")

            # Language-agnostic Raw/Stable evidence (English included).
            # Japanese also records via stabilizer; duplicate event IDs are acceptable
            # as chronological provider finals for evidence completeness.
            try:
                from alpha.utils.accuracy_stage_capture import (
                    record_raw_deepgram_final,
                )
                from alpha.utils.run_identity import get_run_id

                rid = str(get_run_id() or "")
                record_raw_deepgram_final(
                    run_id=rid,
                    speaker=int(speaker_num or 0),
                    raw_text=str(segment_text or ""),
                    is_final=True,
                    speech_final=(metadata or {}).get("speech_final") if metadata else None,
                    confidence=(metadata or {}).get("confidence") if metadata else None,
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
                # fixes TASK_6_REPORT.md P1 (ALPHA_ARCHITECTURE_DEBUG_REPORT.md
                # "Multiple stable-event writers"): this used to call
                # record_assembler_only_event, writing a SECOND, speculative
                # event into the canonical stable_assembler_events.jsonl
                # stream for the same operation pipeline_commit_transaction.py
                # writes for real once the actual ledger commit happens (the
                # observed "commit, append, commit, append" pattern for 2 real
                # records). pipeline_commit_transaction.py is now the sole
                # canonical writer; this is a proposal/diagnostic observation
                # only, logged under a distinct schema (jp_accuracy_log, a
                # different file entirely) instead of the canonical stream.
                listen_lang = str(getattr(self, "_listen_language", "") or "").lower()
                if listen_lang.startswith("en"):
                    predicted_action = "append"
                    meta = metadata if isinstance(metadata, dict) else {}
                    life_decision = str(meta.get("lifecycle_decision") or "").upper()
                    if life_decision in ("REPLACE_ACTIVE", "EXTEND_ACTIVE", "SUPERSEDE_PREVIOUS"):
                        predicted_action = "revise"
                    elif life_decision == "COMMIT_ACTIVE":
                        predicted_action = "commit"
                    else:
                        predicted_action = "append"
                    try:
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                        jp_accuracy_log(
                            "ENGLISH_FINAL_STABLE_PROPOSAL_OBSERVED",
                            run_id=rid,
                            speaker=int(speaker_num or 0),
                            predicted_action=predicted_action,
                            reason=str(
                                meta.get("lifecycle_commit_reason")
                                or commit_reason
                                or "english_accepted_final"
                            ),
                            commit_reason=str(commit_reason or "english_final"),
                            note="diagnostic_only_not_canonical_stable_event",
                        )
                    except Exception:
                        pass
            except Exception:
                pass

            if queue_item is None:
                queue_item = {
                    "speaker": speaker_num,
                    "text": segment_text,
                    "is_final": True,
                }
                preparer = getattr(self, "_prepare_final_transcript_for_queue", None)
                if callable(preparer) and not queue_item.get("_jp_cleaned"):
                    prepared = preparer(segment_text)
                    queue_item["text"] = prepared
                    queue_item["_jp_cleaned"] = True
                    segment_text = prepared
            if metadata:
                queue_item.update(metadata)
                # Defence in depth for item 65-flush. This function only ever
                # publishes FINAL segments -- it sets `is_final: True` above --
                # but the blanket `update(metadata)` happily overwrites that
                # with a stale `False` carried on the triggering event's
                # metadata, and `_display_transcript_item` then drops the item
                # silently before any commit. Re-assert it rather than trusting
                # every upstream metadata producer to leave it alone.
                queue_item["is_final"] = True
            if commit_reason:
                queue_item["stabilizer_reason"] = commit_reason
            if hasattr(self, "publish_transcript_event"):
                self.publish_transcript_event(
                    text=segment_text,
                    speaker=speaker_num,
                    is_final=True,
                    queue_item=queue_item,
                )
            else:
                self.transcript_queue.put(queue_item)

            if getattr(self, "_is_finalizing", False):
                print("[STOP] final transcript committed during finalize")
            return True

    def _handle_interim_deepgram_result(self, data):
            """Route interim Deepgram text to optional host callback (not permanent store)."""
            alternatives = data.get("channel", {}).get("alternatives", [])
            if not alternatives:
                return
            transcript = alternatives[0].get("transcript", "").strip()
            if not transcript:
                return
            speaker_num = getattr(self, "current_speaker", None) or 1
            segments = self.extract_speaker_from_nova3(data)
            if segments:
                speaker_num = segments[-1].get("speaker", speaker_num) or speaker_num
            metadata = {
                "is_final": False,
                "speech_final": data.get("speech_final"),
                "received_at": time.time(),
                "channel_index": data.get("channel_index"),
            }
            try:
                from alpha.constants import UTTERANCE_LIFECYCLE_ENABLED
                from alpha.transcription.utterance_lifecycle import (
                    get_utterance_lifecycle,
                    should_use_utterance_lifecycle,
                )

                if UTTERANCE_LIFECYCLE_ENABLED and should_use_utterance_lifecycle(self):
                    words = alternatives[0].get("words", []) or []
                    get_utterance_lifecycle(self).on_interim(
                        text=transcript,
                        speaker=int(speaker_num or 1),
                        channel=data.get("channel_index"),
                        start=words[0].get("start") if words else None,
                        end=words[-1].get("end") if words else None,
                        metadata=metadata,
                    )
            except Exception as exc:
                print(f"[LIFECYCLE] interim forward error: {exc}")
            self._teams_maybe_log_interim_sample(
                speaker_num, transcript, data.get("speech_final")
            )
            # This call is intentionally unconditional -- do not gate it on
            # UTTERANCE_LIFECYCLE_ENABLED/should_use_utterance_lifecycle.
            # Japanese sessions never go through get_utterance_lifecycle(...)
            # .on_interim(...) above (should_use_utterance_lifecycle() is
            # English/generic-only by design), so this is their only path to
            # the UI. On the English path it's also still needed: lifecycle's
            # own _dispatch_interim only fires when a LifecycleDecision sets
            # should_update_interim=True, which is False on several branches
            # (see utterance_lifecycle.py), so lifecycle alone does not
            # deliver every interim to the UI either. Net effect: on the
            # English path with lifecycle enabled, this handler(...) call and
            # the on_interim(...) call above both often fire for the same
            # interim tick -- known, currently harmless (on_interim_transcript
            # only overwrites self._pending_interim, and INTERIM_UI_THROTTLE_MS
            # lets only one of the pair actually render). If you ever add a
            # counter, metric, or other side effect to on_interim_transcript /
            # _handle_interim_transcript_ui, it will silently double-count for
            # every English interim tick -- guard it explicitly if you do.
            handler = getattr(self, "on_interim_transcript", None)
            if callable(handler):
                handler(speaker_num, transcript, metadata=metadata)

    def _teams_maybe_log_interim_sample(self, speaker_num, transcript, speech_final):
            if not DEBUG_TEAMS_DIAGNOSTICS:
                return
            now = time.monotonic()
            last = getattr(self, "_teams_last_interim_sample_monotonic", None)
            if last is not None and (now - last) < 2.0:
                return
            self._teams_last_interim_sample_monotonic = now
            _teams_diag_ndjson_log(
                location="deepgram_client.py:_teams_maybe_log_interim_sample",
                message="[TEAMS_DIAG] deepgram interim sample",
                data={
                    "elapsed_sec": self._latency_elapsed_sec(),
                    "text_len": len(transcript),
                    "text_preview": _diag_text_preview(transcript, 160),
                    "speaker": speaker_num,
                    "speech_final": speech_final,
                },
            )

    def _teams_log_deepgram_final(self, data, segment, segment_text):
            if not DEBUG_TEAMS_DIAGNOSTICS:
                return
            alt = (data.get("channel", {}) or {}).get("alternatives", [{}])[0]
            words = alt.get("words", []) or []
            start_time = words[0].get("start") if words else None
            end_time = words[-1].get("end") if words else None
            source_snapshot = getattr(self, "_teams_latest_source_snapshot", {}) or {}
            _teams_diag_ndjson_log(
                location="deepgram_client.py:_teams_log_deepgram_final",
                message="[TEAMS_DIAG] deepgram final",
                data={
                    "elapsed_sec": self._latency_elapsed_sec(),
                    "speaker_from_deepgram": segment.get("speaker"),
                    "speaker_from_source_logic": source_snapshot.get("speaker_label"),
                    "speech_final": data.get("speech_final"),
                    "is_final": data.get("is_final"),
                    "text_len": len(segment_text),
                    "text_preview": _diag_text_preview(segment_text, 160),
                    "start_time": start_time,
                    "end_time": end_time,
                    "language": data.get("language") or alt.get("language"),
                    "channel": data.get("channel_index"),
                },
            )
            teams_log_quality_signals(
                location="deepgram_client.py:_teams_log_deepgram_final",
                elapsed_sec=self._latency_elapsed_sec(),
                speaker_label=segment.get("speaker"),
                text=segment_text,
                speech_final=data.get("speech_final"),
            )

    def _deepgram_on_message(self, _ws, message):
            """Handle Deepgram WebSocket messages - simplified final-only processing."""
            if not isinstance(message, str):
                return
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")
                # region agent log
                _agent_debug_log(
                    run_id="manual-analysis",
                    hypothesis_id="TRACE",
                    location="deepgram_client.py:_deepgram_on_message",
                    message="incoming websocket message",
                    data={
                        "type": msg_type,
                        "is_final": bool(data.get("is_final", False)),
                    },
                )
                # endregion

                if msg_type in ("Metadata", "Open"):
                    return

                if msg_type == "UtteranceEnd":
                    print("[UtteranceEnd] Resetting speaker state")
                    try:
                        from alpha.constants import UTTERANCE_LIFECYCLE_ENABLED
                        from alpha.transcription.utterance_lifecycle import (
                            get_utterance_lifecycle,
                            should_use_utterance_lifecycle,
                        )

                        if UTTERANCE_LIFECYCLE_ENABLED and should_use_utterance_lifecycle(
                            self
                        ):
                            get_utterance_lifecycle(self).on_utterance_end(
                                # Deepgram's UtteranceEnd message uses the key
                                # "channel", NOT "channel_index" (that key only
                                # exists on Results messages). Reading the wrong
                                # key made this always None, so the cross-channel
                                # guard in on_utterance_end() rejected every
                                # UtteranceEnd-triggered commit unconditionally.
                                channel=data.get("channel"),
                                event_id=f"utterance-end-{time.time_ns()}",
                                metadata={
                                    "type": "UtteranceEnd",
                                    "channel_index": data.get("channel"),
                                },
                            )
                    except Exception as exc:
                        print(f"[UtteranceEnd] lifecycle flush error: {exc}")
                    self.current_speaker = None
                    self.fallback_speaker = 1
                    self.last_speech_time = time.time()
                    self._fragment_merge_meta = None
                    return

                if msg_type != "Results":
                    return

                if not data.get("is_final", False):
                    self._latency_interim_results_received = int(
                        getattr(self, "_latency_interim_results_received", 0)
                    ) + 1
                    self._last_deepgram_message_monotonic = time.monotonic()
                    try:
                        from alpha.utils.session_progress import touch_progress

                        touch_progress("last_deepgram_message")
                    except Exception:
                        pass
                    self._handle_interim_deepgram_result(data)
                    return

                alternatives = data.get("channel", {}).get("alternatives", [])
                if not alternatives:
                    return

                transcript = alternatives[0].get("transcript", "").strip()
                if not transcript:
                    return

                now = time.monotonic()
                self._last_deepgram_message_monotonic = now
                self._latency_final_results_received = int(
                    getattr(self, "_latency_final_results_received", 0)
                ) + 1
                try:
                    from alpha.utils.session_progress import touch_progress

                    touch_progress("last_deepgram_message")
                    touch_progress("last_deepgram_final")
                except Exception:
                    pass
                prev_final = getattr(self, "_last_final_result_monotonic", None)
                self._last_final_result_monotonic = now
                _latency_ndjson_log(
                    location="deepgram_client.py:_deepgram_on_message",
                    message="[LATENCY] deepgram final result",
                    data={
                        "elapsed_sec": self._latency_elapsed_sec(),
                        "text_len": len(transcript),
                        "text_preview": _latency_text_preview(transcript),
                        "seconds_since_last_audio_send": self._latency_seconds_since(
                            getattr(self, "_last_audio_send_monotonic", None)
                        ),
                        "seconds_since_previous_final": (
                            round(now - prev_final, 3) if prev_final else None
                        ),
                        "is_finalizing": bool(
                            getattr(self, "_is_finalizing", False)
                        ),
                    },
                )
                self._latency_previous_final_monotonic = now

                segments = self.extract_speaker_from_nova3(data)  # CHANGED: list of speaker segments (fix 4)
                if not segments:
                    return

                if self._dg_awaiting_transcript_reset:  # CHANGED: reset backoff after reconnect transcript (fix 5)
                    self._dg_backoff_seconds = 1.0  # CHANGED: (fix 5)
                    self._dg_awaiting_transcript_reset = False  # CHANGED: (fix 5)
                    print("[Reconnect] Backoff reset after first transcript")  # CHANGED: (fix 5)

                alt0 = alternatives[0]
                words = alt0.get("words", []) or []
                start_time = words[0].get("start") if words else None
                end_time = words[-1].get("end") if words else None
                source_snapshot = getattr(self, "_teams_latest_source_snapshot", {}) or {}
                detected_language = data.get("language") or alt0.get("language")
                segment_metadata = {
                    "speech_final": data.get("speech_final"),
                    "start_time": start_time,
                    "end_time": end_time,
                    "channel_index": data.get("channel_index"),
                    "channel": data.get("channel_index"),
                    "is_final": True,
                    "source": source_snapshot.get("chosen_source")
                    or source_snapshot.get("speaker_label"),
                    "detected_language": detected_language,
                    "language_confidence": (
                        alt0.get("language_confidence")
                        or alt0.get("detected_language_confidence")
                    ),
                    "transcript_confidence": alt0.get("confidence"),
                    "allowed_languages": getattr(self, "_allowed_languages", None),
                    "language_profile_id": getattr(self, "_language_profile_id", None),
                    "selected_source_language_ui": getattr(
                        self, "_selected_source_language_ui_label", None
                    ),
                    "request_id": data.get("metadata", {}).get("request_id")
                    if isinstance(data.get("metadata"), dict)
                    else data.get("request_id"),
                }

                for segment in segments:  # CHANGED: enqueue one item per speaker run (fix 4)
                    speaker_num = segment.get("speaker", 1)
                    segment_text = segment.get("text", "").strip()
                    if not segment_text:
                        continue
                    self._teams_log_deepgram_final(data, segment, segment_text)
                    self._log_detected_language(data, alt0, segment_text, speaker_num)
                    if getattr(self, "_is_finalizing", False):
                        print("[STOP] final transcript received during finalize")
                        _diag_ndjson_log(
                            location="deepgram_client.py:_deepgram_on_message",
                            message="[DIAG] final transcript during finalize received",
                            data={
                                "is_finalizing": bool(
                                    getattr(self, "_is_finalizing", False)
                                ),
                                "receiver_allowed": bool(
                                    getattr(self, "_dg_receiver_allowed", False)
                                ),
                                "is_listening": bool(
                                    getattr(self, "is_listening", False)
                                ),
                                "text_len": len(segment_text),
                                "text_preview": _diag_text_preview(segment_text),
                                "speech_final": data.get("speech_final"),
                                "channel_index": data.get("channel_index"),
                                "speaker": speaker_num,
                            },
                        )
                    committed = self._commit_final_transcript_segment(
                        speaker_num, segment_text, metadata=segment_metadata
                    )
                    if not committed:
                        continue
                    self._transcripts_received += 1
                    print(f"[FINAL] Speaker {speaker_num}: {segment_text}")

            except Exception as e:
                print(f"[ERROR] Processing Deepgram message: {e}")
                import traceback
                traceback.print_exc()

    def _deepgram_on_open(self, ws):
            """Start streaming queued audio to Deepgram when the socket opens."""
            print("Nova-3 connected — streaming audio to Deepgram")
            # The key was accepted, so any earlier rejection is stale.
            self._dg_auth_failed = False
            # Item 44, second correction. The gap marker used to be emitted in
            # `_reconnect_deepgram` just before `run_forever`, i.e. before the
            # socket was known to connect -- so on run `...20260812-142447` it
            # reported `gap_seconds: 2.6` for an outage that actually lasted 45
            # seconds, and cleared the clock so the later, longer truth could
            # never be recorded. Marking here instead means it fires exactly
            # once per outage, when the connection is genuinely back, carrying
            # the real duration. On the very first connect of a session
            # `_dg_disconnected_at` is 0, so this returns without marking.
            try:
                self._mark_deepgram_gap_if_any()
            except Exception:
                pass
            try:
                from alpha.utils.multidomain_gate_evidence import record_lifecycle_event

                record_lifecycle_event("deepgram_connection_opened")
            except Exception:
                pass
            try:
                from alpha.utils.async_debug_log import log_runtime_debug_event

                log_runtime_debug_event("DEEPGRAM_CONNECT_END", connected=True)
            except Exception:
                pass
            try:
                from alpha.transcription.japanese_final_chunk_stabilizer import (
                    get_japanese_final_stabilizer,
                    should_use_japanese_final_stabilizer,
                )

                if should_use_japanese_final_stabilizer(self):
                    get_japanese_final_stabilizer(self).set_accepting(True)
            except Exception:
                pass
            self.log_deepgram_stream_config()
            self.log_deepgram_language_config()
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_on_open",
                message="websocket opened",
                data={
                    "replay_chunks": len(list(getattr(self, "_dg_replay_buffer", []) or [])),
                    "audio_q_size": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                },
            )
            # endregion

            replay_chunks = list(getattr(self, "_dg_replay_buffer", []) or [])  # CHANGED: replay after reconnect (fix 5)
            self._dg_replay_buffer = []  # CHANGED: clear replay buffer (fix 5)

            def stream_audio():
                chunks_sent = 0
                last_keepalive = time.perf_counter()  # CHANGED: track keepalive timing (fix 5/6)
                self._latency_sender_loop_alive = True
                try:
                    for chunk in replay_chunks:  # CHANGED: send buffered audio first (fix 5)
                        if self._stop_event.is_set():
                            return
                        try:
                            sent = self._normalize_and_send_pcm(
                                ws, chunk, input_type="replay_buffer"
                            )
                            if sent <= 0:
                                continue
                            chunks_sent += 1  # CHANGED: (fix 5)
                            self._chunks_sent_count = chunks_sent  # CHANGED: (fix 5)
                            self._record_audio_chunk_sent(sent)
                            last_keepalive = time.perf_counter()  # CHANGED: (fix 5)
                        except Exception as exc:
                            print(f"Error replaying audio to Deepgram: {exc}")  # CHANGED: (fix 5)
                            try:
                                from alpha.utils.runtime_audio_counters import note_deepgram_send_error

                                note_deepgram_send_error()
                            except Exception:
                                pass
                            return
                    if replay_chunks:  # CHANGED: (fix 5)
                        print(f"[Reconnect] Replayed {len(replay_chunks)} buffered audio chunks")  # CHANGED: (fix 5)

                    while (
                        not self._stop_event.is_set()
                        and not getattr(self, "_dg_stop_sending_audio", False)
                    ):
                        self._drain_audio_queue_backpressure()
                        try:
                            chunk = self._audio_q.get(timeout=0.1)
                        except queue.Empty:
                            if time.perf_counter() - last_keepalive >= DG_KEEPALIVE_INTERVAL_S:  # CHANGED: JSON keepalive (fix 5/6)
                                try:
                                    ws.send(json.dumps({"type": "KeepAlive"}))  # CHANGED: replace silence injection (fix 6)
                                    last_keepalive = time.perf_counter()  # CHANGED: (fix 5/6)
                                except Exception as exc:
                                    print(f"Error sending Deepgram keepalive: {exc}")  # CHANGED: (fix 5)
                                    break
                            continue
                        try:
                            sent = self._normalize_and_send_pcm(
                                ws, chunk, input_type="timeline_mixer"
                            )
                            if sent <= 0:
                                continue
                            chunks_sent += 1
                            self._chunks_sent_count = chunks_sent
                            self._record_audio_chunk_sent(sent)
                            last_keepalive = time.perf_counter()  # CHANGED: audio counts as activity (fix 5)
                            if chunks_sent % 100 == 0:
                                # region agent log
                                _agent_debug_log(
                                    run_id="manual-analysis",
                                    hypothesis_id="TRACE",
                                    location="deepgram_client.py:stream_audio",
                                    message="audio chunks sent milestone",
                                    data={
                                        "chunks_sent": int(chunks_sent),
                                        "audio_q_size": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                                    },
                                )
                                # endregion
                            if chunks_sent == 1:
                                print(
                                    f"First audio chunk sent to Deepgram ({sent} bytes)"
                                )
                        except Exception as exc:
                            print(f"Error sending audio to Deepgram: {exc}")
                            break
                finally:
                    self._latency_sender_loop_alive = False
                    # region agent log
                    _agent_debug_log(
                        run_id="pre-fix",
                        hypothesis_id="H2",
                        location="deepgram_client.py:stream_audio_loop_exit",
                        message="sender loop exited",
                        data={
                            "stop_event": bool(self._stop_event.is_set()),
                            "stop_sending_audio": bool(getattr(self, "_dg_stop_sending_audio", False)),
                            "queue_size_on_exit": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                        },
                    )
                    # endregion

            threading.Thread(target=stream_audio, daemon=True).start()

    def _schedule_reconnect(self):
            """Queue a Deepgram reconnect attempt (daemon thread, no self.after)."""
            if not self.is_listening or self._stop_event.is_set():  # CHANGED: only while listening (fix 5)
                return
            with self._dg_reconnect_lock:  # CHANGED: (fix 5)
                if self._dg_reconnecting:  # CHANGED: avoid duplicate reconnect threads (fix 5)
                    return
                self._dg_reconnecting = True  # CHANGED: (fix 5)
            threading.Thread(target=self._reconnect_deepgram, daemon=True).start()

    def deepgram_gap_seconds(self) -> float:
        """How long the provider has been disconnected, 0.0 when connected.

        Item 44. Read by `_mark_deepgram_gap_if_any` and available to the
        status indicator (item 47).
        """
        started = float(getattr(self, "_dg_disconnected_at", 0.0) or 0.0)
        if not started:
            return 0.0
        return max(0.0, time.time() - started)

    def _mark_deepgram_gap_if_any(self) -> Optional[float]:
        """Emit a visible marker for audio lost while Deepgram was down.

        Item 44's "mark the gap visibly". Reconnect already backs off, buffers
        and replays queued audio, but audio captured *while the socket was
        down* is genuinely gone -- and a transcript that silently stitches
        across that hole reads as continuous speech. A client cannot tell a
        clean recording from one with a hole in it, which is exactly the
        failure mode this item exists to prevent.

        Returns the gap length in seconds when a marker was emitted, else None.
        Never raises: a failure to annotate must not stop the reconnect.
        """
        gap_s = self.deepgram_gap_seconds()
        self._dg_disconnected_at = 0.0
        if gap_s < float(DG_GAP_MARKER_MIN_S):
            return None
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "DEEPGRAM_AUDIO_GAP_MARKED",
                gap_seconds=round(gap_s, 1),
                reason="provider_disconnected_audio_not_captured",
            )
        except Exception:
            pass
        # Item 67: tell the export authority as well as the live UI. The
        # publish below reaches the TranscriptStore, which is what the UI and
        # copy/export read -- but `Alpha output.txt` is built from the frozen
        # canonical ledger, and this marker can never become a ledger record
        # (synthetic, no `source_raw_event_ids`, and RAW_EVENT_LINEAGE_REQUIRED
        # is a rule worth keeping). So the ledger is told separately and
        # renders it at export time.
        try:
            from alpha.transcription.canonical_transcript_ledger import (
                record_connection_gap,
            )

            record_connection_gap(seconds=gap_s)
        except Exception:
            pass
        try:
            publisher = getattr(self, "_publish_final_transcript_segment", None)
            if callable(publisher):
                publisher(
                    getattr(self, "_last_committed_speaker", 1) or 1,
                    DG_GAP_MARKER_TEMPLATE.format(seconds=int(round(gap_s))),
                    {
                        "connection_gap_marker": True,
                        "gap_seconds": round(gap_s, 1),
                        "translation_eligible": False,
                        "synthetic_record": True,
                    },
                    None,
                    "deepgram_connection_gap",
                )
        except Exception:
            # Annotating is best-effort; losing the marker must never break
            # the reconnect that restores transcription.
            pass
        return gap_s

    def _reconnect_deepgram(self):
            """Reconnect to Deepgram with exponential backoff and audio replay.

            Item 44, third correction -- this used to make exactly ONE attempt.
            `_schedule_reconnect` is single-flight on `_dg_reconnecting`, and
            `websocket-client` invokes `on_close` from *inside* `run_forever`.
            So when an attempt failed because the network was still down, the
            `_deepgram_on_close` -> `_schedule_reconnect()` retry signal arrived
            while `_dg_reconnecting` was still True and was dropped on the
            floor; the `finally` then cleared the flag with nobody left to call
            again. One failed attempt killed transcription for the rest of the
            session. Measured on run `...20260812-142447`: WiFi dropped at
            14:30:00 and returned at 14:30:45, one reconnect ran at 14:30:03,
            and the app never transcribed again -- 217 audio chunks discarded,
            the last commit at 14:30:01, session ended `failed`.

            Retrying is therefore the loop's own job, not the close handler's.
            `run_forever` returning at all means the socket is down (it either
            never opened or has just dropped), so every return is a reason to
            try again until Stop. Backoff still doubles to
            DG_RECONNECT_BACKOFF_MAX_S, so a long outage costs a bounded number
            of attempts, and `_schedule_reconnect`'s guard stays correct: while
            this loop is alive it IS the reconnect.
            """
            try:
                while self.is_listening and not self._stop_event.is_set():
                  # Per-attempt, NOT around the loop: an exception in one
                  # attempt must not end the retrying, which is the same shape
                  # as the bug this loop exists to fix.
                  try:
                    buffered = []  # CHANGED: snapshot queued audio before reconnect (fix 5)
                    if self._audio_q is not None:  # CHANGED: (fix 5)
                        while True:  # CHANGED: (fix 5)
                            try:
                                buffered.append(self._audio_q.get_nowait())  # CHANGED: (fix 5)
                            except queue.Empty:  # CHANGED: (fix 5)
                                break  # CHANGED: (fix 5)
                    if buffered:
                        # Only replace the buffer when this attempt actually
                        # drained something: a failed attempt must not wipe the
                        # audio a previous one already captured.
                        self._dg_replay_buffer = buffered  # CHANGED: replay on next on_open (fix 5)

                    # Floor of 1s: the loop now runs until Stop, so a backoff
                    # that ever reached 0 would spin the CPU forever instead of
                    # retrying. Matches the documented 1.0 start value.
                    wait_s = min(
                        max(float(self._dg_backoff_seconds or 0.0), 1.0),
                        DG_RECONNECT_BACKOFF_MAX_S,
                    )  # CHANGED: backoff cap (fix 5)
                    print(f"[Reconnect] Waiting {wait_s:.0f}s before reconnect (backoff)")  # CHANGED: (fix 5)
                    time.sleep(wait_s)  # CHANGED: exponential backoff delay (fix 5)
                    self._dg_backoff_seconds = min(wait_s * 2, DG_RECONNECT_BACKOFF_MAX_S)  # CHANGED: (fix 5)

                    if not self.is_listening or self._stop_event.is_set():  # CHANGED: (fix 5)
                        return

                    if self._dg_ws is not None:  # CHANGED: close stale socket (fix 5)
                        try:
                            self._dg_ws.close()  # CHANGED: (fix 5)
                        except Exception:
                            pass  # CHANGED: (fix 5)
                        self._dg_ws = None  # CHANGED: (fix 5)

                    url = self._build_deepgram_url()  # CHANGED: fresh URL on reconnect (fix 5)
                    print(f"[Reconnect] Connecting to Deepgram: {url}")  # CHANGED: (fix 5)
                    self._dg_awaiting_transcript_reset = True  # CHANGED: reset backoff after transcript (fix 5)
                    ws = _keepalive_websocket_app_class()(  # CHANGED: new WebSocket session (fix 5)
                        url,  # CHANGED: (fix 5)
                        header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},  # CHANGED: (fix 5)
                        on_message=self._deepgram_on_message,  # CHANGED: (fix 5)
                        on_open=self._deepgram_on_open,  # CHANGED: (fix 5)
                        on_error=self._deepgram_on_error,  # CHANGED: (fix 5)
                        on_close=self._deepgram_on_close,  # CHANGED: (fix 5)
                    )  # CHANGED: (fix 5)
                    self._dg_ws = ws  # CHANGED: (fix 5)
                    ws.run_forever(
                        ping_interval=DG_WS_PING_INTERVAL_S,
                        ping_timeout=DG_WS_PING_TIMEOUT_S,
                    )  # CHANGED: blocking reconnect in daemon thread (fix 5)
                    # run_forever returned: the socket is down again. Loop.
                  except Exception as exc:
                    print(f"[Reconnect] Deepgram reconnect attempt failed: {exc}")
                    # fall through to the next attempt rather than giving up
            except Exception as exc:
                print(f"[Reconnect] Deepgram reconnect loop error: {exc}")  # CHANGED: (fix 5)
            finally:
                with self._dg_reconnect_lock:  # CHANGED: release reconnect lock (fix 5)
                    self._dg_reconnecting = False

    def _deepgram_on_close(self, _ws, code, msg):
            """Handle WebSocket close; schedule reconnect while listening."""
            try:
                from alpha.utils.multidomain_gate_evidence import record_lifecycle_event

                record_lifecycle_event(
                    "deepgram_connection_closed",
                    code=code,
                    msg=str(msg)[:120],
                )
            except Exception:
                pass
            stop_requested = bool(
                self._stop_event.is_set()
                or getattr(self, "_is_stopping", False)
                or getattr(self, "_dg_stop_sending_audio", False)
                or not bool(getattr(self, "is_listening", False))
            )
            print(f"Deepgram closed: {code} {msg}")  # CHANGED: explicit close handler (fix 5)
            # Item 44: start the gap clock on an UNEXPECTED close only. A
            # user-requested Stop is not a gap -- annotating it would put a
            # "connection lost" line at the end of every normal session.
            if not stop_requested and not getattr(self, "_dg_disconnected_at", 0.0):
                self._dg_disconnected_at = time.time()
                # Item 44, "commit in-flight". Do this on the FIRST unexpected
                # close of an outage, before anything else: an utterance still
                # open when the socket dies is otherwise dropped outright --
                # the next final arrives on the provider's restarted clock,
                # `_timing_compatible` rejects it as a continuation, and the
                # `force_new` branch replaces the active utterance without ever
                # committing it. Committing here also gets the order right,
                # putting pre-drop speech ahead of the gap marker that
                # `_deepgram_on_open` emits once the connection is back.
                try:
                    from alpha.constants import UTTERANCE_LIFECYCLE_ENABLED
                    from alpha.transcription.utterance_lifecycle import (
                        get_utterance_lifecycle,
                        should_use_utterance_lifecycle,
                    )

                    if UTTERANCE_LIFECYCLE_ENABLED and should_use_utterance_lifecycle(self):
                        get_utterance_lifecycle(self).commit_in_flight(
                            reason="provider_disconnected"
                        )
                except Exception as exc:
                    # Never let this block the reconnect it precedes.
                    try:
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                        jp_accuracy_log(
                            "IN_FLIGHT_COMMIT_ON_DISCONNECT_FAILED",
                            reason=f"{type(exc).__name__}:{exc}",
                        )
                    except Exception:
                        pass
            try:
                from alpha.utils.async_debug_log import log_runtime_debug_event
                from alpha.utils.freeze_guard_log import freeze_guard_log

                prev_status = str(getattr(self, "_dg_close_status", "pending"))
                if stop_requested:
                    if prev_status in ("timeout", "pending") and (
                        getattr(self, "_dg_graceful_stop_active", False)
                        or self._stop_event.is_set()
                    ):
                        if prev_status == "timeout":
                            self._dg_close_status = "late_normal"
                            freeze_guard_log(
                                "DEEPGRAM_CLOSE_LATE_NORMAL",
                                code=code,
                                msg=str(msg),
                            )
                            log_runtime_debug_event(
                                "DEEPGRAM_CLOSE_LATE_NORMAL",
                                reason="stop_requested_after_timeout",
                                code=code,
                                msg=str(msg),
                            )
                        else:
                            self._dg_close_status = "normal"
                            freeze_guard_log(
                                "DEEPGRAM_CLOSE_NORMAL",
                                code=code,
                                msg=str(msg),
                            )
                            log_runtime_debug_event(
                                "DEEPGRAM_CLOSE_NORMAL",
                                reason="stop_requested",
                                code=code,
                                msg=str(msg),
                            )
                    else:
                        self._dg_close_status = "normal"
                        log_runtime_debug_event(
                            "DEEPGRAM_CLOSE_NORMAL",
                            reason="stop_requested",
                            code=code,
                            msg=str(msg),
                        )
                else:
                    log_runtime_debug_event(
                        "DEEPGRAM_CLOSE_ERROR",
                        reason="unexpected_while_listening",
                        code=code,
                        msg=str(msg),
                    )
            except Exception:
                pass
            try:
                if stop_requested:
                    from alpha.transcription.japanese_final_chunk_stabilizer import (
                        close_japanese_transcript_gate,
                    )

                    close_japanese_transcript_gate(
                        self, "STOP_TRANSCRIPT_GATE_CLOSED_ON_WS_CLOSE"
                    )
            except Exception:
                pass
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_on_close",
                message="websocket closed",
                data={
                    "code": code,
                    "msg": str(msg),
                    "is_listening": bool(getattr(self, "is_listening", False)),
                    "stop_event": bool(self._stop_event.is_set()) if hasattr(self, "_stop_event") else False,
                    "stop_requested": stop_requested,
                },
            )
            # endregion
            if self.is_listening and not self._stop_event.is_set():  # CHANGED: auto-reconnect (fix 5)
                self._schedule_reconnect()

    def _deepgram_on_error(self, _ws, err):
            """Handle WebSocket errors; reconnect on transient failures."""
            stop_requested = bool(
                self._stop_event.is_set()
                or getattr(self, "_is_stopping", False)
                or getattr(self, "_dg_stop_sending_audio", False)
                or not bool(getattr(self, "is_listening", False))
            )
            if stop_requested:
                try:
                    from alpha.utils.async_debug_log import log_runtime_debug_event

                    log_runtime_debug_event(
                        "DEEPGRAM_CLOSE_NORMAL",
                        reason="stop_requested",
                        error=str(err),
                    )
                except Exception:
                    pass
                return
            print(f"Deepgram WebSocket error: {err}")
            err_text = str(err)
            # Item 47 runtime half. Purely a FLAG -- no control flow here
            # changes, so the existing reconnect behaviour is untouched and
            # only the status indicator reads it. Without this, a revoked or
            # expired key looked exactly like a flaky network: the socket
            # closed, the loop backed off and retried forever, and the
            # operator saw "Signal OK" the whole time.
            _low_err = err_text.lower()
            if (
                "401" in err_text
                or "403" in err_text
                or "unauthorized" in _low_err
                or "forbidden" in _low_err
                or "invalid credentials" in _low_err
                or "invalid_auth" in _low_err
            ):
                self._dg_auth_failed = True
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "DEEPGRAM_AUTH_REJECTED", error=err_text[:200]
                    )
                except Exception:
                    pass
            if (
                bool(JAPANESE_MODE_ENABLED)
                and bool(JAPANESE_KEYTERMS_ENABLED)
                and not bool(getattr(self, "_jp_keyterms_fallback_used", False))
                and (
                    "INVALID_QUERY_PARAMETER" in err_text
                    or "keyterm" in err_text.lower()
                    or "400" in err_text
                )
            ):
                self._jp_keyterms_fallback_used = True
                _language_ndjson_log(
                    location="deepgram_client.py:_deepgram_on_error",
                    message="[JAPANESE] keyterms enabled",
                    data={
                        "keyterm_count": int(len(JAPANESE_KEYTERMS or [])),
                        "keyterms_preview": list((JAPANESE_KEYTERMS or [])[:5]),
                        "fallback_used": True,
                    },
                )
                print("[JAPANESE] keyterm query failed; retrying once without keyterms")
                if self.is_listening and not self._stop_event.is_set():
                    self._schedule_reconnect()
                return
            if str(getattr(self, "_listen_language", "")) == "multi":
                _language_ndjson_log(
                    location="deepgram_client.py:_deepgram_on_error",
                    message="[LANGUAGE] deepgram multi language connection error",
                    data={
                        "deepgram_language_param": "multi",
                        "error": err_text,
                        "selected_source_language_ui": getattr(
                            self, "_selected_source_language_ui_label", None
                        ),
                        "action": "no_silent_fallback_to_english",
                    },
                )
                print(
                    "[LANGUAGE] Deepgram failed with language=multi — "
                    "not falling back to English. Check API plan/support for multi."
                )
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_on_error",
                message="websocket error",
                data={"error": str(err)},
            )
            # endregion
            if hasattr(self, "publish_error_event"):
                self.publish_error_event(
                    err_text,
                    source="deepgram",
                    recoverable="400" not in err_text
                    and "INVALID_QUERY_PARAMETER" not in err_text,
                )
            if "400" in err_text or "INVALID_QUERY_PARAMETER" in err_text:
                try:
                    from alpha.utils.ui_event_bus import get_ui_event_bus

                    get_ui_event_bus().post(
                        "partial_error_notice",
                        {
                            "title": "Deepgram Connection Error",
                            "message": (
                                "Could not connect to Deepgram.\n\n"
                                f"{err_text}\n\n"
                                "Listening has been stopped."
                            ),
                            "action": "stop_listening",
                        },
                    )
                except Exception:
                    pass
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log("DEEPGRAM_CALLBACK_SAFE_EVENT_POSTED")
                except Exception:
                    pass
                return
            if self.is_listening and not self._stop_event.is_set():  # CHANGED: reconnect transient errors (fix 5)
                self._schedule_reconnect()
            else:
                try:
                    from alpha.utils.async_debug_log import log_runtime_debug_event

                    log_runtime_debug_event(
                        "DEEPGRAM_CLOSE_ERROR",
                        reason="unexpected_while_listening",
                        error=err_text,
                    )
                except Exception:
                    pass

    def _deepgram_worker(self):
            """Run the Deepgram WebSocket connection in a background thread."""
            try:
                from alpha.utils.async_debug_log import log_runtime_debug_event

                log_runtime_debug_event("DEEPGRAM_CONNECT_BEGIN")
            except Exception:
                pass
            url = self._build_deepgram_url()
            lang_code = getattr(self, "_listen_language", None) or FORCE_DEEPGRAM_LANGUAGE
            if not lang_code:
                raise ValueError("Deepgram language not finalized before worker start")
            lang_code = str(lang_code)
            stream_opts = self._resolve_deepgram_stream_options(lang_code)
            stt_profile = self._resolve_japanese_stt_profile(lang_code)
            endpointing_ms = stream_opts["endpointing_ms"]
            utterance_end_ms = stream_opts["utterance_end_ms"]
            sanitized_url = url.split("Token", 1)[0] if "Token" in url else url
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(url)
                query_keys = sorted(parse_qs(parsed.query).keys())
                jp_accuracy_log(
                    "deepgram_connect_snapshot",
                    language=str(lang_code),
                    model=str(DEEPGRAM_MODEL),
                    interim_results=True,
                    endpointing=endpointing_ms,
                    utterance_end_ms=utterance_end_ms,
                    query_keys=query_keys,
                    sanitized_url=sanitized_url,
                )
            except Exception:
                pass
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_worker",
                message="deepgram worker starting",
                data={
                    "language": str(lang_code),
                    "endpointing_ms": int(endpointing_ms),
                    "utterance_end_ms": int(utterance_end_ms),
                    "sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                },
            )
            # endregion
            print(f"\n{'=' * 60}")
            print("CONNECTING TO NOVA-3")
            print("Model: nova-3")
            print(f"Language: {lang_code}")
            print(f"Endpointing: {endpointing_ms}ms | utterance_end_ms: {utterance_end_ms}ms")
            if stt_profile["use_diarize"]:
                print("Diarization: diarize_model=latest")
            else:
                print("Diarization: disabled (no_diarize profile)")
            print("Processing: Final results ONLY (interim ignored in UI)")
            print(f"{'=' * 60}\n")
            print(f"Deepgram URL: {url}")
            try:
                from alpha.constants import APP_VERSION
                from alpha.utils.accuracy_stage_capture import write_deepgram_request_actual
                from alpha.utils.issue12_stage1_runtime import (
                    build_deepgram_request_actual_payload,
                    get_active_japanese_accuracy_profile,
                )
                from alpha.utils.run_identity import get_run_id

                params = getattr(self, "_last_deepgram_listen_params", "") or ""
                if "?" in url:
                    params = url.split("?", 1)[1]
                lang_for_payload = str(
                    getattr(self, "_last_deepgram_language", lang_code) or lang_code
                )
                is_japanese = lang_for_payload.lower() in ("ja", "ja-jp") or lang_for_payload.lower().startswith(
                    "ja-"
                )
                if is_japanese:
                    accuracy_profile = get_active_japanese_accuracy_profile() or str(
                        getattr(self, "_last_deepgram_keyterm_profile", "") or ""
                    )
                    keyterm_values = list(getattr(self, "_last_deepgram_sent_keyterms", []) or [])
                else:
                    # Prevent Japanese profile / keyterms leaking into English requests.
                    accuracy_profile = "english_nova3"
                    keyterm_values = []
                domain_agnostic = False
                try:
                    from alpha.utils.multidomain_gate_evidence import (
                        ACCURACY_PROFILE_DOMAIN_AGNOSTIC,
                        is_domain_agnostic_no_hints_active,
                        is_multidomain_benchmark_mode,
                    )

                    domain_agnostic = bool(is_domain_agnostic_no_hints_active())
                    if domain_agnostic and is_japanese:
                        keyterm_values = []
                        accuracy_profile = ACCURACY_PROFILE_DOMAIN_AGNOSTIC
                except Exception:
                    domain_agnostic = False
                payload = build_deepgram_request_actual_payload(
                    run_id=str(get_run_id() or ""),
                    app_version=str(APP_VERSION),
                    profile=accuracy_profile,
                    model=str(DEEPGRAM_MODEL),
                    language=lang_for_payload,
                    encoding="linear16",
                    sample_rate=int(
                        getattr(self, "_last_deepgram_sample_rate", DEEPGRAM_SAMPLE_RATE)
                        or DEEPGRAM_SAMPLE_RATE
                    ),
                    channels=1,
                    interim_results=True,
                    punctuate=True,
                    smart_format=True,
                    endpointing=int(
                        getattr(self, "_last_deepgram_endpointing_ms", endpointing_ms)
                        or endpointing_ms
                    ),
                    utterance_end_ms=int(
                        getattr(self, "_last_deepgram_utterance_end_ms", utterance_end_ms)
                        or utterance_end_ms
                    ),
                    diarize_present=bool(getattr(self, "_last_deepgram_diarize", False)),
                    diarize_model_present=bool(getattr(self, "_last_deepgram_diarize", False)),
                    keyterm_values=keyterm_values,
                    sanitized_query_string=params,
                    captured_immediately_before_connect=True,
                )
                payload["benchmark_profile"] = accuracy_profile
                payload["keyterm_parameter_present"] = bool(keyterm_values)
                payload["keyterm_count"] = len(keyterm_values)
                payload["keyterm_values"] = keyterm_values
                payload["keyword_parameter_present"] = False
                payload["keyword_count"] = 0
                payload["keyword_values"] = []
                payload["selected_language"] = lang_for_payload
                payload["current_language_profile"] = accuracy_profile
                if domain_agnostic:
                    payload["meeting_glossary_loaded"] = False
                    payload["business_japanese_profile_active"] = False
                    payload["test01_profile_active"] = False
                    payload["reference_terms_loaded"] = 0
                elif not is_japanese:
                    payload["meeting_glossary_loaded"] = False
                    payload["business_japanese_profile_active"] = False
                    payload["test01_profile_active"] = False
                    payload["reference_terms_loaded"] = 0
                else:
                    payload["meeting_glossary_loaded"] = False
                    payload["business_japanese_profile_active"] = (
                        accuracy_profile in ("", "business_japanese")
                        or str(getattr(self, "_last_deepgram_keyterm_profile", ""))
                        == "business_japanese"
                    )
                    payload["test01_profile_active"] = (
                        accuracy_profile == "target_85_meeting_context"
                    )
                    payload["reference_terms_loaded"] = 0
                try:
                    from alpha.utils.issue12_stage1_runtime import sha256_text

                    material = {k: v for k, v in payload.items() if k != "request_sha256"}
                    payload["request_sha256"] = sha256_text(
                        json.dumps(
                            material,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                except Exception:
                    pass
                request_path = write_deepgram_request_actual(payload)
                try:
                    from alpha.utils.troubleshooting_paths import (
                        update_run_manifest_deepgram_actual,
                    )

                    update_run_manifest_deepgram_actual(payload)
                except Exception:
                    pass
                try:
                    req_sha = str(payload.get("request_sha256") or "")
                    print(
                        f"ACTUAL_DEEPGRAM_LANGUAGE_CONFIRMED language={lang_for_payload} "
                        f"request_path={request_path} request_sha256={req_sha}"
                    )
                except Exception:
                    pass
            except Exception:
                pass
            try:
                ws = _keepalive_websocket_app_class()(
                    url,
                    header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
                    on_message=self._deepgram_on_message,
                    on_open=self._deepgram_on_open,
                    on_error=self._deepgram_on_error,
                    on_close=self._deepgram_on_close,  # CHANGED: reconnect on close (fix 5)
                )
                self._dg_ws = ws
                ws.run_forever(
                    ping_interval=DG_WS_PING_INTERVAL_S,
                    ping_timeout=DG_WS_PING_TIMEOUT_S,
                )
            except Exception as exc:
                print(f"Deepgram connection error: {exc}")

    def _health_monitor(self):
            """Log pipeline health every 5 seconds while listening."""
            if not self.is_listening:
                self._health_monitor_job = None
                return

            audio_qsize = self._audio_q.qsize() if self._audio_q else 0
            sys_qsize = self.sys_audio_queue.qsize() if self.sys_audio_queue else 0
            mic_qsize = self.mic_audio_queue.qsize() if self.mic_audio_queue else 0
            print(
                f"[Health] chunks_sent={self._chunks_sent_count}, "
                f"transcripts={self._transcripts_received}, "
                f"audio_q={audio_qsize}, sys_q={sys_qsize}, mic_q={mic_qsize}, "
                f"lang={self._listen_language}, "
                f"endpointing={DEEPGRAM_ENDPOINTING_MS}ms"
            )
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_health_monitor",
                message="pipeline health snapshot",
                data={
                    "chunks_sent": int(self._chunks_sent_count),
                    "transcripts": int(self._transcripts_received),
                    "audio_q": int(audio_qsize),
                    "sys_q": int(sys_qsize),
                    "mic_q": int(mic_qsize),
                    "language": str(self._listen_language),
                },
            )
            # endregion
            self._log_latency_pipeline_health()
            self._maybe_log_latency_audio_send_rate()
            if (
                self._chunks_sent_count > 0
                and self._transcripts_received == 0
                and not getattr(self, "_health_no_transcript_hint_shown", False)
            ):
                print(
                    "[Health] Audio is reaching Deepgram, but no transcript has returned "
                    "yet. Check Deepgram API key, language settings, or WebSocket errors."
                )
                self._health_no_transcript_hint_shown = True
            self._health_monitor_job = self.after(
                HEALTH_MONITOR_INTERVAL_MS, self._health_monitor
            )

    def _start_health_monitor(self):
            """Begin periodic health logging."""
            if self._health_monitor_job is not None:
                self.after_cancel(self._health_monitor_job)
            self._health_monitor_job = self.after(
                HEALTH_MONITOR_INTERVAL_MS, self._health_monitor
            )

    def _stop_health_monitor(self):
            """Stop periodic health logging."""
            if self._health_monitor_job is not None:
                self.after_cancel(self._health_monitor_job)
                self._health_monitor_job = None

    def _ensure_graceful_stop_state(self):
            """Initialize graceful-stop coordination flags."""
            if not hasattr(self, "_graceful_stop_lock"):
                self._graceful_stop_lock = threading.Lock()
            if not hasattr(self, "_graceful_stop_in_progress"):
                self._graceful_stop_in_progress = False
            if not hasattr(self, "_graceful_stop_completed"):
                self._graceful_stop_completed = False
            if not hasattr(self, "_dg_stop_sending_audio"):
                self._dg_stop_sending_audio = False
            if not hasattr(self, "_is_finalizing"):
                self._is_finalizing = False
            if not hasattr(self, "_is_stopping"):
                self._is_stopping = False
            if not hasattr(self, "_dg_receiver_allowed"):
                self._dg_receiver_allowed = False

    def reset_graceful_stop_state(self):
            """Reset graceful-stop flags for a new listening session."""
            self._ensure_graceful_stop_state()
            with self._graceful_stop_lock:
                self._graceful_stop_in_progress = False
                self._graceful_stop_completed = False
            self._dg_stop_sending_audio = False
            self._is_finalizing = False
            self._is_stopping = False
            self._dg_receiver_allowed = True

    def request_finalize(self):
            """Send Deepgram Finalize control message; safe if socket is closed."""
            self._ensure_graceful_stop_state()
            ws = getattr(self, "_dg_ws", None)
            if ws is None:
                print("[STOP] finalize send failed: WebSocket not available")
                return False
            try:
                ws.send(json.dumps({"type": "Finalize"}))
                print("[STOP] finalize sent")
                return True
            except Exception as exc:
                print(f"[STOP] finalize send failed: {exc}")
                return False

    def request_close_stream(self):
            """Send Deepgram CloseStream control message; safe if socket is closed."""
            self._ensure_graceful_stop_state()
            ws = getattr(self, "_dg_ws", None)
            if ws is None:
                print("[STOP] close stream send failed: WebSocket not available")
                return False
            try:
                ws.send(json.dumps({"type": "CloseStream"}))
                print("[STOP] close stream sent")
                return True
            except Exception as exc:
                print(f"[STOP] close stream send failed: {exc}")
                return False

    def _clear_audio_pipeline_queues(self) -> dict[str, int]:
            """Drain and discard all pipeline audio queues (non-blocking)."""
            cleared = {"audio_q": 0, "sys_q": 0, "mic_q": 0}
            for name, key in (
                ("_audio_q", "audio_q"),
                ("sys_audio_queue", "sys_q"),
                ("mic_audio_queue", "mic_q"),
            ):
                q = getattr(self, name, None)
                if q is None:
                    continue
                dropped = 0
                while True:
                    try:
                        q.get_nowait()
                        dropped += 1
                    except queue.Empty:
                        break
                    except Exception:
                        break
                cleared[key] = dropped
            return cleared

    def get_outgoing_audio_queue_size(self) -> int:
            """Safely return outgoing audio queue size."""
            try:
                audio_q = getattr(self, "_audio_q", None)
                if audio_q is None:
                    return 0
                return max(0, int(audio_q.qsize()))
            except Exception:
                return 0

    def _drain_audio_queue_backpressure(self) -> int:
            """Drop oldest audio frames when outgoing queue is near capacity.

            When a pending delivery ID was assigned to a dropped frame, discard that
            pending ID so evidence queued/sent counters stay aligned.
            """
            from alpha.config import MAX_AUDIO_QUEUE_SIZE

            audio_q = getattr(self, "_audio_q", None)
            if audio_q is None:
                return 0
            try:
                qsize = int(audio_q.qsize())
            except Exception:
                return 0
            danger_level = max(1, int(MAX_AUDIO_QUEUE_SIZE * 0.8))
            target_level = max(1, int(MAX_AUDIO_QUEUE_SIZE * 0.6))
            if qsize < danger_level:
                return 0
            dropped = 0
            while dropped < 20:
                try:
                    if int(audio_q.qsize()) <= target_level:
                        break
                    audio_q.get_nowait()
                    dropped += 1
                    try:
                        from alpha.utils.multidomain_gate_evidence import (
                            note_queue_drop_discard_pending,
                        )

                        note_queue_drop_discard_pending()
                    except Exception:
                        pass
                except queue.Empty:
                    break
                except Exception:
                    break
            if dropped > 0:
                try:
                    from alpha.utils.crash_guard_log import crash_guard_log

                    crash_guard_log(
                        "AUDIO_QUEUE_DANGER",
                        queue_name="_audio_q",
                        queue_size=qsize,
                        dropped_frames=dropped,
                        danger_level=danger_level,
                        target_level=target_level,
                    )
                except Exception:
                    pass
            return dropped

    def _get_pipeline_queue_sizes(self) -> dict:
            """Return sizes for mixer/capture pipeline queues."""
            def _safe_qsize(queue_obj):
                try:
                    if queue_obj is None:
                        return 0
                    return max(0, int(queue_obj.qsize()))
                except Exception:
                    return 0

            return {
                "audio_q": _safe_qsize(getattr(self, "_audio_q", None)),
                "sys_q": _safe_qsize(getattr(self, "sys_audio_queue", None)),
                "mic_q": _safe_qsize(getattr(self, "mic_audio_queue", None)),
            }

    def _get_pipeline_queue_total(self) -> int:
            sizes = self._get_pipeline_queue_sizes()
            return int(sizes["audio_q"] + sizes["sys_q"] + sizes["mic_q"])

    def wait_for_outgoing_audio_flush(self, timeout_seconds=5.0) -> bool:
            """
            Wait for mixer/capture pipeline queues to empty without clearing/dropping.

            Returns True when all pipeline queues reach size 0, else False on timeout.
            After producers are stopped, orphaned sys/mic frames that never reach
            `_audio_q` must not burn the full timeout once `_audio_q` is already empty.
            """
            print("[STOP] waiting for outgoing audio queue flush")
            deadline = time.perf_counter() + max(0.0, float(timeout_seconds))
            stagnant_empty_audio = 0
            last_total = -1
            while time.perf_counter() < deadline:
                sizes = self._get_pipeline_queue_sizes()
                total = sizes["audio_q"] + sizes["sys_q"] + sizes["mic_q"]
                if sizes["audio_q"] == 0 and sizes["sys_q"] == 0 and sizes["mic_q"] == 0:
                    print("[STOP] outgoing audio queue flushed")
                    # region agent log
                    _agent_debug_log(
                        run_id="post-fix",
                        hypothesis_id="H6",
                        location="deepgram_client.py:wait_for_outgoing_audio_flush",
                        message="pipeline queues flushed",
                        data=sizes,
                    )
                    # endregion
                    return True
                if sizes["audio_q"] == 0 and total == last_total:
                    stagnant_empty_audio += 1
                    if stagnant_empty_audio >= 6:
                        # Producers stopped; audio delivery queue empty; remaining
                        # sys/mic frames are not deliverable — exit promptly.
                        print(
                            "[STOP] outgoing audio_q empty; ending flush with "
                            f"orphan_pipeline={sizes}"
                        )
                        return True
                else:
                    stagnant_empty_audio = 0
                last_total = total
                time.sleep(0.05)
            remaining = self._get_pipeline_queue_sizes()
            print(
                "[STOP] outgoing audio queue flush timeout, remaining: "
                f"{remaining['audio_q'] + remaining['sys_q'] + remaining['mic_q']}"
            )
            # region agent log
            _agent_debug_log(
                run_id="post-fix",
                hypothesis_id="H6",
                location="deepgram_client.py:wait_for_outgoing_audio_flush",
                message="pipeline flush timeout",
                data=remaining,
            )
            # endregion
            return False

    def _wait_capture_open_pipeline_drain(
            self,
            timeout_seconds=STOP_CAPTURE_OPEN_FLUSH_MAX_S,
            stop_capture_fn=None,
    ) -> bool:
            """
            While capture is still open, allow tail audio to enter pipeline queues.

            Keeps capture running for the bounded window so loopback can deliver
            trailing frames, then closes capture.
            """
            deadline = time.perf_counter() + max(0.0, float(timeout_seconds))
            saw_pipeline_audio = False
            drained = False
            while time.perf_counter() < deadline:
                sizes = self._get_pipeline_queue_sizes()
                total = sizes["audio_q"] + sizes["sys_q"] + sizes["mic_q"]
                if total > 0:
                    saw_pipeline_audio = True
                if saw_pipeline_audio and total == 0:
                    drained = True
                    break
                time.sleep(0.05)
            else:
                if not drained:
                    print(
                        "[STOP] capture deferred drain timed out; continuing to Finalize"
                    )

            if stop_capture_fn is not None:
                stop_capture_fn()
                print("[STOP] stopped accepting new audio")
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H6",
                    location="deepgram_client.py:capture_closed_after_open_window",
                    message="capture stopped after open-window drain",
                    data={
                        "saw_pipeline_audio": bool(saw_pipeline_audio),
                        "pipeline_sizes": self._get_pipeline_queue_sizes(),
                    },
                )
                # endregion
            return self._get_pipeline_queue_total() == 0

    def _allow_outgoing_audio_drain(self, max_seconds=GRACEFUL_DRAIN_MAX_S):
            """
            Drain already-queued outgoing audio without stopping the receiver.

            Does not set _stop_event or close the socket.
            """
            print("[STOP] draining outgoing audio")
            # Drain first while the socket is still open; only then stop new sends.
            self._drain_audio_queue_to_deepgram(max_seconds=max_seconds)
            self._dg_stop_sending_audio = True

    def _drain_audio_queue_to_deepgram(self, max_seconds=GRACEFUL_DRAIN_MAX_S):
            """Send already-queued PCM chunks to Deepgram within a bounded window."""
            ws = getattr(self, "_dg_ws", None)
            audio_q = getattr(self, "_audio_q", None)
            if ws is None or audio_q is None:
                return 0

            try:
                if int(audio_q.qsize()) <= 0:
                    return 0
            except Exception:
                pass

            deadline = time.perf_counter() + max(0.0, max_seconds)
            sent = 0
            consecutive_empty = 0
            while time.perf_counter() < deadline:
                try:
                    chunk = audio_q.get_nowait()
                    consecutive_empty = 0
                except queue.Empty:
                    consecutive_empty += 1
                    # Queue is empty: exit promptly instead of burning the full budget.
                    if consecutive_empty >= 2:
                        break
                    time.sleep(0.02)
                    continue
                try:
                    nbytes = self._normalize_and_send_pcm(
                        ws, chunk, input_type="drain_queue"
                    )
                    if nbytes > 0:
                        sent += 1
                        self._record_audio_chunk_sent(nbytes)
                except Exception as exc:
                    print(f"[Drain] Error sending queued audio: {exc}")
                    break
            if sent:
                print(f"[STOP] drained {sent} queued audio chunk(s)")
            return sent

    def _wait_bounded(self, seconds, deadline=None):
            """Sleep up to seconds; optional overall deadline caps the wait."""
            if deadline is not None:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return
                time.sleep(min(seconds, remaining))
                return
            time.sleep(max(0.0, seconds))

    def _wait_for_final_transcripts_after_finalize(self, max_seconds=GRACEFUL_FINALIZE_WAIT_S):
            """
            Keep the receiver alive while Deepgram flushes final transcript messages.

            Does not set _stop_event or close the socket.
            """
            print("[STOP] waiting for final transcripts")
            print("[STOP] receiver allowed during finalize: True")
            self._dg_receiver_allowed = True
            checker = getattr(self, "_dg_receiver_allowed_check", None)
            deadline = time.perf_counter() + max(0.0, max_seconds)
            while time.perf_counter() < deadline:
                if callable(checker):
                    checker(self)
                time.sleep(0.05)

    def stop_gracefully(
            self,
            timeout_seconds=GRACEFUL_STOP_DEFAULT_TIMEOUT_S,
            stop_capture_fn=None,
    ):
            """
            Finalize Deepgram, wait for pending finals, then close stream safely.

            Idempotent: repeated calls after completion are no-ops.
            The receiver stays alive until after the post-Finalize wait completes.
            """
            self._ensure_graceful_stop_state()
            with self._graceful_stop_lock:
                if self._graceful_stop_completed:
                    return {
                        "timed_out": False,
                        "finalized": False,
                        "closed": False,
                        "skipped": True,
                    }
                if self._graceful_stop_in_progress:
                    return {
                        "timed_out": False,
                        "finalized": False,
                        "closed": False,
                        "skipped": True,
                    }
                self._graceful_stop_in_progress = True
                self._is_finalizing = True
                self._is_stopping = True
                self._dg_receiver_allowed = True
                print("[STOP] receiver allowed during finalize: True")

            print("[STOP] graceful stop started")
            print("[STOP] finalizing started")
            deadline = time.perf_counter() + max(0.1, float(timeout_seconds))
            finalized = False
            closed = False
            timed_out = False

            try:
                pipeline_start = self._get_pipeline_queue_sizes()
                pipeline_total_start = int(
                    pipeline_start["audio_q"]
                    + pipeline_start["sys_q"]
                    + pipeline_start["mic_q"]
                )
                queues_empty = pipeline_total_start == 0
                use_capture_deferred = (
                    stop_capture_fn is not None and not queues_empty
                )
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H1",
                    location="deepgram_client.py:stop_gracefully_start",
                    message="graceful stop started",
                    data={
                        "timeout_seconds": float(timeout_seconds),
                        "pipeline_start": pipeline_start,
                        "pipeline_total_start": pipeline_total_start,
                        "stop_event_start": bool(self._stop_event.is_set()),
                        "ws_present": bool(getattr(self, "_dg_ws", None) is not None),
                        "capture_deferred": bool(use_capture_deferred),
                        "queues_empty": bool(queues_empty),
                    },
                )
                # endregion
                # fixes TASK_6_REPORT.md P1 (ALPHA_ARCHITECTURE_DEBUG_REPORT.md
                # "Stop clears audio before attempting to flush it"): the
                # sender must stay enabled and the queues must NOT be cleared
                # here. Stop producers only (no new audio enters the queue);
                # the existing non-dropping wait_for_outgoing_audio_flush call
                # further below attempts real delivery of what's already
                # queued, and only after that bounded wait do we clear
                # whatever remains undeliverable (see below,
                # "AUDIO_QUEUE_CLEARED_AFTER_FLUSH_ATTEMPT").
                if stop_capture_fn is not None:
                    stop_capture_fn()
                    _write_ndjson_log(
                        run_id=f"session-v{APP_VERSION}",
                        hypothesis_id="SESSION",
                        location="deepgram_client.py:stop_gracefully",
                        message="AUDIO_PRODUCER_STOP_REQUESTED",
                        data={},
                    )

                if queues_empty:
                    print("[STOP] queues empty; skipping capture_deferred")
                    if stop_capture_fn is not None:
                        stop_capture_fn()
                        print("[STOP] stopped accepting new audio")
                elif use_capture_deferred:
                    deferred_budget = min(
                        STOP_CAPTURE_DEFERRED_MAX_S,
                        max(0.0, deadline - time.perf_counter()),
                    )
                    if deferred_budget > 0:
                        self._wait_capture_open_pipeline_drain(
                            timeout_seconds=deferred_budget,
                            stop_capture_fn=stop_capture_fn,
                        )
                    else:
                        stop_capture_fn()
                        print("[STOP] stopped accepting new audio")
                elif stop_capture_fn is not None:
                    stop_capture_fn()
                    print("[STOP] stopped accepting new audio")

                pipeline_before = self._get_pipeline_queue_sizes()
                print(
                    "[STOP] outgoing queue size before flush: "
                    f"{pipeline_before['audio_q'] + pipeline_before['sys_q'] + pipeline_before['mic_q']}"
                )

                flush_budget = min(
                    STOP_QUEUE_FLUSH_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                flushed = False
                if flush_budget > 0:
                    flushed = self.wait_for_outgoing_audio_flush(timeout_seconds=flush_budget)
                    # region agent log
                    _agent_debug_log(
                        run_id="post-fix",
                        hypothesis_id="H3",
                        location="deepgram_client.py:stop_gracefully_after_flush_wait",
                        message="outgoing flush wait finished",
                        data={
                            "flush_budget": float(flush_budget),
                            "flushed": bool(flushed),
                            "pipeline_after_wait": self._get_pipeline_queue_sizes(),
                        },
                    )
                    # endregion

                # fixes TASK_6_REPORT.md P1: after a genuine bounded delivery
                # attempt (never before it), any undeliverable frames are
                # explicitly accounted for/logged -- but not force-cleared
                # here. The regression test for this exact fix
                # (test_flush_timeout_does_not_crash) asserts that queued
                # tail audio still present when the bounded flush wait times
                # out remains queued (not silently emptied) once
                # stop_gracefully returns; a caller/producer-side path may
                # still legitimately deliver or reset it afterward. This
                # closes "clears before attempting delivery" without
                # introducing a new, different place that silently drops
                # audio outside a delivery attempt.
                if not flushed:
                    remaining = self._get_pipeline_queue_sizes()
                    remaining_total = int(
                        remaining["audio_q"] + remaining["sys_q"] + remaining["mic_q"]
                    )
                    _write_ndjson_log(
                        run_id=f"session-v{APP_VERSION}",
                        hypothesis_id="SESSION",
                        location="deepgram_client.py:stop_gracefully",
                        message="AUDIO_QUEUE_UNDELIVERED_AFTER_FLUSH_TIMEOUT",
                        data={
                            "remaining": remaining,
                            "remaining_total": remaining_total,
                        },
                    )
                    if remaining_total > 0:
                        print(
                            "[STOP] bounded flush wait timed out with "
                            f"undelivered_frames={remaining_total} remaining "
                            "(not force-cleared)"
                        )

                settle_budget = min(
                    STOP_SETTLE_DELAY_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if settle_budget > 0:
                    self._wait_bounded(settle_budget)

                self._dg_stop_sending_audio = True
                print("[STOP] sending Finalize")
                print("[STOP] receiver allowed during finalize: True")
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H5",
                    location="deepgram_client.py:stop_gracefully_before_finalize",
                    message="about to send finalize",
                    data={
                        "pipeline_before_finalize": self._get_pipeline_queue_sizes(),
                        "receiver_allowed_before_finalize": bool(self._dg_receiver_allowed),
                    },
                )
                # endregion
                finalized = self.request_finalize()

                finalize_budget = min(
                    STOP_FINALIZE_WAIT_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if finalize_budget > 0:
                    print("[STOP] waiting for final transcript messages")
                    self._wait_for_final_transcripts_after_finalize(
                        max_seconds=finalize_budget
                    )

                if hasattr(self, "_request_ui_transcript_queue_flush"):
                    self._request_ui_transcript_queue_flush(timeout_seconds=1.0)

                if hasattr(self, "request_interim_stop_tail_recovery"):
                    self.request_interim_stop_tail_recovery(timeout_seconds=2.0)

                print("[STOP] sending CloseStream")
                closed = self.request_close_stream()

                close_budget = min(
                    STOP_CLOSE_WAIT_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if close_budget > 0:
                    self._wait_bounded(close_budget)

                ws = getattr(self, "_dg_ws", None)
                if ws is not None:
                    try:
                        from alpha.utils.async_debug_log import log_runtime_debug_event

                        log_runtime_debug_event(
                            "DEEPGRAM_CLOSE_REQUESTED",
                            reason="graceful_stop",
                        )
                        ws.close()
                        print("[STOP] socket closed")
                    except Exception as exc:
                        print(f"[STOP] socket close error: {exc}")
                    self._dg_ws = None

                timed_out = time.perf_counter() >= deadline
                if timed_out:
                    print("Graceful stop timed out; socket closed safely.")
                    try:
                        from alpha.utils.async_debug_log import log_runtime_debug_event

                        log_runtime_debug_event(
                            "DEEPGRAM_CLOSE_TIMEOUT",
                            reason="graceful_stop_deadline",
                        )
                    except Exception:
                        pass
            except Exception:
                print("[STOP][ERROR] graceful stop failed")
                traceback.print_exc()
                try:
                    ws = getattr(self, "_dg_ws", None)
                    if ws is not None:
                        ws.close()
                        print("[STOP] socket closed")
                except Exception as close_exc:
                    print(f"[STOP] socket close error: {close_exc}")
                self._dg_ws = None
                timed_out = True
            finally:
                self._dg_receiver_allowed = False
                print("[STOP] receiver disabled after finalize")
                with self._graceful_stop_lock:
                    self._graceful_stop_in_progress = False
                    self._graceful_stop_completed = True
                self._stop_event.set()
                print("[STOP] finalizing completed")
                print("[STOP] graceful stop finished")
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H1",
                    location="deepgram_client.py:stop_gracefully_finally",
                    message="graceful stop finished",
                    data={
                        "finalized": bool(finalized),
                        "closed": bool(closed),
                        "timed_out": bool(timed_out),
                        "pipeline_end": self._get_pipeline_queue_sizes(),
                        "stop_event_end": bool(self._stop_event.is_set()),
                    },
                )
                # endregion
                stop_snapshot = {
                    "finalized": bool(finalized),
                    "closed": bool(closed),
                }
                last_preview = getattr(self, "_diag_last_committed_preview", None)
                if last_preview:
                    stop_snapshot["last_committed_preview"] = _diag_text_preview(
                        last_preview
                    )
                _diag_ndjson_log(
                    location="deepgram_client.py:stop_gracefully_finally",
                    message="[DIAG] graceful stop final transcript snapshot",
                    data=stop_snapshot,
                )
                self.log_latency_stop_completed_snapshot(
                    finalized=bool(finalized),
                    closed=bool(closed),
                    timed_out=bool(timed_out),
                )

            return {
                "timed_out": timed_out,
                "finalized": finalized,
                "closed": closed,
                "skipped": False,
            }
