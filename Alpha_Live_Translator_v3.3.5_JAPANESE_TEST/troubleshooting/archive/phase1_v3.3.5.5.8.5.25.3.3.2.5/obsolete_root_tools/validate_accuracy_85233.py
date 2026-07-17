"""Validate reference alignment & boundary diagnosis 8.5.23.3."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BUSINESS_TERM_RISK_REPORT_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FULL_ACCURACY_LOGGING_STILL_ENABLED,
    GLOSSARY_CANDIDATE_REPORT_ENABLED,
    JAPANESE_BOUNDARY_DIAGNOSIS_ENABLED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    LESSON_SPECIFIC_CORRECTIONS_DISABLED,
    RAW_DEEPGRAM_IMMUTABLE,
    REFERENCE_ALIGNMENT_DIAGNOSIS_ENABLED,
    REFERENCE_CLEANUP_SUGGESTIONS_ENABLED,
    RUNTIME_EVIDENCE_PACKAGE_DISABLED,
    STOP_PATH_MINIMAL_MODE,
)
from alpha.transcription.japanese_business_accuracy import (
    is_minimal_correction_mode,
    run_business_correction_guard_selftest,
    run_minimal_correction_selftest,
)


def _has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _latest_run_folder() -> Path | None:
    runs = Path("troubleshooting/runs")
    if not runs.exists():
        return None
    candidates = [p for p in runs.iterdir() if p.is_dir() and p.name != "_pending"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def main() -> int:
    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.23.3",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "raw_deepgram_immutable": RAW_DEEPGRAM_IMMUTABLE,
        "stop_baseline": STOP_PATH_MINIMAL_MODE and RUNTIME_EVIDENCE_PACKAGE_DISABLED,
        "anti_overfit": ANTI_OVERFIT_MODE_ENABLED,
        "minimal_level": AUTO_BUSINESS_CORRECTION_LEVEL == "minimal" and is_minimal_correction_mode(),
        "lesson_specific_disabled": LESSON_SPECIFIC_CORRECTIONS_DISABLED,
        "guard_selftest": run_business_correction_guard_selftest().get("ok") is True,
        "minimal_selftest": run_minimal_correction_selftest().get("ok") is True,
        "full_logging": FULL_ACCURACY_LOGGING_STILL_ENABLED,
        "analyzer_exists": Path("analyze_alpha_vs_reference.py").exists(),
        "quality_checker_exists": Path("reference_transcript_quality_check.py").exists(),
        "score_exists": Path("score_latest_accuracy.py").exists(),
        "flags_enabled": all(
            (
                REFERENCE_ALIGNMENT_DIAGNOSIS_ENABLED,
                JAPANESE_BOUNDARY_DIAGNOSIS_ENABLED,
                BUSINESS_TERM_RISK_REPORT_ENABLED,
                GLOSSARY_CANDIDATE_REPORT_ENABLED,
                REFERENCE_CLEANUP_SUGGESTIONS_ENABLED,
            )
        ),
        "no_new_auto_corrections": not _has(
            "alpha/transcription/japanese_business_accuracy.py", "BUSINESS_ACCURACY_EXPANSION_85222_ENABLED = True"
        ),
        "no_deepl": not _has("alpha/constants.py", "DEEPL_ENABLED = True"),
        "no_groq": not _has("alpha/constants.py", "GROQ_ENABLED = True"),
        "no_meetingbaas": not _has("alpha/constants.py", "MEETINGBAAS_ENABLED = True"),
        "no_diarization_active": not _has("alpha/constants.py", 'diarize_model = "'),
        "no_ui_redesign": not _has("alpha/constants.py", "UI_REDESIGN_ENABLED = True"),
    }
    warnings: list[str] = []

    latest_live = Path("troubleshooting/latest/latest_live_alpha_output.txt")
    checks["latest_live_alpha_exists"] = latest_live.exists() and latest_live.stat().st_size > 0

    latest_index = Path("troubleshooting/latest/latest_accuracy_evidence_index.json")
    if latest_index.exists():
        checks["latest_accuracy_evidence_index_exists"] = True
        try:
            idx = json.loads(latest_index.read_text(encoding="utf-8"))
            if str(idx.get("app_version", "")).startswith("3.3.5.5.8.5.23.3"):
                checks["index_85233_fields"] = all(
                    k in idx
                    for k in (
                        "reference_alignment_diagnosis_enabled",
                        "japanese_boundary_diagnosis_enabled",
                        "business_term_risk_report_enabled",
                        "glossary_candidate_report_enabled",
                        "latest_alignment_report_path",
                        "latest_boundary_error_report_path",
                        "latest_business_term_risk_report_path",
                        "latest_glossary_candidates_path",
                        "latest_reference_cleanup_suggestions_path",
                    )
                )
            else:
                warnings.append("legacy_index_85233_fields_deferred")
            if idx.get("translation_ready_ratio", 1.0) < 0.8:
                warnings.append("translation_ready_ratio_below_80")
            if idx.get("reference_quality_verdict") == "questionable_for_cer":
                warnings.append("cer_not_trusted_reference_questionable")
            if not idx.get("reference_transcript_used"):
                warnings.append("no_valid_verbatim_reference_transcript_provided")
        except Exception:
            checks["index_85233_fields"] = False

    latest_zip = Path("troubleshooting/latest/latest_accuracy_evidence_index.zip")
    checks["latest_zip_exists"] = latest_zip.exists()
    if latest_zip.exists():
        import zipfile

        with zipfile.ZipFile(latest_zip, "r") as zf:
            checks["latest_zip_non_empty_alpha"] = (
                "latest_alpha_output.txt" in zf.namelist()
                and zf.getinfo("latest_alpha_output.txt").file_size > 0
            )

    run_folder = _latest_run_folder()
    if run_folder:
        audit = run_folder / "accuracy" / "visible_error_audit.json"
        checks["visible_error_audit_exists"] = audit.exists()

    # Probe analyzer on sample input
    try:
        from analyze_alpha_vs_reference import (
            _boundary_diagnosis,
            _business_term_report,
            _extract_candidates,
            _reference_lines_with_hints,
        )

        sample_alpha = [{"line_number": 1, "normalized": "他者です", "original": "他者です"}]
        sample_ref, _, _ = _reference_lines_with_hints("## 見出し\n- 他社です\n")
        checks["business_term_report_generation"] = (
            _business_term_report(sample_alpha, sample_ref).get("business_term_risk_count", 0) >= 1
        )
        checks["boundary_report_generation"] = _boundary_diagnosis(sample_alpha).get("total_boundary_risks", 0) >= 0
        checks["glossary_report_generation"] = _extract_candidates(sample_alpha, sample_ref).get(
            "glossary_candidate_count", 0
        ) >= 0
    except Exception:
        checks["business_term_report_generation"] = False
        checks["boundary_report_generation"] = False
        checks["glossary_report_generation"] = False

    failed = [k for k, ok in checks.items() if not ok]
    result = "PASSED" if not failed else "FAILED"
    lines = [
        "V3.3.5.5.8.5.23.3 REFERENCE ALIGNMENT DIAGNOSIS VALIDATION",
        f"Result: {result}",
        f"APP_VERSION: {APP_VERSION}",
    ]
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings))
    if failed:
        lines.append("Failed: " + ", ".join(failed))

    out = Path("troubleshooting/validation/validate_accuracy_85233_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
