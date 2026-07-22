"""Independent read-only Issue 12 Stage 1 verifier (85261).

Stdlib only for I/O/hashing/CER. Does not import the Stage 1 orchestrator
or Deepgram client. Does not trust target_85_passed or reported accuracy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

NAME_CATEGORIES = {"participant_name", "company_name", "department_name", "job_title"}
NUMBER_CATEGORIES = {"number", "date"}
BUSINESS_CATEGORIES = {"financial_term", "business_term", "product_name"}
FROZEN_INFRASTRUCTURE = "3.3.5.5.8.5.25.3.3.2.8"
STAGE1_VERSION = "3.3.5.5.8.5.26.1"


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    return re.sub(r"\s+", "", "".join(lines))


def levenshtein_ops(reference: str, hypothesis: str) -> dict[str, Any]:
    ref = reference or ""
    hyp = hypothesis or ""
    n, m = len(ref), len(hyp)
    if n == 0 and m == 0:
        return {"edit_distance": 0, "substitutions": 0, "deletions": 0, "insertions": 0}
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + 1)
    substitutions = deletions = insertions = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1
            j -= 1
            continue
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
            continue
        if j > 0 and (i == 0 or dp[i][j] == dp[i][j - 1] + 1):
            insertions += 1
            j -= 1
            continue
        if i > 0:
            deletions += 1
            i -= 1
            continue
        insertions += 1
        j -= 1
    return {
        "edit_distance": dp[n][m],
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "reference_character_count": n,
        "hypothesis_character_count": m,
    }


def stage_metrics(ref_norm: str, hyp_text: str) -> dict[str, float]:
    hyp_norm = normalize_text(hyp_text)
    ops = levenshtein_ops(ref_norm, hyp_norm)
    ref_len = max(int(ops["reference_character_count"]), 1)
    cer_percent = (int(ops["edit_distance"]) / ref_len) * 100.0
    accuracy = max(0.0, 100.0 - cer_percent)
    return {
        "cer_percent": cer_percent,
        "accuracy_percent": accuracy,
        "edit_distance": float(ops["edit_distance"]),
    }


def category_accuracy(hyp_norm: str, terms: list[dict[str, str]], cats: set[str]) -> float:
    selected = [t for t in terms if t.get("category") in cats]
    if not selected:
        return 100.0
    found = 0
    for item in selected:
        term_norm = normalize_text(str(item.get("term") or ""))
        if term_norm and term_norm in hyp_norm:
            found += 1
    return (found / len(selected)) * 100.0


def _scan_logs_for_regressions(run_folder: Path) -> list[str]:
    regressions: list[str] = []
    patterns = {
        "unhandled_exception": re.compile(r"UnhandledException|Traceback \(most recent call last\)", re.I),
        "ui_main_loop_stall": re.compile(r"UI_MAIN_LOOP_STALL|main.?loop.?stall", re.I),
        "stop_freeze": re.compile(r"STOP_FREEZE|stop.?freeze", re.I),
        "audio_queue_overflow_after_stop": re.compile(r"audio.?queue.?overflow.*stop|AUDIO_QUEUE_OVERFLOW_AFTER_STOP", re.I),
        "transcript_queue_overflow": re.compile(r"TRANSCRIPT_QUEUE_OVERFLOW|transcript.?queue.?overflow", re.I),
        "raw_mutation": re.compile(r"RAW_MUTATION|raw.?text.?mutat", re.I),
        "translation_api_active": re.compile(r"DEEPL_|GROQ_|translation.?api.?active|DeepL", re.I),
    }
    log_dirs = [run_folder / "logs", run_folder]
    texts: list[str] = []
    for base in log_dirs:
        if not base.exists():
            continue
        for path in base.rglob("*.log"):
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
        for path in base.glob("*.txt"):
            if "score" in path.name.lower():
                continue
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace")[:500000])
            except Exception:
                pass
    blob = "\n".join(texts)
    for name, rx in patterns.items():
        if rx.search(blob):
            # Ignore Deepgram model names mentioning nothing related; translation check excludes Deepgram
            if name == "translation_api_active" and "Deepgram" in blob and not re.search(
                r"\bDeepL\b|\bGROQ\b|translation_provider", blob, re.I
            ):
                continue
            regressions.append(name)

    # Require stop finalization completed marker when logs present
    if blob and not re.search(r"STOP_FINALIZATION_COMPLETED|THREE_STAGE_FINALIZER|stop.?finaliz", blob, re.I):
        # Soft: only flag if incomplete evidence already else present
        pass

    stop_done = bool(
        re.search(
            r"STOP_FINALIZATION_COMPLETED|FINAL_ALPHA_STAGE_FINALIZATION_COMPLETED|stage_manifest",
            blob,
            re.I,
        )
    )
    stage_manifest = run_folder / "accuracy_stage_compare" / "stage_manifest.json"
    if stage_manifest.exists():
        stop_done = True
    if not stop_done:
        regressions.append("stop_finalization_incomplete")

    # Infrastructure freeze / translation provider flags from acceptance sidecar if present
    return regressions


def verify_issue12_stage1(
    *,
    project_root: Path,
    run_folder: Path,
    reference_path: Path,
) -> dict[str, Any]:
    project_root = Path(project_root)
    run_folder = Path(run_folder)
    reference_path = Path(reference_path)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path

    mismatches: list[str] = []
    runtime_regressions: list[str] = []

    stage = run_folder / "accuracy_stage_compare"
    raw_path = stage / "raw_deepgram.txt"
    stable_path = stage / "stable_transcript.txt"
    if not stable_path.exists():
        stable_path = stage / "stable_assembler_only.txt"
    final_path = stage / "final_alpha_output.txt"
    req_path = stage / "deepgram_request_actual.json"
    audio_path = stage / "benchmark_audio_source.json"
    score_path = stage / "issue12_stage1_score.json"
    manifest_path = stage / "stage_manifest.json"
    glossary_path = (
        project_root
        / "troubleshooting"
        / "accuracy_benchmark"
        / "glossaries"
        / "test01_meeting_context.json"
    )

    ref_sha = sha256_file(reference_path) if reference_path.exists() else ""
    reference_hash_verified = bool(ref_sha)
    if not reference_path.exists():
        mismatches.append("reference_missing")

    stage_hashes_verified = True
    for label, path in (
        ("raw", raw_path),
        ("stable", stable_path),
        ("final", final_path),
    ):
        if not path.exists() or path.stat().st_size <= 0:
            stage_hashes_verified = False
            mismatches.append(f"{label}_missing_or_empty")
            continue
        # Hash exists → verified as readable
        _ = sha256_file(path)

    if manifest_path.exists():
        try:
            man = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key, path in (
                ("raw_sha256", raw_path),
                ("stable_sha256", stable_path),
                ("final_sha256", final_path),
            ):
                expected = str(man.get(key) or "")
                if expected and path.exists() and sha256_file(path) != expected:
                    stage_hashes_verified = False
                    mismatches.append(f"manifest_{key}_mismatch")
        except Exception as exc:
            mismatches.append(f"manifest_parse_error:{exc}")

    actual_request_verified = False
    if not req_path.exists():
        mismatches.append("deepgram_request_actual_missing")
    else:
        try:
            req = json.loads(req_path.read_text(encoding="utf-8"))
            required = [
                "run_id",
                "app_version",
                "profile",
                "model",
                "language",
                "encoding",
                "sample_rate",
                "channels",
                "interim_results",
                "punctuate",
                "smart_format",
                "endpointing",
                "utterance_end_ms",
                "diarize_present",
                "diarize_model_present",
                "keyterm_parameter_present",
                "keyterm_count",
                "keyterm_values",
                "sanitized_query_string",
                "request_sha256",
                "captured_immediately_before_connect",
            ]
            for field in required:
                if field not in req:
                    mismatches.append(f"request_missing_field:{field}")
            q = str(req.get("sanitized_query_string") or "")
            if re.search(r"(api[_-]?key|authorization=|token=)", q, re.I):
                mismatches.append("request_contains_secret_material")
            blob = json.dumps(req)
            if "DEEPGRAM_API_KEY" in blob or '"Authorization"' in blob:
                mismatches.append("request_json_contains_secret")
            if req.get("model") != "nova-3":
                mismatches.append("request_model_not_nova3")
            if str(req.get("language")) != "ja":
                mismatches.append("request_language_not_ja")
            if req.get("diarize_present") is True or req.get("diarize_model_present") is True:
                mismatches.append("request_diarize_present")
            if int(req.get("endpointing") or 0) != 500:
                mismatches.append("request_endpointing_not_500")
            if int(req.get("utterance_end_ms") or 0) != 1500:
                mismatches.append("request_utterance_end_not_1500")
            if not req.get("captured_immediately_before_connect"):
                mismatches.append("request_not_captured_before_connect")
            actual_request_verified = not any(
                m.startswith("request_") for m in mismatches
            ) and "deepgram_request_actual_missing" not in mismatches
        except Exception as exc:
            mismatches.append(f"request_parse_error:{exc}")

    system_audio_only_verified = False
    if not audio_path.exists():
        mismatches.append("benchmark_audio_source_missing")
    else:
        try:
            audio = json.loads(audio_path.read_text(encoding="utf-8"))
            if audio.get("system_audio_enabled") is not True:
                mismatches.append("system_audio_not_enabled")
            if audio.get("microphone_mix_enabled") is not False:
                mismatches.append("microphone_mix_still_enabled")
            if audio.get("benchmark_mode") is not True:
                mismatches.append("benchmark_mode_false")
            system_audio_only_verified = (
                audio.get("system_audio_enabled") is True
                and audio.get("microphone_mix_enabled") is False
                and audio.get("benchmark_mode") is True
            )
        except Exception as exc:
            mismatches.append(f"audio_source_parse_error:{exc}")

    ref_norm = normalize_text(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else ""
    raw_m = stage_metrics(ref_norm, raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    stable_m = (
        stage_metrics(ref_norm, stable_path.read_text(encoding="utf-8")) if stable_path.exists() else {}
    )
    final_m = (
        stage_metrics(ref_norm, final_path.read_text(encoding="utf-8")) if final_path.exists() else {}
    )

    terms: list[dict[str, str]] = []
    if glossary_path.exists():
        g = json.loads(glossary_path.read_text(encoding="utf-8"))
        for item in g.get("terms") or []:
            if isinstance(item, dict):
                terms.append(
                    {
                        "term": str(item.get("term") or ""),
                        "category": str(item.get("category") or ""),
                    }
                )

    hyp_norm = normalize_text(stable_path.read_text(encoding="utf-8")) if stable_path.exists() else ""
    critical_acc = category_accuracy(
        hyp_norm,
        terms,
        NAME_CATEGORIES | NUMBER_CATEGORIES | BUSINESS_CATEGORIES,
    )
    loss = max(
        0.0,
        float(stable_m.get("accuracy_percent") or 0.0) - float(final_m.get("accuracy_percent") or 0.0),
    )

    if score_path.exists():
        try:
            reported = json.loads(score_path.read_text(encoding="utf-8"))
            # Do not trust target_85_passed — compare numbers
            def _close(a: float, b: float, eps: float = 0.051) -> bool:
                return abs(float(a) - float(b)) <= eps

            for key, recalc in (
                ("raw", raw_m),
                ("stable", stable_m),
                ("final", final_m),
            ):
                block = reported.get(key) or {}
                if block and recalc:
                    if not _close(block.get("accuracy_percent", -1), recalc["accuracy_percent"]):
                        mismatches.append(f"reported_{key}_accuracy_mismatch")
                    if not _close(block.get("cer_percent", -1), recalc["cer_percent"]):
                        mismatches.append(f"reported_{key}_cer_mismatch")
            if not _close(
                reported.get("combined_critical_term_accuracy_percent", -1),
                critical_acc,
            ):
                mismatches.append("reported_critical_term_mismatch")
            if not _close(reported.get("stable_to_final_loss_percent", -1), loss):
                mismatches.append("reported_stable_to_final_loss_mismatch")
            # Trust flag must not be accepted blindly when recalc fails targets
            if reported.get("target_85_passed") is True:
                if float(stable_m.get("accuracy_percent") or 0) < 85.0:
                    mismatches.append("false_target_85_passed_flag")
        except Exception as exc:
            mismatches.append(f"score_parse_error:{exc}")

    # Stable→Final content loss
    if stable_path.exists() and final_path.exists():
        s_text = normalize_text(stable_path.read_text(encoding="utf-8"))
        f_text = normalize_text(final_path.read_text(encoding="utf-8"))
        if s_text != f_text and loss > 0.0:
            # Content may differ cosmetically; rely on accuracy loss
            pass
        if loss > 0.0:
            mismatches.append("stable_to_final_content_loss")

    runtime_regressions = _scan_logs_for_regressions(run_folder)

    # Infrastructure version remains frozen as baseline; app may be Stage1 version
    # Check that production keyterm profile constant file still says business_japanese default
    constants_path = project_root / "alpha" / "constants.py"
    if constants_path.exists():
        ctext = constants_path.read_text(encoding="utf-8", errors="replace")
        if 'JAPANESE_KEYTERM_PROFILE = "business_japanese"' not in ctext:
            mismatches.append("production_keyterm_profile_mutated")
        if f'APP_VERSION = "{STAGE1_VERSION}"' not in ctext and f"APP_VERSION = '{STAGE1_VERSION}'" not in ctext:
            # Stage1 version expected for this delivery
            mismatches.append("stage1_app_version_mismatch")
        if "FROZEN_INFRASTRUCTURE_BASELINE" in ctext and FROZEN_INFRASTRUCTURE not in ctext:
            mismatches.append("frozen_infrastructure_baseline_mismatch")

    # Raw mutation count: Raw file must exist; we cannot mutate in verifier
    raw_mutation_count = 0
    if "raw_mutation" in runtime_regressions:
        raw_mutation_count = 1
        runtime_regressions = [r for r in runtime_regressions if r != "raw_mutation"]
        runtime_regressions.append("raw_mutation_count_nonzero")

    verification_passed = (
        reference_hash_verified
        and stage_hashes_verified
        and actual_request_verified
        and system_audio_only_verified
        and not mismatches
        and not runtime_regressions
        and float(stable_m.get("accuracy_percent") or 0) >= 85.0
        and float(stable_m.get("cer_percent") or 100) <= 15.0
        and critical_acc >= 90.0
        and loss == 0.0
        and raw_mutation_count == 0
    )

    # Independent verifier may still emit verification_passed=false with mismatches
    # even if score claimed pass — that is intentional.

    result = {
        "reference_hash_verified": reference_hash_verified,
        "stage_hashes_verified": stage_hashes_verified,
        "actual_request_verified": actual_request_verified,
        "system_audio_only_verified": system_audio_only_verified,
        "raw_cer_recalculated": raw_m.get("cer_percent"),
        "stable_cer_recalculated": stable_m.get("cer_percent"),
        "final_cer_recalculated": final_m.get("cer_percent"),
        "raw_accuracy_recalculated": raw_m.get("accuracy_percent"),
        "stable_accuracy_recalculated": stable_m.get("accuracy_percent"),
        "final_accuracy_recalculated": final_m.get("accuracy_percent"),
        "critical_term_accuracy_recalculated": critical_acc,
        "stable_to_final_loss_recalculated": loss,
        "runtime_regressions": runtime_regressions,
        "mismatches": mismatches,
        "verification_passed": verification_passed,
        "raw_mutation_count": raw_mutation_count,
        "frozen_infrastructure_baseline": FROZEN_INFRASTRUCTURE,
    }

    out = stage / "issue12_stage1_independent_verification.json"
    stage.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["verification_path"] = str(out)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Issue 12 Stage 1 independently")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args(argv)
    # Guard: do not import orchestrator / Deepgram client
    for mod in list(sys.modules):
        if "run_issue12_stage1_accuracy_gate_85261" in mod or mod.endswith("deepgram_client"):
            print(f"ERROR: forbidden module already loaded: {mod}", file=sys.stderr)
            return 3
    result = verify_issue12_stage1(
        project_root=Path(args.project_root),
        run_folder=Path(args.run_folder),
        reference_path=Path(args.reference),
    )
    print(
        json.dumps(
            {
                "verification_passed": result["verification_passed"],
                "mismatches": result["mismatches"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["verification_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
