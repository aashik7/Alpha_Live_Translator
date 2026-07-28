"""Lossless clean export coverage analysis and conservative suppression (8.5.25.1)."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from alpha.constants import (
    APP_VERSION,
    CONSERVATIVE_DUPLICATE_SUPPRESSION_ENABLED,
    EXPORT_COVERAGE_GATE_ENABLED,
    LOSSLESS_CLEAN_EXPORT_ENABLED,
    SUPPRESSION_DECISION_LOG_ENABLED,
    VALID_SEGMENT_LOSS_BLOCKER_ENABLED,
)
from alpha.transcription.stable_line_revision import prefix_overlap_ratio, _strip_speaker
from alpha.utils.cjk_text import compact_cjk_for_compare

_NUMBER_RE = re.compile(r"[0-9０-９%％億万千百十]+")
_BUSINESS_HINTS = (
    "売上", "営業利益", "経常利益", "純利益", "決算", "四半期", "通期", "予算",
    "保育", "不動産", "自己資本", "負債", "資産", "進捗", "増収", "減収",
)
_GLOSSARY_HINTS = (
    "さくら", "桜咲", "公定価格", "増床", "中央区", "葉酸", "ご支援",
)


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _normalize(text: str) -> str:
    return compact_cjk_for_compare(_strip_speaker(text or ""), "ja")


def _jp_char_len(text: str) -> int:
    try:
        from alpha.transcription.japanese_stable_accuracy import count_japanese_chars

        return count_japanese_chars(text)
    except Exception:
        return len(_normalize(text))


def contains_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(_strip_speaker(text)))


def contains_glossary_term(text: str) -> bool:
    body = _strip_speaker(text)
    return any(h in body for h in _GLOSSARY_HINTS)


def contains_business_term(text: str) -> bool:
    body = _strip_speaker(text)
    return any(h in body for h in _BUSINESS_HINTS)


def is_sentence_ending_predicate(text: str) -> bool:
    body = _strip_speaker(text).rstrip()
    return body.endswith(("ました", "ません", "です", "おります", "いたします", "となりました", "でした"))


def is_meaningful_segment(text: str) -> bool:
    body = _strip_speaker(text).strip()
    if not body:
        return False
    if len(body) <= 2 and not contains_number(body):
        return False
    if _jp_char_len(body) >= 8:
        return True
    if contains_number(body):
        return True
    if contains_glossary_term(body):
        return True
    if contains_business_term(body):
        return True
    if is_sentence_ending_predicate(body):
        return True
    return False


def is_punctuation_only(text: str) -> bool:
    body = _strip_speaker(text).strip()
    if not body:
        return True
    stripped = re.sub(r"[。、！？\s\.\,\;\:\[\]（）()「」『』]", "", body)
    return len(stripped) == 0


def coverage_ratio_against_export(segment: str, export_lines: list[str]) -> float:
    seg = _normalize(segment)
    if not seg:
        return 1.0
    export_joined = _normalize("\n".join(export_lines))
    if seg in export_joined:
        return 1.0
    best = 0.0
    for ln in export_lines:
        exp = _normalize(ln)
        if not exp:
            continue
        if seg in exp:
            return len(seg) / max(len(seg), 1)
        if exp in seg:
            best = max(best, len(exp) / len(seg))
        ov = prefix_overlap_ratio(segment, ln)
        best = max(best, ov)
    return best


def unique_char_count_not_in_export(segment: str, export_lines: list[str]) -> int:
    seg = _normalize(segment)
    export_joined = _normalize("\n".join(export_lines))
    unique = 0
    for ch in seg:
        if ch not in export_joined:
            unique += 1
    return unique


def safe_to_suppress_duplicate(
    cur: str,
    prev: str,
    export_so_far: list[str],
    *,
    replacement_text: str = "",
) -> tuple[bool, str]:
    cur_body = _strip_speaker(cur)
    prev_body = _strip_speaker(prev)

    if is_punctuation_only(cur):
        return True, "punctuation_only"

    if not cur_body.strip():
        return True, "empty"

    if replacement_text and _normalize(replacement_text) in _normalize("\n".join(export_so_far)):
        return True, "revision_replaced"

    prev_norm = _normalize(prev)
    cur_norm = _normalize(cur)
    if prev_norm and cur_norm == prev_norm:
        return True, "exact_normalized_duplicate"

    export_joined = _normalize("\n".join(export_so_far))
    if cur_norm and cur_norm in export_joined:
        cov = coverage_ratio_against_export(cur, export_so_far)
        if cov >= 0.95:
            return True, "contained_in_longer_export"

    if contains_number(cur) and unique_char_count_not_in_export(cur, export_so_far) >= 3:
        return False, "retained_unique_number"

    if contains_glossary_term(cur) and unique_char_count_not_in_export(cur, export_so_far) >= 3:
        return False, "retained_unique_glossary_term"

    if contains_business_term(cur) and unique_char_count_not_in_export(cur, export_so_far) >= 5:
        return False, "retained_unique_business_term"

    if unique_char_count_not_in_export(cur, export_so_far) > 15:
        return False, "retained_unique_chars"

    if is_meaningful_segment(cur) and is_sentence_ending_predicate(cur):
        cov = coverage_ratio_against_export(cur, export_so_far)
        if cov < 0.95:
            return False, "retained_complete_sentence"

    is_prefix = cur_body.startswith(prev_body) and _jp_char_len(prev_body) >= 12 and len(cur_body) > len(prev_body)
    overlap = prefix_overlap_ratio(prev, cur) >= 0.7
    prev_in_cur = prev_body in cur_body and len(cur_body) - len(prev_body) >= 4

    if CONSERVATIVE_DUPLICATE_SUPPRESSION_ENABLED:
        if is_prefix or prev_in_cur:
            if len(cur_body) >= len(prev_body):
                return True, "prefix_containment_longer_kept"
            if unique_char_count_not_in_export(cur, export_so_far) <= 4:
                return True, "prefix_containment_shorter_suppressed"
            return False, "retained_uncertain"
        if overlap and unique_char_count_not_in_export(cur, export_so_far) <= 4:
            return True, "overlap_duplicate"
        if overlap:
            return False, "retained_uncertain"

    return False, "retained_uncertain"


def conservative_sweep_residual_duplicates(
    lines: list[str],
    *,
    run_id: str = "",
) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    from alpha.transcription.final_output_cleanup import (
        cleanup_punctuation_artifacts,
        count_punctuation_artifacts,
        sweep_residual_duplicates,
    )
    from alpha.constants import RESIDUAL_DUPLICATE_CLEANUP_ENABLED

    if not LOSSLESS_CLEAN_EXPORT_ENABLED or not CONSERVATIVE_DUPLICATE_SUPPRESSION_ENABLED:
        out, metrics = sweep_residual_duplicates(lines)
        return out, metrics, []

    _jp_log("EXPORT_COVERAGE_ANALYSIS_STARTED", input_lines=len(lines))
    decisions: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "residual_duplicate_before_count": 0,
        "residual_duplicate_after_count": 0,
        "residual_duplicate_suppressed_count": 0,
        "residual_duplicate_revised_count": 0,
        "punctuation_artifact_before_count": count_punctuation_artifacts(lines),
        "punctuation_artifact_after_count": 0,
        "punctuation_artifact_cleaned_count": 0,
        "conservative_suppression_retained_count": 0,
    }

    cleaned_input: list[str] = []
    for i, ln in enumerate(lines):
        body = ln.strip()
        if not body:
            continue
        sp = re.match(r"^(\[Speaker\s+\d+\]\s*)", body)
        prefix = sp.group(1) if sp else ""
        text_body = body[len(prefix) :] if prefix else body
        cleaned, punct_changed = cleanup_punctuation_artifacts(text_body)
        if punct_changed:
            metrics["punctuation_artifact_cleaned_count"] += 1
        cleaned_input.append(f"{prefix}{cleaned}" if prefix else cleaned)

    if not RESIDUAL_DUPLICATE_CLEANUP_ENABLED:
        metrics["punctuation_artifact_after_count"] = count_punctuation_artifacts(cleaned_input)
        return cleaned_input, metrics, decisions

    out: list[str] = []
    for i, cur in enumerate(cleaned_input):
        if not out:
            out.append(cur)
            decisions.append(_decision_record(i, cur, "exported", "first_line", out, run_id))
            continue
        prev = out[-1]
        safe, reason = safe_to_suppress_duplicate(cur, prev, out)
        rec = _decision_record(i, cur, "", reason, out, run_id, safe_to_suppress=safe)
        rec["coverage_ratio_against_export"] = coverage_ratio_against_export(cur, out)
        rec["unique_char_count"] = unique_char_count_not_in_export(cur, out)
        rec["contains_number"] = contains_number(cur)
        rec["contains_glossary_term"] = contains_glossary_term(cur)
        rec["contains_business_term"] = contains_business_term(cur)

        if safe:
            metrics["residual_duplicate_before_count"] += 1
            prev_body = _strip_speaker(prev)
            cur_body = _strip_speaker(cur)
            if len(cur_body) >= len(prev_body) and reason.startswith("prefix"):
                out[-1] = cur
                metrics["residual_duplicate_revised_count"] += 1
                rec["decision"] = "suppressed_revision"
                rec["replacement_text_if_any"] = cur
                _jp_log("SUPPRESSION_DECISION_CONTAINED_IN_LONGER_EXPORT")
            else:
                metrics["residual_duplicate_suppressed_count"] += 1
                rec["decision"] = "suppressed_duplicate" if "duplicate" in reason else f"suppressed_{reason}"
                _jp_log("EXPORT_COVERAGE_SEGMENT_SUPPRESSED_DUPLICATE", reason=reason)
        else:
            out.append(cur)
            metrics["conservative_suppression_retained_count"] += 1
            rec["decision"] = "retained_uncertain" if "uncertain" in reason else reason
            if "number" in reason:
                _jp_log("SUPPRESSION_DECISION_RETAINED_UNIQUE_NUMBER")
            elif "glossary" in reason:
                _jp_log("SUPPRESSION_DECISION_RETAINED_UNIQUE_GLOSSARY_TERM")
            elif "business" in reason:
                _jp_log("SUPPRESSION_DECISION_RETAINED_UNIQUE_BUSINESS_TERM")
            else:
                _jp_log("EXPORT_COVERAGE_UNCERTAIN_SEGMENT_RETAINED", reason=reason)
            _jp_log("SUPPRESSION_DECISION_RETAINED_UNCERTAIN", reason=reason)
        decisions.append(rec)

    metrics["punctuation_artifact_after_count"] = count_punctuation_artifacts(out)
    for i in range(1, len(out)):
        prev = _strip_speaker(out[i - 1])
        cur = _strip_speaker(out[i])
        if cur.startswith(prev) and len(cur) > len(prev) and _jp_char_len(prev) >= 12:
            metrics["residual_duplicate_after_count"] += 1

    return out, metrics, decisions


def _decision_record(
    segment_id: int,
    text: str,
    decision: str,
    reason: str,
    export_so_far: list[str],
    run_id: str,
    *,
    safe_to_suppress: bool = False,
) -> dict[str, Any]:
    return {
        "timestamp": time.time(),
        "run_id": run_id,
        "segment_id": segment_id,
        "source_file": "clean_export_sweep",
        "input_text": text,
        "decision": decision or ("suppressed_duplicate" if safe_to_suppress else "exported"),
        "reason": reason,
        "coverage_ratio_against_export": coverage_ratio_against_export(text, export_so_far),
        "unique_char_count": unique_char_count_not_in_export(text, export_so_far),
        "contains_number": contains_number(text),
        "contains_glossary_term": contains_glossary_term(text),
        "contains_business_term": contains_business_term(text),
        "replacement_text_if_any": "",
        "safe_to_suppress": safe_to_suppress,
    }


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


def _load_alpha_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def collect_candidate_segments(
    *,
    ui_exported_path: Path | None = None,
    stable_commits_path: Path | None = None,
    clean_active_path: Path | None = None,
    stable_revision_path: Path | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(text: str, source: str, seg_id: str, speaker: Any = None) -> None:
        body = (text or "").strip()
        if not body or not is_meaningful_segment(body):
            return
        norm = _normalize(body)
        if norm in seen:
            return
        seen.add(norm)
        from alpha.utils.ui_speaker_label import format_ui_speaker_line

        if body.startswith("Speaker:") or body.startswith("[Speaker"):
            full = format_ui_speaker_line(body)
        else:
            full = format_ui_speaker_line(body)
        candidates.append({"text": full, "source": source, "segment_id": seg_id, "speaker": speaker})

    if ui_exported_path and ui_exported_path.exists():
        for row in _load_jsonl(ui_exported_path):
            ui_text = row.get("ui_text", "")
            sp = row.get("speaker_label", "")
            speaker = int(sp.replace("Speaker", "").strip()) if sp and "Speaker" in sp else None
            _add(ui_text, "ui_exported_segments", row.get("ui_segment_id", ""), speaker)

    if stable_commits_path and stable_commits_path.exists():
        for i, row in enumerate(_load_jsonl(stable_commits_path)):
            _add(row.get("stable_text", ""), "stable_commits", f"stable-{i+1}", row.get("speaker"))

    if clean_active_path and clean_active_path.exists():
        for row in _load_jsonl(clean_active_path):
            _add(row.get("text", ""), "clean_active_transcript", row.get("stable_line_id", ""), row.get("speaker"))

    if stable_revision_path and stable_revision_path.exists():
        for i, row in enumerate(_load_jsonl(stable_revision_path)):
            if row.get("event_type") in ("created", "revised", "glossary_corrected"):
                _add(row.get("new_text", ""), "stable_revision_history", f"rev-{i+1}")

    return candidates


def build_lossless_export_lines(
    base_lines: list[str],
    candidates: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Ensure meaningful candidate segments are represented in final export."""
    from alpha.constants import (
        LINEAGE_BASED_EXPORT_COVERAGE_ENABLED,
        TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY,
    )

    if LINEAGE_BASED_EXPORT_COVERAGE_ENABLED and TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY:
        _jp_log("LINEAGE_CORRECTED_LINE_REPRESENTS_SOURCE")
        return list(base_lines), []

    out = list(base_lines)
    recovery_decisions: list[dict[str, Any]] = []
    export_uncertain_retained = 0

    for cand in candidates:
        text = cand["text"]
        cov = coverage_ratio_against_export(text, out)
        if cov >= 0.95:
            _jp_log("EXPORT_COVERAGE_SEGMENT_MATCHED", source=cand.get("source", ""))
            continue
        if not is_meaningful_segment(text):
            continue
        out.append(text)
        export_uncertain_retained += 1
        recovery_decisions.append(
            {
                "timestamp": time.time(),
                "segment_id": cand.get("segment_id", ""),
                "source_file": cand.get("source", ""),
                "input_text": text,
                "decision": "loss_detected",
                "reason": "valid_segment_recovered",
                "coverage_ratio_against_export": cov,
                "unique_char_count": unique_char_count_not_in_export(text, base_lines),
                "contains_number": contains_number(text),
                "contains_glossary_term": contains_glossary_term(text),
                "contains_business_term": contains_business_term(text),
                "replacement_text_if_any": "",
                "safe_to_suppress": False,
                "export_uncertain_retained": True,
            }
        )
        _jp_log("EXPORT_COVERAGE_VALID_SEGMENT_LOSS_DETECTED", text_preview=_strip_speaker(text)[:60])
        _jp_log("SUPPRESSION_DECISION_VALID_LOSS_BLOCKED")

    return out, recovery_decisions


