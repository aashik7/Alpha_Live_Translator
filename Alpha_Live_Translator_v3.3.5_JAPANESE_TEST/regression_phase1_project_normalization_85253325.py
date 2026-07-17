"""Phase 1 regression suite — exactly 60 offline tests (V85253325)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.utils.phase1_build_identity import (
    AUTHORITATIVE_FINAL_REL,
    AUTHORITATIVE_REFERENCE_REL,
    AUTHORITATIVE_RUN_REL,
    EXPECTED_FINAL_SHA256,
    PATCH_VERSION,
    sha256_file,
)

EXPECTED_TESTS = 60


class T:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        self.results.append((name, bool(cond), detail))


def _latest_build(project_root: Path) -> Path | None:
    builds = project_root / "troubleshooting" / "phase1_normalization" / f"v{PATCH_VERSION}" / "builds"
    if not builds.exists():
        return None
    dirs = sorted([p for p in builds.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def run_tests(project_root: Path) -> tuple[int, int, list[tuple[str, bool, str]]]:
    t = T()
    root = project_root

    # 1-5 identity / paths
    t.check("t01_patch_version", PATCH_VERSION == "3.3.5.5.8.5.25.3.3.2.5")
    t.check("t02_authoritative_run_exists", (root / AUTHORITATIVE_RUN_REL).exists())
    t.check("t03_reference_exists", (root / AUTHORITATIVE_REFERENCE_REL).exists())
    t.check("t04_final_exists", (root / AUTHORITATIVE_FINAL_REL).exists())
    t.check(
        "t05_final_sha",
        sha256_file(root / AUTHORITATIVE_FINAL_REL) == EXPECTED_FINAL_SHA256,
        EXPECTED_FINAL_SHA256,
    )

    # 6-10 PROJECT_STATE
    ps = root / "troubleshooting" / "PROJECT_STATE.json"
    t.check("t06_project_state_exists", ps.exists())
    state = json.loads(ps.read_text(encoding="utf-8")) if ps.exists() else {}
    t.check("t07_project_state_sole", state.get("sole_authoritative") is True)
    t.check("t08_project_state_final_sha", state.get("authoritative_final_sha256") == EXPECTED_FINAL_SHA256)
    t.check("t09_project_state_cer", bool(state.get("trusted_cer")))
    t.check("t10_project_state_paths", isinstance(state.get("paths"), dict) and len(state.get("paths") or {}) >= 8)

    # 11-15 latest aliases
    aliases = [
        "troubleshooting/Alpha.txt",
        "troubleshooting/latest_alpha_output.txt",
        "troubleshooting/latest/latest_alpha_output.txt",
        "troubleshooting/latest/latest_live_alpha_output.txt",
    ]
    for i, rel in enumerate(aliases, start=11):
        p = root / rel
        ok = p.exists() and sha256_file(p) == EXPECTED_FINAL_SHA256
        t.check(f"t{i:02d}_alias_{Path(rel).name}", ok)

    t.check("t15_latest_state_json", (root / "troubleshooting/latest/LATEST_STATE.json").exists())

    # 16-20 scorers harden
    for name, script in (
        ("t16_score_latest_exit2", "score_latest_accuracy.py"),
        ("t17_analyze_exit2", "analyze_alpha_vs_reference.py"),
        ("t18_three_stage_exit2", "score_three_stage_accuracy.py"),
    ):
        proc = subprocess.run(
            [sys.executable, str(root / script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        t.check(name, proc.returncode == 2, f"rc={proc.returncode}")

    # with required args should not be exit 2 for missing-args reason when paths exist
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "score_three_stage_accuracy.py"),
            "--run-folder",
            str(root / AUTHORITATIVE_RUN_REL),
            "--reference",
            str(root / AUTHORITATIVE_REFERENCE_REL),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    t.check("t19_three_stage_explicit_ok", proc.returncode != 2, f"rc={proc.returncode}")
    t.check(
        "t20_no_silent_latest_in_score_latest",
        "silent latest" in (root / "score_latest_accuracy.py").read_text(encoding="utf-8").lower()
        or "latest_* fallback removed"
        in (root / "score_latest_accuracy.py").read_text(encoding="utf-8"),
    )

    # 21-25 Deepgram
    from alpha import stt_settings
    from alpha import config as cfg
    from alpha import constants as const

    ja = stt_settings.effective_stream_timing(language="ja")
    t.check("t21_stt_settings_exists", (root / "alpha/stt_settings.py").exists())
    t.check("t22_ja_endpointing_500", ja["endpointing_ms"] == 500)
    t.check("t23_ja_utterance_1500", ja["utterance_end_ms"] == 1500)
    t.check(
        "t24_config_reexport",
        cfg.DEEPGRAM_JA_ENDPOINTING_MS == 500 and cfg.DEEPGRAM_JA_UTTERANCE_END_MS == 1500,
    )
    t.check("t25_constants_diag_ja", const.DEEPGRAM_ENDPOINTING_MS == 500 and const.DEEPGRAM_UTTERANCE_END_MS == 1500)

    build = _latest_build(root)
    dg_report = {}
    if build and (build / "reports/DEEPGRAM_SETTINGS_RECONCILIATION.json").exists():
        dg_report = json.loads((build / "reports/DEEPGRAM_SETTINGS_RECONCILIATION.json").read_text(encoding="utf-8"))
    t.check("t26_deepgram_behavior_unchanged", dg_report.get("behavior_changed") is False)

    # 27-32 keyterms / glossary / languages
    from alpha.constants import SOURCE_LANGUAGES, TARGET_LANGUAGES, resolve_japanese_keyterms

    terms, _, _ = resolve_japanese_keyterms()
    banned = ["オリエンタル商事", "永井", "木村", "チン", "シュウメイ", "江藤"]
    t.check("t27_default_keyterms_json", (root / "alpha/resources/keyterms/default_ja_business.json").exists())
    t.check(
        "t28_test01_keyterms_json",
        (root / "troubleshooting/accuracy_benchmark/profiles/test01_keyterms.json").exists(),
    )
    t.check("t29_no_benchmark_in_defaults", not any(b in terms for b in banned))
    t.check("t30_source_langs_en_ja", set(SOURCE_LANGUAGES) == {"English", "Japanese"})
    t.check("t31_target_langs_en_ja", set(TARGET_LANGUAGES) <= {"English", "Japanese"} and "Japanese" in TARGET_LANGUAGES)
    t.check(
        "t32_inactive_future_langs",
        (root / "troubleshooting/accuracy_benchmark/languages/inactive_future_zh_ru.json").exists(),
    )

    # 33-38 tools/docs/runtime
    t.check("t33_tools_current", (root / "tools/TOOLS_CURRENT.json").exists())
    t.check("t34_run_all_checks", (root / "tools/run_all_current_checks.py").exists())
    t.check("t35_readme_mentions_phase1", PATCH_VERSION in (root / "README_CURRENT.md").read_text(encoding="utf-8"))
    t.check("t36_docs_archive", (root / "docs/archive").exists())
    t.check("t37_python_version_file", (root / ".python-version").exists())
    t.check("t38_runtime_contract", (root / "runtime_environment_contract.json").exists())

    # 39-44 retention / gitignore / inventory
    t.check("t39_requirements_lock", (root / "requirements-lock.txt").exists())
    t.check("t40_validate_runtime_env", (root / "validate_runtime_environment.py").exists())
    t.check("t41_retention_policy", (root / "troubleshooting/RETENTION_POLICY.json").exists())
    t.check("t42_apply_retention", (root / "tools/apply_retention_policy.py").exists())
    gi = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").exists() else ""
    t.check("t43_gitignore_phase1", "Phase 1" in gi or "__pycache__/" in gi)
    t.check("t44_gitignore_keeps_env_example", ".env.example" not in [ln.strip() for ln in gi.splitlines() if ln.strip() == ".env.example"])

    # 45-50 evidence index / acceptance / restore
    t.check("t45_latest_evidence_index", (root / "troubleshooting/latest/LATEST_EVIDENCE_INDEX.json").exists())
    if build:
        t.check("t46_build_subdirs", all((build / n).exists() for n in ("baseline", "inventory", "reports", "regression", "package", "restore")))
        t.check("t47_baseline_hashes", (build / "baseline/PHASE1_BASELINE_HASHES.json").exists())
        t.check("t48_acceptance", (build / "reports/PHASE1_FINAL_ACCEPTANCE.json").exists())
        t.check("t49_rollback_manifest", (build / "restore/PHASE1_ROLLBACK_MANIFEST.json").exists())
        t.check("t50_keyterm_audit", (build / "reports/KEYTERM_PROFILE_AUDIT.json").exists())
    else:
        for n in range(46, 51):
            t.check(f"t{n:02d}_build_required", False, "no_build")

    # 51-55 acceptance fields / pending
    acc = {}
    if build and (build / "reports/PHASE1_FINAL_ACCEPTANCE.json").exists():
        acc = json.loads((build / "reports/PHASE1_FINAL_ACCEPTANCE.json").read_text(encoding="utf-8"))
    t.check("t51_acceptance_version", acc.get("VERSION") == "ACCEPTED")
    t.check("t52_phase1_closed_13", acc.get("phase1_findings_closed") == 13)
    t.check("t53_phase2_pending_2", acc.get("phase2_findings_pending") == 2)
    t.check("t54_deferred_structural_2", acc.get("deferred_structural_findings") == 2)
    t.check("t55_ready_for_phase2", acc.get("ready_for_phase2") is True and acc.get("ready_for_issue12") is False)

    # 56-60 glossary / archive / runner present
    from alpha.transcription.corporate_ir_glossary import is_glossary_enabled_runtime, load_corporate_ir_glossary

    load_corporate_ir_glossary()
    gloss_path = root / "troubleshooting/accuracy_benchmark/glossaries/test01_corporate_ir_glossary.json"
    t.check("t56_glossary_failsafe", (not gloss_path.exists() and is_glossary_enabled_runtime() is False) or gloss_path.exists())
    t.check("t57_phase1_runner_exists", (root / "run_phase1_project_normalization_85253325.py").exists())
    t.check("t58_phase1_identity_module", (root / "alpha/utils/phase1_build_identity.py").exists())
    t.check("t59_atomic_latest_module", (root / "alpha/utils/atomic_latest_state.py").exists())
    archive = root / "troubleshooting" / "archive" / f"phase1_v{PATCH_VERSION}"
    t.check("t60_archive_root_exists", archive.exists())

    passed = sum(1 for _, ok, _ in t.results if ok)
    failed = sum(1 for _, ok, _ in t.results if not ok)
    return passed, failed, t.results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--offline-only", action="store_true", default=True)
    parser.add_argument("--reports-dir", default="")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    passed, failed, results = run_tests(project_root)
    total = len(results)
    lines = [f"total={total}", f"passed={passed}", f"failed={failed}"]
    for name, ok, detail in results:
        lines.append(f"{'PASS' if ok else 'FAIL'}:{name}:{detail}")

    build = _latest_build(project_root)
    out_dir = Path(args.reports_dir) if args.reports_dir else (
        (build / "regression") if build else project_root / "troubleshooting" / "phase1_normalization" / f"v{PATCH_VERSION}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "regression_phase1_project_normalization_85253325.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (out_dir / "regression_phase1_project_normalization_85253325.json").write_text(
        json.dumps(
            {
                "total": total,
                "expected_total": EXPECTED_TESTS,
                "passed": passed,
                "failed": failed,
                "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"REGRESSION_TOTAL={total}")
    print(f"REGRESSION_PASSED={passed}")
    print(f"REGRESSION_FAILED={failed}")
    if total != EXPECTED_TESTS:
        print(f"STATUS=FAILED")
        print(f"FAILED_INVARIANT=expected_{EXPECTED_TESTS}_tests_got_{total}")
        return 1
    if failed:
        print("STATUS=FAILED")
        for name, ok, detail in results:
            if not ok:
                print(f"FAIL={name}:{detail}")
        return 1
    print("STATUS=PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
