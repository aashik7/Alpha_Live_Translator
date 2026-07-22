"""Canonical transcript lineage model and final export lock (8.5.25.2)."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_VERSION,
    CANONICAL_CORRECTION_LINEAGE_REQUIRED,
    CANONICAL_TRANSCRIPT_LINEAGE_ENABLED,
    CORRECTED_LINE_REPRESENTS_SOURCE_ENABLED,
    FINAL_EXPORT_LOCK_ENABLED,
    INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED,
    LINEAGE_BASED_EXPORT_COVERAGE_ENABLED,
    MALFORMED_NUMERIC_OUTPUT_BLOCK_ENABLED,
    PRE_CORRECTION_REENTRY_BLOCK_ENABLED,
    SOURCE_COMMIT_LINEAGE_REQUIRED,
    SUPPRESSION_AWARE_LINEAGE_COVERAGE_ENABLED,
    TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY,
)
from alpha.transcription.stable_line_revision import _strip_speaker
from alpha.utils.cjk_text import compact_cjk_for_compare


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _new_canonical_id() -> str:
    return f"cl_{uuid.uuid4().hex[:12]}"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _normalize(text: str) -> str:
    return compact_cjk_for_compare(_strip_speaker(text or ""), "ja")


def _speaker_prefix(speaker: Any) -> str:
    if speaker is None:
        return ""
    return f"[Speaker {speaker}] "


class TranscriptLineageRegistry:
    """Tracks canonical lines through stable-layer transformations."""

    def __init__(self) -> None:
        self._lines: list[dict[str, Any]] = []
        self._ledger: list[dict[str, Any]] = []
        self._counter = 0
        _jp_log("TRANSCRIPT_LINEAGE_MODEL_INITIALIZED")

    def _append_ledger(self, record: dict[str, Any]) -> None:
        record.setdefault("timestamp", time.time())
        record.setdefault("raw_mutation", False)
        self._ledger.append(record)

    def create_canonical_line(
        self,
        text: str,
        *,
        source_commit_ids: list[str],
        stable_line_id: str = "",
        speaker: Any = None,
        transformation_chain: list[str] | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        self._counter += 1
        cid = _new_canonical_id()
        row = {
            "canonical_line_id": cid,
            "stable_line_id": stable_line_id or f"sl_lineage_{self._counter:05d}",
            "text": text,
            "speaker": speaker,
            "status": status,
            "source_commit_ids": list(source_commit_ids),
            "represented_source_ids": list(source_commit_ids),
            "revision_number": 1,
            "created_at": time.time(),
            "updated_at": time.time(),
            "transformation_chain": transformation_chain or ["assembler"],
            "glossary_corrections": [],
            "financial_number_corrections": [],
            "raw_mutation": False,
            "replaced_by_canonical_line_id": "",
            "lineage_missing_but_retained": not bool(source_commit_ids),
        }
        self._lines.append(row)
        self._append_ledger(
            {
                "event_type": "created",
                "canonical_line_id": cid,
                "stable_line_id": row["stable_line_id"],
                "source_commit_ids": row["source_commit_ids"],
                "represented_source_ids": row["represented_source_ids"],
                "old_text": "",
                "new_text": text,
                "status": status,
                "reason": "canonical_line_created",
                "transformation_chain": row["transformation_chain"],
            }
        )
        _jp_log("CANONICAL_LINE_CREATED", canonical_line_id=cid)
        _jp_log("SOURCE_COMMIT_IDS_PROPAGATED", count=len(source_commit_ids))
        return row

    def revise_canonical_line(
        self,
        canonical_line_id: str,
        new_text: str,
        *,
        reason: str,
        transformation: str,
        preserve_source_ids: bool = True,
    ) -> dict[str, Any] | None:
        active = None
        for row in self._lines:
            if row.get("canonical_line_id") == canonical_line_id and row.get("status") == "active":
                active = row
                break
        if not active:
            return None
        old_text = active["text"]
        source_ids = active.get("source_commit_ids", []) if preserve_source_ids else []
        represented = active.get("represented_source_ids", source_ids) if preserve_source_ids else []
        active["status"] = "revised"
        active["updated_at"] = time.time()
        active["replaced_by_canonical_line_id"] = ""
        self._append_ledger(
            {
                "event_type": "boundary_revised",
                "canonical_line_id": canonical_line_id,
                "stable_line_id": active.get("stable_line_id", ""),
                "source_commit_ids": source_ids,
                "represented_source_ids": represented,
                "old_text": old_text,
                "new_text": new_text,
                "status": "revised",
                "reason": reason,
                "transformation_chain": active.get("transformation_chain", []) + [transformation],
            }
        )
        _jp_log("PRE_CORRECTION_LINE_MARKED_DEBUG_ONLY", canonical_line_id=canonical_line_id)

        new_row = self.create_canonical_line(
            new_text,
            source_commit_ids=source_ids,
            stable_line_id=active.get("stable_line_id", ""),
            speaker=active.get("speaker"),
            transformation_chain=active.get("transformation_chain", []) + [transformation],
            status="active",
        )
        new_row["represented_source_ids"] = represented
        new_row["revision_number"] = int(active.get("revision_number", 1)) + 1
        new_row["revised_from_canonical_line_id"] = canonical_line_id
        active["replaced_by_canonical_line_id"] = new_row["canonical_line_id"]
        _jp_log("CANONICAL_LINE_REVISED", old=canonical_line_id, new=new_row["canonical_line_id"])
        return new_row

    def apply_glossary_correction(
        self,
        canonical_line_id: str,
        corrected_text: str,
        *,
        before: str,
        after: str,
        correction_type: str = "glossary_term",
        glossary_term: str = "",
    ) -> dict[str, Any] | None:
        _jp_log("GLOSSARY_CORRECTION_LINEAGE_STARTED", canonical_line_id=canonical_line_id)
        row = self.revise_canonical_line(
            canonical_line_id,
            corrected_text,
            reason=f"glossary:{before}->{after}",
            transformation="glossary_correction",
            preserve_source_ids=True,
        )
        if not row:
            return None
        row["glossary_corrections"].append(
            {"before": before, "after": after, "correction_type": correction_type, "glossary_term": glossary_term}
        )
        self._append_ledger(
            {
                "event_type": "glossary_corrected",
                "canonical_line_id": row["canonical_line_id"],
                "stable_line_id": row.get("stable_line_id", ""),
                "source_commit_ids": row.get("source_commit_ids", []),
                "represented_source_ids": row.get("represented_source_ids", []),
                "old_text": before,
                "new_text": after,
                "status": "active",
                "reason": "glossary_correction",
                "transformation_chain": row.get("transformation_chain", []),
            }
        )
        _jp_log("GLOSSARY_CORRECTION_PRESERVED_SOURCE_IDS")
        _jp_log("GLOSSARY_CORRECTION_MARKED_OLD_TEXT_DEBUG_ONLY")
        _jp_log("GLOSSARY_CORRECTION_ACTIVE_CANONICAL_LINE_SET")
        _jp_log("GLOSSARY_CORRECTION_LINEAGE_COMPLETED")
        _jp_log("CORRECTION_LINEAGE_PRESERVED")
        return row

    def apply_financial_correction(
        self,
        canonical_line_id: str,
        corrected_text: str,
        *,
        before: str,
        after: str,
        validation_status: str = "safe",
    ) -> dict[str, Any] | None:
        row = self.revise_canonical_line(
            canonical_line_id,
            corrected_text,
            reason=f"financial:{before}->{after}",
            transformation="financial_number_correction",
            preserve_source_ids=True,
        )
        if not row:
            return None
        row["financial_number_corrections"].append(
            {"before": before, "after": after, "validation_status": validation_status}
        )
        if "numeric_safety_validated" not in row.get("transformation_chain", []):
            row["transformation_chain"].append("numeric_safety_validated")
        self._append_ledger(
            {
                "event_type": "financial_number_corrected",
                "canonical_line_id": row["canonical_line_id"],
                "stable_line_id": row.get("stable_line_id", ""),
                "source_commit_ids": row.get("source_commit_ids", []),
                "represented_source_ids": row.get("represented_source_ids", []),
                "old_text": before,
                "new_text": after,
                "status": "active",
                "reason": validation_status,
                "transformation_chain": row.get("transformation_chain", []),
            }
        )
        return row

    def record_suppressed_source(
        self,
        *,
        source_commit_id: str,
        text: str,
        suppression_classification: str,
        suppression_reason: str,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        self._append_ledger(
            {
                "event_type": "source_commit_suppressed",
                "canonical_line_id": "",
                "stable_line_id": source_commit_id,
                "source_commit_ids": [source_commit_id],
                "represented_source_ids": [source_commit_id],
                "old_text": text,
                "new_text": text,
                "status": "suppressed",
                "exportable": False,
                "debug_history_only": True,
                "reason": suppression_reason,
                "suppression_classification": suppression_classification,
                "suppression_provenance": provenance or {},
                "transformation_chain": ["assembler", "source_commit_suppressed"],
            }
        )
        _jp_log("SOURCE_COMMIT_CLASSIFIED_INTENTIONALLY_SUPPRESSED", source_commit_id=source_commit_id)

    def get_active_lines(self) -> list[dict[str, Any]]:
        return [ln for ln in self._lines if ln.get("status") == "active"]

    def get_ledger(self) -> list[dict[str, Any]]:
        return list(self._ledger)

    def get_represented_source_ids(self) -> set[str]:
        out: set[str] = set()
        for ln in self.get_active_lines():
            for sid in ln.get("represented_source_ids") or ln.get("source_commit_ids") or []:
                out.add(sid)
        return out


def build_export_chain_from_stable_commits(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay stable commits into export chain with source_commit lineage."""
    chain: list[dict[str, Any]] = []
    commit_index = 0
    for row in commits:
        text = (row.get("stable_text") or "").strip()
        if not text:
            continue
        commit_index += 1
        cid = row.get("stable_commit_id") or f"stable-{commit_index}"
        export_eligibility = row.get("export_eligibility") or (row.get("assembler_metadata") or {}).get(
            "export_eligibility", "export_required"
        )
        if export_eligibility == "intentionally_suppressed" or row.get("debug_history_only"):
            _jp_log("SUPPRESSED_SOURCE_EXCLUDED_FROM_REQUIRED_COVERAGE", source_commit_id=cid)
            continue
        meta = row.get("assembler_metadata") or {}
        should_revise = bool(
            meta.get("boundary_should_revise")
            or meta.get("replaces_previous_stable_line")
            or row.get("stable_line_status") == "revision"
            or meta.get("boundary_action") in (
                "revise_previous_line",
                "merge_with_previous",
                "merge_pending_and_current",
            )
        )
        if should_revise and chain:
            prev = chain[-1]
            prev_ids = list(prev.get("source_commit_ids", []))
            if cid not in prev_ids:
                prev_ids.append(cid)
            chain[-1] = {
                **prev,
                "text": text,
                "source_commit_ids": prev_ids,
                "represented_source_ids": prev_ids,
                "revision_count": int(prev.get("revision_count", 1)) + 1,
            }
            _jp_log("BOUNDARY_REVISION_LINEAGE_ENABLED", source_ids=prev_ids)
        else:
            chain.append(
                {
                    "text": text,
                    "source_commit_ids": [cid],
                    "represented_source_ids": [cid],
                    "revision_count": 1,
                    "speaker": None,
                }
            )
    return chain


