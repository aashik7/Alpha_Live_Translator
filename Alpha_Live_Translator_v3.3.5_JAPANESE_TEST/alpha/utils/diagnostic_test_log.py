"""V3.3.5.5.6 temporary diagnostic test logger — file only, no console spam."""

from __future__ import annotations

import atexit
import json
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Test mode flags — set DIAGNOSTIC_LOGGING = False to disable entirely
# ---------------------------------------------------------------------------
DIAGNOSTIC_LOGGING = True
DEBUG_FULL_TEXT_LOGGING = False

_SLOW_MS = 50.0
_LOG_THROTTLE_MS = 2000
_MAJOR_AFTER_LOOPS = frozenset(
    {
        "_run_ui_queue_tick",
        "_apply_responsive_layout_debounced",
        "_set_initial_pane_ratio",
        "_deferred_post_show_init",
        "_emit_deferred_startup_logs",
        "_schedule_ui_queue_tick",
        "_ui_lag_monitor_tick",
        "_apply_waveform_layout_debounced",
        "_animate_waveform",
        "_animate_live_pulse",
        "_update_timer",
        "_flush_pending_interim_ui",
        "_deferred_apply_logo",
        "_health_monitor",
        "_finish_start_listening",
        "_finish_graceful_stop",
    }
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_log_queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=5000)
_writer_started = False
_writer_thread: Optional[threading.Thread] = None
_app_start_mono: Optional[float] = None
_first_render_logged = False
_hooks_installed = False
_state_lock = threading.Lock()
_runtime_flags = {
    "first_audio_frame": False,
    "first_deepgram_message": False,
    "first_transcript": False,
}
_throttle_at: dict[str, float] = {}


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _elapsed_ms_since_app_start() -> Optional[float]:
    if _app_start_mono is None:
        return None
    return round((time.perf_counter() - _app_start_mono) * 1000, 2)


def _should_throttle(key: str, interval_ms: int = _LOG_THROTTLE_MS) -> bool:
    now = time.perf_counter()
    last = _throttle_at.get(key)
    if last is not None and (now - last) * 1000 < interval_ms:
        return True
    _throttle_at[key] = now
    return False


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str) and not DEBUG_FULL_TEXT_LOGGING:
            lower = key.lower()
            if "text" in lower or "preview" in lower or "transcript" in lower:
                safe[key] = value[:120] + ("…" if len(value) > 120 else "")
                continue
        safe[key] = value
    return safe


def _format_line(category: str, event: str, data: Optional[dict[str, Any]] = None) -> str:
    payload: dict[str, Any] = {"event": event}
    elapsed = _elapsed_ms_since_app_start()
    if elapsed is not None:
        payload["elapsed_ms_since_app_start"] = elapsed
    if data:
        payload.update(_sanitize_payload(data))
    return f"{_timestamp()} | {category} | {json.dumps(payload, ensure_ascii=False, default=str)}"


def _enqueue_line(line: str) -> None:
    if not DIAGNOSTIC_LOGGING:
        return
    try:
        _log_queue.put_nowait(line)
    except queue.Full:
        if not _should_throttle("log_queue_overflow", 5000):
            try:
                _log_queue.get_nowait()
                _log_queue.put_nowait(line)
            except Exception:
                pass


def diag_log(category: str, event: str, data: Optional[dict[str, Any]] = None) -> None:
    """Non-blocking diagnostic log write."""
    if not DIAGNOSTIC_LOGGING:
        return
    _enqueue_line(_format_line(category, event, data))


def diag_log_throttled(
    key: str,
    category: str,
    event: str,
    data: Optional[dict[str, Any]] = None,
    interval_ms: int = _LOG_THROTTLE_MS,
) -> None:
    if not DIAGNOSTIC_LOGGING or _should_throttle(key, interval_ms):
        return
    diag_log(category, event, data)


def diag_log_exception(category: str, event: str, exc: BaseException) -> None:
    if not DIAGNOSTIC_LOGGING:
        return
    diag_log(
        category,
        event,
        {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "stack_trace": traceback.format_exc(),
        },
    )


