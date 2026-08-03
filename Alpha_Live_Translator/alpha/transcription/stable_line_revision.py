"""Stable line revision model — clean active transcript for Alpha export (8.5.24.1)."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    CLEAN_ALPHA_EXPORT_ENABLED,
    CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED,
    FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ENABLED,
    STABLE_LINE_REVISION_MODEL_ENABLED,
    STABLE_REVISION_HISTORY_PERSISTENCE_ENABLED,
)
from alpha.utils.cjk_text import compact_cjk_for_compare
from alpha.transcription.speaker_boundary_guard import speakers_confirmed_same

_manager: Optional["StableLineRevisionManager"] = None


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _run_folder() -> Path | None:
    try:
        from alpha.utils.troubleshooting_paths import get_run_folder

        run = get_run_folder()
        if run:
            return Path(run)
    except Exception:
        pass
    return None


def _strip_speaker(text: str) -> str:
    return re.sub(r"^\[Speaker\s+\d+\]\s*", "", (text or "").strip())


def prefix_overlap_ratio(previous: str, current: str) -> float:
    prev_c = compact_cjk_for_compare(_strip_speaker(previous), "ja")
    cur_c = compact_cjk_for_compare(_strip_speaker(current), "ja")
    if not prev_c or not cur_c:
        return 0.0
    if cur_c.startswith(prev_c):
        return len(prev_c) / max(len(cur_c), 1)
    if prev_c in cur_c:
        return len(prev_c) / max(len(cur_c), 1)
    shared = 0
    for i in range(min(len(prev_c), len(cur_c))):
        if prev_c[i] == cur_c[i]:
            shared += 1
        else:
            break
    return shared / max(len(cur_c), 1)


def detect_cumulative_duplicate(previous: str, current: str) -> bool:
    if not CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED:
        return False
    prev = _strip_speaker(previous)
    cur = _strip_speaker(current)
    if not prev or not cur:
        return False
    if cur.startswith(prev) and len(cur) > len(prev):
        return True
    if prev in cur and len(cur) - len(prev) >= 4:
        return True
    if prefix_overlap_ratio(prev, cur) >= 0.7:
        return True
    if len(prev) >= 30 and cur.startswith(prev[-30:]):
        return True
    return False


class StableLineRevisionManager:
    """Tracks active stable lines; revises instead of appending cumulative duplicates."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._lines: list[dict[str, Any]] = []
        self._active_ids: list[str] = []
        self._revision_events: list[dict[str, Any]] = []
        self._line_counter = 0
        self._metrics = {
            "append_new_line_count": 0,
            "revise_previous_line_count": 0,
            "hold_pending_count": 0,
            "suppress_current_count": 0,
            "revised_line_count": 0,
            "suppressed_line_count": 0,
            "cumulative_duplicate_detected_count": 0,
            "cumulative_duplicate_converted_to_revision_count": 0,
            "duplicate_after_merge_suppressed_count": 0,
        }

    def _new_id(self) -> str:
        self._line_counter += 1
        return f"sl_{self._line_counter:05d}_{uuid.uuid4().hex[:8]}"

    def _active_line(self) -> dict[str, Any] | None:
        if not self._active_ids:
            return None
        lid = self._active_ids[-1]
        for row in reversed(self._lines):
            if row.get("stable_line_id") == lid and row.get("status") == "active":
                return row
        return None

    def _write_revision_history(
        self,
        *,
        event_type: str,
        stable_line_id: str,
        old_text: str,
        new_text: str,
        reason: str,
        replaced_by: str = "",
        source_commit_ids: list[str] | None = None,
    ) -> None:
        record = {
            "event_type": event_type,
            "stable_line_id": stable_line_id,
            "old_text": old_text,
            "new_text": new_text,
            "reason": reason,
            "replaced_by_stable_line_id": replaced_by,
            "source_commit_ids": source_commit_ids or [],
            "raw_mutation": False,
            "timestamp": time.time(),
        }
        self._revision_events.append(record)
        run = _run_folder()
        if not run:
            return
        path = run / "transcripts" / "stable_revision_history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        _jp_log("STABLE_REVISION_HISTORY_WRITTEN", event_type=event_type, stable_line_id=stable_line_id)

    def _flush_clean_active_transcript(self) -> None:
        if not CLEAN_ALPHA_EXPORT_ENABLED:
            return
        run = _run_folder()
        if not run:
            return
        path = run / "transcripts" / "clean_active_transcript.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [ln for ln in self._lines if ln.get("status") == "active"]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        _jp_log("CLEAN_ACTIVE_TRANSCRIPT_UPDATED", active_count=len(rows))

    def create_line(
        self,
        text: str,
        *,
        speaker: Any = None,
        boundary_action: str = "",
        boundary_reason: str = "",
        source_commit_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        lid = self._new_id()
        row = {
            "stable_line_id": lid,
            "text": text,
            "speaker": speaker,
            "created_at": now,
            "updated_at": now,
            "revision_number": 1,
            "status": "active",
            "replaced_by_stable_line_id": "",
            "source_commit_ids": source_commit_ids or [],
            "boundary_action": boundary_action,
            "boundary_reason": boundary_reason,
        }
        self._lines.append(row)
        self._active_ids.append(lid)
        self._metrics["append_new_line_count"] += 1
        self._write_revision_history(
            event_type="created",
            stable_line_id=lid,
            old_text="",
            new_text=text,
            reason=boundary_reason or "append_new_line",
        )
        self._flush_clean_active_transcript()
        _jp_log("STABLE_LINE_CREATED", stable_line_id=lid, text_preview=text[:80])
        return row

    def revise_active_line(
        self,
        new_text: str,
        *,
        reason: str = "",
        boundary_action: str = "revise_previous_line",
        source_commit_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        active = self._active_line()
        if not active:
            return self.create_line(
                new_text,
                boundary_action=boundary_action,
                boundary_reason=reason,
                source_commit_ids=source_commit_ids,
            )
        old_text = active["text"]
        old_id = active["stable_line_id"]
        active["status"] = "revised"
        active["updated_at"] = time.time()
        self._metrics["revised_line_count"] += 1
        self._metrics["revise_previous_line_count"] += 1

        now = time.time()
        new_id = self._new_id()
        new_row = {
            "stable_line_id": new_id,
            "text": new_text,
            "speaker": active.get("speaker"),
            "created_at": active.get("created_at", now),
            "updated_at": now,
            "revision_number": int(active.get("revision_number", 1)) + 1,
            "status": "active",
            "replaced_by_stable_line_id": "",
            "source_commit_ids": source_commit_ids or [],
            "boundary_action": boundary_action,
            "boundary_reason": reason,
            "revised_from_stable_line_id": old_id,
        }
        active["replaced_by_stable_line_id"] = new_id
        self._lines.append(new_row)
        self._active_ids[-1] = new_id

        self._write_revision_history(
            event_type="revised",
            stable_line_id=old_id,
            old_text=old_text,
            new_text=new_text,
            reason=reason,
            replaced_by=new_id,
        )
        self._flush_clean_active_transcript()
        _jp_log("STABLE_LINE_REVISED", old_id=old_id, new_id=new_id, reason=reason)
        _jp_log("STABLE_LINE_REPLACED_BY_MERGE", old_preview=old_text[:60], new_preview=new_text[:60])
        _jp_log("STABLE_LINE_REVISION_FINAL_ACTIVE", stable_line_id=new_id)
        return new_row

    def suppress_current(self, *, reason: str = "") -> None:
        self._metrics["suppress_current_count"] += 1
        self._metrics["suppressed_line_count"] += 1
        self._metrics["duplicate_after_merge_suppressed_count"] += 1
        _jp_log("STABLE_LINE_SUPPRESSED", reason=reason)
        _jp_log("DUPLICATE_AFTER_MERGE_SUPPRESSED", reason=reason)

    def apply_boundary_output(
        self,
        stab: dict[str, Any],
        *,
        speaker: Any = None,
        previous_text: str = "",
    ) -> dict[str, Any]:
        """Apply stabilizer output contract to revision model."""
        action = stab.get("output_action") or stab.get("action", "")
        text = (stab.get("output_text") or "").strip()
        should_revise = bool(stab.get("should_revise") or stab.get("update_previous"))
        suppress = bool(stab.get("suppress_current"))
        should_append = stab.get("should_append")
        if should_append is None:
            should_append = not should_revise and not suppress and bool(text)

        if suppress or action == "suppress_current":
            self.suppress_current(reason=stab.get("revision_reason") or stab.get("reason", ""))
            return {"applied": "suppress", "export": False}

        if not text:
            self._metrics["hold_pending_count"] += 1
            return {"applied": "hold", "export": False}

        if (
            CUMULATIVE_MERGE_DUPLICATE_GUARD_ENABLED
            and previous_text
            and detect_cumulative_duplicate(previous_text, text)
            and not action.startswith("hold")
        ):
            self._metrics["cumulative_duplicate_detected_count"] += 1
            self._metrics["cumulative_duplicate_converted_to_revision_count"] += 1
            _jp_log("CUMULATIVE_MERGE_DUPLICATE_DETECTED")
            _jp_log("CUMULATIVE_MERGE_CONVERTED_TO_REVISION")
            should_revise = True
            should_append = False
            action = "revise_previous_line"

        if should_revise or action in (
            "revise_previous_line",
            "merge_with_previous",
            "merge_pending_and_current",
            "punctuation_cleanup_revision",
        ):
            # fixes TASK_2C_REPORT.md: speaker identity is checked BEFORE any
            # text-adjacency/revision logic is allowed to run. A different
            # speaker (or an unknown speaker on either side) can never
            # revise/extend the active line -- always create a new separate
            # canonical line instead. Fail-closed, not a downstream filter.
            active = self._active_line()
            active_speaker = active.get("speaker") if active else None
            if not speakers_confirmed_same(active_speaker, speaker):
                row = self.create_line(
                    text,
                    speaker=speaker,
                    boundary_action="append_new_line",
                    boundary_reason="speaker_boundary_forced_new_line",
                )
                _jp_log(
                    "SPEAKER_BOUNDARY_REVISION_BLOCKED",
                    active_speaker=active_speaker,
                    candidate_speaker=speaker,
                )
                return {"applied": "append", "export": True, "stable_line_id": row["stable_line_id"]}
            row = self.revise_active_line(
                text,
                reason=stab.get("revision_reason") or stab.get("reason", ""),
                boundary_action=action,
            )
            _jp_log("STABLE_LINE_REVISION_EXPORT_SKIPPED_OLD_VERSION")
            return {"applied": "revise", "export": True, "stable_line_id": row["stable_line_id"] if row else ""}

        if should_append or action in ("append_new_line", "emit_unchanged", "cleanup_punctuation_only", "stop_flush_emit"):
            row = self.create_line(
                text,
                speaker=speaker,
                boundary_action=action,
                boundary_reason=stab.get("reason", ""),
            )
            return {"applied": "append", "export": True, "stable_line_id": row["stable_line_id"]}

        return {"applied": "none", "export": False}

    def get_active_lines(self) -> list[dict[str, Any]]:
        return [ln for ln in self._lines if ln.get("status") == "active"]

    def format_clean_alpha_text(self) -> str:
        lines: list[str] = []
        for row in self.get_active_lines():
            text = (row.get("text") or "").strip()
            if not text:
                continue
            speaker = row.get("speaker")
            prefix = f"[Speaker {speaker}] " if speaker is not None else ""
            lines.append(f"{prefix}{text}")
        return "\n".join(lines)

    def get_metrics(self) -> dict[str, Any]:
        active = self.get_active_lines()
        return {
            **self._metrics,
            "active_line_count": len(active),
            "output_clean_line_count": len(active),
            "clean_active_line_count": len(active),
            "output_stable_commit_history_count": len(self._lines),
            "stable_commit_history_count": len(self._lines),
            "revision_history_event_count": len(self._revision_events),
        }

    def apply_final_clean_lines(
        self,
        cleaned_lines: list[str],
        *,
        cleanup_metrics: dict[str, Any] | None = None,
        canonical_records: list[dict[str, Any]] | None = None,
    ) -> None:
        cleanup_metrics = cleanup_metrics or {}
        canonical_records = canonical_records or []
        self._canonical_records = canonical_records
        parsed: list[tuple[Any, str]] = []
        for raw in cleaned_lines:
            body = (raw or "").strip()
            if not body:
                continue
            sp = re.match(r"^\[Speaker\s+(\d+)\]\s*(.*)$", body)
            if sp:
                parsed.append((int(sp.group(1)), sp.group(2).strip()))
            else:
                parsed.append((None, body))

        actives = self.get_active_lines()
        punct_applied = cleanup_metrics.get("punctuation_artifact_cleaned_count", 0) > 0
        dup_applied = cleanup_metrics.get("residual_duplicate_suppressed_count", 0) > 0 or cleanup_metrics.get(
            "residual_duplicate_revised_count", 0
        ) > 0

        glossary_applied = cleanup_metrics.get("glossary_corrections_count", 0) > 0 or cleanup_metrics.get(
            "financial_number_corrections_count", 0
        ) > 0

        for idx, (speaker, text) in enumerate(parsed):
            if idx < len(actives):
                row = actives[idx]
                old_text = row.get("text", "")
                if old_text != text:
                    evt = "glossary_corrected" if glossary_applied else "revised"
                    if len(parsed) < len(actives):
                        evt = "duplicate_sweep_suppressed"
                    self._write_revision_history(
                        event_type=evt,
                        stable_line_id=row["stable_line_id"],
                        old_text=old_text,
                        new_text=text,
                        reason="corporate_ir_glossary" if glossary_applied else "final_output_cleanup",
                    )
                row["text"] = text
                row["updated_at"] = time.time()
                row["duplicate_cleanup_applied"] = dup_applied
                row["punctuation_cleanup_applied"] = punct_applied
                row["glossary_cleanup_applied"] = glossary_applied
                row["glossary_enabled"] = cleanup_metrics.get("glossary_enabled", False)
                row["raw_mutation"] = False
                if speaker is not None:
                    row["speaker"] = speaker
            else:
                self.create_line(
                    text,
                    speaker=speaker,
                    boundary_action="final_output_cleanup",
                    boundary_reason="residual_duplicate_sweep",
                )

        from alpha.constants import LOSSLESS_CLEAN_EXPORT_ENABLED

        if not LOSSLESS_CLEAN_EXPORT_ENABLED:
            for extra in actives[len(parsed) :]:
                extra["status"] = "suppressed"
                extra["updated_at"] = time.time()
                self._write_revision_history(
                    event_type="duplicate_sweep_suppressed",
                    stable_line_id=extra["stable_line_id"],
                    old_text=extra.get("text", ""),
                    new_text="",
                    reason="residual_duplicate_sweep",
                )
                self._metrics["suppressed_line_count"] += 1

        self._active_ids = [a["stable_line_id"] for a in self.get_active_lines()]

    def finalize_on_stop(self, run_folder: Path) -> dict[str, str]:
        paths: dict[str, str] = {}
        run_folder = Path(run_folder)
        run_folder.mkdir(parents=True, exist_ok=True)
        active = self.get_active_lines()

        if FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ENABLED:
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_FROM_CANONICAL_LINES_STARTED")
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_FINAL_WRITE_STARTED")
            final_path = run_folder / "transcripts" / "clean_active_transcript.jsonl"
            final_path.parent.mkdir(parents=True, exist_ok=True)
            rows: list[dict[str, Any]] = []
            canonical_records = getattr(self, "_canonical_records", None) or []
            if canonical_records:
                for row in canonical_records:
                    body = (row.get("text") or "").strip()
                    sp = re.match(r"^\[Speaker\s+(\d+)\]\s*(.*)$", body)
                    text = sp.group(2).strip() if sp else body
                    speaker = int(sp.group(1)) if sp else row.get("speaker")
                    rows.append(
                        {
                            "canonical_line_id": row.get("canonical_line_id", ""),
                            "stable_line_id": row.get("stable_line_id", ""),
                            "source_commit_ids": row.get("source_commit_ids", []),
                            "represented_source_ids": row.get("represented_source_ids", []),
                            "text": text,
                            "speaker": speaker,
                            "status": "active",
                            "revision_number": row.get("revision_number", 1),
                            "transformation_chain": row.get("transformation_chain", []),
                            "glossary_corrections": row.get("glossary_corrections", []),
                            "financial_number_corrections": row.get("financial_number_corrections", []),
                            "created_at": row.get("created_at", time.time()),
                            "updated_at": row.get("updated_at", time.time()),
                            "duplicate_cleanup_applied": bool(row.get("duplicate_cleanup_applied", False)),
                            "punctuation_cleanup_applied": bool(row.get("punctuation_cleanup_applied", False)),
                            "glossary_cleanup_applied": bool(row.get("glossary_corrections")),
                            "glossary_enabled": bool(row.get("glossary_corrections")),
                            "raw_mutation": False,
                        }
                    )
                    _jp_log("CLEAN_ACTIVE_TRANSCRIPT_CANONICAL_RECORD_WRITTEN")
            else:
                for row in active:
                    rows.append(
                        {
                            "stable_line_id": row.get("stable_line_id", ""),
                            "text": row.get("text", ""),
                            "revision_number": row.get("revision_number", 1),
                            "status": "active",
                            "created_at": row.get("created_at", time.time()),
                            "updated_at": row.get("updated_at", time.time()),
                            "source_commit_ids": row.get("source_commit_ids", []),
                            "boundary_action": row.get("boundary_action", ""),
                            "boundary_reason": row.get("boundary_reason", ""),
                            "duplicate_cleanup_applied": bool(row.get("duplicate_cleanup_applied", False)),
                            "punctuation_cleanup_applied": bool(row.get("punctuation_cleanup_applied", False)),
                            "glossary_cleanup_applied": bool(row.get("glossary_cleanup_applied", False)),
                            "glossary_enabled": bool(row.get("glossary_enabled", False)),
                            "raw_mutation": False,
                        }
                    )
            final_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
                encoding="utf-8",
            )
            paths["clean_active_transcript_path"] = str(final_path).replace("\\", "/")
            latest = Path("troubleshooting/latest/clean_active_transcript.jsonl")
            latest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_path, latest)
            paths["latest_clean_active_transcript_path"] = str(latest).replace("\\", "/")
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_FINAL_WRITTEN", path=str(final_path), lines=len(rows))
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_LATEST_COPY_WRITTEN", path=str(latest))
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_LINE_COUNT_VERIFIED", count=len(rows))
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_CANONICAL_LINE_COUNT_MATCHED", count=len(rows))
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_FROM_CANONICAL_LINES_COMPLETED")
            _jp_log("CLEAN_ACTIVE_TRANSCRIPT_GLOSSARY_METADATA_WRITTEN")

        if STABLE_REVISION_HISTORY_PERSISTENCE_ENABLED:
            _jp_log("STABLE_REVISION_HISTORY_FINAL_WRITE_STARTED")
            hist_path = run_folder / "transcripts" / "stable_revision_history.jsonl"
            hist_path.parent.mkdir(parents=True, exist_ok=True)
            hist_path.write_text(
                "\n".join(json.dumps(e, ensure_ascii=False) for e in self._revision_events)
                + ("\n" if self._revision_events else ""),
                encoding="utf-8",
            )
            paths["stable_revision_history_path"] = str(hist_path).replace("\\", "/")
            latest_hist = Path("troubleshooting/latest/stable_revision_history.jsonl")
            latest_hist.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hist_path, latest_hist)
            paths["latest_stable_revision_history_path"] = str(latest_hist).replace("\\", "/")
            _jp_log("STABLE_REVISION_HISTORY_FINAL_WRITTEN", path=str(hist_path))
            _jp_log("STABLE_REVISION_HISTORY_LATEST_COPY_WRITTEN", path=str(latest_hist))

        return paths


