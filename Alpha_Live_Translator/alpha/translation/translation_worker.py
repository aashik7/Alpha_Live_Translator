"""Async Stable-only DeepL translation worker.
Never runs on audio / Deepgram / assembler / UI threads.
Consumes committed Stable segments only; never mutates source transcripts.
Ordering uses translation_sequence (dense 1..N on accept), never source segment_id
arithmetic. Sparse / gapped source IDs cannot block later commits.
"""
from __future__ import annotations
import hashlib
import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from alpha.constants import (
    TRANSLATION_ENABLED,
    TRANSLATION_CIRCUIT_BREAK_AFTER,
    TRANSLATION_CIRCUIT_COOLDOWN_MAX_S,
    TRANSLATION_CIRCUIT_COOLDOWN_S,
    TRANSLATION_CONTEXT_LINES,
    TRANSLATION_CONTEXT_MAX_CHARS,
    TRANSLATION_MAX_RETRIES,
    TRANSLATION_PROVIDER,
    TRANSLATION_QUEUE_MAX_SIZE,
    TRANSLATION_SHUTDOWN_TIMEOUT_SECONDS,
    TRANSLATE_STABLE_ONLY,
)
from alpha.translation.deepl_client import DeepLClient, DeepLError
from alpha.translation.language_map import get_deepl_source_code, target_for_source
from alpha.utils.logging_utils import get_logger
logger = get_logger(__name__)
_STOP = object()
TERMINAL_COMPLETED = "completed"
TERMINAL_PERMANENTLY_FAILED = "permanently_failed"
TERMINAL_CANCELLED_SHUTDOWN = "cancelled_during_bounded_shutdown"
TERMINAL_SUPERSEDED = "superseded"
TERMINAL_CANCELLED = "cancelled"

def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def _percentile(sorted_vals: List[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))

def _latency_stats(values: List[float]) -> Dict[str, Optional[float]]:
    s = sorted(float(v) for v in values)
    return {
        "p50": _percentile(s, 50),
        "p95": _percentile(s, 95),
        "max": float(s[-1]) if s else None,
        "count": len(s),
    }

@dataclass
class StableTranslationJob:
    run_id: str
    segment_id: int  # source_segment_id (lineage only; may be sparse)
    source_language: str
    source_text: str
    source_text_hash: str
    stable_committed_at: float
    source_character_count: int = 0
    queued_at: float = 0.0
    stable_commit_timestamp: float = 0.0
    translation_sequence: int = 0
    source_segment_id: int = 0
    canonical_utterance_id: str = ""
    source_version: int = 1
    source_record_id: str = ""
    session_id: str = ""
    def __post_init__(self) -> None:
        if not self.source_character_count:
            self.source_character_count = len(self.source_text or "")
        if not self.source_text_hash:
            self.source_text_hash = _sha256_text(self.source_text)
        if not self.stable_committed_at and self.stable_commit_timestamp:
            self.stable_committed_at = float(self.stable_commit_timestamp)
        if not self.stable_commit_timestamp and self.stable_committed_at:
            self.stable_commit_timestamp = float(self.stable_committed_at)
        if not self.source_segment_id:
            self.source_segment_id = int(self.segment_id)

@dataclass
class TranslationResult:
    run_id: str
    segment_id: int  # source_segment_id (lineage)
    source_language: str
    target_language: str
    source_text: str
    source_text_hash: str
    translated_text: str
    status: str
    retry_count: int = 0
    error_code: str = ""
    source_character_count: int = 0
    translation_sequence: int = 0
    source_segment_id: int = 0
    terminal_state: str = ""
    canonical_utterance_id: str = ""
    source_version: int = 1
    source_record_id: str = ""
    session_id: str = ""
    obsolete_result_rejected: bool = False
    # Timestamps
    stable_committed_at: float = 0.0
    queued_at: float = 0.0
    started_at: float = 0.0
    provider_completed_at: float = 0.0
    ordered_commit_at: float = 0.0
    ui_update_scheduled_at: float = 0.0
    ui_update_completed_at: float = 0.0
    # Latencies
    queue_wait_ms: float = 0.0
    provider_latency_ms: float = 0.0
    ordering_wait_ms: float = 0.0
    translation_end_to_end_ms: float = 0.0
    ui_end_to_end_ms: float = 0.0
    # Back-compat
    latency_ms: float = 0.0
    completed_at: float = 0.0
    def __post_init__(self) -> None:
        if not self.source_segment_id:
            self.source_segment_id = int(self.segment_id)
TranslationJob = StableTranslationJob

