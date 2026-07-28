# -*- coding: utf-8 -*-
"""Deterministic pre-live validator for session lifecycle repair.

Usage (from Alpha_Live_Translator root):

    python .\\tools\\validate_session_lifecycle_repair.py

Does not require microphone or DeepL billing. Exercises production session
factory, canonical ledger, TranslationWorker, and AlphaApp UI callbacks via
FakeHost / FakeTextBox.
"""

from __future__ import annotations

import json
import py_compile
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Shared mutable evidence accumulators
# ---------------------------------------------------------------------------
_FROZEN_LEDGER_ERRORS = 0  # unexpected failures on writable sessions (READY gate)
_INTENTIONAL_FROZEN_PROBES = 0
_SESSION_IDENTITIES: list[dict[str, Any]] = []


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _note_frozen_error(*, expected: bool = False) -> None:
    global _FROZEN_LEDGER_ERRORS, _INTENTIONAL_FROZEN_PROBES
    if expected:
        _INTENTIONAL_FROZEN_PROBES += 1
    else:
        _FROZEN_LEDGER_ERRORS += 1


# ---------------------------------------------------------------------------
# Fake UI widgets (same pattern as validate_live_pipeline_repair.py)
# ---------------------------------------------------------------------------
class FakeTextBox:
    def __init__(self) -> None:
        self._text = ""
        self._marks: dict[str, int] = {}
        self._tags: set[str] = set()
        self._scrollbar = None
        self._placeholder_text = ""

    def configure(self, **kwargs):  # noqa: ANN003
        return None

    def mark_set(self, name, index):  # noqa: ANN001
        self._marks[name] = len(self._text)

    def mark_unset(self, name):  # noqa: ANN001
        self._marks.pop(name, None)

    def compare(self, a, op, b):  # noqa: ANN001
        return a in self._marks

    def delete(self, start, end=None):  # noqa: ANN001
        if start in self._marks:
            idx = self._marks[start]
            if end and "lineend" in str(end):
                nl = self._text.find("\n", idx)
                self._text = self._text[:idx] + (self._text[nl + 1 :] if nl >= 0 else "")
            else:
                self._text = self._text[:idx]
            for k, v in list(self._marks.items()):
                if v >= idx:
                    self._marks.pop(k, None)
            return
        if str(start).startswith("end-2l"):
            lines = self._text.splitlines(True)
            self._text = "".join(lines[:-1]) if lines else ""
            return
        if start == "1.0":
            self._text = ""
            self._marks.clear()

    def insert(self, index, text, tags=None):  # noqa: ANN001
        self._text += text

    def index(self, _):  # noqa: ANN001
        return "end"

    def see(self, _):  # noqa: ANN001
        return None

    def tag_names(self):
        return tuple(self._tags)

    def tag_configure(self, name, **kwargs):  # noqa: ANN001, ANN003
        self._tags.add(name)

    def tag_add(self, name, start, end):  # noqa: ANN001
        self._tags.add(name)

    def get(self, start="1.0", end="end"):  # noqa: ANN001
        return self._text


class FakeHost:
    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.initial_verse_box = FakeTextBox()
        self.translated_verse_box = FakeTextBox()
        self._latest_interim_text = ""
        self._latest_interim_speaker = 1
        self._displayed_segment_count = 0
        self._exported_ui_segment_count = 0
        self._ui_insert_durations_ms: list[float] = []
        self._translation_display_lines: list[str] = []
        self._translation_loading_items: dict[int, dict[str, Any]] = {}
        self._translation_segment_seq = 0
        self._pending_translation_payload = None
        self._translation_debounce_after_id = None
        self._live_session_id = ""
        self._session_state = "IDLE"
        self._finalizing_session_id = ""
        self._frozen_ledger_error_count = 0
        self._ui_callback_stats: dict[str, int] = {
            "scheduled": 0,
            "started": 0,
            "widget_updated": 0,
            "loading_cleared": 0,
            "completed": 0,
            "cancelled": 0,
        }
        self.translation_enabled = True
        self.translation_worker = None
        self.source_language = SimpleNamespace(get=lambda: "en")
        self._listen_language = "en"
        self.last_translation_speaker = None
        self._after_jobs: list[tuple[int, Any]] = []
        self._after_seq = 0
        self._starting_listening = False
        self.is_listening = False
        self._is_stopping = False
        self._is_finalizing = False
        self._session_runtime = None
        self._run_identity = None
        self.transcript_store = None
        self.transcript_queue = None

    def _transcript_box(self):
        return self.initial_verse_box

    def after(self, ms, cb, *args):  # noqa: ANN001
        self._after_seq += 1
        job = self._after_seq

        def _wrap() -> None:
            if args:
                cb(*args)
            else:
                cb()

        self._after_jobs.append((job, _wrap))
        return job

    def after_cancel(self, job):  # noqa: ANN001
        self._after_jobs = [(j, c) for j, c in self._after_jobs if j != job]

    def flush_after(self) -> None:
        jobs = list(self._after_jobs)
        self._after_jobs.clear()
        for _, cb in jobs:
            cb()

    def _clear_text_placeholder(self, box):  # noqa: ANN001
        return None

    def _show_text_placeholder(self, box):  # noqa: ANN001
        return None

    def _speaker_tag(self, speaker):  # noqa: ANN001
        return "speaker"

    def _maybe_scroll_transcript_box(self, box):  # noqa: ANN001
        return None

    def _refresh_transcript_scrollbar(self, box):  # noqa: ANN001
        return None

    def check_scrollbar_visibility(self, box, scrollbar):  # noqa: ANN001
        return None

    def _record_translation_segment(self, *a, **k):  # noqa: ANN001, ANN003
        return None

    def _set_translation_status(self, message: str) -> None:
        return None


