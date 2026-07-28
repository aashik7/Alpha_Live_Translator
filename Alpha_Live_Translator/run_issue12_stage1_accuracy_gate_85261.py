"""Issue 12 Stage 1 accuracy gate orchestrator (85261).

One controlled live test only. Fail-closed acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGE1_VERSION = "3.3.5.5.8.5.26.1"
FROZEN_INFRASTRUCTURE = "3.3.5.5.8.5.25.3.3.2.8"
ACCURACY_PROFILE = "target_85_meeting_context"
EXPECTED_REFERENCE_SHA256 = "09634a0da9ff86ce4825fb8326c3bca99e64be955c971d7e2db7f7b7823e5b8b"
DEFAULT_REFERENCE = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_frozen_infrastructure(project_root: Path) -> dict[str, Any]:
    constants = (project_root / "alpha" / "constants.py").read_text(encoding="utf-8")
    issues: list[str] = []
    if FROZEN_INFRASTRUCTURE not in constants:
        issues.append("frozen_infrastructure_marker_missing")
    if 'JAPANESE_KEYTERM_PROFILE = "business_japanese"' not in constants:
        issues.append("business_japanese_default_missing")
    if 'JAPANESE_STT_PROFILE = "no_diarize"' not in constants:
        issues.append("stt_profile_changed")
    readiness = (
        project_root
        / "troubleshooting"
        / "issue12_readiness"
        / f"v{FROZEN_INFRASTRUCTURE}"
    )
    if not readiness.exists():
        issues.append("frozen_readiness_delivery_missing")
    state = project_root / "troubleshooting" / "PROJECT_STATE.json"
    ready = False
    if state.exists():
        try:
            payload = json.loads(state.read_text(encoding="utf-8"))
            ready = bool(payload.get("ready_for_issue12") or payload.get("ready_for_issue_12"))
        except Exception:
            ready = False
    return {
        "ok": not issues,
        "issues": issues,
        "ready_for_issue12": ready,
        "frozen_infrastructure_baseline": FROZEN_INFRASTRUCTURE,
    }


def validate_reference(reference_path: Path) -> dict[str, Any]:
    if not reference_path.exists():
        raise FileNotFoundError(f"reference_missing:{reference_path}")
    digest = sha256_file(reference_path)
    if digest != EXPECTED_REFERENCE_SHA256:
        raise ValueError(f"reference_sha256_mismatch:{digest}")
    return {"path": str(reference_path), "sha256": digest, "ok": True}


def build_acceptance(*, score: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    stable = score.get("stable") or {}
    stable_acc = float(stable.get("accuracy_percent") or 0.0)
    stable_cer = float(stable.get("cer_percent") or 100.0)
    combined = float(score.get("combined_critical_term_accuracy_percent") or 0.0)
    loss = float(score.get("stable_to_final_loss_percent") or 0.0)
    trusted = bool(score.get("trusted_score"))
    verdict = str(score.get("reference_quality_verdict") or "")
    mismatches = list(verification.get("mismatches") or [])
    regressions = list(verification.get("runtime_regressions") or [])

    failures: list[str] = list(score.get("failures") or [])
    if not trusted or verdict != "valid_for_cer":
        failures.append("reference_not_trusted")
    if stable_acc < 85.00:
        failures.append("stable_accuracy_below_85")
    if stable_cer > 15.00:
        failures.append("stable_cer_above_15")
    if combined < 90.00:
        failures.append("combined_critical_term_below_90")
    if loss > 0.0:
        failures.append("stable_to_final_loss_nonzero")
    if mismatches:
        failures.append("independent_verification_mismatches")
    if regressions:
        failures.append("runtime_regressions_present")
    if not verification.get("verification_passed"):
        failures.append("independent_verification_failed")

    # Deduplicate while preserving order
    uniq: list[str] = []
    seen: set[str] = set()
    for item in failures:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    failures = uniq

    passed = (
        trusted
        and verdict == "valid_for_cer"
        and stable_acc >= 85.00
        and stable_cer <= 15.00
        and combined >= 90.00
        and loss == 0.0
        and not mismatches
        and not regressions
        and bool(verification.get("verification_passed"))
    )

    return {
        "VERSION": "ACCEPTED" if passed else "NOT_ACCEPTED",
        "STATUS": "PASSED" if passed else "TARGET_NOT_REACHED",
        "issue12_stage": 1,
        "profile_status": "eligible_for_stage2_freeze" if passed else "experimental_only",
        "ready_for_step2": bool(passed),
        "raw_accuracy_percent": float((score.get("raw") or {}).get("accuracy_percent") or 0.0),
        "stable_accuracy_percent": stable_acc,
        "final_accuracy_percent": float((score.get("final") or {}).get("accuracy_percent") or 0.0),
        "trusted_stable_cer_percent": stable_cer,
        "combined_critical_term_accuracy_percent": combined,
        "stable_to_final_loss_percent": loss,
        "runtime_regressions": len(regressions),
        "runtime_regression_list": regressions,
        "gap_to_85_percent": max(0.0, 85.00 - stable_acc),
        "highest_impact_edit_categories": score.get("highest_impact_edit_categories") or [],
        "failures": failures,
        "profile_name": ACCURACY_PROFILE,
        "production_profile_unchanged": True,
        "app_version": STAGE1_VERSION,
        "frozen_infrastructure_baseline": FROZEN_INFRASTRUCTURE,
        "created_at": utc_now_iso(),
    }


def write_stage1_manifest(
    *,
    run_folder: Path,
    reference_path: Path,
    audio_source: str,
    started_at: str,
    stopped_at: str,
) -> Path:
    stage = run_folder / "accuracy_stage_compare"
    stage.mkdir(parents=True, exist_ok=True)
    raw = stage / "raw_deepgram.txt"
    stable = stage / "stable_transcript.txt"
    if not stable.exists():
        asm = stage / "stable_assembler_only.txt"
        if asm.exists():
            stable.write_text(asm.read_text(encoding="utf-8"), encoding="utf-8")
    final = stage / "final_alpha_output.txt"
    dg = stage / "deepgram_request_actual.json"

    def _info(path: Path) -> tuple[str, str]:
        if not path.exists():
            return "", ""
        rel = str(path.relative_to(run_folder)).replace("\\", "/")
        return rel, sha256_file(path)

    raw_p, raw_h = _info(raw)
    st_p, st_h = _info(stable)
    fi_p, fi_h = _info(final)
    dg_p, dg_h = _info(dg)

    # Preserve existing manifest extras if present
    existing: dict[str, Any] = {}
    path = stage / "stage_manifest.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    payload = {
        **existing,
        "run_id": run_folder.name,
        "reference_path": str(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "raw_path": raw_p,
        "raw_sha256": raw_h,
        "stable_path": st_p,
        "stable_sha256": st_h,
        "final_path": fi_p,
        "final_sha256": fi_h,
        "deepgram_request_path": dg_p,
        "deepgram_request_sha256": dg_h,
        "audio_source": audio_source,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "completed": bool(raw_h and st_h and fi_h and dg_h),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def create_upload_package(
    *,
    project_root: Path,
    run_folder: Path,
    run_id: str,
    report_text: str,
) -> Path:
    stage = run_folder / "accuracy_stage_compare"
    report_path = stage / "Cursor final report.txt"
    report_path.write_text(report_text, encoding="utf-8")

    zip_path = run_folder / f"ISSUE12_STAGE1_ANALYSIS_PACKAGE_{run_id}.zip"
    members: list[tuple[Path, str]] = []

    required_stage = [
        "raw_deepgram.txt",
        "stable_transcript.txt",
        "final_alpha_output.txt",
        "stage_manifest.json",
        "deepgram_request_actual.json",
        "issue12_stage1_score.json",
        "issue12_stage1_score.txt",
        "issue12_stage1_independent_verification.json",
        "issue12_stage1_acceptance.json",
    ]
    for name in required_stage:
        src = stage / name
        if src.exists():
            members.append((src, f"accuracy_stage_compare/{name}"))

    gloss_dir = project_root / "troubleshooting" / "accuracy_benchmark" / "glossaries"
    for name in ("test01_meeting_context.json", "test01_meeting_context_report.json"):
        src = gloss_dir / name
        if src.exists():
            members.append((src, f"glossaries/{name}"))

    # Relevant logs (versioned) — no wav/audio
    log_candidates = [
        project_root / "troubleshooting" / "logs" / f"v{STAGE1_VERSION}_japanese_accuracy.log",
        project_root / "troubleshooting" / "logs" / f"v{STAGE1_VERSION}_diagnostic_test.log",
        project_root / "troubleshooting" / "logs" / f"v{STAGE1_VERSION}_freeze_guard.log",
        project_root / "troubleshooting" / "logs" / f"v{STAGE1_VERSION}_debug.log",
    ]
    # Also search run folder logs
    for p in (run_folder / "logs").glob("*.log") if (run_folder / "logs").exists() else []:
        log_candidates.append(p)
    for p in project_root.glob(f"**/v{STAGE1_VERSION}_japanese_accuracy.log"):
        log_candidates.append(p)
    for p in project_root.glob(f"**/v{STAGE1_VERSION}_diagnostic_test.log"):
        log_candidates.append(p)
    for p in project_root.glob(f"**/v{STAGE1_VERSION}_freeze_guard.log"):
        log_candidates.append(p)
    for p in project_root.glob(f"**/v{STAGE1_VERSION}_*debug*.log"):
        log_candidates.append(p)

    seen_names: set[str] = set()
    for src in log_candidates:
        if not src.exists() or not src.is_file():
            continue
        if src.suffix.lower() in {".wav", ".raw", ".pcm", ".flac"}:
            continue
        arc = f"logs/{src.name}"
        if arc in seen_names:
            continue
        seen_names.add(arc)
        members.append((src, arc))

    members.append((report_path, "Cursor final report.txt"))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arc in members:
            if src.suffix.lower() in {".wav", ".raw", ".pcm", ".flac"}:
                continue
            zf.write(src, arcname=arc)
    return zip_path


def locate_run_folder(project_root: Path, *, started_after: float, gate_token: str) -> Path:
    runs = project_root / "troubleshooting" / "runs"
    if not runs.exists():
        raise FileNotFoundError("runs_dir_missing")

    pointer = project_root / "troubleshooting" / "issue12_stage1_last_run.json"
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            if data.get("gate_token") == gate_token and data.get("run_folder"):
                cand = Path(data["run_folder"])
                if not cand.is_absolute():
                    cand = project_root / cand
                if cand.exists():
                    return cand
        except Exception:
            pass

    candidates: list[Path] = []
    for child in runs.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime + 5 < started_after:
            continue
        stage = child / "accuracy_stage_compare"
        if (stage / "raw_deepgram.txt").exists() or (stage / "final_alpha_output.txt").exists():
            candidates.append(child)
        elif STAGE1_VERSION in child.name:
            candidates.append(child)
    if not candidates:
        # Fallback: newest run containing version string
        versioned = [p for p in runs.iterdir() if p.is_dir() and STAGE1_VERSION in p.name]
        if versioned:
            candidates = versioned
    if not candidates:
        raise FileNotFoundError("completed_run_not_found")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def print_verdict(acceptance: dict[str, Any], package: Path) -> None:
    print(f"VERSION={acceptance['VERSION']}")
    print(f"STATUS={acceptance['STATUS']}")
    print(f"issue12_stage={acceptance['issue12_stage']}")
    print(f"raw_accuracy_percent={round(float(acceptance['raw_accuracy_percent']), 2):.2f}")
    print(f"stable_accuracy_percent={round(float(acceptance['stable_accuracy_percent']), 2):.2f}")
    print(f"final_accuracy_percent={round(float(acceptance['final_accuracy_percent']), 2):.2f}")
    print(
        f"trusted_stable_cer_percent={round(float(acceptance['trusted_stable_cer_percent']), 2):.2f}"
    )
    print(
        "combined_critical_term_accuracy_percent="
        f"{round(float(acceptance['combined_critical_term_accuracy_percent']), 2):.2f}"
    )
    print(
        f"stable_to_final_loss_percent={round(float(acceptance['stable_to_final_loss_percent']), 2):.2f}"
    )
    print(f"runtime_regressions={acceptance['runtime_regressions']}")
    if acceptance["STATUS"] != "PASSED":
        print(f"gap_to_85_percent={round(float(acceptance['gap_to_85_percent']), 2):.2f}")
        cats = acceptance.get("highest_impact_edit_categories") or []
        if cats:
            print("highest_impact_edit_categories=")
            for item in cats[:5]:
                print(f"  - {item}")
    print(f"ready_for_step2={'true' if acceptance['ready_for_step2'] else 'false'}")
    print("live_tests_completed=1")
    print(f"analysis_package={package}")


def launch_application(project_root: Path, env: dict[str, str]) -> subprocess.Popen[Any]:
    main_py = project_root / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"main_py_missing:{main_py}")
    merged = os.environ.copy()
    merged.update(env)
    # Never enable neutral keyterm benchmark mode for Stage 1
    merged.pop("ALPHA_ACCURACY_BENCHMARK", None)
    return subprocess.Popen(
        [sys.executable, str(main_py)],
        cwd=str(project_root),
        env=merged,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue 12 Stage 1 accuracy gate")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    parser.add_argument("--video-label", required=True)
    parser.add_argument("--expected-duration-seconds", type=float, required=True)
    parser.add_argument(
        "--run-folder",
        default="",
        help="Optional: score an already-completed Stage 1 run (skips live launch).",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from alpha.utils.issue12_stage1_runtime import build_meeting_context_glossary
    from score_issue12_stage1_85261 import score_issue12_stage1
    from verify_issue12_stage1_85261 import verify_issue12_stage1

    print("=== Issue 12 Stage 1 — Controlled Raw Japanese Accuracy Lift ===")
    print(f"app_version={STAGE1_VERSION}")
    print(f"frozen_infrastructure={FROZEN_INFRASTRUCTURE}")
    print(f"video_label={args.video_label}")
    print(f"expected_duration_seconds={args.expected_duration_seconds}")

    # 1. Validate frozen infrastructure
    infra = validate_frozen_infrastructure(project_root)
    if not infra["ok"]:
        print(f"FROZEN_INFRASTRUCTURE_INVALID={infra['issues']}")
        return 2

    # 2–3. Validate reference + SHA
    reference_path = Path(args.reference)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path
    ref_info = validate_reference(reference_path)
    print(f"reference_sha256={ref_info['sha256']}")

    # 4–5. Create + validate glossary
    gloss = build_meeting_context_glossary(
        project_root=project_root, reference_path=reference_path
    )
    if gloss["glossary"]["term_count"] > 40:
        print("GLOSSARY_TERM_COUNT_EXCEEDED")
        return 2
    print(f"glossary_term_count={gloss['glossary']['term_count']}")

    live_tests_completed = 0
    started_at = utc_now_iso()
    gate_token = str(uuid.uuid4())
    started_after = time.time()

    if args.run_folder:
        run_folder = Path(args.run_folder)
        if not run_folder.is_absolute():
            run_folder = project_root / run_folder
        live_tests_completed = 1  # treat as completed single test evidence
        print(f"USING_EXISTING_RUN={run_folder}")
    else:
        # 6. Fresh run ID / gate token
        print(f"gate_token={gate_token}")

        # 7–8. Activate benchmark mode + accuracy profile
        env = {
            "ISSUE12_STAGE1_BENCHMARK": "1",
            "ALPHA_ISSUE12_STAGE1_BENCHMARK": "1",
            "JAPANESE_ACCURACY_PROFILE": ACCURACY_PROFILE,
            "BENCHMARK_AUDIO_SOURCE": "system_audio_only",
            "ISSUE12_STAGE1_GATE_TOKEN": gate_token,
        }
        pointer = project_root / "troubleshooting" / "issue12_stage1_gate_pending.json"
        pointer.write_text(
            json.dumps(
                {
                    "gate_token": gate_token,
                    "profile": ACCURACY_PROFILE,
                    "benchmark_audio_source": "system_audio_only",
                    "started_at": started_at,
                    "video_label": args.video_label,
                    "expected_duration_seconds": args.expected_duration_seconds,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # 9. Launch application (exactly once)
        print("Launching application for ONE live Stage 1 test...")
        print("1) Start listening")
        print(f"2) Play the exact test video: {args.video_label}")
        print("3) Stop after the exact test section")
        print("4) Close the application")
        proc = launch_application(project_root, env)
        live_tests_completed = 1
        try:
            input("Press Enter after the application has been closed...")
        except EOFError:
            proc.wait()
        finally:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=30)
                except Exception:
                    pass

        stopped_at = utc_now_iso()

        # 13. Locate completed run
        run_folder = locate_run_folder(
            project_root, started_after=started_after, gate_token=gate_token
        )
        print(f"located_run_folder={run_folder}")
        (project_root / "troubleshooting" / "issue12_stage1_last_run.json").write_text(
            json.dumps(
                {
                    "gate_token": gate_token,
                    "run_folder": str(run_folder),
                    "stopped_at": stopped_at,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    stopped_at = utc_now_iso()
    stage = run_folder / "accuracy_stage_compare"

    # Ensure stable_transcript alias
    asm = stage / "stable_assembler_only.txt"
    alias = stage / "stable_transcript.txt"
    if asm.exists() and (not alias.exists() or alias.stat().st_size <= 0):
        alias.write_text(asm.read_text(encoding="utf-8"), encoding="utf-8")

    # 14–15. Verify evidence exists
    req = stage / "deepgram_request_actual.json"
    raw = stage / "raw_deepgram.txt"
    stable = stage / "stable_transcript.txt"
    final = stage / "final_alpha_output.txt"
    for label, path in (
        ("deepgram_request_actual", req),
        ("raw_deepgram", raw),
        ("stable_transcript", stable),
        ("final_alpha_output", final),
    ):
        if not path.exists() or path.stat().st_size <= 0:
            print(f"MISSING_EVIDENCE={label}:{path}")
            # Continue to package diagnostics rather than crashing without report

    write_stage1_manifest(
        run_folder=run_folder,
        reference_path=reference_path,
        audio_source="system_audio_only",
        started_at=started_at,
        stopped_at=stopped_at,
    )

    # 16. Trusted scoring
    score = score_issue12_stage1(
        project_root=project_root,
        run_folder=run_folder,
        reference_path=reference_path,
    )

    # 17. Independent verification
    verification = verify_issue12_stage1(
        project_root=project_root,
        run_folder=run_folder,
        reference_path=reference_path,
    )

    # 18. Acceptance (fail-closed)
    acceptance = build_acceptance(score=score, verification=verification)
    acceptance_path = stage / "issue12_stage1_acceptance.json"
    acceptance_path.write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Do not change production profile on failure or success (freeze is Stage 2)
    report_lines = [
        "Issue 12 Stage 1 — Cursor final report",
        f"VERSION={acceptance['VERSION']}",
        f"STATUS={acceptance['STATUS']}",
        f"stable_accuracy_percent={acceptance['stable_accuracy_percent']}",
        f"combined_critical_term_accuracy_percent={acceptance['combined_critical_term_accuracy_percent']}",
        f"stable_to_final_loss_percent={acceptance['stable_to_final_loss_percent']}",
        f"runtime_regressions={acceptance['runtime_regressions']}",
        f"ready_for_step2={acceptance['ready_for_step2']}",
        f"profile_status={acceptance['profile_status']}",
        f"failures={acceptance.get('failures')}",
        f"highest_impact_edit_categories={acceptance.get('highest_impact_edit_categories')}",
        f"live_tests_completed={live_tests_completed}",
        f"production_profile_unchanged={acceptance.get('production_profile_unchanged')}",
    ]
    report_text = "\n".join(report_lines) + "\n"

    # 19. Upload package
    package = create_upload_package(
        project_root=project_root,
        run_folder=run_folder,
        run_id=run_folder.name,
        report_text=report_text,
    )

    # 20. Exact verdict
    print_verdict(acceptance, package)
    print(f"live_tests_completed={live_tests_completed}")
    return 0 if acceptance["STATUS"] == "PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