def write_diagnostic_run_header(*, identity: Any = None) -> None:
    """Write strong run marker at session start for consistency checks."""
    if not DIAGNOSTIC_LOGGING:
        return
    try:
        from alpha.constants import (
            APP_CODENAME,
            APP_VERSION,
            DEEPGRAM_ENDPOINTING_MS,
            DEEPGRAM_LANGUAGE,
            DEEPGRAM_MODEL,
            DEEPGRAM_UTTERANCE_END_MS,
            JAPANESE_ACCURACY_MODE,
            JAPANESE_KEYTERM_PROFILE,
            JAPANESE_STT_PROFILE,
            UI_PERFORMANCE_MODE,
        )
        from alpha.utils.run_identity import get_current_run_identity

        identity = identity or get_current_run_identity()
        payload = {
            "app_version": APP_VERSION,
            "app_codename": APP_CODENAME,
            "run_id": getattr(identity, "run_id", ""),
            "run_timestamp": getattr(identity, "run_timestamp", ""),
            "run_type": getattr(identity, "run_type", ""),
            "selected_language": getattr(identity, "selected_language", "ja"),
            "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
            "JAPANESE_KEYTERM_PROFILE": JAPANESE_KEYTERM_PROFILE,
            "japanese_accuracy_mode": JAPANESE_ACCURACY_MODE,
            "ui_performance_mode": UI_PERFORMANCE_MODE,
            "deepgram_model": DEEPGRAM_MODEL,
            "deepgram_language": DEEPGRAM_LANGUAGE,
            "endpointing_ms": DEEPGRAM_ENDPOINTING_MS,
            "utterance_end_ms": DEEPGRAM_UTTERANCE_END_MS,
            "diarize_model_absent": True,
            "deepL_active": False,
            "translation_layer_active": False,
        }
        diag_log("session", "DIAGNOSTIC_RUN_HEADER", payload)
    except Exception:
        pass


def _resolve_log_file() -> Path:
    from alpha.utils.troubleshooting_paths import get_log_path

    return get_log_path("diagnostic_test")


def _writer_loop() -> None:
    current_path: Optional[Path] = None
    handle = None
    while True:
        line = _log_queue.get()
        if line is None:
            break
        target = _resolve_log_file()
        if target != current_path:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = open(target, "a", encoding="utf-8")
            current_path = target
        if handle is not None:
            handle.write(line + "\n")
            handle.flush()


def rebind_runtime_log_writer() -> None:
    """Force writer to reopen at the active run folder path on next line."""
    pass


def _start_writer() -> None:
    global _writer_started, _writer_thread
    if _writer_started or not DIAGNOSTIC_LOGGING:
        return
    _writer_started = True
    _writer_thread = threading.Thread(
        target=_writer_loop, name="DiagnosticLogWriter", daemon=True
    )
    _writer_thread.start()
    atexit.register(shutdown_diagnostic_logging)


def shutdown_diagnostic_logging() -> None:
    if not _writer_started:
        return
    try:
        _log_queue.put_nowait(None)
    except Exception:
        pass
    if _writer_thread is not None:
        _writer_thread.join(timeout=2.0)


def diag_init() -> None:
    """Call once at process entry before UI creation."""
    global _app_start_mono
    if not DIAGNOSTIC_LOGGING:
        return
    _app_start_mono = time.perf_counter()
    _start_writer()
    diag_log("startup", "app_start", {"log_file": str(_resolve_log_file())})


def diag_log_startup_safety_flags() -> None:
    """Log Japanese safety and English lock confirmations once."""
    if not DIAGNOSTIC_LOGGING:
        return
    try:
        from alpha.constants import (
            AUTO_LANGUAGE_ENABLED,
            FORCE_DEEPGRAM_LANGUAGE,
            LANGUAGE_GATE_ENABLED,
            MEETING_SEGMENT_REPAIR_ENABLED,
        )
    except Exception as exc:
        diag_log_exception("startup", "japanese_safety_flags_read_failed", exc)
        return

    english_lock_path = _PROJECT_ROOT.parent / "Alpha_Live_Translator_v3.3"
    diag_log(
        "startup",
        "japanese_safety_flags",
        {
            "language_ja_confirmed": str(FORCE_DEEPGRAM_LANGUAGE) == "ja",
            "force_deepgram_language": FORCE_DEEPGRAM_LANGUAGE,
            "auto_language_disabled_confirmed": AUTO_LANGUAGE_ENABLED is False,
            "language_multi_disabled_confirmed": True,
            "language_gate_disabled_confirmed": LANGUAGE_GATE_ENABLED is False,
            "multilingual_segment_repair_disabled_confirmed": (
                MEETING_SEGMENT_REPAIR_ENABLED is False
            ),
            "english_lock_folder_untouched_confirmed": english_lock_path.is_dir(),
            "english_lock_folder_path": str(english_lock_path),
        },
    )