def bind_host(host: FakeHost) -> FakeHost:
    from alpha.transcription.duplicate_protection import DuplicateProtectionMixin
    from alpha.ui.main_window import AlphaApp

    for name in (
        "_ui_speaker_label_text",
        "_remove_interim_line_from_display",
        "_insert_speaker_segment_line",
        "_update_interim_line_only",
        "_append_pending_interim_to_display",
        "_clear_interim_tail",
        "_on_store_segment_added",
        "_on_store_segment_updated",
        "submit_text_for_translation",
        "_flush_pending_translation_submit",
        "_show_translation_loading_item",
        "_clear_translation_loading_item",
        "loading_indicators_pending",
        "_on_translation_worker_result",
        "_handle_translation_worker_result",
        "_append_translation_result",
        "_run_on_ui_thread",
        "_set_starting_status",
        "_set_stopping_ui_state",
    ):
        setattr(host, name, getattr(AlphaApp, name).__get__(host, FakeHost))

    # Bind duplicate-protection helpers when available
    for name in ("_display_transcript_item", "reset_transcript_stability_state"):
        if hasattr(DuplicateProtectionMixin, name):
            setattr(
                host,
                name,
                getattr(DuplicateProtectionMixin, name).__get__(host, FakeHost),
            )

    host.status_text_label = SimpleNamespace(configure=lambda **k: None)
    host.listen_button = SimpleNamespace(configure=lambda **k: None)
    host.listen_button_menu = SimpleNamespace(configure=lambda **k: None)
    host.live_indicator = None
    host.live_pill = None
    host.signal_label = None
    host._waveform_job = None
    host._timer_job = None
    host._live_pulse_job = None
    host._draw_waveform = lambda idle=False: None
    return host


def pump_ui(host: FakeHost, rounds: int = 8) -> None:
    """Drain UI event bus (immediate schedule_after) then FakeHost after jobs."""
    from alpha.utils.ui_event_bus import get_ui_event_bus

    bus = get_ui_event_bus()
    for _ in range(rounds):
        bus.drain_until_empty(host, max_rounds=50, time_budget_ms=250.0)
        host.flush_after()
        if not host._after_jobs and bus.stats().get("ui_bus_queue_remaining", 0) == 0:
            break


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------
def _make_result(job, tgt, *, text=None, status="success", terminal="completed"):
    from alpha.translation.translation_worker import (
        TERMINAL_COMPLETED,
        TERMINAL_PERMANENTLY_FAILED,
        TranslationResult,
    )

    term = TERMINAL_COMPLETED if status == "success" else TERMINAL_PERMANENTLY_FAILED
    if terminal:
        term = terminal
    return TranslationResult(
        run_id=job.run_id,
        segment_id=job.segment_id,
        source_segment_id=job.source_segment_id,
        translation_sequence=job.translation_sequence,
        source_language=job.source_language,
        target_language=tgt,
        source_text=job.source_text,
        source_text_hash=job.source_text_hash,
        translated_text=(text if text is not None else f"TR:{job.source_text}"),
        status=status if status != "success" else "success",
        terminal_state=term,
        stable_committed_at=job.stable_committed_at,
        queued_at=job.queued_at,
        started_at=time.time(),
        provider_completed_at=time.time(),
        completed_at=time.time(),
    )


def _attach_worker(host: FakeHost, *, run_id: Optional[str] = None):
    from alpha.translation.translation_worker import TranslationWorker

    rid = run_id or str(getattr(host, "_live_session_id", "") or "validate")
    worker = TranslationWorker(run_id=rid, evidence_dir=None)
    worker._enabled = True
    worker._accepting = True
    worker._quota_disabled = False
    worker._fake_translate = lambda job, tgt: _make_result(job, tgt)
    worker._translate_job = worker._fake_translate  # type: ignore
    worker.on_translation_ready = host._on_translation_worker_result
    host.translation_worker = worker
    return worker


def _drain_worker(worker, host: Optional[FakeHost] = None) -> None:
    while True:
        try:
            item = worker._queue.get_nowait()
        except Exception:
            break
        job, tgt = item
        fake = getattr(worker, "_fake_translate", None)
        if callable(fake):
            result = fake(job, tgt)
            with worker._lock:
                worker._counters["TRANSLATION_REQUESTS_SENT"] += 1
                worker._counters["requests_sent"] += 1
        else:
            result = worker._translate_job(job, tgt)
        worker._handle_result(result)
        if host is not None:
            pump_ui(host)


def _worker_counters(worker) -> dict[str, int]:
    with worker._lock:
        return {
            "accepted": int(worker._counters.get("STABLE_TRANSLATION_JOBS_ACCEPTED", 0) or 0),
            "sent": int(worker._counters.get("TRANSLATION_REQUESTS_SENT", 0) or 0),
            "commits": int(worker._counters.get("TRANSLATION_COMMITS_COMPLETED", 0) or 0),
            "interim": int(worker._counters.get("INTERIM_SUBMISSIONS_REJECTED", 0) or 0),
            "dup": int(worker._counters.get("DUPLICATE_SUBMISSIONS_REJECTED", 0) or 0),
            "held": len(worker._held),
            "queue": int(worker._queue.qsize()),
            "in_flight": int(getattr(worker, "_in_flight", 0) or 0)
            if not hasattr(worker, "_in_flight")
            else (
                len(worker._in_flight)
                if isinstance(worker._in_flight, (set, list, dict))
                else int(worker._in_flight or 0)
            ),
        }


def _ledger_apply_stable(
    text: str, *, speaker: int = 1, seq: int = 1, expect_frozen: bool = False
) -> dict[str, Any]:
    from alpha.transcription.canonical_transcript_ledger import apply_decision
    from alpha.utils.pipeline_integrity import PipelineIntegrityError

    try:
        return apply_decision(
            speaker=speaker,
            assembler_text=text,
            final_text=text,
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=[f"raw-{seq}"],
            commit_reason="validate_stable",
            source="validate_session_lifecycle",
            metadata={"synthetic_record": True},
        )
    except PipelineIntegrityError as exc:
        if "frozen" in str(exc).lower():
            _note_frozen_error(expected=expect_frozen)
        return {"ok": False, "error": str(exc)}


