"""Japanese-only final-chunk stabilizer (V3.3.5.5.8.5.11.1).

Pass-through to the continuity assembler with transcript acceptance gate.
Blocks stale finals after Stop Listening and rogue direct Japanese commits.
"""

from __future__ import annotations

import threading
from functools import wraps
from typing import Any, Optional

from alpha.constants import (
    FORCE_DEEPGRAM_LANGUAGE,
    JAPANESE_ACCURACY_MODE,
    JAPANESE_CONTINUITY_ASSEMBLER_ENABLED,
    JAPANESE_MODE_ENABLED,
    MEETING_SEGMENT_BUFFER_ENABLED,
    MEETING_SEGMENT_REPAIR_ENABLED,
)
from alpha.transcription.japanese_sentence_assembler import (
    flush_japanese_sentence_assembler,
    get_japanese_continuity_assembler,
    reset_japanese_sentence_assembler,
)
from alpha.utils.cjk_text import (
    cleanup_japanese_per_fragment,
    detect_raw_stt_error_suspected,
)
from alpha.utils.japanese_accuracy_log import (
    jp_accuracy_log,
    log_japanese_accuracy_run_started,
)

JAPANESE_FINAL_STABILIZER_ENABLED = True

_VALID_JP_COMMIT_PREFIXES = (
    "japanese_continuity_assembler_",
    "stop_flush_incomplete_tail",
    "assembler_exception_direct_commit_fallback",
)


def _is_valid_japanese_commit_reason(reason: str) -> bool:
    if not reason:
        return False
    return any(reason.startswith(p) for p in _VALID_JP_COMMIT_PREFIXES)


def should_use_japanese_final_stabilizer(host: Any) -> bool:
    if not JAPANESE_FINAL_STABILIZER_ENABLED or not JAPANESE_MODE_ENABLED:
        return False
    if MEETING_SEGMENT_REPAIR_ENABLED or MEETING_SEGMENT_BUFFER_ENABLED:
        return False
    lang = str(
        getattr(host, "_listen_language", None) or FORCE_DEEPGRAM_LANGUAGE or "ja"
    ).lower()
    if lang == "multi":
        return False
    return lang == "ja" or lang.startswith("ja-")


