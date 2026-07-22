"""Classify which component stalled first during long-session failures."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from alpha.utils.path_types import ensure_path

_last_classification: Optional[dict[str, Any]] = None
_first_bad_mono: dict[str, float] = {}
_consecutive_bad: dict[str, int] = {}
_consecutive_good: dict[str, int] = {}
_component_state: dict[str, str] = {}
_component_final_states: dict[str, str] = {}
_component_history: list[dict[str, Any]] = {}

_KNOWN_COMPONENTS = (
    "ui_mainloop",
    "audio_capture",
    "deepgram",
    "stable_pipeline",
    "ui_commit",
    "async_logger",
    "partial_autosave",
)

_SUSPECTED_THRESHOLD = 2
_CONFIRMED_THRESHOLD = 3
_RECOVERED_THRESHOLD = 2


def _mono() -> float:
    return time.monotonic()


def _age_ok(age_ms: float) -> bool:
    return age_ms >= 0


def _record_sample(component: str, is_bad: bool) -> tuple[int, int]:
    if is_bad:
        _consecutive_bad[component] = _consecutive_bad.get(component, 0) + 1
        _consecutive_good[component] = 0
    else:
        _consecutive_good[component] = _consecutive_good.get(component, 0) + 1
        _consecutive_bad[component] = 0
    return _consecutive_bad.get(component, 0), _consecutive_good.get(component, 0)


def _emit_classification(
    component: str,
    classification: str,
    confidence: str,
    reason: str,
    metrics: dict[str, Any],
    *,
    host: Any = None,
) -> None:
    global _last_classification
    key = component
    if key not in _first_bad_mono:
        _first_bad_mono[key] = _mono()
    entry = {
        "suspected_component": component,
        "classification": classification,
        "confidence": confidence,
        "reason": reason,
        "first_bad_timestamp": _first_bad_mono[key],
        "last_good_timestamp": None,
        "involved_metrics": metrics,
    }
    _last_classification = entry
    _component_state[component] = classification
    _component_history.append({"component": component, **entry})

    if classification == "recovered":
        _component_final_states[component] = "recovered"
    elif classification == "confirmed":
        _component_final_states[component] = "confirmed"
    elif classification == "suspected":
        if _component_final_states.get(component) not in ("confirmed", "recovered"):
            _component_final_states[component] = "suspected"
    elif classification == "transient_spike":
        if _component_final_states.get(component) not in ("confirmed", "suspected", "recovered"):
            _component_final_states[component] = "transient_recovered"

    event_map = {
        "transient_spike": "COMPONENT_STALL_TRANSIENT_SPIKE",
        "suspected": "COMPONENT_STALL_SUSPECTED_AFTER_CONSECUTIVE_SAMPLES",
        "confirmed": "COMPONENT_STALL_CLASSIFICATION",
        "recovered": "COMPONENT_STALL_RECOVERED",
        "suppressed": "COMPONENT_STALL_FALSE_POSITIVE_SUPPRESSED",
    }
    jp_event = event_map.get(classification, "COMPONENT_STALL_CLASSIFICATION")
    fr_map = {
        "ui_mainloop": "ui_stall_suspected",
        "audio_capture": "audio_stall_suspected",
        "deepgram": "deepgram_stall_suspected",
        "stable_pipeline": "stable_pipeline_stall_suspected",
        "ui_commit": "ui_commit_stall_suspected",
        "async_logger": "async_logger_stall_suspected",
        "partial_autosave": "partial_autosave_stall_suspected",
        "recovered": "component_stall_recovered",
    }
    fr_event = fr_map.get(component if classification != "recovered" else "recovered", "component_stall_classification")
    if classification == "confirmed" and component == "ui_mainloop":
        fr_event = "ui_stall_confirmed"

    try:
        from alpha.utils.flight_recorder import record_flight_event
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(jp_event, **entry)
        if classification in ("suspected", "confirmed", "transient_spike", "recovered"):
            record_flight_event(fr_event, host=host, force=True, **entry)
    except Exception:
        pass


def _evaluate_component(
    component: str,
    is_bad: bool,
    *,
    immediate_confirm: bool = False,
    reason: str,
    metrics: dict[str, Any],
    host: Any = None,
) -> None:
    bad_count, good_count = _record_sample(component, is_bad)

    if not is_bad:
        prev = _component_state.get(component)
        if prev in ("suspected", "confirmed", "transient_spike") and good_count >= _RECOVERED_THRESHOLD:
            _emit_classification(
                component,
                "recovered",
                "low",
                f"recovered after {good_count} good samples",
                metrics,
                host=host,
            )
        return

    if immediate_confirm:
        _emit_classification(component, "confirmed", "high", reason, metrics, host=host)
        return

    if bad_count == 1:
        _emit_classification(
            component,
            "transient_spike",
            "low",
            reason,
            metrics,
            host=host,
        )
        return

    if bad_count < _SUSPECTED_THRESHOLD:
        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log

            jp_accuracy_log(
                "COMPONENT_STALL_FALSE_POSITIVE_SUPPRESSED",
                component=component,
                bad_count=bad_count,
                reason=reason,
            )
        except Exception:
            pass
        return

    if bad_count >= _CONFIRMED_THRESHOLD:
        _emit_classification(component, "confirmed", "high", reason, metrics, host=host)
    elif bad_count >= _SUSPECTED_THRESHOLD:
        _emit_classification(component, "suspected", "medium", reason, metrics, host=host)


def classify_component_stalls(payload: dict[str, Any], *, host: Any = None) -> list[dict[str, Any]]:
    """Return list of stall classifications (may be empty)."""
    global _last_classification
    results: list[dict[str, Any]] = []
    listening = bool(payload.get("listening", False))
    ui_hb = float(payload.get("ui_heartbeat_age_ms", -1))
    audio_rx = float(payload.get("audio_received_age_ms", -1))
    audio_tx = float(payload.get("audio_sent_age_ms", -1))
    dg_msg = float(payload.get("deepgram_message_age_ms", -1))
    dg_final = float(payload.get("deepgram_final_age_ms", -1))
    stable_age = float(payload.get("stable_commit_age_ms", -1))
    ui_commit_age = float(payload.get("ui_commit_age_ms", -1))
    ui_q = int(payload.get("transcript_ui_queue_size", 0))
    async_q = int(payload.get("async_log_queue_size", 0))
    async_flush = float(payload.get("async_log_flush_age_ms", -1))
    autosave_age = float(payload.get("artifact_autosave_age_ms", -1))
    stable_count = int(payload.get("internal_stable_commit_count", 0))

    if _age_ok(ui_hb) and ui_hb > 20_000:
        _evaluate_component(
            "ui_mainloop",
            True,
            immediate_confirm=True,
            reason="ui_heartbeat_age_ms > 20000",
            metrics={"ui_heartbeat_age_ms": ui_hb},
            host=host,
        )
    elif _age_ok(ui_hb) and ui_hb > 10_000:
        _evaluate_component(
            "ui_mainloop",
            True,
            reason="ui_heartbeat_age_ms > 10000",
            metrics={"ui_heartbeat_age_ms": ui_hb},
            host=host,
        )
    else:
        _evaluate_component(
            "ui_mainloop",
            False,
            reason="ui_heartbeat_ok",
            metrics={"ui_heartbeat_age_ms": ui_hb},
            host=host,
        )

    audio_bad = (
        listening
        and _age_ok(audio_rx)
        and audio_rx > 10_000
        and _age_ok(dg_msg)
        and dg_msg > 10_000
    )
    _evaluate_component(
        "audio_capture",
        audio_bad,
        reason="audio_received stale and deepgram stale",
        metrics={"audio_received_age_ms": audio_rx, "deepgram_message_age_ms": dg_msg},
        host=host,
    )

    deepgram_bad = (
        _age_ok(audio_rx)
        and audio_rx < 2000
        and _age_ok(audio_tx)
        and audio_tx < 2000
        and _age_ok(dg_msg)
        and dg_msg > 15_000
    )
    _evaluate_component(
        "deepgram",
        deepgram_bad,
        reason="audio fresh but deepgram_message stale",
        metrics={
            "audio_received_age_ms": audio_rx,
            "deepgram_message_age_ms": dg_msg,
        },
        host=host,
    )

    stable_bad = (
        _age_ok(dg_final)
        and dg_final < 5000
        and _age_ok(stable_age)
        and stable_age > 15_000
        and stable_count > 0
    )
    _evaluate_component(
        "stable_pipeline",
        stable_bad,
        reason="deepgram final fresh but stable_commit stale",
        metrics={"deepgram_final_age_ms": dg_final, "stable_commit_age_ms": stable_age},
        host=host,
    )

    ui_commit_bad = (
        _age_ok(stable_age)
        and stable_age < 5000
        and _age_ok(ui_commit_age)
        and ui_commit_age > 10_000
        and ui_q > 0
    )
    _evaluate_component(
        "ui_commit",
        ui_commit_bad,
        reason="stable commits fresh but ui_commit stale with queued UI work",
        metrics={
            "stable_commit_age_ms": stable_age,
            "ui_commit_age_ms": ui_commit_age,
            "transcript_ui_queue_size": ui_q,
        },
        host=host,
    )

    async_bad = async_q > 50 and _age_ok(async_flush) and async_flush > 10_000
    _evaluate_component(
        "async_logger",
        async_bad,
        reason="async log queue growing and flush stale",
        metrics={"async_log_queue_size": async_q, "async_log_flush_age_ms": async_flush},
        host=host,
    )

    autosave_bad = (
        stable_count > 5
        and _age_ok(autosave_age)
        and autosave_age > 90_000
    )
    _evaluate_component(
        "partial_autosave",
        autosave_bad,
        reason="stable commits exist but partial_autosave stale",
        metrics={
            "artifact_autosave_age_ms": autosave_age,
            "internal_stable_commit_count": stable_count,
        },
        host=host,
    )

    if _last_classification is not None:
        results.append(_last_classification)
    return results


def get_last_stall_classification() -> Optional[dict[str, Any]]:
    return _last_classification


def get_component_final_states() -> dict[str, str]:
    return dict(_component_final_states)


def _metrics_look_healthy(final_metrics: dict[str, Any]) -> bool:
    thresholds = {
        "ui_heartbeat_age_ms": 10_000,
        "audio_received_age_ms": 10_000,
        "audio_sent_age_ms": 10_000,
        "deepgram_message_age_ms": 15_000,
        "deepgram_final_age_ms": 15_000,
        "stable_commit_age_ms": 15_000,
        "ui_commit_age_ms": 10_000,
        "async_log_flush_age_ms": 10_000,
        "artifact_autosave_age_ms": 90_000,
    }
    for key, limit in thresholds.items():
        if key not in final_metrics:
            continue
        try:
            if float(final_metrics.get(key, -1)) > limit:
                return False
        except Exception:
            continue
    if int(final_metrics.get("async_log_queue_size", 0) or 0) > 50:
        return False
    if int(final_metrics.get("transcript_ui_queue_size", 0) or 0) > 0 and float(
        final_metrics.get("ui_commit_age_ms", 0) or 0
    ) > 10_000:
        return False
    return True


def finalize_stall_classifications(
    final_metrics: Optional[dict[str, Any]] = None,
    *,
    run_folder: str | Path | None = None,
    host: Any = None,
) -> dict[str, Any]:
    """Persist authoritative end-of-run stall classification summary."""
    run_folder = ensure_path(run_folder)
    metrics = dict(final_metrics or {})
    healthy_end = _metrics_look_healthy(metrics)
    components: dict[str, dict[str, Any]] = {}

    for component in _KNOWN_COMPONENTS:
        state = _component_final_states.get(component, "healthy")
        if state in ("suspected", "confirmed", "transient_recovered") and healthy_end:
            state = "recovered"
            _emit_classification(
                component,
                "recovered",
                "medium",
                "final metrics healthy at stop",
                metrics,
                host=host,
            )
        elif state == "transient_recovered":
            state = "transient_recovered"
        elif state in ("suspected", "confirmed") and not healthy_end:
            state = "unresolved" if state == "suspected" else "confirmed"
        elif state not in ("recovered", "confirmed", "suspected", "unresolved", "transient_recovered"):
            state = "healthy"
        components[component] = {
            "final_state": state,
            "last_runtime_state": _component_state.get(component, "healthy"),
        }
        _component_final_states[component] = state

    summary = {
        "stall_suspected_count": sum(1 for c in components.values() if c["final_state"] == "suspected"),
        "stall_confirmed_count": sum(1 for c in components.values() if c["final_state"] == "confirmed"),
        "stall_recovered_count": sum(1 for c in components.values() if c["final_state"] == "recovered"),
        "transient_recovered_count": sum(
            1 for c in components.values() if c["final_state"] == "transient_recovered"
        ),
        "unresolved_stall_count": sum(1 for c in components.values() if c["final_state"] == "unresolved"),
        "components": components,
        "finalized_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "final_metrics_healthy": healthy_end,
    }

    if run_folder is not None:
        health_dir = run_folder / "health"
        health_dir.mkdir(parents=True, exist_ok=True)
        out_path = health_dir / "STALL_CLASSIFICATION_SUMMARY.json"
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["path"] = str(out_path)

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("STALL_CLASSIFICATION_SUMMARY_WRITTEN", **summary)
        from alpha.utils.flight_recorder import record_flight_event

        record_flight_event("stall_classification_finalized", host=host, force=True, **summary)
    except Exception:
        pass
    return summary


def reset_stall_classification() -> None:
    global _last_classification, _first_bad_mono
    _last_classification = None
    _first_bad_mono.clear()
    _consecutive_bad.clear()
    _consecutive_good.clear()
    _component_state.clear()
    _component_final_states.clear()
    _component_history.clear()