def diag_mark_first_ui_render() -> None:
    global _first_render_logged
    if not DIAGNOSTIC_LOGGING or _first_render_logged:
        return
    _first_render_logged = True
    total_ms = _elapsed_ms_since_app_start()
    diag_log(
        "startup",
        "first_ui_render_completed",
        {"total_ms_until_ui_visible": total_ms},
    )


def _wrap_timed_method(
    method_name: str,
    category: str = "ui_loop",
    slow_only: bool = True,
):
    def decorator(orig: Callable) -> Callable:
        @wraps(orig)
        def wrapped(self, *args, **kwargs):
            if not DIAGNOSTIC_LOGGING:
                return orig(self, *args, **kwargs)
            start = time.perf_counter()
            try:
                return orig(self, *args, **kwargs)
            except Exception as exc:
                diag_log_exception(category, f"{method_name}_error", exc)
                raise
            finally:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                if not slow_only or elapsed_ms > _SLOW_MS:
                    diag_log(
                        category,
                        f"{method_name}_slow" if elapsed_ms > _SLOW_MS else method_name,
                        {"duration_ms": elapsed_ms, "slow_threshold_ms": _SLOW_MS},
                    )

        return wrapped

    return decorator


def _mark_first_audio_frame(source: str) -> None:
    if not DIAGNOSTIC_LOGGING:
        return
    with _state_lock:
        if not _runtime_flags["first_audio_frame"]:
            _runtime_flags["first_audio_frame"] = True
            diag_log("start_listening", "first_audio_frame_time", {"source": source})


def _attach_queue_monitors(app: Any) -> None:
    """Detect queue overflow/drops without changing queue logic."""
    import queue as queue_mod

    for name in ("_audio_q", "sys_audio_queue", "mic_audio_queue", "transcript_queue"):
        q = getattr(app, name, None)
        if q is None or getattr(q, "_diag_wrapped", False):
            continue

        orig_put = q.put

        def put_wrapper(item, block=True, timeout=None, *, __q=q, __name=name, __orig=orig_put):
            try:
                return __orig(item, block=block, timeout=timeout)
            except queue_mod.Full:
                diag_log_throttled(
                    f"{__name}_overflow",
                    "audio" if "audio" in __name or __name.startswith("_audio") else "ui_queue",
                    "queue_overflow",
                    {"queue_name": __name, "queue_size": __q.qsize()},
                    interval_ms=3000,
                )
                raise

        q.put = put_wrapper  # type: ignore[method-assign]
        q._diag_wrapped = True