def _snapshot_session(host: FakeHost, label: str) -> dict[str, Any]:
    from alpha.transcription.canonical_transcript_ledger import (
        get_ledger_identity,
        is_frozen,
    )
    from alpha.utils.session_runtime import get_session_object_identity

    ident = get_session_object_identity(host)
    ledger = get_ledger_identity()
    worker = getattr(host, "translation_worker", None)
    wstats = _worker_counters(worker) if worker is not None else {}
    stats = dict(getattr(host, "_ui_callback_stats", {}) or {})
    snap = {
        "label": label,
        "session_id": ident.get("session_id"),
        "run_id": ident.get("run_id"),
        "ledger_object_id": ledger.get("ledger_object_id") or ident.get("ledger_object_id"),
        "ledger_generation": ledger.get("ledger_generation"),
        "ledger_writable_at_start": not bool(ledger.get("frozen")),
        "ledger_frozen": bool(is_frozen()),
        "canonical_stable_commit_count": int(ledger.get("active_record_count") or 0),
        "translation_accepted_count": wstats.get("accepted", 0),
        "provider_request_count": wstats.get("sent", 0),
        "ordered_commit_count": wstats.get("commits", 0),
        "ui_scheduled_count": int(stats.get("scheduled", 0) or 0),
        "ui_completed_count": int(stats.get("completed", 0) or 0),
        "loading_pending_at_exit": host.loading_indicators_pending()
        if hasattr(host, "loading_indicators_pending")
        else len(getattr(host, "_translation_loading_items", {}) or {}),
        "queue_pending_at_exit": wstats.get("queue", 0),
        "in_flight_at_exit": wstats.get("in_flight", 0),
        "ordering_buffer_at_exit": wstats.get("held", 0),
        "frozen_ledger_error_count": int(
            getattr(host, "_frozen_ledger_error_count", 0) or 0
        )
        + _FROZEN_LEDGER_ERRORS,
        "session_object_identity": ident,
        "ledger_identity": ledger,
    }
    _SESSION_IDENTITIES.append(snap)
    return snap


def _idle_flags(host: FakeHost) -> None:
    """Return FakeHost to IDLE so begin_live_session accepts a new Start."""
    host._starting_listening = False
    host.is_listening = False
    host._is_stopping = False
    host._is_finalizing = False


def _stop_session(host: FakeHost, *, freeze: bool = True) -> None:
    from alpha.transcription.canonical_transcript_ledger import freeze_snapshot
    from alpha.utils.session_runtime import mark_session_finalizing, mark_session_stopped

    host._is_stopping = True
    host._is_finalizing = True
    mark_session_finalizing(host)
    if freeze:
        freeze_snapshot()
    mark_session_stopped(host)
    _idle_flags(host)


def _begin_session(host: FakeHost) -> Any:
    from alpha.utils.session_runtime import begin_live_session

    host._listen_language = "en"
    _idle_flags(host)
    runtime = begin_live_session(host)
    host.is_listening = True
    host._starting_listening = False
    host._session_state = "LISTENING"
    if getattr(host, "_session_runtime", None) is not None:
        host._session_runtime.state = "LISTENING"
    return runtime


def _commit_and_translate(host: FakeHost, texts: list[str]) -> dict[str, Any]:
    """Apply canonical stable commits and complete fake translations."""
    worker = host.translation_worker
    if worker is None:
        worker = _attach_worker(host)
    applied = 0
    for i, text in enumerate(texts, start=1):
        result = _ledger_apply_stable(text, seq=i)
        if result.get("ok") is False:
            continue
        applied += 1
        host.submit_text_for_translation(text, speaker=1, force_flush_previous=True)
        pump_ui(host)
    _drain_worker(worker, host)
    pump_ui(host)
    return {"applied": applied, **_worker_counters(worker)}


# ---------------------------------------------------------------------------
# Tests A–H + compile / freeze checks
# ---------------------------------------------------------------------------
def test_compile() -> dict[str, Any]:
    files = [
        ROOT / "alpha/utils/session_runtime.py",
        ROOT / "alpha/transcription/canonical_transcript_ledger.py",
        ROOT / "alpha/translation/translation_worker.py",
        ROOT / "alpha/ui/main_window.py",
        ROOT / "alpha/transcription/duplicate_protection.py",
        ROOT / "alpha/utils/ui_event_bus.py",
        ROOT / "alpha/utils/english_deepgram_request.py",
        ROOT / "alpha/utils/run_identity.py",
        ROOT / "tools/validate_session_lifecycle_repair.py",
    ]
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="alpha_sess_compile_") as td:
        for f in files:
            if not f.is_file():
                errors.append(f"missing: {f}")
                continue
            try:
                cfile = Path(td) / (f.stem + ".pyc")
                py_compile.compile(str(f), cfile=str(cfile), doraise=True)
            except Exception as exc:
                errors.append(f"{f.name}: {exc}")
    for mod in (
        "alpha.utils.session_runtime",
        "alpha.transcription.canonical_transcript_ledger",
        "alpha.translation.translation_worker",
        "alpha.utils.ui_event_bus",
        "alpha.utils.english_deepgram_request",
    ):
        try:
            __import__(mod)
        except Exception as exc:
            errors.append(f"import {mod}: {exc}")
    return {"name": "PRODUCTION_COMPILE", "passed": not errors, "errors": errors}