def get_stable_line_revision_manager() -> StableLineRevisionManager:
    global _manager
    if _manager is None:
        _manager = StableLineRevisionManager()
        if STABLE_LINE_REVISION_MODEL_ENABLED:
            _jp_log("STABLE_LINE_REVISION_MODEL_ACTIVE")
    return _manager


def reset_stable_line_revision_manager() -> None:
    global _manager
    if _manager is not None:
        _manager.reset()
    _manager = None


def detect_cumulative_alpha_lines(lines: list[str]) -> dict[str, Any]:
    """Detect cumulative duplicate pattern in Alpha output lines."""
    cumulative_count = 0
    prefix_chain = 0
    for i in range(1, len(lines)):
        prev = _strip_speaker(lines[i - 1])
        cur = _strip_speaker(lines[i])
        if not prev or not cur:
            continue
        if cur.startswith(prev) and len(cur) > len(prev):
            cumulative_count += 1
            prefix_chain += 1
        elif prefix_overlap_ratio(prev, cur) >= 0.7:
            cumulative_count += 1
    suspected = cumulative_count >= 3 or prefix_chain >= 2
    return {
        "cumulative_duplicate_count": cumulative_count,
        "prefix_chain_count": prefix_chain,
        "alpha_output_cumulative_duplicate_suspected": suspected,
    }