def _probe_cjk_cleanup_steps(app: Any, text: str) -> dict[str, bool]:
    """Read-only probe on a copy — does not alter transcription pipeline."""
    from alpha.constants import (
        CJK_BOUNDARY_PUNCTUATION_FIX_ENABLED,
        CJK_LOCAL_REPEAT_FIX_ENABLED,
        CJK_PREFIX_OVERLAP_FIX_ENABLED,
        JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED,
        JAPANESE_PARTIAL_OVERLAP_REMOVAL_ENABLED,
        JAPANESE_TEXT_NORMALIZATION_ENABLED,
    )
    from alpha.utils.cjk_text import (
        fix_cjk_boundary_punctuation_with_log,
        normalize_cjk_spacing,
        remove_cjk_local_repeats,
        remove_cjk_prefix_overlap,
    )

    segment = (text or "").strip()
    probes: dict[str, bool] = {
        "spacing_normalization_ran": False,
        "repeated_fragment_cleanup_ran": False,
        "overlap_prefix_cleanup_ran": False,
    }
    if not segment:
        return probes

    lang = "ja"
    if hasattr(app, "_cjk_language_code"):
        try:
            lang = app._cjk_language_code()
        except Exception:
            pass

    s = segment
    if JAPANESE_TEXT_NORMALIZATION_ENABLED:
        n = normalize_cjk_spacing(s)
        probes["spacing_normalization_ran"] = n != s
        s = n
    if JAPANESE_INTERNAL_REPEAT_REMOVAL_ENABLED and hasattr(
        app, "_remove_internal_japanese_repeat"
    ):
        n = app._remove_internal_japanese_repeat(s)
        if hasattr(app, "_remove_short_internal_japanese_repeat"):
            n = app._remove_short_internal_japanese_repeat(n)
        if n != s:
            probes["repeated_fragment_cleanup_ran"] = True
        s = n
    if CJK_LOCAL_REPEAT_FIX_ENABLED:
        n = remove_cjk_local_repeats(s, lang, None)
        if n != s:
            probes["repeated_fragment_cleanup_ran"] = True
        s = n
    if CJK_PREFIX_OVERLAP_FIX_ENABLED:
        n = remove_cjk_prefix_overlap(s, lang, None)
        if n != s:
            probes["overlap_prefix_cleanup_ran"] = True
        s = n
    elif hasattr(app, "_remove_japanese_prefix_repeat"):
        n = app._remove_japanese_prefix_repeat(s)
        if n != s:
            probes["overlap_prefix_cleanup_ran"] = True
        s = n
    if JAPANESE_PARTIAL_OVERLAP_REMOVAL_ENABLED and hasattr(
        app, "_remove_japanese_partial_overlap_repeat"
    ):
        n = app._remove_japanese_partial_overlap_repeat(s)
        if n != s:
            probes["overlap_prefix_cleanup_ran"] = True
        s = n
    if CJK_BOUNDARY_PUNCTUATION_FIX_ENABLED:
        n = fix_cjk_boundary_punctuation_with_log(s, lang, None)
        if n != s:
            probes["overlap_prefix_cleanup_ran"] = True
    return probes