def test_a_start_stop_start() -> dict[str, Any]:
    from alpha.transcription.canonical_transcript_ledger import is_frozen
    from alpha.utils.session_runtime import session_accepts_callback

    host = bind_host(FakeHost())
    rt_a = _begin_session(host)
    _attach_worker(host, run_id=rt_a.run_id or rt_a.session_id)
    start_a = _snapshot_session(host, "A_start")
    stats_a = _commit_and_translate(
        host, ["Session A sentence one.", "Session A sentence two."]
    )
    assert session_accepts_callback(host, rt_a.session_id)

    # Attempt apply while frozen → must count frozen-ledger errors
    _stop_session(host, freeze=True)
    assert is_frozen() is True
    frozen_try = _ledger_apply_stable(
        "should fail on frozen ledger", seq=99, expect_frozen=True
    )
    stop_a = _snapshot_session(host, "A_stop")

    rt_b = _begin_session(host)
    _attach_worker(host, run_id=rt_b.run_id or rt_b.session_id)
    start_b = _snapshot_session(host, "B_start")
    assert is_frozen() is False
    stats_b = _commit_and_translate(
        host, ["Session B sentence one.", "Session B sentence two."]
    )
    _stop_session(host, freeze=True)
    stop_b = _snapshot_session(host, "B_stop")

    different_ids = rt_a.session_id != rt_b.session_id
    different_gens = int(rt_a.ledger_generation) != int(rt_b.ledger_generation)
    different_ledger = str(rt_a.ledger_object_id) != str(rt_b.ledger_object_id)
    b_writable = start_b.get("ledger_writable_at_start") is True
    commits_ok = stats_a["applied"] >= 2 and stats_b["applied"] >= 2
    xlat_ok = stats_a["commits"] >= 2 and stats_b["commits"] >= 2
    loading_ok = stop_a["loading_pending_at_exit"] == 0 and stop_b["loading_pending_at_exit"] == 0
    # Frozen attempt should have failed (counted); post-reset session B must have zero *new* frozen failures during commits
    frozen_attempt_failed = frozen_try.get("ok") is False

    ok = (
        different_ids
        and different_gens
        and different_ledger
        and b_writable
        and commits_ok
        and xlat_ok
        and loading_ok
        and frozen_attempt_failed
    )
    return {
        "name": "A_START_STOP_START",
        "passed": ok,
        "session_a": rt_a.session_id,
        "session_b": rt_b.session_id,
        "ledger_gen_a": rt_a.ledger_generation,
        "ledger_gen_b": rt_b.ledger_generation,
        "ledger_id_a": rt_a.ledger_object_id,
        "ledger_id_b": rt_b.ledger_object_id,
        "stats_a": stats_a,
        "stats_b": stats_b,
        "start_a": start_a,
        "stop_a": stop_a,
        "start_b": start_b,
        "stop_b": stop_b,
        "frozen_apply_result": frozen_try,
    }


def test_b_three_consecutive() -> dict[str, Any]:
    host = bind_host(FakeHost())
    sessions: list[dict[str, Any]] = []
    for idx, label in enumerate(("A", "B", "C"), start=1):
        rt = _begin_session(host)
        _attach_worker(host, run_id=rt.run_id or rt.session_id)
        start = _snapshot_session(host, f"{label}_start")
        stats = _commit_and_translate(host, [f"Session {label} line one.", f"Session {label} line two."])
        # Stale callback from a prior session must not mutate current UI
        if idx > 1:
            from alpha.translation.translation_worker import (
                TERMINAL_COMPLETED,
                TranslationResult,
            )

            stale = TranslationResult(
                run_id="stale",
                segment_id=999,
                source_segment_id=999,
                translation_sequence=999,
                source_language="EN",
                target_language="JA",
                source_text="stale",
                source_text_hash="stale",
                translated_text="STALE_SHOULD_NOT_APPEAR",
                status="success",
                terminal_state=TERMINAL_COMPLETED,
            )
            prior = sessions[-1]["session_id"]
            host._handle_translation_worker_result(stale, session_id=prior)
            pump_ui(host)
        _stop_session(host, freeze=True)
        stop = _snapshot_session(host, f"{label}_stop")
        sessions.append(
            {
                "session_id": rt.session_id,
                "ledger_generation": rt.ledger_generation,
                "ledger_object_id": rt.ledger_object_id,
                "stats": stats,
                "start": start,
                "stop": stop,
            }
        )

    ids = [s["session_id"] for s in sessions]
    gens = [s["ledger_generation"] for s in sessions]
    ledgers = [s["ledger_object_id"] for s in sessions]
    stale_clean = all(
        "STALE_SHOULD_NOT_APPEAR" not in "\n".join(getattr(host, "_translation_display_lines", []) or [])
        for _ in (0,)
    )
    # Re-check on last session display lines only (stale should never append)
    display = "\n".join(getattr(host, "_translation_display_lines", []) or [])
    stale_clean = "STALE_SHOULD_NOT_APPEAR" not in display
    ok = (
        len(set(ids)) == 3
        and len(set(gens)) == 3
        and len(set(ledgers)) == 3
        and all(s["stats"]["applied"] >= 2 for s in sessions)
        and all(s["stats"]["commits"] >= 2 for s in sessions)
        and stale_clean
    )
    return {
        "name": "B_THREE_CONSECUTIVE_SESSIONS",
        "passed": ok,
        "sessions": sessions,
        "unique_session_ids": len(set(ids)),
        "unique_ledger_gens": len(set(gens)),
        "stale_callback_blocked": stale_clean,
    }


def test_c_short_session_restart() -> dict[str, Any]:
    from alpha.transcription.canonical_transcript_ledger import is_frozen

    host = bind_host(FakeHost())
    rt_a = _begin_session(host)
    _attach_worker(host, run_id=rt_a.run_id or rt_a.session_id)
    # Short session: freeze quickly with no meaningful commits
    time.sleep(0.05)
    _stop_session(host, freeze=True)
    assert is_frozen() is True
    snap_a = _snapshot_session(host, "short_A_stop")

    rt_b = _begin_session(host)
    _attach_worker(host, run_id=rt_b.run_id or rt_b.session_id)
    assert is_frozen() is False
    snap_b_start = _snapshot_session(host, "short_B_start")
    stats = _commit_and_translate(
        host,
        [
            "短いセッション後の安定文です。",
            "もう一つの安定した文です。",
        ],
    )
    _stop_session(host, freeze=True)
    snap_b_stop = _snapshot_session(host, "short_B_stop")

    ok = (
        rt_a.session_id != rt_b.session_id
        and int(rt_a.ledger_generation) != int(rt_b.ledger_generation)
        and stats["applied"] > 0
        and stats["sent"] > 0
        and stats["commits"] > 0
        and len(host._translation_display_lines) > 0
        and snap_b_start.get("ledger_writable_at_start") is True
    )
    return {
        "name": "C_SHORT_SESSION_RESTART",
        "passed": ok,
        "session_a": rt_a.session_id,
        "session_b": rt_b.session_id,
        "stats_b": stats,
        "snap_a": snap_a,
        "snap_b_start": snap_b_start,
        "snap_b_stop": snap_b_stop,
        "translated_lines": list(host._translation_display_lines),
    }


