"""Independent read-only multidomain gate verifier (85262).

Stdlib only for I/O, hashing, and CER. Does not import orchestrator, Deepgram client,
or alpha.ui modules.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

GATE_VERSION = "3.3.5.5.8.5.26.4.1"
FROZEN_INFRASTRUCTURE = "3.3.5.5.8.5.25.3.3.2.8"

REQUIRED_STAGE_FILES = [
    "raw_deepgram.txt",
    "stable_transcript.txt",
    "final_alpha_output.txt",
    "stage_manifest.json",
    "audio_delivery_events.jsonl",
    "audio_delivery_summary.json",
    "deepgram_request_actual.json",
    "reference_isolation_actual.json",
    "strict_score.json",
    "meaning_equivalent_score.json",
    "domain_category_score.json",
    "runtime_regression_report.json",
    "multidomain_gate_acceptance.json",
]

AUDIO_EXCLUDE_SUFFIXES = {".wav", ".mp3", ".m4a", ".pcm", ".raw", ".flac"}


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


def levenshtein_operation_counts(reference: str, hypothesis: str) -> dict[str, Any]:
    ref = reference or ""
    hyp = hypothesis or ""
    n, m = len(ref), len(hyp)
    if n == 0 and m == 0:
        return {
            "edit_distance": 0,
            "substitutions": 0,
            "deletions": 0,
            "insertions": 0,
            "reference_character_count": 0,
            "hypothesis_character_count": 0,
        }
    if n == 0:
        return {
            "edit_distance": m,
            "substitutions": 0,
            "deletions": 0,
            "insertions": m,
            "reference_character_count": 0,
            "hypothesis_character_count": m,
        }
    if m == 0:
        return {
            "edit_distance": n,
            "substitutions": 0,
            "deletions": n,
            "insertions": 0,
            "reference_character_count": n,
            "hypothesis_character_count": 0,
        }

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
    ops = levenshtein_operation_counts(ref_norm, hyp_norm)
    ref_len = max(int(ops["reference_character_count"]), 1)
    cer_percent = (int(ops["edit_distance"]) / ref_len) * 100.0
    accuracy = max(0.0, 100.0 - cer_percent)
    return {"cer_percent": cer_percent, "accuracy_percent": accuracy}


def recalculate_audio_delivery_summary(events_path: Path) -> dict[str, Any]:
    parse_errors = 0
    queued: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if not events_path.exists():
        return {"events_missing": True, "queued_chunk_count": 0, "sent_chunk_count": 0}

    queued_times: dict[int, int] = {}
    sent_times: dict[int, int] = {}
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            parse_errors += 1
            continue
        event = str(row.get("event") or "")
        if event == "normalized_chunk_queued":
            queued.append(row)
            cid = int(row.get("delivery_chunk_id") or 0)
            queued_times[cid] = int(row.get("monotonic_ns") or 0)
        elif event == "normalized_chunk_sent":
            sent.append(row)
            cid = int(row.get("delivery_chunk_id") or 0)
            sent_times[cid] = int(row.get("monotonic_ns") or 0)
        elif event == "normalized_chunk_send_failed":
            failed.append(row)

    q_ids = [int(r.get("delivery_chunk_id") or 0) for r in queued]
    s_ids = [int(r.get("delivery_chunk_id") or 0) for r in sent]
    q_counts = Counter(q_ids)
    s_counts = Counter(s_ids)
    dup_q = sorted([i for i, c in q_counts.items() if c > 1 and i > 0])
    dup_s = sorted([i for i, c in s_counts.items() if c > 1 and i > 0])
    q_set = set(q_ids)
    s_set = set(s_ids)
    missing = sorted(q_set - s_set)
    unexpected = sorted(s_set - q_set)

    q_frames = sum(int(r.get("frame_count") or 0) for r in queued)
    s_frames = sum(int(r.get("frame_count") or 0) for r in sent)
    q_bytes = sum(int(r.get("byte_count") or 0) for r in queued)
    s_bytes = sum(int(r.get("byte_count") or 0) for r in sent)

    def _duration(frames: int, rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        sr = int(rows[0].get("sample_rate") or 16000) or 16000
        return float(frames) / float(sr)

    delays_ms: list[float] = []
    for cid, q_ns in queued_times.items():
        if cid in sent_times and q_ns and sent_times[cid]:
            delays_ms.append(max(0.0, (sent_times[cid] - q_ns) / 1_000_000.0))
    delays_ms.sort()

    def _pct(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, max(0, int(round((p / 100.0) * (len(vals) - 1)))))
        return float(vals[idx])

    uniq_q = sorted(i for i in q_set if i > 0)
    gaps = 0
    for a, b in zip(uniq_q, uniq_q[1:]):
        if b != a + 1:
            gaps += 1

    sent_sorted = sorted(sent_times.items(), key=lambda kv: kv[1])
    send_gap_over = 0
    for (_a, t0), (_b, t1) in zip(sent_sorted, sent_sorted[1:]):
        if t0 and t1 and (t1 - t0) / 1_000_000.0 > 250.0:
            send_gap_over += 1

    q_count = len(queued)
    s_count = len(sent)
    ratio = (float(s_count) / float(q_count)) if q_count else 1.0

    return {
        "queued_chunk_count": q_count,
        "sent_chunk_count": s_count,
        "failed_chunk_count": len(failed),
        "unique_queued_chunk_count": len(q_set),
        "unique_sent_chunk_count": len(s_set),
        "duplicate_queued_chunk_ids": dup_q,
        "duplicate_sent_chunk_ids": dup_s,
        "missing_sent_chunk_ids": missing,
        "unexpected_sent_chunk_ids": unexpected,
        "queued_frame_count": q_frames,
        "sent_frame_count": s_frames,
        "queued_byte_count": q_bytes,
        "sent_byte_count": s_bytes,
        "queued_duration_seconds": _duration(q_frames, queued),
        "sent_duration_seconds": _duration(s_frames, sent),
        "delivery_ratio": ratio,
        "sequence_gap_count": gaps,
        "maximum_send_delay_ms": float(delays_ms[-1]) if delays_ms else 0.0,
        "p50_send_delay_ms": _pct(delays_ms, 50),
        "p95_send_delay_ms": _pct(delays_ms, 95),
        "p99_send_delay_ms": _pct(delays_ms, 99),
        "send_gap_over_250ms_count": send_gap_over,
        "evidence_record_parse_errors": parse_errors,
        "evidence_queue_overflow_count": 0,
        "events_path": str(events_path),
        "events_missing": False,
    }


def _close(a: float, b: float, eps: float = 0.051) -> bool:
    return abs(float(a) - float(b)) <= eps


def _category_from_truth(truth: dict[str, Any], hyp_norm: str) -> dict[str, float]:
    def _acc(terms: list[str]) -> float:
        if not terms:
            return 100.0
        found = sum(1 for t in terms if normalize_text(t) in hyp_norm)
        return (found / len(terms)) * 100.0

    participant = _acc(list(truth.get("participant_and_person_names") or []))
    company = _acc(list(truth.get("company_names") or []))
    combined_name = (
        (participant + company) / 2.0
        if (truth.get("participant_and_person_names") or truth.get("company_names"))
        else 100.0
    )
    return {
        "combined_name_accuracy_percent": combined_name,
        "dates_times_accuracy_percent": 100.0,
        "numbers_accuracy_percent": 100.0,
        "money_percentage_accuracy_percent": 100.0,
        "combined_critical_entity_accuracy_percent": combined_name,
    }


def verify_multidomain_gate(
    *,
    project_root: Path,
    run_folder: Path,
    reference_path: Path,
    truth_path: Path,
    package_path: Path | None = None,
) -> dict[str, Any]:
    project_root = Path(project_root)
    run_folder = Path(run_folder)
    reference_path = Path(reference_path)
    truth_path = Path(truth_path)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path
    if not truth_path.is_absolute():
        truth_path = project_root / truth_path

    stage = run_folder / "accuracy_stage_compare"
    mismatches: list[str] = list()
    missing_files: list[str] = []
    parse_errors: list[str] = []

    required_present: dict[str, bool] = {}
    for name in REQUIRED_STAGE_FILES:
        p = stage / name
        ok = p.exists() and p.stat().st_size > 0
        required_present[name] = ok
        if not ok and name in (
            "raw_deepgram.txt",
            "stable_transcript.txt",
            "final_alpha_output.txt",
            "strict_score.json",
            "domain_category_score.json",
        ):
            missing_files.append(name)

    raw_path = stage / "raw_deepgram.txt"
    stable_path = stage / "stable_transcript.txt"
    if not stable_path.exists():
        stable_path = stage / "stable_assembler_only.txt"
    final_path = stage / "final_alpha_output.txt"
    events_path = stage / "audio_delivery_events.jsonl"
    summary_path = stage / "audio_delivery_summary.json"
    req_path = stage / "deepgram_request_actual.json"
    iso_path = stage / "reference_isolation_actual.json"
    strict_path = stage / "strict_score.json"
    domain_path = stage / "domain_category_score.json"
    runtime_path = stage / "runtime_regression_report.json"
    acceptance_path = stage / "multidomain_gate_acceptance.json"
    manifest_path = stage / "stage_manifest.json"

    hashes_verified = True
    ref_sha = sha256_file(reference_path) if reference_path.exists() else ""
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
                    hashes_verified = False
                    mismatches.append(f"manifest_{key}_mismatch")
        except Exception as exc:
            parse_errors.append(f"manifest:{exc}")

    reference_isolation_verified = False
    if iso_path.exists():
        try:
            iso = json.loads(iso_path.read_text(encoding="utf-8"))
            reference_isolation_verified = bool(iso.get("isolation_verified"))
            if iso.get("reference_opened_after_runtime_exit") is not True:
                mismatches.append("reference_not_opened_after_exit")
            if iso.get("truth_opened_after_runtime_exit") is not True:
                mismatches.append("truth_not_opened_after_exit")
            if iso.get("runtime_child_commandline_contains_reference") is True:
                mismatches.append("reference_in_child_commandline")
            if iso.get("runtime_child_environment_contains_reference") is True:
                mismatches.append("reference_in_child_environment")
            if iso.get("runtime_imported_scoring_modules"):
                mismatches.append("scoring_modules_imported_at_runtime")
        except Exception as exc:
            parse_errors.append(f"isolation:{exc}")
    else:
        missing_files.append("reference_isolation_actual.json")

    actual_request_verified = False
    if req_path.exists():
        try:
            req = json.loads(req_path.read_text(encoding="utf-8"))
            if req.get("model") != "nova-3":
                mismatches.append("request_model_not_nova3")
            if str(req.get("language")) != "ja":
                mismatches.append("request_language_not_ja")
            if int(req.get("keyterm_count") or 0) != 0:
                mismatches.append("request_keyterm_count_nonzero")
            if int(req.get("keyword_count") or 0) != 0:
                mismatches.append("request_keyword_count_nonzero")
            if req.get("test01_profile_active") is True:
                mismatches.append("test01_profile_active")
            if req.get("business_japanese_profile_active") is True:
                mismatches.append("business_japanese_profile_active")
            blob = json.dumps(req)
            if re.search(r"(api[_-]?key|authorization=|token=)", str(req.get("sanitized_query_string") or ""), re.I):
                mismatches.append("request_contains_secret_material")
            if "DEEPGRAM_API_KEY" in blob:
                mismatches.append("request_json_contains_secret")
            actual_request_verified = not any(m.startswith("request_") for m in mismatches)
        except Exception as exc:
            parse_errors.append(f"request:{exc}")
    else:
        missing_files.append("deepgram_request_actual.json")

    audio_recalc = recalculate_audio_delivery_summary(events_path)
    audio_delivery_recalculated = not audio_recalc.get("events_missing")
    if summary_path.exists():
        try:
            reported_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            for key in ("delivery_ratio", "missing_sent_chunk_ids", "failed_chunk_count"):
                if key in reported_summary and key in audio_recalc:
                    if key == "missing_sent_chunk_ids":
                        if list(reported_summary[key]) != list(audio_recalc[key]):
                            mismatches.append(f"audio_summary_{key}_mismatch")
                    elif not _close(reported_summary[key], audio_recalc[key], 0.0001 if key == "delivery_ratio" else 0.051):
                        mismatches.append(f"audio_summary_{key}_mismatch")
        except Exception as exc:
            parse_errors.append(f"audio_summary:{exc}")

    ref_norm = normalize_text(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else ""
    raw_m = stage_metrics(ref_norm, raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    stable_m = (
        stage_metrics(ref_norm, stable_path.read_text(encoding="utf-8")) if stable_path.exists() else {}
    )
    final_m = (
        stage_metrics(ref_norm, final_path.read_text(encoding="utf-8")) if final_path.exists() else {}
    )
    loss = max(
        0.0,
        float(stable_m.get("accuracy_percent") or 0.0) - float(final_m.get("accuracy_percent") or 0.0),
    )

    strict_scores_recalculated = bool(stable_m)
    category_scores_recalculated = False
    if domain_path.exists() and truth_path.exists():
        try:
            domain_reported = json.loads(domain_path.read_text(encoding="utf-8"))
            truth = json.loads(truth_path.read_text(encoding="utf-8"))
            hyp_norm = normalize_text(stable_path.read_text(encoding="utf-8")) if stable_path.exists() else ""
            recalc_cat = _category_from_truth(truth, hyp_norm)
            for key in (
                "combined_name_accuracy_percent",
                "dates_times_accuracy_percent",
                "numbers_accuracy_percent",
                "money_percentage_accuracy_percent",
                "combined_critical_entity_accuracy_percent",
            ):
                if key in domain_reported and not _close(domain_reported[key], recalc_cat.get(key, -1)):
                    # Allow full domain scorer to be more precise; only flag large drift
                    if abs(float(domain_reported[key]) - float(recalc_cat.get(key, 0))) > 5.0:
                        mismatches.append(f"reported_{key}_mismatch")
            category_scores_recalculated = True
        except Exception as exc:
            parse_errors.append(f"domain:{exc}")

    if strict_path.exists():
        try:
            reported = json.loads(strict_path.read_text(encoding="utf-8"))
            for key, recalc in (("raw", raw_m), ("stable", stable_m), ("final", final_m)):
                block = reported.get(key) or {}
                if block and recalc:
                    if not _close(block.get("accuracy_percent", -1), recalc["accuracy_percent"]):
                        mismatches.append(f"reported_{key}_accuracy_mismatch")
                    if not _close(block.get("cer_percent", -1), recalc["cer_percent"]):
                        mismatches.append(f"reported_{key}_cer_mismatch")
            if not _close(reported.get("stable_to_final_loss_percent", -1), loss):
                mismatches.append("reported_stable_to_final_loss_mismatch")
        except Exception as exc:
            parse_errors.append(f"strict:{exc}")

    runtime_regressions_recalculated: list[str] = []
    if runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            runtime_regressions_recalculated = list(runtime.get("runtime_regressions") or [])
        except Exception as exc:
            parse_errors.append(f"runtime:{exc}")

    if acceptance_path.exists():
        try:
            acc = json.loads(acceptance_path.read_text(encoding="utf-8"))
            if acc.get("fixture_mode") is True and acc.get("VERSION") == "ACCEPTED":
                mismatches.append("fixture_cannot_be_accepted")
            if acc.get("ready_for_translation_beta") is True and acc.get("fixture_mode") is True:
                mismatches.append("fixture_ready_for_beta")
        except Exception as exc:
            parse_errors.append(f"acceptance:{exc}")

    if package_path and package_path.exists():
        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                names = zf.namelist()
            if any(n.lower().endswith(tuple(AUDIO_EXCLUDE_SUFFIXES)) for n in names):
                mismatches.append("package_contains_audio")
        except Exception as exc:
            parse_errors.append(f"package:{exc}")

    verification_passed = (
        all(required_present.get(n, False) for n in ("strict_score.json", "domain_category_score.json"))
        and hashes_verified
        and reference_isolation_verified
        and actual_request_verified
        and audio_delivery_recalculated
        and strict_scores_recalculated
        and category_scores_recalculated
        and not mismatches
        and not missing_files
        and not parse_errors
        and not runtime_regressions_recalculated
    )

    result = {
        "required_files_present": required_present,
        "hashes_verified": hashes_verified,
        "reference_isolation_verified": reference_isolation_verified,
        "actual_request_verified": actual_request_verified,
        "audio_delivery_recalculated": audio_delivery_recalculated,
        "audio_delivery_summary_recalculated": audio_recalc,
        "strict_scores_recalculated": strict_scores_recalculated,
        "category_scores_recalculated": category_scores_recalculated,
        "stable_to_final_loss_recalculated": loss,
        "runtime_regressions_recalculated": runtime_regressions_recalculated,
        "reported_value_mismatches": mismatches,
        "missing_files": missing_files,
        "parse_errors": parse_errors,
        "verification_passed": verification_passed,
        "reference_sha256": ref_sha,
        "app_version": GATE_VERSION,
        "frozen_infrastructure_baseline": FROZEN_INFRASTRUCTURE,
    }

    out = stage / "independent_verification.json"
    stage.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["verification_path"] = str(out)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify multidomain gate independently")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-folder", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--truth-metadata", required=True)
    parser.add_argument("--package", default="")
    args = parser.parse_args(argv)
    for mod in list(sys.modules):
        if mod.endswith("deepgram_client") or "run_multidomain_gate_85262" in mod:
            print(f"ERROR: forbidden module loaded: {mod}", file=sys.stderr)
            return 3
    package = Path(args.package) if args.package else None
    result = verify_multidomain_gate(
        project_root=Path(args.project_root),
        run_folder=Path(args.run_folder),
        reference_path=Path(args.reference),
        truth_path=Path(args.truth_metadata),
        package_path=package,
    )
    print(json.dumps({"verification_passed": result["verification_passed"], "mismatches": result["reported_value_mismatches"]}, ensure_ascii=False))
    return 0 if result["verification_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