def install_diagnostic_hooks(app_cls: type) -> None:
    """Monkey-patch AlphaApp/mixins for diagnostic capture without changing logic."""
    global _hooks_installed
    if not DIAGNOSTIC_LOGGING or _hooks_installed:
        return
    _hooks_installed = True

    # --- after() scheduling visibility ---
    _orig_after = app_cls.after

    def patched_after(self, ms, func=None, *args, **kwargs):
        if DIAGNOSTIC_LOGGING and func is not None:
            from alpha.constants import DEBUG_AFTER_LOOP_VERBOSE

            loop_name = getattr(func, "__name__", repr(func))
            if DEBUG_AFTER_LOOP_VERBOSE and (
                loop_name in _MAJOR_AFTER_LOOPS or (isinstance(ms, int) and ms >= 100)
            ):
                diag_log_throttled(
                    f"after_schedule:{loop_name}",
                    "ui_loop",
                    "after_loop_scheduled",
                    {"loop_name": loop_name, "interval_ms": ms},
                    interval_ms=5000,
                )
        return _orig_after(self, ms, func, *args, **kwargs)

    app_cls.after = patched_after  # type: ignore[method-assign]

    # --- UI queue ---
    app_cls._run_ui_queue_tick = _wrap_timed_method(  # type: ignore[method-assign]
        "_run_ui_queue_tick", slow_only=True
    )(app_cls._run_ui_queue_tick)

    _orig_process_queue = app_cls._process_ui_queue_once

    @wraps(_orig_process_queue)
    def patched_process_queue(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_process_queue(self, *args, **kwargs)
        start = time.perf_counter()
        queued_before = self.transcript_queue.qsize()
        try:
            return _orig_process_queue(self, *args, **kwargs)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            queued_after = self.transcript_queue.qsize()
            deferred = queued_after
            if elapsed_ms > _SLOW_MS or deferred > 0:
                data = {
                    "duration_ms": elapsed_ms,
                    "queued_before": queued_before,
                    "queued_after": queued_after,
                    "deferred_items": deferred,
                    "batch_limit_reached": deferred > 0,
                }
                if deferred > 0:
                    diag_log("ui_queue", "ui_queue_batch_limit_deferred", data)
                if elapsed_ms > _SLOW_MS:
                    diag_log("ui_queue", "ui_queue_drain_slow", data)
                    try:
                        from alpha.utils.runtime_evidence import get_ui_performance_counters

                        perf = get_ui_performance_counters()
                        perf.ui_queue_drain_slow_count += 1
                        perf.ui_queue_tick_slow_count += 1
                        perf.max_ui_queue_tick_ms = max(
                            perf.max_ui_queue_tick_ms, float(elapsed_ms)
                        )
                        perf.max_ui_queue_depth = max(
                            perf.max_ui_queue_depth, int(queued_before)
                        )
                    except Exception:
                        pass

    app_cls._process_ui_queue_once = patched_process_queue  # type: ignore[method-assign]

    # --- Transcript display ---
    _orig_display = app_cls._display_transcript_item

    @wraps(_orig_display)
    def patched_display(self, item, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_display(self, item, *args, **kwargs)
        start = time.perf_counter()
        is_final = bool((item or {}).get("is_final", True))
        try:
            return _orig_display(self, item, *args, **kwargs)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            if elapsed_ms > _SLOW_MS:
                diag_log(
                    "ui_transcript",
                    "transcript_ui_insert_slow",
                    {
                        "duration_ms": elapsed_ms,
                        "is_final": is_final,
                        "text_len": len((item or {}).get("text") or ""),
                    },
                )
                try:
                    from alpha.utils.runtime_evidence import get_ui_performance_counters

                    perf = get_ui_performance_counters()
                    perf.transcript_ui_insert_slow_count += 1
                    perf.max_transcript_insert_ms = max(
                        perf.max_transcript_insert_ms, float(elapsed_ms)
                    )
                except Exception:
                    pass
            if is_final:
                with _state_lock:
                    if not _runtime_flags["first_transcript"]:
                        _runtime_flags["first_transcript"] = True
                        diag_log(
                            "start_listening",
                            "first_transcript_time",
                            {"duration_ms": elapsed_ms},
                        )

    app_cls._display_transcript_item = patched_display  # type: ignore[method-assign]

    # --- CJK cleanup (final segments only) ---
    _orig_cjk_timed = app_cls._apply_japanese_final_cleanup_timed

    @wraps(_orig_cjk_timed)
    def patched_cjk_timed(self, text, source="ui"):
        if not DIAGNOSTIC_LOGGING:
            return _orig_cjk_timed(self, text, source=source)
        if source not in ("stt_worker", "ui_commit"):
            return _orig_cjk_timed(self, text, source=source)
        inp = (text or "").strip()
        start = time.perf_counter()
        try:
            out = _orig_cjk_timed(self, text, source=source)
        except Exception as exc:
            diag_log_exception("cjk_cleanup", "cjk_cleanup_error", exc)
            raise
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        probes = _probe_cjk_cleanup_steps(self, inp)
        payload = {
            "source": source,
            "input_length": len(inp),
            "output_length": len((out or "").strip()),
            "cleanup_duration_ms": elapsed_ms,
            **probes,
        }
        if DEBUG_FULL_TEXT_LOGGING:
            payload["input_preview"] = inp
            payload["output_preview"] = (out or "").strip()
        diag_log("cjk_cleanup", "final_segment_cleanup", payload)
        return out

    app_cls._apply_japanese_final_cleanup_timed = patched_cjk_timed  # type: ignore[method-assign]

    # --- Start listening flow ---
    _orig_toggle = app_cls.toggle_listening

    @wraps(_orig_toggle)
    def patched_toggle(self, *args, **kwargs):
        if DIAGNOSTIC_LOGGING and not getattr(self, "is_listening", False):
            diag_log("start_listening", "start_button_clicked")
        return _orig_toggle(self, *args, **kwargs)

    app_cls.toggle_listening = patched_toggle  # type: ignore[method-assign]

    _orig_start = app_cls._start_listening

    @wraps(_orig_start)
    def patched_start(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_start(self, *args, **kwargs)
        diag_log("start_listening", "start_callback_enter")
        start = time.perf_counter()
        try:
            return _orig_start(self, *args, **kwargs)
        finally:
            diag_log(
                "start_listening",
                "start_callback_exit",
                {"duration_ms": round((time.perf_counter() - start) * 1000, 2)},
            )

    app_cls._start_listening = patched_start  # type: ignore[method-assign]

    _orig_worker = app_cls._start_listening_worker

    @wraps(_orig_worker)
    def patched_worker(self, dropdown_lang, deepgram_lang, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_worker(self, dropdown_lang, deepgram_lang, *args, **kwargs)
        diag_log("start_listening", "background_start_thread_begin")
        try:
            result = _orig_worker(self, dropdown_lang, deepgram_lang, *args, **kwargs)
            _attach_queue_monitors(self)
            return result
        except Exception as exc:
            diag_log_exception("start_listening", "background_start_thread_error", exc)
            raise

    app_cls._start_listening_worker = patched_worker  # type: ignore[method-assign]

    # --- Audio capture ---
    _orig_wasapi = app_cls._start_wasapi_loopback

    @wraps(_orig_wasapi)
    def patched_wasapi(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_wasapi(self, *args, **kwargs)
        diag_log("audio", "wasapi_capture_init_begin")
        start = time.perf_counter()
        try:
            result = _orig_wasapi(self, *args, **kwargs)
            device_name = getattr(self, "_diag_wasapi_device_name", "unknown")
            diag_log(
                "audio",
                "wasapi_capture_init_end",
                {
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "sample_rate": getattr(self, "_wasapi_rate", None),
                    "channels": getattr(self, "_wasapi_channels", None),
                    "selected_wasapi_device": device_name,
                },
            )
            _log_deepgram_audio_config(self)
            return result
        except Exception as exc:
            diag_log(
                "audio",
                "wasapi_capture_init_end",
                {
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "error": str(exc),
                },
            )
            diag_log_exception("audio", "wasapi_capture_init_error", exc)
            raise

    app_cls._start_wasapi_loopback = patched_wasapi  # type: ignore[method-assign]

    _orig_mic = app_cls._start_microphone_capture

    @wraps(_orig_mic)
    def patched_mic(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_mic(self, *args, **kwargs)
        diag_log("audio", "mic_capture_init_begin")
        start = time.perf_counter()
        try:
            result = _orig_mic(self, *args, **kwargs)
            device_name = "unknown"
            try:
                from alpha.audio.microphone import _import_sounddevice as _isd

                sd = _isd()
                device = sd.default.device[0]
                device_name = sd.query_devices(device).get("name", "unknown")
            except Exception:
                pass
            from alpha.config import DEEPGRAM_SAMPLE_RATE

            diag_log(
                "audio",
                "mic_capture_init_end",
                {
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "selected_mic_device": device_name,
                    "sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                    "channels": 1,
                },
            )
            return result
        except Exception as exc:
            diag_log(
                "audio",
                "mic_capture_init_end",
                {
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "error": str(exc),
                },
            )
            diag_log_exception("audio", "mic_capture_init_error", exc)
            raise

    app_cls._start_microphone_capture = patched_mic  # type: ignore[method-assign]

    _orig_wasapi_inner = app_cls._get_wasapi_loopback_device

    @wraps(_orig_wasapi_inner)
    def patched_wasapi_scan(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_wasapi_inner(self, *args, **kwargs)
        diag_log("audio", "audio_device_scan_begin")
        start = time.perf_counter()
        try:
            result = _orig_wasapi_inner(self, *args, **kwargs)
            loopback = result[1] if isinstance(result, tuple) and len(result) > 1 else {}
            self._diag_wasapi_device_name = loopback.get("name", "unknown")
            diag_log(
                "audio",
                "audio_device_scan_end",
                {
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "selected_wasapi_device": self._diag_wasapi_device_name,
                },
            )
            return result
        except Exception as exc:
            diag_log(
                "audio",
                "audio_device_scan_end",
                {
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "error": str(exc),
                },
            )
            raise

    if hasattr(app_cls, "_get_wasapi_loopback_device"):
        app_cls._get_wasapi_loopback_device = patched_wasapi_scan  # type: ignore[method-assign]

    # --- Deepgram ---
    _orig_dg_worker = app_cls._deepgram_worker

    @wraps(_orig_dg_worker)
    def patched_dg_worker(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_dg_worker(self, *args, **kwargs)
        diag_log("deepgram", "deepgram_connect_begin")
        try:
            from alpha.utils.async_debug_log import log_runtime_debug_event

            log_runtime_debug_event("DEEPGRAM_CONNECT_BEGIN")
        except Exception:
            pass
        _log_deepgram_audio_config(self)
        try:
            return _orig_dg_worker(self, *args, **kwargs)
        except Exception as exc:
            diag_log_exception("deepgram", "deepgram_connect_error", exc)
            raise

    app_cls._deepgram_worker = patched_dg_worker  # type: ignore[method-assign]

    _orig_dg_open = app_cls._deepgram_on_open

    @wraps(_orig_dg_open)
    def patched_dg_open(self, ws, *args, **kwargs):
        if DIAGNOSTIC_LOGGING:
            diag_log("deepgram", "deepgram_connect_end", {"connected": True})
            try:
                from alpha.utils.async_debug_log import log_runtime_debug_event

                log_runtime_debug_event("DEEPGRAM_CONNECT_END", connected=True)
            except Exception:
                pass
        return _orig_dg_open(self, ws, *args, **kwargs)

    app_cls._deepgram_on_open = patched_dg_open  # type: ignore[method-assign]

    _orig_dg_msg = app_cls._deepgram_on_message

    @wraps(_orig_dg_msg)
    def patched_dg_msg(self, ws, message, *args, **kwargs):
        if DIAGNOSTIC_LOGGING:
            with _state_lock:
                if not _runtime_flags["first_deepgram_message"]:
                    _runtime_flags["first_deepgram_message"] = True
                    diag_log("start_listening", "first_deepgram_message_time")
        try:
            return _orig_dg_msg(self, ws, message, *args, **kwargs)
        except Exception as exc:
            diag_log_exception("deepgram", "deepgram_message_error", exc)
            raise

    app_cls._deepgram_on_message = patched_dg_msg  # type: ignore[method-assign]

    for err_name in ("_deepgram_on_error", "_deepgram_on_close"):
        if hasattr(app_cls, err_name):
            _orig_err = getattr(app_cls, err_name)

            @wraps(_orig_err)
            def patched_err(self, *a, __orig=_orig_err, __name=err_name, **kw):
                if DIAGNOSTIC_LOGGING:
                    diag_log_throttled(
                        f"dg_{__name}",
                        "deepgram",
                        __name,
                        {"args_preview": str(a)[:200]},
                    )
                try:
                    return __orig(self, *a, **kw)
                except Exception as exc:
                    diag_log_exception("deepgram", f"{__name}_error", exc)
                    raise

            setattr(app_cls, err_name, patched_err)

    if hasattr(app_cls, "_schedule_reconnect"):
        _orig_reconnect_sched = app_cls._schedule_reconnect

        @wraps(_orig_reconnect_sched)
        def patched_reconnect_sched(self, *args, **kwargs):
            if DIAGNOSTIC_LOGGING:
                diag_log_throttled(
                    "deepgram_reconnect",
                    "deepgram",
                    "deepgram_reconnect_scheduled",
                    {},
                )
            return _orig_reconnect_sched(self, *args, **kwargs)

        app_cls._schedule_reconnect = patched_reconnect_sched  # type: ignore[method-assign]

    # --- First audio frame + mixer errors ---
    _orig_mixer = app_cls.audio_mixer_worker

    @wraps(_orig_mixer)
    def patched_mixer(self, *args, **kwargs):
        if not DIAGNOSTIC_LOGGING:
            return _orig_mixer(self, *args, **kwargs)
        try:
            return _orig_mixer(self, *args, **kwargs)
        except Exception as exc:
            diag_log_exception("audio", "audio_mixer_error", exc)
            raise

    app_cls.audio_mixer_worker = patched_mixer  # type: ignore[method-assign]

    if hasattr(app_cls, "_normalize_and_send_pcm"):
        _orig_send_pcm = app_cls._normalize_and_send_pcm

        @wraps(_orig_send_pcm)
        def patched_send_pcm(self, ws, chunk, *args, **kwargs):
            result = _orig_send_pcm(self, ws, chunk, *args, **kwargs)
            if DIAGNOSTIC_LOGGING and result and int(result) > 0:
                _mark_first_audio_frame("normalize_and_send_pcm")
            return result

        app_cls._normalize_and_send_pcm = patched_send_pcm  # type: ignore[method-assign]

    _orig_mic_cb = getattr(app_cls, "_mic_callback", None)
    if _orig_mic_cb is not None:

        @wraps(_orig_mic_cb)
        def patched_mic_cb(self, *a, **kw):
            if DIAGNOSTIC_LOGGING:
                _mark_first_audio_frame("microphone_callback")
            return _orig_mic_cb(self, *a, **kw)

        app_cls._mic_callback = patched_mic_cb  # type: ignore[method-assign]

    # Global exception hook
    _orig_excepthook = sys.excepthook

    def diagnostic_excepthook(exc_type, exc, tb):
        if DIAGNOSTIC_LOGGING and exc is not None:
            diag_log(
                "error",
                "unhandled_exception",
                {
                    "error_type": getattr(exc_type, "__name__", str(exc_type)),
                    "error_message": str(exc),
                    "stack_trace": "".join(traceback.format_exception(exc_type, exc, tb)),
                },
            )
        _orig_excepthook(exc_type, exc, tb)

    sys.excepthook = diagnostic_excepthook

    diag_log("diagnostic", "hooks_installed", {"target_class": app_cls.__name__})


def _log_deepgram_audio_config(app: Any) -> None:
    try:
        from alpha.config import (
            DEEPGRAM_JA_ENDPOINTING_MS,
            DEEPGRAM_JA_UTTERANCE_END_MS,
            DEEPGRAM_MODEL as CONFIG_DEEPGRAM_MODEL,
            DEEPGRAM_SAMPLE_RATE,
            clamp_deepgram_utterance_end_ms,
        )
        from alpha.constants import (
            DEEPGRAM_ENDPOINTING_MS,
            DEEPGRAM_INTERIM_RESULTS,
            DEEPGRAM_LANGUAGE,
            DEEPGRAM_MODEL,
            DEEPGRAM_PUNCTUATE,
            DEEPGRAM_SMART_FORMAT,
            DEEPGRAM_UTTERANCE_END_MS,
            FORCE_DEEPGRAM_LANGUAGE,
        )

        lang = FORCE_DEEPGRAM_LANGUAGE or getattr(app, "_listen_language", "ja")
        code = str(lang or DEEPGRAM_LANGUAGE).lower()
        if code == "ja" or code.startswith("ja-"):
            endpointing_ms = int(DEEPGRAM_JA_ENDPOINTING_MS)
            utterance_end_ms, _ = clamp_deepgram_utterance_end_ms(
                int(DEEPGRAM_JA_UTTERANCE_END_MS)
            )
        else:
            endpointing_ms = int(DEEPGRAM_ENDPOINTING_MS)
            utterance_end_ms, _ = clamp_deepgram_utterance_end_ms(
                int(DEEPGRAM_UTTERANCE_END_MS)
            )
        model = CONFIG_DEEPGRAM_MODEL or DEEPGRAM_MODEL
        diag_log_throttled(
            "deepgram_config",
            "deepgram",
            "deepgram_stream_config",
            {
                "deepgram_model": model,
                "deepgram_language": lang,
                "sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                "channels": 1,
                "endpointing_ms": endpointing_ms,
                "utterance_end_ms": utterance_end_ms,
                "interim_results_enabled": bool(DEEPGRAM_INTERIM_RESULTS),
                "punctuate": bool(DEEPGRAM_PUNCTUATE),
                "smart_format": bool(DEEPGRAM_SMART_FORMAT),
                "final_results_only_in_ui": True,
            },
            interval_ms=10000,
        )
    except Exception as exc:
        diag_log_exception("deepgram", "deepgram_config_read_failed", exc)


def get_log_file_path() -> Path:
    return _resolve_log_file()
