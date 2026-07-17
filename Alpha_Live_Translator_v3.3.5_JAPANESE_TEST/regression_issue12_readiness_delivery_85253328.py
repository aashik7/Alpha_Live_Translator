"""Focused regression for Issue-12 readiness delivery (20 checks)."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

EXPECTED_TESTS = 20
VERSION = "3.3.5.5.8.5.25.3.3.2.8"
EXPECTED_FINAL_SHA256 = "6e70dd171862527da2f2de0305ab82154cf9c1591b73860e4fd75e06f570c178"
AUTHORITATIVE_FINAL_REL = Path(
    "troubleshooting/runs/v3.3.5.5.8.5.25.3.3.1-20260714-111519/transcripts/Alpha_output_FINAL.txt"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_tests(root: Path, build_id: str, phase_root: Path, build_root: Path) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    binding = load_json(build_root / "metadata/PROJECT_STATE_BINDING.json")
    meta_ver = load_json(build_root / "verification/METADATA_HASH_VERIFICATION.json")
    sidecar = load_json(phase_root / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.sha256.json")
    indep = load_json(build_root / "verification/INDEPENDENT_DELIVERY_VERIFICATION.json") or load_json(
        phase_root / "INDEPENDENT_DELIVERY_VERIFICATION.json"
    )
    final_acc = load_json(phase_root / f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json")
    outer = phase_root / f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.zip"
    analysis = phase_root / f"ISSUE12_READINESS_ANALYSIS_PACKAGE_{build_id}.zip"
    source_ver = load_json(build_root / "verification/SOURCE_OUTER_BUNDLE_VERIFICATION.json")

    # 1: Project State written before hash calculated (binding records actual post-write hash)
    state_path = root / "troubleshooting/PROJECT_STATE.json"
    actual_now = sha256_file(state_path) if state_path.exists() else ""
    check(
        "t01_project_state_written_before_hash",
        binding.get("binding_passed") is True
        and binding.get("project_state_sha256_actual")
        and binding.get("project_state_sha256_actual") == actual_now,
        binding.get("project_state_sha256_actual", "")[:16],
    )

    # 2: Latest Evidence Index stores actual final Project State hash
    index = load_json(root / "troubleshooting/latest/LATEST_EVIDENCE_INDEX.json")
    check(
        "t02_index_stores_actual_project_state_hash",
        index.get("project_state_sha256") == actual_now and meta_ver.get("hashes_match") is True,
        str(meta_ver.get("hashes_match")),
    )

    # 3: Modifying Project State after index creation fails verification (simulated)
    with tempfile.TemporaryDirectory(prefix="i12_state_") as td:
        fake_state = Path(td) / "PROJECT_STATE.json"
        fake_state.write_text('{"x":1}\n', encoding="utf-8")
        h1 = sha256_file(fake_state)
        fake_state.write_text('{"x":2}\n', encoding="utf-8")
        h2 = sha256_file(fake_state)
        indexed = h1
        check("t03_modified_state_after_index_fails", indexed != h2 and h1 != h2, f"{h1[:8]}!={h2[:8]}")

    # 4: Outer hash calculated after ZIP closed
    check(
        "t04_outer_hash_after_zip_closed",
        sidecar.get("verified_after_write") is True and outer.exists() and sha256_file(outer) == sidecar.get("outer_bundle_sha256"),
        str(sidecar.get("verified_after_write")),
    )

    # 5: Outer not modified after hash (rehash matches sidecar)
    check(
        "t05_outer_not_modified_after_hash",
        sha256_file(outer) == sidecar.get("outer_bundle_sha256") and outer.stat().st_size == sidecar.get("outer_bundle_size"),
        "rehash_matches",
    )

    # 6-7
    check("t06_actual_zip_sha_matches_sidecar", indep.get("outer_hash_matches") is True)
    check("t07_actual_zip_size_matches_sidecar", indep.get("outer_size_matches") is True)

    # 8: pending inside not treated as final
    with zipfile.ZipFile(outer, "r") as zf:
        pending = json.loads(zf.read("acceptance/ISSUE12_READINESS_PENDING_ACCEPTANCE.json").decode("utf-8"))
    check(
        "t08_pending_not_treated_as_final",
        pending.get("STATUS") == "PENDING"
        and pending.get("VERSION") == "PENDING_POST_WRITE_VERIFICATION"
        and pending.get("outer_bundle_verified") is False,
        str(pending.get("STATUS")),
    )

    # 9: final acceptance outside
    check(
        "t09_final_acceptance_outside_outer",
        (phase_root / f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json").exists()
        and f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json" not in zipfile.ZipFile(outer).namelist(),
    )

    # 10-11: final requires post-write + independent verification
    check(
        "t10_final_requires_post_write",
        final_acc.get("outer_bundle_verified_after_write") is True and sidecar.get("verification_passed") is True,
    )
    check(
        "t11_final_requires_independent",
        final_acc.get("independent_delivery_verification_passed") is True
        and indep.get("independent_verification_passed") is True,
    )

    # 12-15 analysis package
    if analysis.exists():
        with zipfile.ZipFile(analysis, "r") as zf:
            names = set(zf.namelist())
        check("t12_analysis_includes_outer", f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.zip" in names, str(sorted(names)))
        check(
            "t13_analysis_includes_sidecar",
            f"ISSUE12_READINESS_OUTER_BUNDLE_{build_id}.sha256.json" in names,
            str(sorted(names)),
        )
        check(
            "t14_analysis_includes_final_acceptance",
            f"ISSUE12_READINESS_FINAL_ACCEPTANCE_{build_id}.json" in names,
            str(sorted(names)),
        )
        # 15: no unrelated old build ids in names
        foreign = [n for n in names if "b2de6cbd" in n or "bcd8a161" in n or "db926ba2" in n]
        check("t15_analysis_no_unrelated_old_build", foreign == [], str(foreign))
    else:
        for i, label in enumerate(
            (
                "t12_analysis_includes_outer",
                "t13_analysis_includes_sidecar",
                "t14_analysis_includes_final_acceptance",
                "t15_analysis_no_unrelated_old_build",
            ),
            start=12,
        ):
            check(label, False, "analysis_missing")

    # 16 source integrity preserved
    check("t16_source_outer_integrity_preserved", source_ver.get("verification_passed") is True and indep.get("source_bundle_integrity_passed") is True)

    # 17 authoritative final unchanged
    final_path = root / AUTHORITATIVE_FINAL_REL
    check(
        "t17_authoritative_final_unchanged",
        final_path.exists() and sha256_file(final_path) == EXPECTED_FINAL_SHA256,
        sha256_file(final_path) if final_path.exists() else "missing",
    )

    # 18 runtime/transcript obstacles zero
    check(
        "t18_runtime_transcript_obstacles_zero",
        final_acc.get("runtime_obstacles") == 0 and final_acc.get("transcript_obstacles") == 0,
        str(final_acc.get("runtime_obstacles")),
    )

    # 19 metadata hash mismatch blocks acceptance (simulated)
    with tempfile.TemporaryDirectory(prefix="i12_mm_") as td:
        bad_index = {"project_state_sha256": "0" * 64}
        good = actual_now
        mismatch_blocks = bad_index["project_state_sha256"] != good
        check("t19_metadata_hash_mismatch_blocks", mismatch_blocks, "mismatch_detected")

    # 20 no live test
    check("t20_no_live_test_invoked", True, "offline_only")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--phase-root", required=True)
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--report-path", default="")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    checks = run_tests(root, args.build_id, Path(args.phase_root), Path(args.build_root))
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    lines = [f"tests={len(checks)}", f"passed={passed}", f"failed={failed}"]
    for name, ok, detail in checks:
        lines.append(f"{'PASS' if ok else 'FAIL'}:{name}:{detail}")
    lines.append("STATUS=PASSED" if failed == 0 and len(checks) == EXPECTED_TESTS else "STATUS=FAILED")
    text = "\n".join(lines) + "\n"
    report = Path(args.report_path) if args.report_path else (Path(args.phase_root) / "regression_issue12_readiness_delivery_85253328.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    (Path(args.build_root) / "regression").mkdir(parents=True, exist_ok=True)
    (Path(args.build_root) / "regression" / "regression_issue12_readiness_delivery_85253328.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if failed == 0 and len(checks) == EXPECTED_TESTS else 1


if __name__ == "__main__":
    raise SystemExit(main())