def test_d_canonical_translation_authority() -> dict[str, Any]:
    from alpha.transcription.canonical_transcript_ledger import (
        freeze_snapshot,
        is_frozen,
        reset_for_run,
    )

    host = bind_host(FakeHost())
    rt = _begin_session(host)
    worker = _attach_worker(host, run_id=rt.run_id or rt.session_id)

    # 1) Interim — must reject translation
    worker.enqueue_stable_segment(
        segment_id=1,
        source_language="en",
        source_text="interim text",
        is_interim=True,
        run_id=rt.run_id or rt.session_id,
    )

    # 2) Failed Stable commit (apply while intentionally frozen mid-session)
    freeze_snapshot()
    assert is_frozen() is True
    failed = _ledger_apply_stable("failed commit text", seq=2, expect_frozen=True)
    # Unfreeze via reset for the successful path (simulates next writable ledger)
    reset_for_run(rt.run_id or rt.session_id)
    # Re-bind generation after manual reset
    from alpha.transcription.canonical_transcript_ledger import get_ledger_identity

    # 3) Successful Stable commit + translation
    ok_apply = _ledger_apply_stable("Canonical success sentence.", seq=3)
    host.submit_text_for_translation(
        "Canonical success sentence.", speaker=1, force_flush_previous=True
    )
    pump_ui(host)
    _drain_worker(worker, host)

    # 4) Duplicate Stable event
    host.submit_text_for_translation(
        "Canonical success sentence.", speaker=1, force_flush_previous=True
    )
    pump_ui(host)
    _drain_worker(worker, host)

    c = _worker_counters(worker)
    interim_ok = c["interim"] >= 1
    failed_ok = failed.get("ok") is False
    success_ok = ok_apply.get("ok") is not False and c["accepted"] == 1 and c["commits"] == 1
    dup_ok = c["dup"] >= 1 and c["sent"] == 1  # no extra provider request for duplicate

    ok = interim_ok and failed_ok and success_ok and dup_ok
    return {
        "name": "D_CANONICAL_STABLE_TRANSLATION_WIRING",
        "passed": ok,
        "interim_rejected": c["interim"],
        "failed_commit": failed,
        "successful_accepted": c["accepted"],
        "successful_commits": c["commits"],
        "provider_requests": c["sent"],
        "duplicate_rejected": c["dup"],
        "ledger_identity": get_ledger_identity(),
        "session_id": rt.session_id,
    }


def test_e_ui_callback_completion() -> dict[str, Any]:
    host = bind_host(FakeHost())
    rt = _begin_session(host)
    worker = _attach_worker(host, run_id=rt.run_id or rt.session_id)

    # Reset stats after begin (begin_live_session already zeroes them)
    host._ui_callback_stats = {
        "scheduled": 0,
        "started": 0,
        "widget_updated": 0,
        "loading_cleared": 0,
        "completed": 0,
        "cancelled": 0,
    }

    for i in range(1, 11):
        text = f"Callback sentence {i}."
        _ledger_apply_stable(text, seq=i)
        host.submit_text_for_translation(text, speaker=1, force_flush_previous=True)
        pump_ui(host)
    _drain_worker(worker, host)
    pump_ui(host, rounds=16)

    stats = dict(host._ui_callback_stats)
    pending = host.loading_indicators_pending()
    ok = (
        stats.get("scheduled") == 10
        and stats.get("started") == 10
        and stats.get("widget_updated") == 10
        and stats.get("loading_cleared") == 10
        and stats.get("completed") == 10
        and stats.get("cancelled", 0) == 0
        and pending == 0
    )
    _snapshot_session(host, "E_ui_callback")
    return {
        "name": "E_UI_CALLBACK_COMPLETION",
        "passed": ok,
        "stats": stats,
        "loading_pending": pending,
        "session_id": rt.session_id,
    }


def test_f_interim_replacement() -> dict[str, Any]:
    host = bind_host(FakeHost())
    rt = _begin_session(host)
    worker = _attach_worker(host, run_id=rt.run_id or rt.session_id)

    for t in ["My..", "My Name..", "My Name is...."]:
        host._latest_interim_text = t
        host._update_interim_line_only()
    host._append_pending_interim_to_display()
    interim_lines = [ln for ln in host.initial_verse_box.get().splitlines() if "⏳" in ln]
    host._clear_interim_tail()

    stable = "My name is Tariqul."
    apply_result = _ledger_apply_stable(stable, seq=1)
    host._on_store_segment_added(1, stable)
    pump_ui(host)
    host.submit_text_for_translation(stable, speaker=1, force_flush_previous=True)
    pump_ui(host)
    _drain_worker(worker, host)

    permanent = [ln for ln in host.initial_verse_box.get().splitlines() if ln.strip()]
    waiting = [ln for ln in permanent if "⏳" in ln]
    c = _worker_counters(worker)
    prefix = host._ui_speaker_label_text()
    expected = f"{prefix}{stable}".strip()
    # Source line may be "Speaker: My name is Tariqul." or similar
    source_ok = any(stable in ln for ln in permanent) and len(waiting) == 0
    interim_ok = len(interim_lines) == 1
    commit_ok = apply_result.get("ok") is not False and c["accepted"] == 1 and c["sent"] == 1
    ok = interim_ok and source_ok and commit_ok
    return {
        "name": "F_INTERIM_REPLACEMENT",
        "passed": ok,
        "interim_lines": len(interim_lines),
        "permanent_lines": permanent,
        "waiting_remaining": len(waiting),
        "expected": expected,
        "jobs_accepted": c["accepted"],
        "provider_requests": c["sent"],
        "session_id": rt.session_id,
    }