def analyze_export_coverage(
    *,
    run_id: str = "",
    run_folder: Path | None = None,
    export_lines: list[str] | None = None,
    ui_exported_path: Path | None = None,
    stable_commits_path: Path | None = None,
    clean_active_path: Path | None = None,
    stable_revision_path: Path | None = None,
    boundary_decisions_path: Path | None = None,
    glossary_decisions_path: Path | None = None,
) -> dict[str, Any]:
    run_folder = Path(run_folder) if run_folder else None
    alpha_path = Path("troubleshooting/latest/latest_live_alpha_output.txt")
    if export_lines is None:
        export_lines = _load_alpha_lines(alpha_path)

    if ui_exported_path is None and run_folder:
        ui_exported_path = run_folder / "transcripts" / "ui_exported_segments.jsonl"
    if stable_commits_path is None and run_folder:
        stable_commits_path = run_folder / "transcripts" / "stable_commits.jsonl"
    if clean_active_path is None:
        clean_active_path = Path("troubleshooting/latest/clean_active_transcript.jsonl")
    if stable_revision_path is None:
        stable_revision_path = Path("troubleshooting/latest/stable_revision_history.jsonl")
    if glossary_decisions_path is None and run_folder:
        glossary_decisions_path = run_folder / "accuracy" / "glossary_correction_decisions.jsonl"

    candidates = collect_candidate_segments(
        ui_exported_path=ui_exported_path,
        stable_commits_path=stable_commits_path,
        clean_active_path=clean_active_path,
        stable_revision_path=stable_revision_path,
    )
    meaningful = [c for c in candidates if is_meaningful_segment(c["text"])]
    exported_meaningful = 0
    valid_loss_items: list[dict[str, Any]] = []
    dup_suppressed = 0
    punct_suppressed = 0
    empty_suppressed = 0

    for cand in meaningful:
        cov = coverage_ratio_against_export(cand["text"], export_lines)
        if cov >= 0.95:
            exported_meaningful += 1
            _jp_log("EXPORT_COVERAGE_SEGMENT_MATCHED")
        else:
            valid_loss_items.append(
                {
                    "segment_id": cand.get("segment_id", ""),
                    "source": cand.get("source", ""),
                    "text_preview": _strip_speaker(cand["text"])[:120],
                    "coverage_ratio": round(cov, 4),
                }
            )
            _jp_log("EXPORT_COVERAGE_VALID_SEGMENT_LOSS_DETECTED")

    glossary_corrected = 0
    financial_corrected = 0
    if glossary_decisions_path and glossary_decisions_path.exists():
        for row in _load_jsonl(glossary_decisions_path):
            if row.get("correction_type") == "financial_number":
                financial_corrected += 1
            elif row.get("before") != row.get("after"):
                glossary_corrected += 1

    ui_count = 0
    if ui_exported_path and ui_exported_path.exists():
        ui_count = sum(1 for r in _load_jsonl(ui_exported_path) if r.get("ui_text"))
    stable_count = 0
    if stable_commits_path and stable_commits_path.exists():
        stable_count = sum(1 for r in _load_jsonl(stable_commits_path) if r.get("stable_text"))
    clean_count = 0
    if clean_active_path and clean_active_path.exists():
        clean_count = len(_load_jsonl(clean_active_path))

    valid_segment_loss_count = len(valid_loss_items)
    suppressed_valid = valid_segment_loss_count
    export_joined = "\n".join(export_lines)
    blockers: list[str] = []
    if valid_segment_loss_count > 0 and VALID_SEGMENT_LOSS_BLOCKER_ENABLED:
        blockers.append("valid_segment_loss_detected")

    export_coverage_ratio = (
        exported_meaningful / len(meaningful) if meaningful else 1.0
    )
    export_lossless = valid_segment_loss_count == 0
    clean_export_ready = export_lossless and EXPORT_COVERAGE_GATE_ENABLED

    report: dict[str, Any] = {
        "app_version": APP_VERSION,
        "run_id": run_id,
        "ui_exported_segments_count": ui_count,
        "stable_commits_count": stable_count,
        "clean_active_transcript_count": clean_count,
        "latest_live_alpha_output_line_count": len(export_lines),
        "final_alpha_char_count": len(export_joined),
        "candidate_meaningful_segment_count": len(meaningful),
        "exported_meaningful_segment_count": exported_meaningful,
        "suppressed_segment_count": dup_suppressed + punct_suppressed + empty_suppressed,
        "suppressed_valid_segment_count": suppressed_valid,
        "valid_segment_loss_count": valid_segment_loss_count,
        "valid_segment_loss_items": valid_loss_items,
        "duplicate_suppressed_count": dup_suppressed,
        "punctuation_only_suppressed_count": punct_suppressed,
        "empty_or_noise_suppressed_count": empty_suppressed,
        "glossary_corrected_segment_count": glossary_corrected,
        "financial_number_corrected_segment_count": financial_corrected,
        "export_lossless": export_lossless,
        "export_coverage_ratio": round(export_coverage_ratio, 4),
        "clean_export_ready_for_scoring": clean_export_ready,
        "blockers": blockers,
    }
    return report


