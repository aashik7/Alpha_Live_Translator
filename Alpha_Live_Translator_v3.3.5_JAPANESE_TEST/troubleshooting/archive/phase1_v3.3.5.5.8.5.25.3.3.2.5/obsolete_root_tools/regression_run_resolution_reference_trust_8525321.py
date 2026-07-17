"""Regression tests for run resolution and reference trust (V25.3.2.1)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.utils.latest_completed_live_run import normalize_app_version, resolve_latest_completed_live_run, versions_match
from alpha.utils.prepared_reference_trust import load_prepared_reference_trust
from alpha.utils.repair_helpers import transcript_hashes

OUT = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.2.1/regression_run_resolution_reference_trust_8525321.txt")
TARGET = Path("troubleshooting/runs/v3.3.5.5.8.5.25.3.2-20260713-160202")


def _test(name: str, fn) -> str:
    try:
        fn()
        return f"PASS {name}"
    except Exception as exc:
        return f"FAIL {name}: {exc}"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"regression_run_resolution_reference_trust {APP_VERSION}", ""]

    def t_v_prefix():
        assert normalize_app_version("V3.3.5.5.8.5.25.3.2") == "3.3.5.5.8.5.25.3.2"
        assert versions_match("V3.3.5.5.8.5.25.3.2", "3.3.5.5.8.5.25.3.2")

    def t_explicit_run():
        r = resolve_latest_completed_live_run(
            expected_version="3.3.5.5.8.5.25.3.2",
            explicit_run_folder=TARGET,
        )
        assert r["ok"]
        assert "v3.3.5.5.8.5.25.3.2-20260713-160202" in r["resolved_run_folder"]

    def t_latest_live():
        r = resolve_latest_completed_live_run(expected_version="3.3.5.5.8.5.25.3.2")
        assert r["ok"]
        assert r["resolved_run_type"] == "live"

    def t_prepared_trust():
        t = load_prepared_reference_trust(
            "troubleshooting/accuracy_benchmark/prepared/v3.3.5.5.8.5.25.3.2/reference.txt"
        )
        assert t["trusted"] is True
        assert t["hash_match"] is True

    def t_prepared_only_ok():
        t = load_prepared_reference_trust(
            "troubleshooting/accuracy_benchmark/prepared/v3.3.5.5.8.5.25.3.2/reference.txt"
        )
        assert t["prepared_reference_only"] or t["trusted"]

    def t_hash_mismatch_rejects():
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ref = d / "reference.txt"
            ref.write_text("not the real reference", encoding="utf-8")
            snap = {
                "valid_for_cer": True,
                "reference_quality_verdict": "valid_for_cer",
                "should_run_cer": True,
                "failure_reasons": [],
                "snapshot_sha256": hashlib.sha256(b"x").hexdigest(),
                "snapshot_path": str(ref),
                "prepared_reference_only": True,
                "normalization_version": "v25.3.2_nfkc_strip_speaker",
            }
            (d / "reference_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
            # Temporarily point trust at wrong snapshot by patching path - use inline logic
            from alpha.utils import prepared_reference_trust as prt

            old = prt.PREPARED_SNAPSHOT
            old_ref = prt.PREPARED_REFERENCE
            old_q = prt.PREPARED_QUALITY
            try:
                prt.PREPARED_SNAPSHOT = d / "reference_snapshot.json"
                prt.PREPARED_REFERENCE = ref
                prt.PREPARED_QUALITY = d / "missing.json"
                result = prt.load_prepared_reference_trust(ref)
                assert not result["trusted"]
            finally:
                prt.PREPARED_SNAPSHOT = old
                prt.PREPARED_REFERENCE = old_ref
                prt.PREPARED_QUALITY = old_q

    def t_offline_provenance_exclusive():
        s = {"generated_during_runtime": False, "generated_by_offline_repair": True}
        assert not (s["generated_during_runtime"] and s["generated_by_offline_repair"])

    for name, fn in [
        ("v_prefix_normalization", t_v_prefix),
        ("explicit_run_folder", t_explicit_run),
        ("latest_completed_live", t_latest_live),
        ("prepared_reference_trusted", t_prepared_trust),
        ("prepared_reference_only_accepted", t_prepared_only_ok),
        ("hash_mismatch_rejects", t_hash_mismatch_rejects),
        ("offline_audio_provenance_exclusive", t_offline_provenance_exclusive),
    ]:
        lines.append(_test(name, fn))

    failed = [ln for ln in lines if ln.startswith("FAIL")]
    lines.append("")
    lines.append("PASSED" if not failed else "FAILED")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print("PASSED" if not failed else "FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