class TranslationWorker:
    """Bounded queue + background DeepL worker with ordered UI commits."""
    def __init__(
        self,
        *,
        run_id: str = "",
        evidence_dir: Optional[Path] = None,
        on_translation_ready: Optional[Callable[[TranslationResult], None]] = None,
        on_callback_exception: Optional[Callable[[BaseException, TranslationResult], None]] = None,
        client: Optional[DeepLClient] = None,
        enabled: Optional[bool] = None,
    ):
        self.run_id = str(run_id or "")
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.on_translation_ready = on_translation_ready
        # Optional observer for harnesses / diagnostics. Production UI leaves this
        # unset; exceptions are still logged and never re-raised into the worker.
        self.on_callback_exception = on_callback_exception
        self._enabled = TRANSLATION_ENABLED if enabled is None else bool(enabled)
        self._provider = str(TRANSLATION_PROVIDER or "deepl").lower()
        self._queue: queue.Queue = queue.Queue(maxsize=int(TRANSLATION_QUEUE_MAX_SIZE))
        self._stop = threading.Event()
        self._drain_complete = threading.Event()
        self._accepting = True
        self._quota_disabled = False
        # Item 45 circuit breaker. `_quota_disabled` already handles the
        # permanent case; this handles a *transient* outage, where every job
        # would otherwise spend the full retry ladder (~7s) before failing.
        # Consecutive failures only -- one success closes it.
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_cooldown_s = float(TRANSLATION_CIRCUIT_COOLDOWN_S)
        self._circuit_open_count = 0
        self._thread: Optional[threading.Thread] = None
        self._client = client
        self._lock = threading.RLock()
        self._in_flight = 0
        self._shutdown_requested = False
        self._highest_accepted_segment_id = 0
        self._next_translation_sequence = 0
        self._next_translation_sequence_to_commit = 1
        self._seen_request_ids: Set[int] = set()
        # fixes TASK_3A_FINDINGS.md Item 3: scoped by (canonical_utterance_id,
        # source_version), not a bare global text hash -- two different
        # utterances saying the same short phrase ("Thank you.") must both
        # be translated, not have the second one rejected as a duplicate.
        self._seen_text_hash_by_utterance_version: Dict[str, str] = {}
        self._accepted_sequences: Set[int] = set()
        self._provider_sent_sequences: Set[int] = set()
        self._committed_sequences: Set[int] = set()
        self._failed_sequences: Set[int] = set()
        self._completed_sequences: Set[int] = set()
        self._sequence_to_source: Dict[int, int] = {}
        self._held: Dict[int, TranslationResult] = {}  # keyed by translation_sequence
        self._latest_version_by_utterance: Dict[str, int] = {}
        self._revision_events: List[Dict[str, Any]] = []
        # Item 50: (source_language, text) tail used to build DeepL `context`.
        self._recent_source_lines: List[tuple] = []
        self._max_queue_depth = 0
        self._status_message = ""
        self._unfinished_sequences: List[int] = []
        self._unfinished_ids: List[int] = []
        self._lat_queue: List[float] = []
        self._lat_provider: List[float] = []
        self._lat_ordering: List[float] = []
        self._lat_e2e: List[float] = []
        self._lat_ui_e2e: List[float] = []
        self._counters: Dict[str, int] = {
            "INTERIM_SUBMISSIONS_REJECTED": 0,
            "DUPLICATE_SUBMISSIONS_REJECTED": 0,
            "EMPTY_SUBMISSIONS_REJECTED": 0,
            "UNSUPPORTED_LANGUAGE_SUBMISSIONS_REJECTED": 0,
            "STABLE_TRANSLATION_JOBS_ACCEPTED": 0,
            "TRANSLATION_JOBS_QUEUED": 0,
            "TRANSLATION_REQUESTS_SENT": 0,
            "TRANSLATION_COMMITS_COMPLETED": 0,
            "TRANSLATION_UI_UPDATES_SCHEDULED": 0,
            "TRANSLATION_UI_UPDATES_COMPLETED": 0,
            "TRANSLATION_REQUESTS_SENT_FROM_INTERIM": 0,
            "DUPLICATE_TRANSLATION_REQUESTS_SENT": 0,
            "DUPLICATE_TRANSLATION_COMMITS": 0,
            "OUT_OF_ORDER_TRANSLATION_COMMITS": 0,
            "MISSING_ACCEPTED_TRANSLATION_SEQUENCES": 0,
            "out_of_order_completions": 0,
            "successful": 0,
            "failed": 0,
            "retries": 0,
            "source_transcript_modifications": 0,
            "source_characters_sent": 0,
            "interim_requests": 0,
            "duplicate_requests": 0,
            "stable_segments_received": 0,
            "jobs_queued": 0,
            "requests_sent": 0,
        }
        self._events_path: Optional[Path] = None
        if self.evidence_dir is not None:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            self._events_path = self.evidence_dir / "translation_events.jsonl"
    @property
    def status_message(self) -> str:
        with self._lock:
            return self._status_message

    # ---- Item 45: transient-outage circuit breaker -----------------------
    #
    # Deliberately separate from `_quota_disabled`, which is permanent for the
    # session and stops accepting work. This one never stops accepting and
    # never blocks a transcript commit: it only short-circuits the *provider
    # call*, so a segment fails fast and visibly instead of spending the full
    # retry ladder on every job while the provider is down.

    def circuit_is_open(self, *, now: Optional[float] = None) -> bool:
        """True while the breaker is holding calls off. Not a permanent state."""
        current = time.time() if now is None else now
        with self._lock:
            return current < self._circuit_open_until

    @property
    def degraded(self) -> bool:
        """Visible degradation for item 47's status indicator."""
        return self.circuit_is_open() or self._quota_disabled

    def _record_translation_success(self) -> None:
        with self._lock:
            was_open = self._circuit_open_until > 0.0
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
            self._circuit_cooldown_s = float(TRANSLATION_CIRCUIT_COOLDOWN_S)
            if was_open:
                self._status_message = "Translation recovered."
                logger.info("DeepL circuit closed after a successful translation")

    def _record_translation_failure(self, code: str, *, now: Optional[float] = None) -> bool:
        """Count a failed job; open the breaker on the Nth consecutive one.

        Returns True when this failure opened (or re-opened) the circuit.
        """
        current = time.time() if now is None else now
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures < int(TRANSLATION_CIRCUIT_BREAK_AFTER):
                return False
            if current < self._circuit_open_until:
                return False
            # Re-opening during a continuing outage backs further off, capped,
            # so a long outage stops hammering the provider -- but the cooldown
            # always expires, so recovery is never refused.
            if self._circuit_open_count:
                self._circuit_cooldown_s = min(
                    self._circuit_cooldown_s * 2.0,
                    float(TRANSLATION_CIRCUIT_COOLDOWN_MAX_S),
                )
            self._circuit_open_until = current + self._circuit_cooldown_s
            self._circuit_open_count += 1
            self._status_message = (
                f"Translation degraded (provider failing, retrying in "
                f"{int(self._circuit_cooldown_s)}s)."
            )
            logger.warning(
                "DeepL circuit opened after %d consecutive failures (last=%s), "
                "cooldown %.0fs",
                self._consecutive_failures,
                code,
                self._circuit_cooldown_s,
            )
            return True
    @property
    def worker_stopped(self) -> bool:
        return self._thread is None or not self._thread.is_alive()
    def start(self) -> bool:
        if not self._enabled:
            self._status_message = "Translation disabled."
            return False
        if self._provider != "deepl":
            self._status_message = f"Unsupported provider: {self._provider}"
            return False
        if self._client is None:
            try:
                self._client = DeepLClient()
            except DeepLError as exc:
                self._status_message = "Translation unavailable (client init)."
                logger.warning("DeepL init failed: %s", exc.code)
                return False
        if not self._client.available:
            self._status_message = "Translation unavailable (missing DEEPL_AUTH_KEY)."
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._drain_complete.clear()
        self._accepting = True
        self._shutdown_requested = False
        self._thread = threading.Thread(
            target=self._run,
            name="TranslationWorker",
            daemon=True,
        )
        self._thread.start()
        self._write_sanitized_config()
        return True
    def enqueue_stable_segment(
        self,
        *,
        segment_id: int,
        source_language: str,
        source_text: str,
        stable_commit_timestamp: Optional[float] = None,
        is_interim: bool = False,
        run_id: Optional[str] = None,
        canonical_utterance_id: str = "",
        source_version: int = 1,
        source_record_id: str = "",
        session_id: str = "",
        force: bool = False,
    ) -> bool:
        """Accept a newly committed Stable segment, or reject with explicit counters.

        fixes TASK_9_REPORT.md Issue 1: main_window.py::_begin_graceful_stop
        calls stop_accepting() synchronously on the UI thread the instant
        Stop is clicked -- before stop_finalize_worker.py's background
        finalize sequence (including its translation_reconciliation step)
        has even started. That makes the ordinary `not self._accepting`
        gate below unconditionally reject every reconciliation forced
        submission, 100% of the time, regardless of whether the record was
        genuinely missed. `force=True` is a deliberate, narrow bypass of
        only that specific gate -- reconciliation already re-confirmed the
        record is a genuine committed, translation-eligible gap, and the
        job still lands in the same queue TranslationWorker.shutdown()
        already bounded-drains, so it is delivered through the normal
        pipeline, not a side channel. _quota_disabled/_enabled are left
        gating even forced submissions -- those reflect the provider
        genuinely being unable to accept work, not merely that Stop was
        clicked.
        """
        with self._lock:
            self._counters["stable_segments_received"] += 1
        if is_interim:
            with self._lock:
                self._counters["INTERIM_SUBMISSIONS_REJECTED"] += 1
                self._counters["interim_requests"] += 1
            return False
        if not TRANSLATE_STABLE_ONLY:
            return False
        if not force and not self._accepting:
            with self._lock:
                self._counters["NOT_ACCEPTING_SUBMISSIONS_REJECTED"] = int(
                    self._counters.get("NOT_ACCEPTING_SUBMISSIONS_REJECTED", 0) or 0
                ) + 1
            return False
        if self._quota_disabled or not self._enabled:
            with self._lock:
                self._counters["QUOTA_OR_DISABLED_SUBMISSIONS_REJECTED"] = int(
                    self._counters.get("QUOTA_OR_DISABLED_SUBMISSIONS_REJECTED", 0) or 0
                ) + 1
            return False
        text = (source_text or "").strip()
        if not text:
            with self._lock:
                self._counters["EMPTY_SUBMISSIONS_REJECTED"] += 1
            return False
        sid = int(segment_id)
        text_hash = _sha256_text(text)
        utterance_key = str(canonical_utterance_id or "").strip()
        version = max(1, int(source_version or 1))
        # fixes TASK_3A_FINDINGS.md Item 3: dedup key is scoped to this exact
        # (canonical_utterance_id, source_version); empty utterance_key means
        # identity can't be confirmed, so no hash-based dedup is attempted
        # for it (fail-closed -- only the per-submission segment_id guard
        # below still applies).
        utterance_version_key = f"{utterance_key}|{version}" if utterance_key else ""
        with self._lock:
            if sid in self._seen_request_ids:
                self._counters["DUPLICATE_SUBMISSIONS_REJECTED"] += 1
                self._counters["duplicate_requests"] += 1
                return False
            if (
                utterance_version_key
                and self._seen_text_hash_by_utterance_version.get(utterance_version_key)
                == text_hash
            ):
                self._counters["DUPLICATE_SUBMISSIONS_REJECTED"] += 1
                self._counters["duplicate_requests"] += 1
                return False
            if utterance_key:
                latest = int(self._latest_version_by_utterance.get(utterance_key, 0) or 0)
                if version < latest:
                    self._counters["OBSOLETE_SUBMISSIONS_REJECTED"] = int(
                        self._counters.get("OBSOLETE_SUBMISSIONS_REJECTED", 0) or 0
                    ) + 1
                    self._revision_events.append(
                        {
                            "canonical_utterance_id": utterance_key,
                            "source_version": version,
                            "accepted": False,
                            "reason": "obsolete_version_on_enqueue",
                            "terminal_state": TERMINAL_SUPERSEDED,
                        }
                    )
                    return False
                self._latest_version_by_utterance[utterance_key] = max(latest, version)
        src = get_deepl_source_code(source_language)
        tgt = target_for_source(source_language)
        if not src or not tgt:
            with self._lock:
                self._counters["UNSUPPORTED_LANGUAGE_SUBMISSIONS_REJECTED"] += 1
            return False
        now = time.time()
        stable_at = float(
            stable_commit_timestamp if stable_commit_timestamp is not None else now
        )
        with self._lock:
            self._next_translation_sequence += 1
            seq = int(self._next_translation_sequence)
            self._accepted_sequences.add(seq)
            self._sequence_to_source[seq] = sid
            self._seen_request_ids.add(sid)
            if utterance_version_key:
                self._seen_text_hash_by_utterance_version[utterance_version_key] = text_hash
            self._highest_accepted_segment_id = max(self._highest_accepted_segment_id, sid)
            self._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"] += 1
            self._counters["TRANSLATION_JOBS_QUEUED"] += 1
            self._counters["jobs_queued"] += 1
            self._revision_events.append(
                {
                    "canonical_utterance_id": utterance_key,
                    "source_record_id": str(source_record_id or ""),
                    "translation_sequence": seq,
                    "source_version": version,
                    "accepted": True,
                    "provider_started": False,
                    "session_id": str(session_id or ""),
                }
            )
        job = StableTranslationJob(
            run_id=str(run_id or self.run_id),
            segment_id=sid,
            source_segment_id=sid,
            translation_sequence=seq,
            source_language=src,
            source_text=text,
            source_text_hash=text_hash,
            stable_committed_at=stable_at,
            stable_commit_timestamp=stable_at,
            queued_at=now,
            canonical_utterance_id=utterance_key,
            source_version=version,
            source_record_id=str(source_record_id or ""),
            session_id=str(session_id or ""),
        )
        try:
            self._queue.put_nowait((job, tgt))
        except queue.Full:
            logger.warning(
                "Translation queue full; dropping source_segment_id=%s sequence=%s",
                sid,
                seq,
            )
            with self._lock:
                self._accepted_sequences.discard(seq)
                self._sequence_to_source.pop(seq, None)
                self._seen_request_ids.discard(sid)
                if (
                    utterance_version_key
                    and self._seen_text_hash_by_utterance_version.get(utterance_version_key)
                    == text_hash
                ):
                    self._seen_text_hash_by_utterance_version.pop(utterance_version_key, None)
                self._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"] = max(
                    0, self._counters["STABLE_TRANSLATION_JOBS_ACCEPTED"] - 1
                )
                self._counters["TRANSLATION_JOBS_QUEUED"] = max(
                    0, self._counters["TRANSLATION_JOBS_QUEUED"] - 1
                )
                self._counters["jobs_queued"] = max(0, self._counters["jobs_queued"] - 1)
            return False
        with self._lock:
            depth = self._queue.qsize()
            if depth > self._max_queue_depth:
                self._max_queue_depth = depth
        return True
    def stop_accepting(self) -> None:
        self._accepting = False
    def stop(self, timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self.shutdown(timeout_seconds=timeout_seconds)
    def shutdown(self, timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Drain accepted jobs (bounded), then stop worker and write summary."""
        self._accepting = False
        self._shutdown_requested = True
        timeout = float(
            timeout_seconds
            if timeout_seconds is not None
            else TRANSLATION_SHUTDOWN_TIMEOUT_SECONDS
        )
        deadline = time.time() + max(0.0, timeout)
        shutdown_started = time.time()
        while time.time() < deadline:
            with self._lock:
                qsize = self._queue.qsize()
                in_flight = int(self._in_flight)
                held = len(self._held)
                accepted = set(self._accepted_sequences)
                done = set(self._committed_sequences)
            if qsize == 0 and in_flight == 0 and held == 0 and accepted.issubset(done):
                break
            time.sleep(0.02)
        self._stop.set()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            remaining = max(0.05, deadline - time.time())
            self._thread.join(timeout=remaining)
        leftover_jobs: List[StableTranslationJob] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _STOP:
                continue
            job, _target_lang = item
            leftover_jobs.append(job)
        for job in leftover_jobs:
            cancelled = TranslationResult(
                run_id=job.run_id,
                segment_id=job.segment_id,
                source_segment_id=job.source_segment_id or job.segment_id,
                translation_sequence=int(job.translation_sequence),
                source_language=job.source_language,
                target_language=target_for_source(job.source_language) or "",
                source_text=job.source_text,
                source_text_hash=job.source_text_hash,
                translated_text="",
                status=TERMINAL_CANCELLED_SHUTDOWN,
                terminal_state=TERMINAL_CANCELLED_SHUTDOWN,
                error_code="cancelled_during_bounded_shutdown",
                source_character_count=job.source_character_count,
                stable_committed_at=job.stable_committed_at,
                queued_at=job.queued_at,
                started_at=time.time(),
                provider_completed_at=time.time(),
                completed_at=time.time(),
            )
            self._handle_result(cancelled)
        with self._lock:
            unresolved = sorted(
                seq
                for seq in self._accepted_sequences
                if seq not in self._committed_sequences
            )
        for seq in unresolved:
            src = self._sequence_to_source.get(seq, 0)
            cancelled = TranslationResult(
                run_id=self.run_id,
                segment_id=src,
                source_segment_id=src,
                translation_sequence=seq,
                source_language="",
                target_language="",
                source_text="",
                source_text_hash="",
                translated_text="",
                status=TERMINAL_CANCELLED_SHUTDOWN,
                terminal_state=TERMINAL_CANCELLED_SHUTDOWN,
                error_code="cancelled_during_bounded_shutdown",
                provider_completed_at=time.time(),
                completed_at=time.time(),
            )
            self._handle_result(cancelled)
        stop_duration_s = max(0.0, time.time() - shutdown_started)
        with self._lock:
            pending_q = int(self._queue.qsize())
            in_flight = int(self._in_flight)
            ordering_pending = len(self._held)
            unfinished_seq = sorted(
                seq
                for seq in self._accepted_sequences
                if seq not in self._committed_sequences
            )
            self._unfinished_sequences = unfinished_seq
            self._unfinished_ids = [
                self._sequence_to_source.get(seq, seq) for seq in unfinished_seq
            ]
            self._counters["MISSING_ACCEPTED_TRANSLATION_SEQUENCES"] = len(unfinished_seq)
            while True:
                try:
                    item = self._queue.get_nowait()
                    if item is not _STOP:
                        pending_q = max(pending_q, 1)
                except queue.Empty:
                    break
            summary = self._build_summary(
                pending_at_exit=0 if not unfinished_seq else max(pending_q, len(unfinished_seq)),
                in_flight_at_exit=0 if not unfinished_seq else in_flight,
                ordering_pending_at_exit=0 if not unfinished_seq else ordering_pending,
            )
            if unfinished_seq:
                summary["TRANSLATION_QUEUE_PENDING_AT_EXIT"] = max(
                    int(summary.get("TRANSLATION_QUEUE_PENDING_AT_EXIT") or 0),
                    len(unfinished_seq),
                )
            else:
                summary["TRANSLATION_QUEUE_PENDING_AT_EXIT"] = 0
                summary["TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT"] = 0
                summary["ORDERING_BUFFER_PENDING_AT_EXIT"] = 0
            summary["TRANSLATION_WORKER_STOPPED"] = bool(self.worker_stopped)
            summary["TRANSLATION_WORKER_STOP_TIMED_OUT"] = bool(
                self._shutdown_requested and not self.worker_stopped
            )
            summary["MISSING_TRANSLATION_SEGMENT_IDS"] = len(unfinished_seq)
            summary["MISSING_ACCEPTED_TRANSLATION_SEQUENCES"] = len(unfinished_seq)
            summary["UNFINISHED_TRANSLATION_SEGMENT_IDS"] = list(self._unfinished_ids)
            summary["UNRESOLVED_TRANSLATION_SEQUENCES"] = list(unfinished_seq)
            summary["FAILED_TRANSLATION_SEGMENT_IDS"] = sorted(
                {
                    self._sequence_to_source.get(seq, seq)
                    for seq in self._failed_sequences
                }
            )
            summary["FAILED_TRANSLATION_SEQUENCES"] = sorted(self._failed_sequences)
            summary["COMPLETED_TRANSLATION_SEGMENT_IDS"] = sorted(
                {
                    self._sequence_to_source.get(seq, seq)
                    for seq in self._completed_sequences
                }
            )
            summary["COMPLETED_TRANSLATION_SEQUENCES"] = sorted(self._completed_sequences)
            summary["highest_accepted_segment_id"] = self._highest_accepted_segment_id
            summary["highest_accepted_translation_sequence"] = self._next_translation_sequence
            summary["stop_duration_s"] = round(stop_duration_s, 3)
        self._write_summary(summary)
        return summary

    def _client_accepts_context(self) -> bool:
        """Does THIS client's `translate_text` take a `context` keyword?

        Item 50, and the reason this check exists at all: `translate_text`'s
        signature is a contract other code implements, not just DeepLClient's
        own method. Five implementations in this repo -- test doubles and
        `tools/validate_utterance_revision_repair.py` -- accept only
        `(text, source_lang, target_lang)`. Passing `context` to those raises
        TypeError inside the worker's translate loop, which fails the job and
        DROPS the translation; `test_task3c_acceptance_gate`'s "final Japanese
        line's translation must not be dropped on Stop" caught exactly that.

        So the keyword is offered only to clients that declare it. An older or
        third-party client keeps working untouched, which is the behaviour a
        quality improvement like context must not trade away.
        """
        cached = getattr(self, "_context_kwarg_supported", None)
        if cached is not None:
            return bool(cached)
        supported = False
        try:
            import inspect

            params = inspect.signature(self._client.translate_text).parameters
            supported = "context" in params or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except Exception:
            supported = False
        self._context_kwarg_supported = supported
        return supported

    def _translation_context(self, source_language: str) -> str:
        """The last few source lines, as background for the current one.

        Item 50. Each line is sent to DeepL alone, stripped of everything said
        before it, so pronouns, honorifics and topic have nothing to resolve
        against -- the usual symptom on the JA side is formality flipping
        between adjacent lines. DeepL reads `context` without translating or
        billing it.

        Only lines in the SAME source language are offered: mixing a Japanese
        line into the context of an English one would be noise at best. Bounded
        by both a line count and a character cap, because this grows for the
        length of the session and an unbounded string here would be sent on
        every single request.

        Never raises and never blocks: context is an optimisation, and failing
        to build it must not cost a translation.
        """
        try:
            lang = str(source_language or "").strip().upper()
            with self._lock:
                recent = [
                    text
                    for text_lang, text in self._recent_source_lines
                    if text_lang == lang
                ][-TRANSLATION_CONTEXT_LINES:]
            if not recent:
                return ""
            joined = " ".join(recent).strip()
            if len(joined) > TRANSLATION_CONTEXT_MAX_CHARS:
                joined = joined[-TRANSLATION_CONTEXT_MAX_CHARS:]
            return joined
        except Exception:
            return ""

    def _remember_source_line(self, source_language: str, text: str) -> None:
        """Keep the tail of what has been translated, for the next request."""
        try:
            cleaned = (text or "").strip()
            if not cleaned:
                return
            lang = str(source_language or "").strip().upper()
            with self._lock:
                self._recent_source_lines.append((lang, cleaned))
                # Keep a little more than TRANSLATION_CONTEXT_LINES so the
                # per-language filter above still has something to choose from
                # in a bilingual session, but never let this grow with session
                # length.
                excess = len(self._recent_source_lines) - (
                    TRANSLATION_CONTEXT_LINES * 4
                )
                if excess > 0:
                    del self._recent_source_lines[:excess]
        except Exception:
            pass

    def _run(self) -> None:
        while True:
            if self._stop.is_set() and self._queue.empty() and self._in_flight == 0:
                break
            try:
                item = self._queue.get(timeout=0.05 if self._stop.is_set() else 0.1)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            if item is _STOP:
                try:
                    self._queue.task_done()
                except Exception:
                    pass
                if self._queue.empty() and self._in_flight == 0:
                    break
                continue
            job, target_lang = item
            with self._lock:
                self._in_flight += 1
            try:
                result = self._translate_job(job, target_lang)
                self._handle_result(result)
            finally:
                with self._lock:
                    self._in_flight = max(0, self._in_flight - 1)
                try:
                    self._queue.task_done()
                except Exception:
                    pass
        self._drain_complete.set()
    def _translate_job(self, job: StableTranslationJob, target_lang: str) -> TranslationResult:
        started = time.time()
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "provider_request_started_at",
                segment_id=int(job.source_segment_id or job.segment_id),
                translation_sequence=int(job.translation_sequence),
            )
        except Exception:
            pass
        retries = 0
        last_err = ""
        # Item 45: while the breaker is open, skip the provider call rather
        # than spending the full retry ladder (~7s) on a provider known to be
        # down. The job still flows through the normal result/evidence path
        # below -- deliberately not an early return, so counters, latency and
        # the events file stay consistent with every other outcome. The
        # transcript never waits on this either way.
        circuit_skipped = self.circuit_is_open()
        status = "failed"
        translated = ""
        if circuit_skipped:
            last_err = "circuit_open"
        status = "failed"
        translated = ""
        original_hash = job.source_text_hash
        seq = int(job.translation_sequence)
        sid = int(job.source_segment_id or job.segment_id)
        with self._lock:
            if seq in self._provider_sent_sequences:
                self._counters["DUPLICATE_TRANSLATION_REQUESTS_SENT"] += 1
                return TranslationResult(
                    run_id=job.run_id,
                    segment_id=sid,
                    source_segment_id=sid,
                    translation_sequence=seq,
                    source_language=job.source_language,
                    target_language=target_lang,
                    source_text=job.source_text,
                    source_text_hash=job.source_text_hash,
                    translated_text="",
                    status=TERMINAL_PERMANENTLY_FAILED,
                    terminal_state=TERMINAL_PERMANENTLY_FAILED,
                    error_code="duplicate_before_provider",
                    source_character_count=job.source_character_count,
                    stable_committed_at=job.stable_committed_at,
                    queued_at=job.queued_at,
                    started_at=started,
                    provider_completed_at=time.time(),
                )
            self._provider_sent_sequences.add(seq)
            self._counters["TRANSLATION_REQUESTS_SENT"] += 1
            self._counters["requests_sent"] += 1
            self._counters["source_characters_sent"] += int(job.source_character_count)
        assert self._client is not None
        while not circuit_skipped:
            try:
                translate_kwargs = {
                    "source_lang": job.source_language,
                    "target_lang": target_lang,
                }
                if self._client_accepts_context():
                    hint = self._translation_context(job.source_language)
                    if hint:
                        translate_kwargs["context"] = hint
                translated = self._client.translate_text(
                    job.source_text, **translate_kwargs
                )
                if _sha256_text(job.source_text) != original_hash:
                    with self._lock:
                        self._counters["source_transcript_modifications"] += 1
                status = "success"
                last_err = ""
                # Item 50: remember AFTER a successful translation, so a line
                # that never reached the provider cannot pollute the context of
                # the next one.
                self._remember_source_line(job.source_language, job.source_text)
                break
            except DeepLError as exc:
                last_err = exc.code
                if exc.code == "quota_exceeded":
                    self._quota_disabled = True
                    self._accepting = False
                    self._status_message = "Translation paused (quota exceeded)."
                    status = "quota_exceeded"
                    break
                if exc.retryable and retries < int(TRANSLATION_MAX_RETRIES):
                    retries += 1
                    with self._lock:
                        self._counters["retries"] += 1
                    time.sleep(min(2.0 ** retries, 4.0))
                    continue
                status = "failed"
                break
            except Exception as exc:
                last_err = type(exc).__name__
                status = "failed"
                break
        # Item 45: feed the outcome to the breaker. Quota is deliberately NOT
        # counted here -- it already disables the worker permanently, and
        # double-counting it would just open a circuit nobody will consult.
        if status == "success":
            self._record_translation_success()
        elif status == "failed":
            self._record_translation_failure(last_err or "unknown")
        provider_done = time.time()
        try:
            from alpha.utils import live_pipeline_profile as lpp

            lpp.mark(
                "provider_response_received_at",
                segment_id=sid,
                translation_sequence=seq,
                status=status,
            )
        except Exception:
            pass
        queue_wait = max(0.0, (started - float(job.queued_at or started)) * 1000.0)
        provider_lat = max(0.0, (provider_done - started) * 1000.0)
        if status == "success":
            terminal = TERMINAL_COMPLETED
            with self._lock:
                self._counters["successful"] += 1
        else:
            terminal = TERMINAL_PERMANENTLY_FAILED
            with self._lock:
                self._counters["failed"] += 1
        with self._lock:
            self._lat_queue.append(queue_wait)
            self._lat_provider.append(provider_lat)
        result = TranslationResult(
            run_id=job.run_id,
            segment_id=sid,
            source_segment_id=sid,
            translation_sequence=seq,
            source_language=job.source_language,
            target_language=target_lang,
            source_text=job.source_text,
            source_text_hash=job.source_text_hash,
            translated_text=translated,
            status=status if status == "success" else terminal,
            terminal_state=terminal,
            retry_count=retries,
            error_code=last_err,
            source_character_count=job.source_character_count,
            stable_committed_at=job.stable_committed_at,
            queued_at=job.queued_at,
            started_at=started,
            provider_completed_at=provider_done,
            completed_at=provider_done,
            queue_wait_ms=queue_wait,
            provider_latency_ms=provider_lat,
            latency_ms=provider_lat,
            canonical_utterance_id=str(getattr(job, "canonical_utterance_id", "") or ""),
            source_version=int(getattr(job, "source_version", 1) or 1),
            source_record_id=str(getattr(job, "source_record_id", "") or ""),
            session_id=str(getattr(job, "session_id", "") or ""),
        )
        self._append_event(result, phase="provider_done")
        return result
    def _handle_result(self, result: TranslationResult) -> None:
        callbacks: List[TranslationResult] = []
        with self._lock:
            seq = int(result.translation_sequence or 0)
            if seq <= 0:
                return
            # Reject obsolete provider responses for a superseded utterance version.
            utterance_key = str(getattr(result, "canonical_utterance_id", "") or "")
            version = int(getattr(result, "source_version", 1) or 1)
            if utterance_key:
                latest = int(self._latest_version_by_utterance.get(utterance_key, 0) or 0)
                if latest and version < latest:
                    result.obsolete_result_rejected = True
                    result.terminal_state = TERMINAL_SUPERSEDED
                    result.status = TERMINAL_SUPERSEDED
                    result.translated_text = ""
                    self._counters["OBSOLETE_TRANSLATION_RESULTS_REJECTED"] = int(
                        self._counters.get("OBSOLETE_TRANSLATION_RESULTS_REJECTED", 0) or 0
                    ) + 1
                    self._revision_events.append(
                        {
                            "canonical_utterance_id": utterance_key,
                            "source_record_id": str(
                                getattr(result, "source_record_id", "") or ""
                            ),
                            "translation_sequence": seq,
                            "source_version": version,
                            "accepted": False,
                            "provider_completed": True,
                            "obsolete_result_rejected": True,
                            "terminal_state": TERMINAL_SUPERSEDED,
                            "loading_cleared": True,
                        }
                    )
            if seq > self._next_translation_sequence_to_commit:
                self._counters["out_of_order_completions"] += 1
            self._held[seq] = result
            while self._next_translation_sequence_to_commit in self._held:
                held = self._held.pop(self._next_translation_sequence_to_commit)
                held_seq = int(held.translation_sequence)
                if held_seq in self._committed_sequences:
                    self._counters["DUPLICATE_TRANSLATION_COMMITS"] += 1
                    self._next_translation_sequence_to_commit += 1
                    continue
                if held_seq != self._next_translation_sequence_to_commit:
                    self._counters["OUT_OF_ORDER_TRANSLATION_COMMITS"] += 1
                    self._held[held_seq] = held
                    break
                ordered_at = time.time()
                held.ordered_commit_at = ordered_at
                held.ordering_wait_ms = max(
                    0.0,
                    (ordered_at - float(held.provider_completed_at or ordered_at)) * 1000.0,
                )
                held.translation_end_to_end_ms = max(
                    0.0,
                    (
                        ordered_at
                        - float(held.stable_committed_at or held.queued_at or ordered_at)
                    )
                    * 1000.0,
                )
                self._lat_ordering.append(held.ordering_wait_ms)
                self._lat_e2e.append(held.translation_end_to_end_ms)
                self._committed_sequences.add(held_seq)
                self._counters["TRANSLATION_COMMITS_COMPLETED"] += 1
                success = (
                    held.status == "success"
                    and (held.translated_text or "").strip()
                    and held.terminal_state in ("", TERMINAL_COMPLETED)
                    and not bool(getattr(held, "obsolete_result_rejected", False))
                )
                if success:
                    held.terminal_state = TERMINAL_COMPLETED
                    self._completed_sequences.add(held_seq)
                    held.ui_update_scheduled_at = time.time()
                    callbacks.append(held)
                else:
                    if getattr(held, "obsolete_result_rejected", False):
                        held.terminal_state = TERMINAL_SUPERSEDED
                    elif not held.terminal_state:
                        held.terminal_state = TERMINAL_PERMANENTLY_FAILED
                    self._failed_sequences.add(held_seq)
                    # Still notify UI so loading indicators can clear.
                    held.ui_update_scheduled_at = time.time()
                    callbacks.append(held)
                self._next_translation_sequence_to_commit += 1
                self._append_event(held, phase="ordered_commit")
        for held in callbacks:
            if self.on_translation_ready:
                try:
                    self.on_translation_ready(held)
                except Exception as exc:
                    logger.exception("translation UI callback failed")
                    observer = getattr(self, "on_callback_exception", None)
                    if callable(observer):
                        try:
                            observer(exc, held)
                        except Exception:
                            logger.exception("translation callback exception observer failed")
    def mark_ui_update_completed(
        self, segment_id: int, completed_at: Optional[float] = None, *, result: Optional[TranslationResult] = None
    ) -> None:
        ts = float(completed_at if completed_at is not None else time.time())
        if not hasattr(self, "_ui_completed"):
            self._ui_completed = {}
        self._ui_completed[int(segment_id)] = ts
        with self._lock:
            self._counters["TRANSLATION_UI_UPDATES_COMPLETED"] = int(
                self._counters.get("TRANSLATION_UI_UPDATES_COMPLETED", 0) or 0
            ) + 1
        target = result
        if target is None:
            # Best-effort: reconstruct minimal completion metrics.
            return
        target.ui_update_completed_at = ts
        if target.ui_update_scheduled_at:
            ui_ms = (ts - float(target.ui_update_scheduled_at)) * 1000.0
            target.ui_end_to_end_ms = float(ui_ms)
            with self._lock:
                self._lat_ui_e2e.append(float(ui_ms))
        try:
            self._append_event(target, phase="ui_completed")
        except Exception:
            pass

    def note_ui_update_scheduled(self, result: Optional[TranslationResult] = None) -> None:
        with self._lock:
            self._counters["TRANSLATION_UI_UPDATES_SCHEDULED"] = int(
                self._counters.get("TRANSLATION_UI_UPDATES_SCHEDULED", 0) or 0
            ) + 1
        if result is not None and not float(getattr(result, "ui_update_scheduled_at", 0) or 0):
            result.ui_update_scheduled_at = time.time()
    def _append_event(self, result: TranslationResult, *, phase: str) -> None:
        if self._events_path is None:
            return
        row = {
            "phase": phase,
            "run_id": result.run_id,
            "segment_id": result.segment_id,
            "source_segment_id": result.source_segment_id or result.segment_id,
            "translation_sequence": result.translation_sequence,
            "source_language": result.source_language,
            "target_language": result.target_language,
            "source_text_hash": result.source_text_hash,
            "source_character_count": result.source_character_count,
            "stable_committed_at": result.stable_committed_at,
            "queued_at": result.queued_at,
            "started_at": result.started_at,
            "provider_completed_at": result.provider_completed_at,
            "ordered_commit_at": result.ordered_commit_at or None,
            "ui_update_scheduled_at": result.ui_update_scheduled_at or None,
            "ui_update_completed_at": result.ui_update_completed_at or None,
            "queue_wait_ms": round(result.queue_wait_ms, 3),
            "provider_latency_ms": round(result.provider_latency_ms, 3),
            "ordering_wait_ms": round(result.ordering_wait_ms, 3),
            "translation_end_to_end_ms": round(result.translation_end_to_end_ms, 3),
            "ui_end_to_end_ms": round(result.ui_end_to_end_ms, 3)
            if result.ui_end_to_end_ms
            else None,
            "status": result.status,
            "terminal_state": result.terminal_state or None,
            "retry_count": result.retry_count,
            "sanitized_error_code": result.error_code or None,
            "translated_text": result.translated_text if phase == "provider_done" else None,
        }
        try:
            with self._events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
    def _build_summary(
        self,
        *,
        pending_at_exit: int = 0,
        in_flight_at_exit: int = 0,
        ordering_pending_at_exit: int = 0,
    ) -> Dict[str, Any]:
        with self._lock:
            c = dict(self._counters)
            unfinished = list(self._unfinished_sequences)
            unfinished_ids = list(self._unfinished_ids)
            max_depth = self._max_queue_depth
            qstats = _latency_stats(self._lat_queue)
            pstats = _latency_stats(self._lat_provider)
            ostats = _latency_stats(self._lat_ordering)
            e2e = _latency_stats(self._lat_e2e)
            ui_e2e = _latency_stats(self._lat_ui_e2e)
            failed = sorted(self._failed_sequences)
            completed = sorted(self._completed_sequences)
        return {
            "run_id": self.run_id,
            "provider": self._provider,
            "ordering_key": "translation_sequence",
            "TRANSLATION_WORKER_STOPPED": self.worker_stopped or self._stop.is_set(),
            "TRANSLATION_QUEUE_PENDING_AT_EXIT": pending_at_exit,
            "TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT": in_flight_at_exit,
            "ORDERING_BUFFER_PENDING_AT_EXIT": ordering_pending_at_exit,
            "UNRESOLVED_TRANSLATION_SEQUENCES": unfinished,
            "INTERIM_SUBMISSIONS_REJECTED": c["INTERIM_SUBMISSIONS_REJECTED"],
            "DUPLICATE_SUBMISSIONS_REJECTED": c["DUPLICATE_SUBMISSIONS_REJECTED"],
            "EMPTY_SUBMISSIONS_REJECTED": c["EMPTY_SUBMISSIONS_REJECTED"],
            "UNSUPPORTED_LANGUAGE_SUBMISSIONS_REJECTED": c[
                "UNSUPPORTED_LANGUAGE_SUBMISSIONS_REJECTED"
            ],
            "STABLE_TRANSLATION_JOBS_ACCEPTED": c["STABLE_TRANSLATION_JOBS_ACCEPTED"],
            "TRANSLATION_JOBS_QUEUED": c["TRANSLATION_JOBS_QUEUED"],
            "TRANSLATION_REQUESTS_SENT": c["TRANSLATION_REQUESTS_SENT"],
            "TRANSLATION_COMMITS_COMPLETED": c["TRANSLATION_COMMITS_COMPLETED"],
            "TRANSLATION_REQUESTS_SENT_FROM_INTERIM": c[
                "TRANSLATION_REQUESTS_SENT_FROM_INTERIM"
            ],
            "DUPLICATE_TRANSLATION_REQUESTS_SENT": c["DUPLICATE_TRANSLATION_REQUESTS_SENT"],
            "DUPLICATE_TRANSLATION_COMMITS": c["DUPLICATE_TRANSLATION_COMMITS"],
            "OUT_OF_ORDER_TRANSLATION_COMMITS": c["OUT_OF_ORDER_TRANSLATION_COMMITS"],
            "MISSING_TRANSLATION_SEGMENT_IDS": len(unfinished),
            "MISSING_ACCEPTED_TRANSLATION_SEQUENCES": c[
                "MISSING_ACCEPTED_TRANSLATION_SEQUENCES"
            ],
            "SOURCE_TRANSCRIPT_MODIFICATIONS": c["source_transcript_modifications"],
            "UNFINISHED_TRANSLATION_SEGMENT_IDS": unfinished_ids,
            "FAILED_TRANSLATION_SEQUENCES": failed,
            "COMPLETED_TRANSLATION_SEQUENCES": completed,
            "FAILED_TRANSLATION_SEGMENT_IDS": [
                self._sequence_to_source.get(seq, seq) for seq in failed
            ],
            "COMPLETED_TRANSLATION_SEGMENT_IDS": [
                self._sequence_to_source.get(seq, seq) for seq in completed
            ],
            "successful_translations": c["successful"],
            "failed_translations": c["failed"],
            "retries": c["retries"],
            "out_of_order_completions": c["out_of_order_completions"],
            "source_characters_sent": c["source_characters_sent"],
            "maximum_queue_depth": max_depth,
            "queue_wait_ms": qstats,
            "provider_latency_ms": pstats,
            "ordering_wait_ms": ostats,
            "translation_end_to_end_ms": e2e,
            "ui_end_to_end_ms": ui_e2e,
            "TRANSLATION_REQUESTS_FROM_INTERIM": c["INTERIM_SUBMISSIONS_REJECTED"],
            "DUPLICATE_TRANSLATION_REQUESTS": c["DUPLICATE_SUBMISSIONS_REJECTED"],
            "p50_provider_latency_ms": pstats["p50"],
            "p95_provider_latency_ms": pstats["p95"],
            "p50_translation_end_to_end_ms": e2e["p50"],
            "p95_translation_end_to_end_ms": e2e["p95"],
            "max_translation_end_to_end_ms": e2e["max"],
            "quota_disabled": self._quota_disabled,
            "status_message": self._status_message,
        }
    def _write_summary(self, summary: Dict[str, Any]) -> None:
        if self.evidence_dir is None:
            return
        try:
            (self.evidence_dir / "translation_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (self.evidence_dir / "translation_validation.json").write_text(
                json.dumps(
                    {
                        "ordering_key": "translation_sequence",
                        "TRANSLATION_REQUESTS_SENT_FROM_INTERIM": summary[
                            "TRANSLATION_REQUESTS_SENT_FROM_INTERIM"
                        ],
                        "DUPLICATE_TRANSLATION_REQUESTS_SENT": summary[
                            "DUPLICATE_TRANSLATION_REQUESTS_SENT"
                        ],
                        "DUPLICATE_TRANSLATION_COMMITS": summary[
                            "DUPLICATE_TRANSLATION_COMMITS"
                        ],
                        "OUT_OF_ORDER_TRANSLATION_COMMITS": summary[
                            "OUT_OF_ORDER_TRANSLATION_COMMITS"
                        ],
                        "MISSING_TRANSLATION_SEGMENT_IDS": summary[
                            "MISSING_TRANSLATION_SEGMENT_IDS"
                        ],
                        "MISSING_ACCEPTED_TRANSLATION_SEQUENCES": summary[
                            "MISSING_ACCEPTED_TRANSLATION_SEQUENCES"
                        ],
                        "UNRESOLVED_TRANSLATION_SEQUENCES": summary[
                            "UNRESOLVED_TRANSLATION_SEQUENCES"
                        ],
                        "SOURCE_TRANSCRIPT_MODIFICATIONS": summary[
                            "SOURCE_TRANSCRIPT_MODIFICATIONS"
                        ],
                        "TRANSLATION_QUEUE_PENDING_AT_EXIT": summary[
                            "TRANSLATION_QUEUE_PENDING_AT_EXIT"
                        ],
                        "TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT": summary[
                            "TRANSLATION_JOBS_IN_FLIGHT_AT_EXIT"
                        ],
                        "ORDERING_BUFFER_PENDING_AT_EXIT": summary[
                            "ORDERING_BUFFER_PENDING_AT_EXIT"
                        ],
                        "TRANSLATION_WORKER_STOPPED": summary[
                            "TRANSLATION_WORKER_STOPPED"
                        ],
                        "INTERIM_SUBMISSIONS_REJECTED": summary[
                            "INTERIM_SUBMISSIONS_REJECTED"
                        ],
                        "DUPLICATE_SUBMISSIONS_REJECTED": summary[
                            "DUPLICATE_SUBMISSIONS_REJECTED"
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
    def _write_sanitized_config(self) -> None:
        if self.evidence_dir is None:
            return
        cfg = {
            "TRANSLATION_ENABLED": TRANSLATION_ENABLED,
            "TRANSLATION_PROVIDER": TRANSLATION_PROVIDER,
            "TRANSLATE_STABLE_ONLY": TRANSLATE_STABLE_ONLY,
            "TRANSLATION_QUEUE_MAX_SIZE": TRANSLATION_QUEUE_MAX_SIZE,
            "TRANSLATION_MAX_RETRIES": TRANSLATION_MAX_RETRIES,
            "TRANSLATION_SHUTDOWN_TIMEOUT_SECONDS": TRANSLATION_SHUTDOWN_TIMEOUT_SECONDS,
            "ordering_key": "translation_sequence",
            "auth_key_present": bool(self._client and self._client.available),
            "auth_key_logged": False,
            "language_map": {
                "ja": {"source": "JA", "target": "EN-US"},
                "en": {"source": "EN", "target": "JA"},
            },
        }
        try:
            (self.evidence_dir / "sanitized_deepl_configuration.json").write_text(
                json.dumps(cfg, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    def reset_session(self, run_id: str, evidence_dir: Optional[Path] = None) -> None:
        with self._lock:
            self.run_id = str(run_id or "")
            self._seen_request_ids.clear()
            self._seen_text_hash_by_utterance_version.clear()
            self._accepted_sequences.clear()
            self._provider_sent_sequences.clear()
            self._committed_sequences.clear()
            self._failed_sequences.clear()
            self._completed_sequences.clear()
            self._sequence_to_source.clear()
            self._held.clear()
            self._latest_version_by_utterance.clear()
            self._revision_events.clear()
            self._next_translation_sequence = 0
            self._next_translation_sequence_to_commit = 1
            self._highest_accepted_segment_id = 0
            self._lat_queue.clear()
            self._lat_provider.clear()
            self._lat_ordering.clear()
            self._lat_e2e.clear()
            self._lat_ui_e2e.clear()
            self._max_queue_depth = 0
            self._unfinished_ids.clear()
            self._unfinished_sequences.clear()
            self._quota_disabled = False
            for k in self._counters:
                self._counters[k] = 0
        if evidence_dir is not None:
            self.evidence_dir = Path(evidence_dir)
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            self._events_path = self.evidence_dir / "translation_events.jsonl"
            self._write_sanitized_config()
    def get_counters(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def get_revision_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._revision_events)