def build_registry_from_export_lines(
    lines: list[str],
    *,
    stable_commits_path: Path | None = None,
    glossary_decisions: list[dict[str, Any]] | None = None,
) -> TranscriptLineageRegistry:
    registry = TranscriptLineageRegistry()
    chain: list[dict[str, Any]] = []
    if stable_commits_path and stable_commits_path.exists():
        chain = build_export_chain_from_stable_commits(_load_jsonl(stable_commits_path))

    glossary_decisions = glossary_decisions or []
    for i, raw in enumerate(lines):
        body = (raw or "").strip()
        if not body:
            continue
        sp = re.match(r"^\[Speaker\s+(\d+)\]\s*(.*)$", body)
        speaker = int(sp.group(1)) if sp else None
        text = sp.group(2).strip() if sp else body
        prefix = _speaker_prefix(speaker)
        full = f"{prefix}{text}"

        source_ids: list[str] = []
        if i < len(chain):
            source_ids = list(chain[i].get("source_commit_ids", []))
        elif chain:
            source_ids = [chain[min(i, len(chain) - 1)].get("source_commit_ids", ["unknown"])[0]]
        else:
            source_ids = [f"lineage-{i+1}"]

        row = registry.create_canonical_line(
            full,
            source_commit_ids=source_ids,
            speaker=speaker,
            transformation_chain=["assembler", "boundary_merge", "duplicate_cleanup", "punctuation_cleanup"],
        )

        for dec in glossary_decisions:
            inp = dec.get("input_text", "")
            out = dec.get("output_text", "")
            corr_type = dec.get("correction_type", "glossary_term")
            if inp and out and _normalize(inp) == _normalize(full):
                if corr_type == "financial_number":
                    registry.apply_financial_correction(
                        row["canonical_line_id"],
                        out,
                        before=dec.get("before", ""),
                        after=dec.get("after", ""),
                        validation_status=dec.get("validation_status", "safe"),
                    )
                else:
                    registry.apply_glossary_correction(
                        row["canonical_line_id"],
                        out,
                        before=dec.get("before", ""),
                        after=dec.get("after", ""),
                        correction_type=corr_type,
                        glossary_term=dec.get("before", ""),
                    )
                break
            elif dec.get("before") and dec.get("after") and dec.get("before") in text:
                corrected = text.replace(dec["before"], dec["after"])
                if corr_type == "financial_number":
                    registry.apply_financial_correction(
                        row["canonical_line_id"],
                        f"{prefix}{corrected}",
                        before=dec["before"],
                        after=dec["after"],
                        validation_status=dec.get("validation_status", "safe"),
                    )
                else:
                    registry.apply_glossary_correction(
                        row["canonical_line_id"],
                        f"{prefix}{corrected}",
                        before=dec["before"],
                        after=dec["after"],
                        correction_type=corr_type,
                        glossary_term=dec["before"],
                    )
                break

    return registry


