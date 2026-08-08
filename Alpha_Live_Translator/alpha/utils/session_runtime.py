# -*- coding: utf-8 -*-
"""Central live-session factory — fresh writable runtime on every Start.

One application process must support unlimited Start → Stop → Start cycles.
Each Start constructs a new session runtime with a new writable canonical ledger.
Previous session exports remain on disk under their own run folders.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SessionRuntime:
    session_id: str
    run_id: str = ""
    run_folder: str = ""
    ledger_generation: int = 0
    ledger_object_id: str = ""
    created_at_mono: float = field(default_factory=time.monotonic)
    state: str = "STARTING"


def _ledger_identity() -> tuple[int, str, bool]:
    from alpha.transcription import canonical_transcript_ledger as ctl

    with ctl._lock:
        gen = int(ctl._ledger_generation)
        frozen = bool(ctl._frozen)
        run_id = str(ctl._run_id or "")
    return gen, f"ledger-gen-{gen}:{run_id or 'none'}:{id(ctl)}", frozen


def begin_live_session(host: Any) -> SessionRuntime:
    """Construct all session-scoped runtime objects for a new Start.

    Call only from IDLE (not while STARTING / LISTENING / STOPPING).
    """
    if bool(getattr(host, "_starting_listening", False)):
        raise RuntimeError("duplicate Start rejected: already STARTING")
    if bool(getattr(host, "is_listening", False)):
        raise RuntimeError("duplicate Start rejected: already LISTENING")
    if bool(getattr(host, "_is_stopping", False)) or bool(
        getattr(host, "_is_finalizing", False)
    ):
        raise RuntimeError("duplicate Start rejected: STOPPING/finalising")

    session_id = f"sess-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    host._live_session_id = session_id
    host._session_state = "STARTING"
    host._finalizing_session_id = ""
    host._frozen_ledger_error_count = int(
        getattr(host, "_frozen_ledger_error_count", 0) or 0
    )

    # fixes TASK_3A_FINDINGS.md Item 1/2: necessary wiring -- these reset the
    # identity-keyed structures that replaced the single global pending
    # payload / flat positional list in main_window.py. Cancel every
    # outstanding per-utterance debounce timer from the *old* dict before
    # replacing it -- resetting the attribute first would orphan them
    # (unreachable via host, but never cancelled).
    try:
        old_timers = getattr(host, "_translation_debounce_after_ids", None) or {}
        if hasattr(host, "after_cancel"):
            for after_id in list(old_timers.values()):
                try:
                    host.after_cancel(after_id)
                except Exception:
                    pass
    except Exception:
        pass

    # UI / translation session-scoped registries
    host._translation_loading_items = {}
    host._pending_translations_by_utterance = {}
    host._translation_debounce_after_ids = {}
    host._translation_items_by_utterance = {}
    host._translation_segment_seq = 0
    host._latest_interim_text = ""
    # Batch 3 item 11b: the watchdog's preserved orphan is session-scoped
    # content -- a new session must never inherit the previous session's
    # uncommitted tail and commit it into the wrong transcript.
    host._watchdog_orphaned_interim_text = ""
    host._watchdog_orphaned_interim_speaker = 1
    host._watchdog_orphaned_interim_utterance_id = ""
    host._watchdog_orphaned_interim_at = 0.0
    host._ui_callback_stats = {
        "scheduled": 0,
        "started": 0,
        "widget_updated": 0,
        "loading_cleared": 0,
        "completed": 0,
        "cancelled": 0,
    }

    try:
        if hasattr(host, "_clear_interim_tail"):
            host._clear_interim_tail()
        elif hasattr(host, "_remove_interim_line_from_display"):
            host._remove_interim_line_from_display()
    except Exception:
        pass

    try:
        if hasattr(host, "reset_transcript_stability_state"):
            host.reset_transcript_stability_state()
    except Exception:
        pass

    try:
        from alpha.transcription.utterance_lifecycle import reset_utterance_lifecycle

        reset_utterance_lifecycle(host, session_id=session_id)
    except Exception:
        pass

    try:
        from alpha.transcription.canonical_identity_registry import reset_for_session

        reset_for_session(session_id)
    except Exception:
        pass

    try:
        from alpha.transcription.japanese_sentence_assembler import (
            reset_japanese_sentence_assembler,
        )

        reset_japanese_sentence_assembler(host)
    except Exception:
        pass

    try:
        from alpha.transcription.stable_line_revision import (
            reset_stable_line_revision_manager,
        )

        reset_stable_line_revision_manager()
    except Exception:
        pass

    try:
        from alpha.transcription.japanese_boundary_stabilizer import (
            reset_boundary_stabilizer,
        )

        reset_boundary_stabilizer()
    except Exception:
        pass

    try:
        store = getattr(host, "transcript_store", None)
        if store is not None and hasattr(store, "clear"):
            store.clear()
    except Exception:
        pass

    try:
        from alpha.utils import live_pipeline_profile as lpp

        lpp.reset_session(session_id)
    except Exception:
        pass

    # Force a new run identity + writable ledger (previous files stay on disk).
    run_id = ""
    run_folder = ""
    try:
        from alpha.utils.run_identity import init_live_run_from_host

        identity = init_live_run_from_host(host)
        host._run_identity = identity
        run_id = str(getattr(identity, "run_id", "") or "")
        run_folder = str(getattr(identity, "run_folder", "") or "")
    except Exception as exc:
        # Language may not be finalized yet on early UI Start; worker will init.
        host._run_identity = None
        host._session_init_deferred_error = str(exc)
        try:
            from alpha.transcription.canonical_transcript_ledger import reset_for_run

            reset_for_run(session_id)
        except Exception:
            pass

    gen, ledger_id, frozen = _ledger_identity()
    if frozen:
        # Should be impossible after reset_for_run; count and force again.
        host._frozen_ledger_error_count = int(
            getattr(host, "_frozen_ledger_error_count", 0) or 0
        ) + 1
        try:
            from alpha.transcription.canonical_transcript_ledger import reset_for_run

            reset_for_run(run_id or session_id)
            gen, ledger_id, frozen = _ledger_identity()
        except Exception:
            pass

    runtime = SessionRuntime(
        session_id=session_id,
        run_id=run_id,
        run_folder=run_folder,
        ledger_generation=gen,
        ledger_object_id=ledger_id,
        state="STARTING",
    )
    host._session_runtime = runtime
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "LIVE_SESSION_RUNTIME_CREATED",
            session_id=session_id,
            run_id=run_id,
            ledger_generation=gen,
            ledger_object_id=ledger_id,
            ledger_frozen=frozen,
        )
    except Exception:
        pass
    return runtime


def mark_session_finalizing(host: Any) -> None:
    sid = str(getattr(host, "_live_session_id", "") or "")
    host._finalizing_session_id = sid
    host._session_state = "STOPPING"
    runtime = getattr(host, "_session_runtime", None)
    if runtime is not None:
        runtime.state = "STOPPING"


def mark_session_stopped(host: Any) -> None:
    host._session_state = "STOPPED"
    runtime = getattr(host, "_session_runtime", None)
    if runtime is not None:
        runtime.state = "STOPPED"


def session_accepts_callback(host: Any, session_id: str) -> bool:
    """Allow callbacks for the active session, including during STOP drain."""
    sid = str(session_id or "")
    if not sid:
        return True
    current = str(getattr(host, "_live_session_id", "") or "")
    finalizing = str(getattr(host, "_finalizing_session_id", "") or "")
    if current and sid == current:
        return True
    if finalizing and sid == finalizing:
        return True
    return False


def get_session_object_identity(host: Any) -> dict[str, Any]:
    gen, ledger_id, frozen = _ledger_identity()
    runtime = getattr(host, "_session_runtime", None)
    worker = getattr(host, "translation_worker", None)
    store = getattr(host, "transcript_store", None)
    return {
        "session_id": str(getattr(host, "_live_session_id", "") or ""),
        "run_id": str(getattr(getattr(host, "_run_identity", None), "run_id", "") or ""),
        "ledger_generation": gen,
        "ledger_object_id": ledger_id,
        "ledger_frozen": frozen,
        "runtime_ledger_object_id": getattr(runtime, "ledger_object_id", None),
        "translation_worker_id": id(worker) if worker is not None else None,
        "transcript_store_id": id(store) if store is not None else None,
        "session_state": str(getattr(host, "_session_state", "") or ""),
    }