def test_g_stop_while_callbacks_pending() -> dict[str, Any]:
    from alpha.utils.session_runtime import (
        mark_session_finalizing,
        session_accepts_callback,
    )

    host = bind_host(FakeHost())
    rt = _begin_session(host)
    worker = _attach_worker(host, run_id=rt.run_id or rt.session_id)

    texts = [f"Pending callback {i}." for i in range(1, 4)]
    for i, text in enumerate(texts, start=1):
        _ledger_apply_stable(text, seq=i)
        host.submit_text_for_translation(text, speaker=1, force_flush_previous=True)
        pump_ui(host)

    # Pull jobs but do not complete yet
    jobs = []
    while True:
        try:
            jobs.append(worker._queue.get_nowait())
        except Exception:
            break

    # Begin Stop while results still pending
    host._is_stopping = True
    host._is_finalizing = True
    mark_session_finalizing(host)
    assert session_accepts_callback(host, rt.session_id) is True

    for job, tgt in jobs:
        result = worker._fake_translate(job, tgt)
        with worker._lock:
            worker._counters["TRANSLATION_REQUESTS_SENT"] += 1
            worker._counters["requests_sent"] += 1
        worker._handle_result(result)
        pump_ui(host)

    pump_ui(host, rounds=12)
    c = _worker_counters(worker)
    pending_loading = host.loading_indicators_pending()
    stats = dict(host._ui_callback_stats)

    # Finish stop and ensure next Start works
    from alpha.transcription.canonical_transcript_ledger import freeze_snapshot
    from alpha.utils.session_runtime import mark_session_stopped

    freeze_snapshot()
    mark_session_stopped(host)
    _idle_flags(host)

    rt2 = _begin_session(host)
    _attach_worker(host, run_id=rt2.run_id or rt2.session_id)
    next_ok = _commit_and_translate(host, ["After stop-pending restart."])
    _stop_session(host, freeze=True)

    ok = (
        c["queue"] == 0
        and c["held"] == 0
        and pending_loading == 0
        and int(stats.get("cancelled", 0) or 0) == 0
        and int(stats.get("completed", 0) or 0) >= 3
        and rt.session_id != rt2.session_id
        and next_ok["applied"] >= 1
        and next_ok["commits"] >= 1
    )
    return {
        "name": "G_STOP_WHILE_CALLBACKS_PENDING",
        "passed": ok,
        "stats": stats,
        "worker": c,
        "loading_pending": pending_loading,
        "session_a": rt.session_id,
        "session_b": rt2.session_id,
        "next_session": next_ok,
    }


def test_h_sparse_ordering() -> dict[str, Any]:
    from alpha.translation.translation_worker import TranslationWorker

    # Sparse IDs
    order: list[int] = []
    worker = TranslationWorker(run_id="sparse", evidence_dir=None)
    worker._enabled = True
    worker._accepting = True
    worker.on_translation_ready = lambda r: order.append(
        int(r.source_segment_id or r.segment_id)
    )
    worker._fake_translate = lambda job, tgt: _make_result(job, tgt)
    worker._translate_job = worker._fake_translate  # type: ignore
    for sid, text in ((1, "a"), (3, "b"), (5, "c"), (9, "d")):
        worker.enqueue_stable_segment(
            segment_id=sid,
            source_language="en",
            source_text=text,
            is_interim=False,
            run_id="sparse",
        )
    _drain_worker(worker)
    sparse_ok = order == [1, 3, 5, 9]

    # Out-of-order completion
    order2: list[int] = []
    worker2 = TranslationWorker(run_id="ooo", evidence_dir=None)
    worker2._enabled = True
    worker2._accepting = True
    worker2.on_translation_ready = lambda r: order2.append(
        int(r.source_segment_id or r.segment_id)
    )
    for sid, text in ((1, "a"), (3, "b"), (5, "c"), (9, "d")):
        worker2.enqueue_stable_segment(
            segment_id=sid,
            source_language="en",
            source_text=text,
            is_interim=False,
            run_id="ooo",
        )
    jobs = []
    while True:
        try:
            jobs.append(worker2._queue.get_nowait())
        except Exception:
            break
    by_seq = {job.translation_sequence: (job, tgt) for job, tgt in jobs}
    completion_seq = [3, 1, 4, 2]
    for seq in completion_seq:
        job, tgt = by_seq[seq]
        worker2._handle_result(_make_result(job, tgt))
    ooo_ok = order2 == [1, 3, 5, 9]

    ok = sparse_ok and ooo_ok
    return {
        "name": "H_SPARSE_ORDERING_REGRESSION",
        "passed": ok,
        "sparse_order": order,
        "ooo_display_order": order2,
        "completion_sequences": completion_seq,
    }


def test_loading_state() -> dict[str, Any]:
    host = bind_host(FakeHost())
    host._live_session_id = "sess-loading"
    host._show_translation_loading_item(segment_id=1, session_id=host._live_session_id)
    host._clear_translation_loading_item(
        segment_id=1,
        terminal_state="completed",
        session_id=host._live_session_id,
        replace_with_text="ok",
    )
    host._show_translation_loading_item(segment_id=2, session_id=host._live_session_id)
    host._clear_translation_loading_item(
        segment_id=2, terminal_state="permanently_failed", session_id=host._live_session_id
    )
    host._show_translation_loading_item(segment_id=3, session_id=host._live_session_id)
    host._clear_translation_loading_item(
        segment_id=3, terminal_state="cancelled", session_id=host._live_session_id
    )
    pending = host.loading_indicators_pending()
    return {"name": "LOADING_STATE", "passed": pending == 0, "loading_pending_at_exit": pending}


