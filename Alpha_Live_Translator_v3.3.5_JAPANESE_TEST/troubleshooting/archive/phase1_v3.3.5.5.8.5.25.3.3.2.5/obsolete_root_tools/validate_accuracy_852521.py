"""Validate Suppression-Aware Lineage 8.5.25.2.1."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from alpha.constants import (
    APP_VERSION,
    ATOMIC_ACCURACY_EVIDENCE_FINALIZATION_ENABLED,
    CANONICAL_CORRECTION_LINEAGE_REQUIRED,
    CANONICAL_OUTPUT_ARTIFACT_UNIFICATION_ENABLED,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_LANGUAGE,
    DEEPGRAM_MODEL,
    DEEPGRAM_UTTERANCE_END_MS,
    FINANCIAL_NUMBER_SEMANTIC_VALIDATION_ENABLED,
    FULL_SPAN_FINANCIAL_NUMBER_CORRECTION_ENABLED,
    INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    MALFORMED_NUMERIC_OUTPUT_BLOCK_ENABLED,
    SUPPRESSION_AWARE_LINEAGE_COVERAGE_ENABLED,
)


def _has(path: str, token: str) -> bool:
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def main() -> int:
    checks = {
        "version": APP_VERSION == "3.3.5.5.8.5.25.2.1",
        "deepgram_unchanged": (
            DEEPGRAM_MODEL == "nova-3"
            and DEEPGRAM_LANGUAGE == "ja"
            and DEEPGRAM_ENDPOINTING_MS == 500
            and DEEPGRAM_UTTERANCE_END_MS == 1500
        ),
        "no_diarize": JAPANESE_STT_PROFILE == "no_diarize",
        "business_japanese": JAPANESE_KEYTERM_PROFILE == "business_japanese",
        "852521_flags": all(
            (
                SUPPRESSION_AWARE_LINEAGE_COVERAGE_ENABLED,
                INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED,
                FULL_SPAN_FINANCIAL_NUMBER_CORRECTION_ENABLED,
                FINANCIAL_NUMBER_SEMANTIC_VALIDATION_ENABLED,
                MALFORMED_NUMERIC_OUTPUT_BLOCK_ENABLED,
                CANONICAL_CORRECTION_LINEAGE_REQUIRED,
                CANONICAL_OUTPUT_ARTIFACT_UNIFICATION_ENABLED,
                ATOMIC_ACCURACY_EVIDENCE_FINALIZATION_ENABLED,
            )
        ),
        "financial_safety_module": Path("alpha/transcription/financial_number_safety.py").exists(),
        "canonical_export_writer": Path("alpha/utils/canonical_export_writer.py").exists(),
        "atomic_evidence_finalize": Path("alpha/utils/atomic_evidence_finalize.py").exists(),
        "suppression_classify": _has("alpha/transcription/transcript_lineage.py", "classify_source_commits"),
        "lineage_v2": _has("alpha/transcription/transcript_lineage.py", "lineage_v2_suppression_aware"),
        "stable_commit_id": _has("alpha/utils/transcript_evidence.py", "stable_commit_id"),
        "stop_tail_stable_id": _has("alpha/utils/accuracy_evidence_export.py", "stable_commit_id"),
        "full_span_correction": _has("alpha/transcription/corporate_ir_stable_corrector.py", "apply_safe_financial_number_correction"),
        "enrich_decisions": _has("alpha/transcription/transcript_lineage.py", "enrich_correction_decisions"),
        "canonical_payload": _has("alpha/utils/canonical_export_writer.py", "CANONICAL_EXPORT_PAYLOAD_CREATED"),
        "atomic_finalize": _has("alpha/utils/atomic_evidence_finalize.py", "ATOMIC_EVIDENCE_FINALIZATION_STARTED"),
    }
    for mod in (
        "main.py",
        "alpha/constants.py",
        "alpha/transcription/transcript_lineage.py",
        "alpha/transcription/financial_number_safety.py",
        "alpha/transcription/corporate_ir_stable_corrector.py",
        "alpha/utils/canonical_export_writer.py",
        "alpha/utils/atomic_evidence_finalize.py",
        "alpha/utils/boundary_evidence_finalize.py",
        "validate_lineage_regression_852521.py",
        "validate_financial_number_safety_852521.py",
        "validate_output_artifact_consistency_852521.py",
        "validate_atomic_evidence_sync_852521.py",
        "validate_accuracy_852521.py",
    ):
        try:
            import py_compile

            py_compile.compile(mod, doraise=True)
            checks[f"compile_{mod.replace('/', '_')}"] = True
        except Exception:
            checks[f"compile_{mod.replace('/', '_')}"] = False

    for script in (
        "validate_lineage_regression_852521.py",
        "validate_financial_number_safety_852521.py",
        "validate_output_artifact_consistency_852521.py",
        "validate_atomic_evidence_sync_852521.py",
    ):
        proc = subprocess.run([sys.executable, script], capture_output=True, text=True)
        checks[f"run_{script}"] = proc.returncode == 0

    failed = [k for k, ok in checks.items() if not ok]
    lines = [
        "V3.3.5.5.8.5.25.2.1 SUPPRESSION-AWARE LINEAGE VALIDATION",
        f"Result: {'PASSED' if not failed else 'FAILED'}",
        f"APP_VERSION: {APP_VERSION}",
        "Warnings: no_new_live_test_after_25.2.1, trusted_score_may_remain_false_for_invalid_reference",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_accuracy_852521_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
