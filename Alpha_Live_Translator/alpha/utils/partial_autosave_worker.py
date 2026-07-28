"""Background partial artifact autosave — never touches Tkinter."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from alpha.constants import (
    LONG_SESSION_STABILITY_MODE,
    PARTIAL_ALPHA_AUTOSAVE_COMMIT_INTERVAL,
    PARTIAL_ALPHA_AUTOSAVE_INTERVAL_S,
    PARTIAL_INDEX_AUTOSAVE_COMMIT_INTERVAL,
    PARTIAL_INDEX_AUTOSAVE_INTERVAL_S,
)

_ALPHA_INTERVAL_S = PARTIAL_ALPHA_AUTOSAVE_INTERVAL_S
_ALPHA_COMMIT_INTERVAL = PARTIAL_ALPHA_AUTOSAVE_COMMIT_INTERVAL
_INDEX_INTERVAL_S = PARTIAL_INDEX_AUTOSAVE_INTERVAL_S
_INDEX_COMMIT_INTERVAL = PARTIAL_INDEX_AUTOSAVE_COMMIT_INTERVAL

_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()
_host_ref: Any = None
_last_alpha_autosave_mono: float = 0.0
_last_index_autosave_mono: float = 0.0
_last_alpha_snapshot_count: int = 0
_last_index_snapshot_count: int = 0
_last_success_mono: float = 0.0
_commit_notify = threading.Event()
_cadence_logged = False


def is_worker_alive() -> bool:
    return _worker_thread is not None and _worker_thread.is_alive()


def get_last_success_mono() -> float:
    return _last_success_mono


def notify_stable_commit() -> None:
    """Wake worker; cadence gates actual writes."""
    _commit_notify.set()


def start_partial_autosave_worker(host: Any) -> None:
    global _host_ref, _worker_thread, _last_alpha_autosave_mono, _last_index_autosave_mono
    global _last_alpha_snapshot_count, _last_index_snapshot_count, _cadence_logged
    _host_ref = host
    now = time.monotonic()
    _last_alpha_autosave_mono = now
    _last_index_autosave_mono = now
    try:
        from alpha.utils.transcript_snapshot_store import snapshot_segment_count

        count = snapshot_segment_count()
        _last_alpha_snapshot_count = count
        _last_index_snapshot_count = count
    except Exception:
        _last_alpha_snapshot_count = 0
        _last_index_snapshot_count = 0
    _worker_stop.clear()
    _commit_notify.clear()
    _cadence_logged = False
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(
        target=_worker_loop, name="PartialArtifactAutosaveWorker", daemon=True
    )
    _worker_thread.start()
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("PARTIAL_AUTOSAVE_WORKER_STARTED")
        if LONG_SESSION_STABILITY_MODE:
            jp_accuracy_log(
                "PARTIAL_AUTOSAVE_CADENCE_CONFIGURED",
                alpha_interval_s=_ALPHA_INTERVAL_S,
                alpha_commit_interval=_ALPHA_COMMIT_INTERVAL,
                index_interval_s=_INDEX_INTERVAL_S,
                index_commit_interval=_INDEX_COMMIT_INTERVAL,
            )
    except Exception:
        pass


def stop_partial_autosave_worker() -> None:
    _worker_stop.set()
    _commit_notify.set()


def _worker_loop() -> None:
    global _last_alpha_autosave_mono, _last_index_autosave_mono
    global _last_alpha_snapshot_count, _last_index_snapshot_count, _last_success_mono
    while not _worker_stop.wait(1.0):
        host = _host_ref
        if host is None:
            continue
        if not bool(getattr(host, "is_listening", False)):
            continue
        try:
            from alpha.utils.transcript_snapshot_store import snapshot_segment_count

            snap_count = snapshot_segment_count()
        except Exception:
            snap_count = 0
        now = time.monotonic()
        _commit_notify.clear()

        if snap_count <= 0:
            continue

        alpha_time_due = (now - _last_alpha_autosave_mono) >= _ALPHA_INTERVAL_S
        alpha_commit_due = (snap_count - _last_alpha_snapshot_count) >= _ALPHA_COMMIT_INTERVAL
        index_time_due = (now - _last_index_autosave_mono) >= _INDEX_INTERVAL_S
        index_commit_due = (snap_count - _last_index_snapshot_count) >= _INDEX_COMMIT_INTERVAL

        if not (alpha_time_due or alpha_commit_due) and not (index_time_due or index_commit_due):
            continue

        try:
            from alpha.utils.japanese_accuracy_log import jp_accuracy_log
            from alpha.utils.run_artifacts import (
                autosave_partial_alpha_background,
                autosave_partial_index_background,
            )

            if alpha_time_due or alpha_commit_due:
                if snap_count == _last_alpha_snapshot_count and not alpha_time_due:
                    jp_accuracy_log("PARTIAL_AUTOSAVE_SKIPPED_CADENCE", layer="alpha")
                else:
                    jp_accuracy_log(
                        "PARTIAL_AUTOSAVE_WORKER_TICK",
                        snapshot_count=snap_count,
                        layer="alpha",
                    )
                    autosave_partial_alpha_background(reason="cadence_alpha", host=host)
                    _last_alpha_autosave_mono = now
                    _last_alpha_snapshot_count = snap_count
                    _last_success_mono = now

            if index_time_due or index_commit_due:
                if snap_count == _last_index_snapshot_count and not index_time_due:
                    jp_accuracy_log("PARTIAL_AUTOSAVE_SKIPPED_CADENCE", layer="index")
                else:
                    jp_accuracy_log(
                        "PARTIAL_AUTOSAVE_WORKER_TICK",
                        snapshot_count=snap_count,
                        layer="index",
                    )
                    autosave_partial_index_background(reason="cadence_index", host=host)
                    _last_index_autosave_mono = now
                    _last_index_snapshot_count = snap_count
                    _last_success_mono = now
        except Exception as exc:
            try:
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log("PARTIAL_AUTOSAVE_WORKER_ERROR", error=str(exc))
            except Exception:
                pass