def test_session_factory() -> dict[str, Any]:
    """Validate begin_live_session constructs fresh runtime fields."""
    from alpha.transcription.canonical_transcript_ledger import is_frozen
    from alpha.utils.session_runtime import begin_live_session, get_session_object_identity

    host = bind_host(FakeHost())
    host._listen_language = "en"
    rt = begin_live_session(host)
    ident = get_session_object_identity(host)
    checks = {
        "has_session_id": bool(rt.session_id),
        "state_starting": rt.state == "STARTING",
        "ledger_not_frozen": is_frozen() is False,
        "loading_empty": len(host._translation_loading_items) == 0,
        "seq_zero": int(host._translation_segment_seq or 0) == 0,
        "stats_zero": all(int(v or 0) == 0 for v in host._ui_callback_stats.values()),
        "identity_matches": ident.get("session_id") == rt.session_id,
    }
    ok = all(checks.values())
    return {
        "name": "SESSION_FACTORY",
        "passed": ok,
        "checks": checks,
        "runtime": {
            "session_id": rt.session_id,
            "run_id": rt.run_id,
            "ledger_generation": rt.ledger_generation,
            "ledger_object_id": rt.ledger_object_id,
            "state": rt.state,
        },
        "identity": ident,
    }


def test_japanese_freeze() -> dict[str, Any]:
    from alpha import stt_settings

    keys = ["DEEPGRAM_MODEL", "DEEPGRAM_ENDPOINTING_MS", "DEEPGRAM_UTTERANCE_END_MS"]
    present = {k: hasattr(stt_settings, k) for k in keys}
    return {"name": "JAPANESE_FREEZE", "passed": all(present.values()), "present": present}


def test_english_freeze() -> dict[str, Any]:
    from alpha.constants import ENGLISH_DIARIZATION_ENABLED
    from alpha.utils.english_deepgram_request import (
        ENGLISH_DIARIZE_MODE_PRODUCTION,
        build_english_live_query_params,
    )

    params = build_english_live_query_params()
    has_diarize = "diarize" in params or "diarize_model" in params
    ok = (
        ENGLISH_DIARIZATION_ENABLED is False
        and ENGLISH_DIARIZE_MODE_PRODUCTION == "off"
        and not has_diarize
    )
    return {
        "name": "ENGLISH_FREEZE",
        "passed": ok,
        "ENGLISH_DIARIZATION_ENABLED": ENGLISH_DIARIZATION_ENABLED,
        "mode": ENGLISH_DIARIZE_MODE_PRODUCTION,
        "params_have_diarize": has_diarize,
    }