def select_final_export_canonical_lines(registry: TranscriptLineageRegistry) -> list[dict[str, Any]]:
    """Final export lock: one active corrected canonical line per lineage group."""
    if not FINAL_EXPORT_LOCK_ENABLED:
        return registry.get_active_lines()

    _jp_log("FINAL_EXPORT_LOCK_STARTED")
    active = registry.get_active_lines()
    groups: dict[str, dict[str, Any]] = {}

    for row in active:
        key_ids = tuple(sorted(row.get("represented_source_ids") or row.get("source_commit_ids") or []))
        key = "|".join(key_ids) if key_ids else row.get("canonical_line_id", "")
        existing = groups.get(key)
        if existing is None:
            groups[key] = row
            _jp_log("FINAL_EXPORT_SELECTED_CANONICAL_LINE", canonical_line_id=row.get("canonical_line_id"))
            continue
        if int(row.get("revision_number", 1)) >= int(existing.get("revision_number", 1)):
            groups[key] = row
            _jp_log("FINAL_EXPORT_SELECTED_CANONICAL_LINE", canonical_line_id=row.get("canonical_line_id"))

    selected = list(groups.values())
    selected.sort(key=lambda r: (r.get("created_at", 0), r.get("canonical_line_id", "")))
    _jp_log("FINAL_EXPORT_GROUPED_BY_LINEAGE", groups=len(selected))
    _jp_log("FINAL_EXPORT_LOCK_COMPLETED")
    return selected