def write_export_coverage_report(
    report: dict[str, Any],
    *,
    run_folder: Path | None = None,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if run_folder:
        run_folder = Path(run_folder)
        acc = run_folder / "accuracy"
        acc.mkdir(parents=True, exist_ok=True)
        run_path = acc / "export_coverage_report.json"
        run_path.write_text(payload, encoding="utf-8")
        paths["export_coverage_report_path"] = str(run_path).replace("\\", "/")
    latest = Path("troubleshooting/latest/export_coverage_report.json")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(payload, encoding="utf-8")
    paths["latest_export_coverage_report_path"] = str(latest).replace("\\", "/")
    _jp_log("EXPORT_COVERAGE_REPORT_WRITTEN", path=str(latest))
    if report.get("export_lossless"):
        _jp_log("EXPORT_COVERAGE_GATE_PASSED")
    else:
        _jp_log("EXPORT_COVERAGE_GATE_FAILED", losses=report.get("valid_segment_loss_count", 0))
    return paths


def write_suppression_decisions(
    decisions: list[dict[str, Any]],
    *,
    run_folder: Path | None = None,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    if not SUPPRESSION_DECISION_LOG_ENABLED:
        return paths
    body = "\n".join(json.dumps(d, ensure_ascii=False) for d in decisions) + ("\n" if decisions else "")
    if run_folder:
        run_folder = Path(run_folder)
        acc = run_folder / "accuracy"
        acc.mkdir(parents=True, exist_ok=True)
        run_path = acc / "export_suppression_decisions.jsonl"
        run_path.write_text(body, encoding="utf-8")
        paths["export_suppression_decisions_path"] = str(run_path).replace("\\", "/")
    latest = Path("troubleshooting/latest/export_suppression_decisions.jsonl")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(body, encoding="utf-8")
    paths["latest_export_suppression_decisions_path"] = str(latest).replace("\\", "/")
    return paths


def finalize_lossless_clean_export(
    base_lines: list[str],
    *,
    run_id: str = "",
    run_folder: Path | None = None,
    ui_exported_path: Path | None = None,
    stable_commits_path: Path | None = None,
) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    """Conservative sweep + recover missing meaningful segments."""
    _jp_log("LOSSLESS_ALPHA_EXPORT_STARTED")
    swept, sweep_metrics, sweep_decisions = conservative_sweep_residual_duplicates(base_lines, run_id=run_id)

    run_folder = Path(run_folder) if run_folder else None
    if ui_exported_path is None and run_folder:
        ui_exported_path = run_folder / "transcripts" / "ui_exported_segments.jsonl"
    if stable_commits_path is None and run_folder:
        stable_commits_path = run_folder / "transcripts" / "stable_commits.jsonl"

    candidates = collect_candidate_segments(
        ui_exported_path=ui_exported_path,
        stable_commits_path=stable_commits_path,
    )
    final_lines, recovery_decisions = build_lossless_export_lines(swept, candidates)
    all_decisions = sweep_decisions + recovery_decisions

    report = analyze_export_coverage(
        run_id=run_id,
        run_folder=run_folder,
        export_lines=final_lines,
        ui_exported_path=ui_exported_path,
        stable_commits_path=stable_commits_path,
    )
    report.update(sweep_metrics)
    paths = write_export_coverage_report(report, run_folder=run_folder)
    sup_paths = write_suppression_decisions(all_decisions, run_folder=run_folder)
    report.update(paths)
    report.update(sup_paths)

    if report.get("clean_export_ready_for_scoring"):
        _jp_log("LOSSLESS_ALPHA_EXPORT_GATE_PASSED")
        _jp_log("LOSSLESS_ALPHA_EXPORT_READY_FOR_SCORING")
    else:
        _jp_log("LOSSLESS_ALPHA_EXPORT_GATE_FAILED")
        _jp_log("LOSSLESS_ALPHA_EXPORT_NOT_READY_FOR_SCORING")

    _jp_log("LOSSLESS_ALPHA_EXPORT_WRITTEN", lines=len(final_lines))
    return final_lines, report, all_decisions