def main() -> int:
    global _FROZEN_LEDGER_ERRORS, _INTENTIONAL_FROZEN_PROBES, _SESSION_IDENTITIES
    _FROZEN_LEDGER_ERRORS = 0
    _INTENTIONAL_FROZEN_PROBES = 0
    _SESSION_IDENTITIES = []

    stamp = _utc_stamp()
    out_dir = ROOT / "troubleshooting" / f"session_lifecycle_repair{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    runners = [
        test_compile,
        test_session_factory,
        test_a_start_stop_start,
        test_b_three_consecutive,
        test_c_short_session_restart,
        test_d_canonical_translation_authority,
        test_e_ui_callback_completion,
        test_f_interim_replacement,
        test_loading_state,
        test_g_stop_while_callbacks_pending,
        test_h_sparse_ordering,
        test_japanese_freeze,
        test_english_freeze,
    ]
    for fn in runners:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(
                {
                    "name": getattr(fn, "__name__", "UNKNOWN"),
                    "passed": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    by = {r["name"]: r for r in results}

    def _ok(name: str) -> bool:
        return bool(by.get(name, {}).get("passed"))

    start_stop = _ok("A_START_STOP_START")
    three = _ok("B_THREE_CONSECUTIVE_SESSIONS")
    short = _ok("C_SHORT_SESSION_RESTART")
    canonical = _ok("D_CANONICAL_STABLE_TRANSLATION_WIRING")
    ui_cb = _ok("E_UI_CALLBACK_COMPLETION")
    interim = _ok("F_INTERIM_REPLACEMENT")
    loading = _ok("LOADING_STATE")
    sparse = _ok("H_SPARSE_ORDERING_REGRESSION")
    stop_pending = _ok("G_STOP_WHILE_CALLBACKS_PENDING")
    ja_freeze = _ok("JAPANESE_FREEZE")
    en_freeze = _ok("ENGLISH_FREEZE")
    compile_ok = _ok("PRODUCTION_COMPILE")
    factory_ok = _ok("SESSION_FACTORY")

    # Session-lifecycle gate: A–H + loading + factory + compile + freezes
    lifecycle_pass = all(
        [
            start_stop,
            three,
            short,
            canonical,
            ui_cb,
            interim,
            loading,
            sparse,
            stop_pending,
            ja_freeze,
            en_freeze,
            compile_ok,
            factory_ok,
        ]
    )
    ready = bool(lifecycle_pass and _FROZEN_LEDGER_ERRORS == 0)

    # Evidence files
    _write_json(out_dir / "SESSION_FACTORY_VALIDATION.json", by.get("SESSION_FACTORY", {}))
    _write_json(out_dir / "START_STOP_START_VALIDATION.json", by.get("A_START_STOP_START", {}))
    _write_json(out_dir / "THREE_SESSION_VALIDATION.json", by.get("B_THREE_CONSECUTIVE_SESSIONS", {}))
    _write_json(
        out_dir / "SHORT_SESSION_RESTART_VALIDATION.json",
        by.get("C_SHORT_SESSION_RESTART", {}),
    )
    _write_json(
        out_dir / "CANONICAL_STABLE_TRANSLATION_WIRING.json",
        by.get("D_CANONICAL_STABLE_TRANSLATION_WIRING", {}),
    )
    _write_json(
        out_dir / "UI_CALLBACK_COMPLETION_VALIDATION.json",
        by.get("E_UI_CALLBACK_COMPLETION", {}),
    )
    _write_json(out_dir / "LOADING_STATE_VALIDATION.json", by.get("LOADING_STATE", {}))
    _write_json(
        out_dir / "INTERIM_REPLACEMENT_VALIDATION.json",
        by.get("F_INTERIM_REPLACEMENT", {}),
    )
    _write_json(
        out_dir / "SPARSE_ORDERING_REGRESSION.json",
        {
            "sparse_ooo": by.get("H_SPARSE_ORDERING_REGRESSION", {}),
            "stop_while_pending": by.get("G_STOP_WHILE_CALLBACKS_PENDING", {}),
            "passed": sparse and stop_pending,
        },
    )
    # Session A/B/C identities from accumulated snapshots
    abc = {
        "sessions": _SESSION_IDENTITIES,
        "session_a_b_c": [
            s
            for s in _SESSION_IDENTITIES
            if s.get("label")
            in {
                "A_start",
                "B_start",
                "C_start",
                "A_stop",
                "B_stop",
                "C_stop",
            }
            or str(s.get("label", "")).endswith("_start")
            or str(s.get("label", "")).endswith("_stop")
        ],
        "frozen_ledger_errors_total": _FROZEN_LEDGER_ERRORS,
    }
    # Prefer three-consecutive labels if present
    three_payload = by.get("B_THREE_CONSECUTIVE_SESSIONS", {})
    if three_payload.get("sessions"):
        abc["A"] = three_payload["sessions"][0]
        abc["B"] = three_payload["sessions"][1]
        abc["C"] = three_payload["sessions"][2]
    _write_json(out_dir / "SESSION_OBJECT_IDENTITY.json", abc)
    _write_json(
        out_dir / "PRODUCTION_COMPILE_RESULT.json",
        by.get("PRODUCTION_COMPILE", {}),
    )

    decision = {
        "STATUS": "READY_FOR_LIVE_RETEST" if ready else "BLOCKED",
        "READY_FOR_LIVE_RETEST": ready,
        "FROZEN_LEDGER_ERRORS": _FROZEN_LEDGER_ERRORS,
        "results": results,
        "timestamp_utc": stamp,
        "evidence_dir": str(out_dir),
    }
    _write_json(out_dir / "PRE_LIVE_SESSION_REPAIR_DECISION.json", decision)

    lines = [
        "Alpha Live Translator — Session Lifecycle Repair Pre-live Validation",
        f"timestamp: {stamp}",
        f"SESSION_LIFECYCLE_TESTS = {'PASSED' if lifecycle_pass else 'FAILED'}",
        f"START_STOP_START = {'PASSED' if start_stop else 'FAILED'}",
        f"THREE_CONSECUTIVE_SESSIONS = {'PASSED' if three else 'FAILED'}",
        f"SHORT_SESSION_RESTART = {'PASSED' if short else 'FAILED'}",
        f"FROZEN_LEDGER_ERRORS = {_FROZEN_LEDGER_ERRORS}",
        f"CANONICAL_STABLE_TRANSLATION_WIRING = {'PASSED' if canonical else 'FAILED'}",
        f"UI_CALLBACK_COMPLETION = {'PASSED' if ui_cb else 'FAILED'}",
        f"LOADING_STATE = {'PASSED' if loading else 'FAILED'}",
        f"INTERIM_REPLACEMENT = {'PASSED' if interim else 'FAILED'}",
        f"SPARSE_ORDERING_REGRESSION = {'PASSED' if sparse else 'FAILED'}",
        f"JAPANESE_FREEZE = {'PASSED' if ja_freeze else 'FAILED'}",
        f"ENGLISH_FREEZE = {'PASSED' if en_freeze else 'FAILED'}",
        f"READY_FOR_LIVE_RETEST = {str(ready).lower()}",
        f"EVIDENCE_DIR={out_dir}",
        "",
    ]
    if not ready:
        fails = [r["name"] for r in results if not r.get("passed")]
        lines.append("FAILING: " + ", ".join(fails))
        lines.append("")
    report = "\n".join(lines)
    (out_dir / "PRE_LIVE_SESSION_REPAIR_REPORT.txt").write_text(report, encoding="utf-8")
    (out_dir / "Cursor final report.txt").write_text(report, encoding="utf-8")
    _write_json(
        out_dir / "implementation_manifest.json",
        {
            "validator": "tools/validate_session_lifecycle_repair.py",
            "production_modules_exercised": [
                "alpha/utils/session_runtime.py",
                "alpha/transcription/canonical_transcript_ledger.py",
                "alpha/translation/translation_worker.py",
                "alpha/ui/main_window.py",
                "alpha/transcription/duplicate_protection.py",
                "alpha/utils/ui_event_bus.py",
                "alpha/constants.py",
                "alpha/stt_settings.py",
                "alpha/utils/english_deepgram_request.py",
            ],
            "timestamp_utc": stamp,
        },
    )

    # Required printed gate lines (exact keys)
    print(f"SESSION_LIFECYCLE_TESTS = {'PASSED' if lifecycle_pass else 'FAILED'}")
    print(f"START_STOP_START = {'PASSED' if start_stop else 'FAILED'}")
    print(f"THREE_CONSECUTIVE_SESSIONS = {'PASSED' if three else 'FAILED'}")
    print(f"SHORT_SESSION_RESTART = {'PASSED' if short else 'FAILED'}")
    print(f"FROZEN_LEDGER_ERRORS = {_FROZEN_LEDGER_ERRORS}")
    print(
        f"CANONICAL_STABLE_TRANSLATION_WIRING = {'PASSED' if canonical else 'FAILED'}"
    )
    print(f"UI_CALLBACK_COMPLETION = {'PASSED' if ui_cb else 'FAILED'}")
    print(f"LOADING_STATE = {'PASSED' if loading else 'FAILED'}")
    print(f"INTERIM_REPLACEMENT = {'PASSED' if interim else 'FAILED'}")
    print(f"SPARSE_ORDERING_REGRESSION = {'PASSED' if sparse else 'FAILED'}")
    print(f"JAPANESE_FREEZE = {'PASSED' if ja_freeze else 'FAILED'}")
    print(f"ENGLISH_FREEZE = {'PASSED' if en_freeze else 'FAILED'}")
    print(f"READY_FOR_LIVE_RETEST = {str(ready).lower()}")
    print(f"EVIDENCE_DIR={out_dir}")
    return 0 if ready else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        print("SESSION_LIFECYCLE_TESTS = FAILED")
        print("READY_FOR_LIVE_RETEST = false")
        raise SystemExit(2)