def scan_pre_correction_reentry(
    registry: TranscriptLineageRegistry,
    *,
    ui_exported_path: Path | None = None,
    stable_commits_path: Path | None = None,
    export_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Block pre-correction lines from re-entering final export."""
    _jp_log("PRE_CORRECTION_REENTRY_SCAN_STARTED")
    represented = registry.get_represented_source_ids()
    active_texts = {_normalize(ln.get("text", "")) for ln in registry.get_active_lines()}
    blocked: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    warnings: list[str] = []

    candidates: list[dict[str, Any]] = []
    if ui_exported_path and ui_exported_path.exists():
        for row in _load_jsonl(ui_exported_path):
            ui_text = row.get("ui_text", "")
            if not ui_text:
                continue
            candidates.append(
                {
                    "text": ui_text,
                    "source_commit_id": row.get("source_stable_commit_id", ""),
                    "source": "ui_exported_segments",
                }
            )
    if stable_commits_path and stable_commits_path.exists():
        idx = 0
        for row in _load_jsonl(stable_commits_path):
            text = (row.get("stable_text") or "").strip()
            if not text:
                continue
            idx += 1
            candidates.append(
                {"text": text, "source_commit_id": f"stable-{idx}", "source": "stable_commits"}
            )

    export_norms = {_normalize(ln) for ln in (export_lines or [])}

    for cand in candidates:
        text = cand["text"]
        sid = cand.get("source_commit_id", "")
        norm = _normalize(text)
        item = {"text_preview": text[:120], "source_commit_id": sid, "source": cand.get("source", "")}

        if sid and sid in represented:
            if norm not in active_texts and norm not in export_norms:
                item["reason"] = "source_represented_by_corrected_line"
                blocked.append(item)
                _jp_log("PRE_CORRECTION_REENTRY_BLOCKED", source_commit_id=sid)
                if CORRECTED_LINE_REPRESENTS_SOURCE_ENABLED:
                    _jp_log("LINEAGE_CORRECTED_LINE_REPRESENTS_SOURCE", source_commit_id=sid)
                continue

        if norm in active_texts or norm in export_norms:
            item["reason"] = "already_in_canonical_export"
            allowed.append(item)
            continue

        if sid and sid not in represented:
            item["reason"] = "unique_lineage_not_represented"
            allowed.append(item)
            _jp_log("PRE_CORRECTION_REENTRY_ALLOWED_UNIQUE_LINEAGE")
        else:
            item["reason"] = "pre_correction_text_blocked"
            blocked.append(item)
            _jp_log("PRE_CORRECTION_REENTRY_BLOCKED")

    report = {
        "app_version": APP_VERSION,
        "pre_correction_candidates_count": len(candidates),
        "pre_correction_blocked_count": len(blocked),
        "pre_correction_allowed_count": len(allowed),
        "blocked_items": blocked[:50],
        "allowed_items": allowed[:20],
        "warnings": warnings,
    }
    _jp_log("PRE_CORRECTION_REENTRY_REPORT_WRITTEN", blocked=len(blocked))
    return report


def format_export_lines(canonical_rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in canonical_rows:
        text = (row.get("text") or "").strip()
        if text:
            lines.append(text)
    return lines


def write_canonical_ledger(
    registry: TranscriptLineageRegistry,
    *,
    run_folder: Path | None = None,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    _jp_log("CANONICAL_TRANSCRIPT_LEDGER_WRITE_STARTED")
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in registry.get_ledger()) + (
        "\n" if registry.get_ledger() else ""
    )
    if run_folder:
        run_folder = Path(run_folder)
        run_path = run_folder / "transcripts" / "canonical_transcript_ledger.jsonl"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(body, encoding="utf-8")
        paths["canonical_transcript_ledger_path"] = str(run_path).replace("\\", "/")
        _jp_log("CANONICAL_TRANSCRIPT_LEDGER_RECORD_WRITTEN", count=len(registry.get_ledger()))
    latest = Path("troubleshooting/latest/canonical_transcript_ledger.jsonl")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(body, encoding="utf-8")
    paths["latest_canonical_transcript_ledger_path"] = str(latest).replace("\\", "/")
    _jp_log("CANONICAL_TRANSCRIPT_LEDGER_FINALIZED")
    _jp_log("CANONICAL_TRANSCRIPT_LEDGER_LATEST_COPY_WRITTEN")
    return paths


def write_pre_correction_report(report: dict[str, Any], *, run_folder: Path | None = None) -> dict[str, str]:
    paths: dict[str, str] = {}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if run_folder:
        run_folder = Path(run_folder)
        acc = run_folder / "accuracy"
        acc.mkdir(parents=True, exist_ok=True)
        p = acc / "pre_correction_reentry_report.json"
        p.write_text(payload, encoding="utf-8")
        paths["pre_correction_reentry_report_path"] = str(p).replace("\\", "/")
    latest = Path("troubleshooting/latest/pre_correction_reentry_report.json")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(payload, encoding="utf-8")
    paths["latest_pre_correction_reentry_report_path"] = str(latest).replace("\\", "/")
    return paths


def classify_source_commits(
    *,
    stable_commits_path: Path | None = None,
    stop_tail_path: Path | None = None,
) -> dict[str, Any]:
    """Classify observed vs export-required vs intentionally suppressed source commits."""
    observed: list[dict[str, Any]] = []
    intentionally_suppressed: list[dict[str, Any]] = []
    export_required: list[str] = []

    stop_tail_by_id: dict[str, dict[str, Any]] = {}
    if stop_tail_path and stop_tail_path.exists():
        for row in _load_jsonl(stop_tail_path):
            sid = row.get("stable_commit_id") or row.get("source_commit_id") or ""
            if sid:
                stop_tail_by_id[sid] = row

    if stable_commits_path and stable_commits_path.exists():
        idx = 0
        for row in _load_jsonl(stable_commits_path):
            text = (row.get("stable_text") or "").strip()
            if not text:
                continue
            idx += 1
            sid = row.get("stable_commit_id") or f"stable-{idx}"
            item = {
                "source_commit_id": sid,
                "text_preview": text[:120],
                "commit_reason": row.get("commit_reason", ""),
                "export_eligibility": row.get("export_eligibility", "export_required"),
            }
            observed.append(item)

            is_suppressed = False
            provenance: dict[str, Any] = {}
            export_elig = row.get("export_eligibility") or (row.get("assembler_metadata") or {}).get(
                "export_eligibility", ""
            )
            if export_elig == "intentionally_suppressed" or row.get("debug_history_only"):
                is_suppressed = True
                provenance = {
                    "source": "stable_commits",
                    "export_eligibility": export_elig,
                    "suppression_classification": row.get("suppression_classification", ""),
                    "suppression_reason": row.get("suppression_reason", ""),
                }
            tail = stop_tail_by_id.get(sid)
            if tail and tail.get("suppressed_from_alpha") and tail.get("classification") in (
                "incomplete_suppressed",
                "noise_suppressed",
            ):
                is_suppressed = True
                provenance = {
                    "source": "stop_tail_decisions",
                    "classification": tail.get("classification"),
                    "suppression_reason": tail.get("suppression_reason") or tail.get("incomplete_reason"),
                    "stable_commit_id": sid,
                }
                _jp_log("INTENTIONAL_SUPPRESSION_PROVENANCE_VERIFIED", source_commit_id=sid)
            elif is_suppressed and INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED:
                _jp_log("INTENTIONAL_SUPPRESSION_PROVENANCE_VERIFIED", source_commit_id=sid)
            elif is_suppressed:
                _jp_log("INTENTIONAL_SUPPRESSION_PROVENANCE_MISSING", source_commit_id=sid)

            if is_suppressed and (provenance or not INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED):
                intentionally_suppressed.append({**item, "provenance": provenance, "reason": provenance.get("suppression_reason", "")})
                _jp_log("SOURCE_COMMIT_CLASSIFIED_INTENTIONALLY_SUPPRESSED", source_commit_id=sid)
            else:
                export_required.append(sid)
                _jp_log("SOURCE_COMMIT_CLASSIFIED_EXPORT_REQUIRED", source_commit_id=sid)

    return {
        "observed_source_commit_ids": [o["source_commit_id"] for o in observed],
        "export_required_source_commit_ids": export_required,
        "intentionally_suppressed_source_commit_ids": [s["source_commit_id"] for s in intentionally_suppressed],
        "intentional_suppression_items": intentionally_suppressed,
        "source_commit_observed_count": len(observed),
        "source_commit_intentionally_suppressed_count": len(intentionally_suppressed),
        "source_commit_required_count": len(export_required),
    }


def enrich_correction_decisions(
    registry: TranscriptLineageRegistry,
    decisions: list[dict[str, Any]],
    export_lines: list[str],
) -> list[dict[str, Any]]:
    if not CANONICAL_CORRECTION_LINEAGE_REQUIRED:
        return decisions
    active = registry.get_active_lines()
    line_map: dict[str, dict[str, Any]] = {}
    for row in active:
        line_map[_normalize(row.get("text", ""))] = row
    enriched: list[dict[str, Any]] = []
    for dec in decisions:
        out = dict(dec)
        out_text = dec.get("output_text") or dec.get("final_text") or ""
        match = line_map.get(_normalize(out_text))
        if not match:
            for row in active:
                if dec.get("before") and dec.get("before") in (row.get("text") or ""):
                    match = row
                    break
        if match:
            out["canonical_line_id"] = match.get("canonical_line_id", "")
            out["stable_line_id"] = match.get("stable_line_id", "")
            out["source_commit_ids"] = list(match.get("source_commit_ids", []))
            out["represented_source_ids"] = list(match.get("represented_source_ids", []))
            out["revision_number"] = match.get("revision_number", 1)
            out["old_text"] = dec.get("before", dec.get("old_text", ""))
            out["candidate_text"] = dec.get("before", "")
            out["final_text"] = dec.get("after", dec.get("corrected_text", ""))
            out["validation_status"] = dec.get("validation_status", "applied")
        enriched.append(out)
    return enriched


def analyze_lineage_export_coverage(
    registry: TranscriptLineageRegistry,
    export_lines: list[str],
    *,
    run_id: str = "",
    pre_correction_report: dict[str, Any] | None = None,
    stable_commits_path: Path | None = None,
    ui_exported_path: Path | None = None,
    stop_tail_path: Path | None = None,
    financial_safety_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lineage-first export coverage (lineage_v2_suppression_aware)."""
    _jp_log("LINEAGE_EXPORT_COVERAGE_STARTED")
    if SUPPRESSION_AWARE_LINEAGE_COVERAGE_ENABLED:
        _jp_log("SUPPRESSION_AWARE_LINEAGE_COVERAGE_ACTIVE")

    classification = classify_source_commits(
        stable_commits_path=stable_commits_path,
        stop_tail_path=stop_tail_path,
    )
    for sid in classification.get("intentionally_suppressed_source_commit_ids", []):
        item = next(
            (x for x in classification.get("intentional_suppression_items", []) if x.get("source_commit_id") == sid),
            {},
        )
        registry.record_suppressed_source(
            source_commit_id=sid,
            text=item.get("text_preview", ""),
            suppression_classification=item.get("provenance", {}).get("classification", "incomplete_suppressed"),
            suppression_reason=item.get("reason", ""),
            provenance=item.get("provenance"),
        )

    export_required_ids = set(classification.get("export_required_source_commit_ids", []))
    represented = registry.get_represented_source_ids()
    represented_required = represented & export_required_ids if export_required_ids else represented
    missing_required = sorted(export_required_ids - represented) if export_required_ids else []

    valid_loss_items: list[dict[str, Any]] = []
    for mid in missing_required:
        valid_loss_items.append({"source_commit_id": mid, "reason": "export_required_not_represented"})
        _jp_log("EXPORT_REQUIRED_SOURCE_COMMIT_MISSING", source_commit_id=mid)

    for sid in represented_required:
        _jp_log("LINEAGE_SOURCE_COMMIT_REPRESENTED", source_commit_id=sid)

    pre_blocked = (pre_correction_report or {}).get("pre_correction_blocked_count", 0)
    final_has_pre = False
    if pre_correction_report:
        for item in pre_correction_report.get("blocked_items", []):
            preview = item.get("text_preview", "")
            for exp in export_lines:
                if _normalize(preview) == _normalize(exp):
                    final_has_pre = True

    from alpha.transcription.financial_number_safety import audit_financial_text, detect_malformed_numeric_output
    from alpha.transcription.final_output_cleanup import detect_cumulative_alpha_lines_v2

    joined = "\n".join(export_lines)
    fin_audit = audit_financial_text(joined)
    fin_metrics = financial_safety_metrics or {}
    malformed_count = fin_audit.get("malformed_numeric_output_count", 0)
    dangerous_count = fin_metrics.get("dangerous_correction_count", fin_metrics.get("dangerous_correction_blocked_count", 0))
    if malformed_count > 0:
        dangerous_count = max(dangerous_count, malformed_count)

    cum = detect_cumulative_alpha_lines_v2(export_lines)
    required_count = classification.get("source_commit_required_count", 0)
    represented_count = len(represented_required)
    source_ratio = represented_count / required_count if required_count else 1.0
    valid_segment_loss_count = len(missing_required)

    blockers: list[str] = []
    if valid_segment_loss_count > 0:
        blockers.append("valid_segment_loss_detected")
    if final_has_pre:
        blockers.append("pre_correction_lines_in_final_export")
    if cum.get("cumulative_duplicate_count", 0) > 0:
        blockers.append("cumulative_duplicate_detected")
    if cum.get("punctuation_artifact_count", 0) > 0:
        blockers.append("punctuation_artifact_detected")
    if malformed_count > 0:
        blockers.append("malformed_numeric_output_detected")
    if dangerous_count > 0:
        blockers.append("dangerous_correction_detected")

    export_lossless = (
        valid_segment_loss_count == 0
        and not final_has_pre
        and cum.get("cumulative_duplicate_count", 0) == 0
        and malformed_count == 0
        and dangerous_count == 0
    )
    clean_ready = (
        export_lossless
        and source_ratio >= 0.98
        and cum.get("punctuation_artifact_count", 0) == 0
    )

    report = {
        "app_version": APP_VERSION,
        "run_id": run_id,
        "coverage_algorithm_version": "lineage_v2_suppression_aware",
        "source_commit_observed_count": classification.get("source_commit_observed_count", 0),
        "source_commit_intentionally_suppressed_count": classification.get("source_commit_intentionally_suppressed_count", 0),
        "source_commit_required_count": required_count,
        "source_commit_represented_required_count": represented_count,
        "source_commit_missing_required_count": len(missing_required),
        "source_commit_total_count": classification.get("source_commit_observed_count", required_count),
        "source_commit_represented_count": represented_count,
        "source_commit_missing_count": len(missing_required),
        "source_commit_coverage_ratio": round(source_ratio, 4),
        "lineage_coverage_ratio": round(source_ratio, 4),
        "text_coverage_ratio": round(source_ratio, 4),
        "intentional_suppression_items": classification.get("intentional_suppression_items", []),
        "represented_by_corrected_line_count": pre_blocked,
        "pre_correction_reentry_blocked_count": pre_blocked,
        "valid_segment_loss_count": valid_segment_loss_count,
        "valid_segment_loss_items": valid_loss_items,
        "export_lossless": export_lossless,
        "export_coverage_ratio": round(source_ratio, 4),
        "clean_export_ready_for_scoring": clean_ready,
        "final_export_contains_pre_correction_lines": final_has_pre,
        "canonical_export_line_count": len(export_lines),
        "blockers": blockers,
        "cumulative_duplicate_count": cum.get("cumulative_duplicate_count", 0),
        "punctuation_artifact_count": cum.get("punctuation_artifact_count", 0),
        "malformed_numeric_output_count": malformed_count,
        "dangerous_correction_count": dangerous_count,
        "financial_number_correction_attempt_count": fin_metrics.get("financial_number_correction_attempt_count", 0),
        "financial_number_correction_applied_count": fin_metrics.get("financial_number_correction_applied_count", 0),
        "financial_number_correction_blocked_count": fin_metrics.get("financial_number_correction_blocked_count", 0),
    }
    if clean_ready:
        _jp_log("SUPPRESSION_AWARE_LINEAGE_COVERAGE_PASSED")
        _jp_log("LINEAGE_EXPORT_COVERAGE_PASSED")
    else:
        _jp_log("SUPPRESSION_AWARE_LINEAGE_COVERAGE_FAILED", blockers=blockers)
        _jp_log("LINEAGE_EXPORT_COVERAGE_FAILED", blockers=blockers)
    return report


def finalize_canonical_export(
    cleaned_lines: list[str],
    *,
    run_id: str = "",
    run_folder: Path | None = None,
    glossary_decisions: list[dict[str, Any]] | None = None,
    financial_safety_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build canonical lineage, apply export lock, return final export."""
    if not CANONICAL_TRANSCRIPT_LINEAGE_ENABLED:
        return {
            "export_lines": cleaned_lines,
            "canonical_records": [],
            "registry": None,
            "coverage_report": {},
            "pre_correction_report": {},
            "ledger_paths": {},
        }

    run_folder = Path(run_folder) if run_folder else None
    stable_path = (run_folder / "transcripts" / "stable_commits.jsonl") if run_folder else None
    ui_path = (run_folder / "transcripts" / "ui_exported_segments.jsonl") if run_folder else None

    registry = build_registry_from_export_lines(
        cleaned_lines,
        stable_commits_path=stable_path,
        glossary_decisions=glossary_decisions,
    )
    selected = select_final_export_canonical_lines(registry)
    export_lines = format_export_lines(selected)

    enriched_decisions = enrich_correction_decisions(registry, glossary_decisions or [], export_lines)

    pre_report = {}
    if PRE_CORRECTION_REENTRY_BLOCK_ENABLED:
        pre_report = scan_pre_correction_reentry(
            registry,
            ui_exported_path=ui_path,
            stable_commits_path=stable_path,
            export_lines=export_lines,
        )
        pre_paths = write_pre_correction_report(pre_report, run_folder=run_folder)
        pre_report.update(pre_paths)

    ledger_paths = write_canonical_ledger(registry, run_folder=run_folder)

    stop_tail_path = (run_folder / "accuracy" / "stop_tail_decisions.jsonl") if run_folder else None
    fin_metrics: dict[str, Any] = dict(financial_safety_metrics or {})
    coverage = analyze_lineage_export_coverage(
        registry,
        export_lines,
        run_id=run_id,
        pre_correction_report=pre_report,
        stable_commits_path=stable_path,
        ui_exported_path=ui_path,
        stop_tail_path=stop_tail_path,
        financial_safety_metrics=fin_metrics,
    )

    try:
        from alpha.utils.canonical_export_writer import set_canonical_export_payload

        set_canonical_export_payload(export_lines, canonical_records=selected, coverage_report=coverage)
    except Exception:
        pass

    _jp_log("LATEST_LIVE_ALPHA_OUTPUT_WRITTEN_FROM_CANONICAL_LINES", lines=len(export_lines))
    _jp_log("CANONICAL_LINE_ACTIVE_FOR_EXPORT", count=len(selected))

    return {
        "export_lines": export_lines,
        "canonical_records": selected,
        "registry": registry,
        "coverage_report": coverage,
        "pre_correction_report": pre_report,
        "ledger_paths": ledger_paths,
        "enriched_decisions": enriched_decisions,
    }