class JapaneseFinalChunkStabilizer:
    """Japanese Deepgram final ingress — pass-through to continuity assembler."""

    def __init__(self, host: Any):
        self._host = host
        self._lock = threading.Lock()
        self._accepting_transcripts = False

    def set_accepting(self, value: bool) -> None:
        with self._lock:
            old = self._accepting_transcripts
            self._accepting_transcripts = value
        if old != value:
            jp_accuracy_log(
                "accepting_transcripts_changed",
                accepting_transcripts=value,
                was=old,
            )

    def is_accepting(self) -> bool:
        with self._lock:
            return self._accepting_transcripts

    def reset(self) -> None:
        with self._lock:
            self._accepting_transcripts = False

    def active(self) -> bool:
        return should_use_japanese_final_stabilizer(self._host)

    def ingest(
        self,
        speaker: int,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        if not self.active():
            return False
        raw = (text or "").strip()
        if not raw:
            return True

        if not self.is_accepting():
            jp_accuracy_log(
                "STALE_FINAL_DROPPED",
                raw_text=raw,
                reason="not_accepting_transcripts",
                is_listening=bool(getattr(self._host, "is_listening", False)),
                is_stopping=bool(getattr(self._host, "_is_stopping", False)),
                is_stopped=not bool(getattr(self._host, "is_listening", False)),
            )
            return True

        meta = dict(metadata or {})
        meta.setdefault("raw_deepgram_text", raw)

        try:
            from alpha.utils.transcript_evidence import log_raw_deepgram_final

            log_raw_deepgram_final(
                raw_text=raw,
                is_final=True,
                accepted_by_gate=True,
                source="deepgram_final_ingress",
            )
        except Exception:
            pass

        lineage_assignment_failed = False
        try:
            from alpha.utils.accuracy_stage_capture import record_raw_deepgram_final
            from alpha.utils.run_identity import get_run_id

            raw_event_id = record_raw_deepgram_final(
                run_id=get_run_id(),
                speaker=speaker,
                raw_text=raw,
                is_final=True,
                speech_final=meta.get("speech_final"),
                confidence=meta.get("transcript_confidence") or meta.get("confidence"),
                channel=meta.get("channel"),
                metadata=meta,
            )
            if raw_event_id:
                meta["raw_event_id"] = raw_event_id
                meta["source_raw_event_ids"] = [raw_event_id]
            else:
                lineage_assignment_failed = True
            try:
                from alpha.utils.live_runtime_metrics import note_raw_deepgram_final

                note_raw_deepgram_final()
            except Exception as exc:
                jp_accuracy_log(
                    "RAW_EVENT_ID_ASSIGNMENT_FAILED",
                    raw_text=raw[:120],
                    reason=f"runtime_counter:{type(exc).__name__}",
                )
        except Exception as exc:
            lineage_assignment_failed = True
            jp_accuracy_log(
                "RAW_EVENT_ID_ASSIGNMENT_FAILED",
                raw_text=raw[:120],
                reason=f"{type(exc).__name__}:{exc}",
            )
        if lineage_assignment_failed:
            meta["lineage_assignment_failed"] = True
            meta["force_append_only"] = True
            meta.pop("raw_event_id", None)
            meta["source_raw_event_ids"] = []
            jp_accuracy_log(
                "RAW_EVENT_ID_ASSIGNMENT_FAILED",
                raw_text=raw[:120],
                reason="lineage_assignment_failed",
                force_append_only=True,
            )

        if detect_raw_stt_error_suspected(raw):
            jp_accuracy_log("RAW_STT_ERROR_CANDIDATE", raw_text=raw)

        cleaned, cleanup_reason, cleanup_flags = cleanup_japanese_per_fragment(raw)
        jp_accuracy_log(
            "raw_deepgram_final",
            speaker=speaker,
            raw_text=raw,
            raw_japanese_transcript=cleaned,
            cleanup_reason=cleanup_reason,
            stage="deepgram_final_basic_cleanup",
            ready_for_translation=False,
        )
        jp_accuracy_log(
            "per_fragment_cleanup",
            per_fragment_cleanup_input=raw,
            per_fragment_cleanup_output=cleaned,
            cleanup_reason=cleanup_reason,
            exact_duplicate_collapse=cleanup_flags.get("exact_duplicate_collapse", False),
            prefix_extension_duplicate_collapse=cleanup_flags.get(
                "prefix_extension_duplicate_collapse", False
            ),
            latin_acronym_spacing_normalized=cleanup_flags.get(
                "latin_acronym_spacing_normalized", False
            ),
        )

        assembler = get_japanese_continuity_assembler(self._host)
        assembler_metadata = dict(meta)
        assembler_metadata.setdefault("raw_japanese_transcript", cleaned)
        try:
            assembler.ingest(
                speaker,
                cleaned,
                assembler_metadata,
                upstream_reason="deepgram_final",
                already_cleaned=True,
                raw_original=raw,
            )
        except Exception as exc:
            try:
                from alpha.utils.crash_guard_log import log_exception

                log_exception(
                    exc,
                    source="japanese_stabilizer_passthrough",
                    host=self._host,
                )
            except Exception:
                pass
        return True

    def flush(self, reason: str) -> None:
        _ = reason


def get_japanese_final_stabilizer(host: Any) -> JapaneseFinalChunkStabilizer:
    stabilizer = getattr(host, "_jp_final_stabilizer", None)
    if stabilizer is None:
        stabilizer = JapaneseFinalChunkStabilizer(host)
        host._jp_final_stabilizer = stabilizer
    return stabilizer


def is_accepting_japanese_transcripts(host: Any) -> bool:
    return get_japanese_final_stabilizer(host).is_accepting()


def close_japanese_transcript_gate(
    host: Any, log_event: str = "STOP_TRANSCRIPT_GATE_CLOSED"
) -> None:
    stabilizer = get_japanese_final_stabilizer(host)
    stabilizer.set_accepting(False)
    jp_accuracy_log(log_event)


def reset_japanese_final_stabilizer(host: Any) -> None:
    reset_japanese_sentence_assembler(host)
    stabilizer = get_japanese_final_stabilizer(host)
    stabilizer.reset()


def emit_japanese_live_session_summary(
    host: Any,
    *,
    reason: str,
    final_output_tail_preview: str = "",
) -> None:
    assembler = get_japanese_continuity_assembler(host)
    assembler.emit_final_live_session_summary(
        reason=reason,
        final_output_tail_preview=final_output_tail_preview,
    )


def flush_japanese_assembler_on_stop(host: Any, reason: str = "stop_listening") -> None:
    """Flush assembler buffer and emit session summary — background worker only."""
    close_japanese_transcript_gate(host, "STOP_TRANSCRIPT_GATE_CLOSED")
    flush_japanese_sentence_assembler(host, reason)
    assembler = get_japanese_continuity_assembler(host)
    try:
        from alpha.transcription.japanese_boundary_stabilizer import get_boundary_stabilizer

        get_boundary_stabilizer().write_summary()
    except Exception:
        pass
    assembler.emit_final_live_session_summary(reason=reason)
    assembler.drop_quarantine("stop_listening")
    assembler.reset()
    jp_accuracy_log("JAPANESE_BUFFER_CLEARED_ON_STOP")


def flush_japanese_final_stabilizer(host: Any, reason: str = "stop_listening") -> None:
    close_japanese_transcript_gate(host, "STOP_TRANSCRIPT_GATE_CLOSED")

    flush_japanese_sentence_assembler(host, reason)

    assembler = get_japanese_continuity_assembler(host)
    try:
        from alpha.utils.runtime_evidence import (
            emit_long_session_accuracy_summary,
            emit_translation_unit_flushed_summary,
        )

        emit_long_session_accuracy_summary(assembler, reason=reason, host=host)
        emit_translation_unit_flushed_summary(assembler)
    except Exception:
        pass
    assembler.emit_final_live_session_summary(reason=reason)
    assembler.drop_quarantine("stop_listening")
    assembler.reset()
    jp_accuracy_log("JAPANESE_BUFFER_CLEARED_ON_STOP")

    try:
        audio_q = getattr(host, "_audio_q", None)
        if audio_q is not None:
            cleared = 0
            while not audio_q.empty():
                try:
                    audio_q.get_nowait()
                    cleared += 1
                except Exception:
                    break
            if cleared > 0:
                jp_accuracy_log("AUDIO_QUEUE_CLEARED_ON_STOP", cleared_frames=cleared)
    except Exception:
        pass


def block_rogue_japanese_direct_commit(
    host: Any, queue_item: dict[str, Any]
) -> bool:
    """Return True if the item should be BLOCKED from UI."""
    if not JAPANESE_CONTINUITY_ASSEMBLER_ENABLED:
        return False
    if not should_use_japanese_final_stabilizer(host):
        return False
    if not (queue_item or {}).get("is_final"):
        return False
    if queue_item.get("_jp_continuity_assembler"):
        return False
    reason = str(
        queue_item.get("stabilizer_reason")
        or queue_item.get("commit_reason")
        or queue_item.get("assembler_reason")
        or ""
    )
    if _is_valid_japanese_commit_reason(reason):
        return False
    jp_accuracy_log(
        "ROGUE_DIRECT_JAPANESE_COMMIT_BLOCKED",
        text_preview=(queue_item.get("text") or "")[:80],
        reason=reason or "no_assembler_metadata",
    )
    return True


def install_japanese_stabilizer_hooks(app_cls: type) -> None:
    if not JAPANESE_FINAL_STABILIZER_ENABLED:
        return

    _orig_start_worker = app_cls._start_listening_worker

    @wraps(_orig_start_worker)
    def patched_start_worker(self, *args, **kwargs):
        reset_japanese_final_stabilizer(self)
        selected_language = (
            args[1]
            if len(args) > 1 and isinstance(args[1], str)
            else getattr(self, "_listen_language", None)
            or FORCE_DEEPGRAM_LANGUAGE
            or "ja"
        )
        log_japanese_accuracy_run_started(str(selected_language))
        try:
            from alpha.utils.run_artifacts import (
                create_initial_run_artifacts_index,
                reset_run_artifacts_session,
            )
            from alpha.utils.run_identity import init_live_run_from_host
            from alpha.utils.runtime_evidence import reset_runtime_evidence_session

            reset_run_artifacts_session()
            reset_runtime_evidence_session()
            identity = init_live_run_from_host(self)
            create_initial_run_artifacts_index(identity=identity, host=self)
            try:
                from alpha.utils.diagnostic_test_log import write_diagnostic_run_header

                write_diagnostic_run_header(identity=identity)
            except Exception:
                pass
        except Exception:
            pass
        if JAPANESE_ACCURACY_MODE:
            jp_accuracy_log(
                "JAPANESE_ACCURACY_MODE_ENABLED",
                stable_over_speed=True,
                raw_transcript_debug_available=True,
                translation_layer_active=False,
            )
        try:
            from alpha.utils.audio_temp_capture import cleanup_old_audio_temp

            cleanup_old_audio_temp(reason="start_listening")
        except Exception:
            pass
        return _orig_start_worker(self, *args, **kwargs)

    app_cls._start_listening_worker = patched_start_worker  # type: ignore[method-assign]

    # Stop finalization is handled by stop_finalize_worker (non-blocking).
    # Hooks below only guard rogue commits and copy-live summary.

    if hasattr(app_cls, "_stop_listening_immediate"):
        _orig_immediate_stop = app_cls._stop_listening_immediate

        @wraps(_orig_immediate_stop)
        def patched_immediate_stop(self, *args, **kwargs):
            close_japanese_transcript_gate(
                self, "STOP_TRANSCRIPT_GATE_CLOSED_IMMEDIATE"
            )
            return _orig_immediate_stop(self, *args, **kwargs)

        app_cls._stop_listening_immediate = patched_immediate_stop  # type: ignore[method-assign]

    if hasattr(app_cls, "_publish_final_transcript_segment"):
        _orig_publish = app_cls._publish_final_transcript_segment

        @wraps(_orig_publish)
        def patched_publish(self, speaker_num, segment_text, metadata=None,
                            queue_item=None, commit_reason=None, **kw):
            candidate_queue_item = dict(queue_item or {})
            candidate_queue_item.setdefault("speaker", speaker_num)
            candidate_queue_item.setdefault("text", segment_text)
            candidate_queue_item.setdefault("is_final", True)
            if metadata:
                candidate_queue_item.update(metadata)
            if commit_reason:
                candidate_queue_item.setdefault("commit_reason", commit_reason)
                candidate_queue_item.setdefault("stabilizer_reason", commit_reason)
            if block_rogue_japanese_direct_commit(self, candidate_queue_item):
                return False
            return _orig_publish(
                self, speaker_num, segment_text,
                metadata=metadata, queue_item=queue_item,
                commit_reason=commit_reason, **kw,
            )

        app_cls._publish_final_transcript_segment = patched_publish  # type: ignore[method-assign]

    if hasattr(app_cls, "copy_live_transcript_to_clipboard"):
        _orig_copy_live = app_cls.copy_live_transcript_to_clipboard

        @wraps(_orig_copy_live)
        def patched_copy_live(self, *args, **kwargs):
            clean_text = ""
            try:
                getter = getattr(self, "_get_clean_transcript_for_copy_export", None)
                if callable(getter):
                    clean_text = str(getter() or "")
            except Exception:
                clean_text = ""
            emit_japanese_live_session_summary(
                self,
                reason="copy_live_transcript",
                final_output_tail_preview=clean_text[-220:] if clean_text else "",
            )
            return _orig_copy_live(self, *args, **kwargs)

        app_cls.copy_live_transcript_to_clipboard = patched_copy_live  # type: ignore[method-assign]
