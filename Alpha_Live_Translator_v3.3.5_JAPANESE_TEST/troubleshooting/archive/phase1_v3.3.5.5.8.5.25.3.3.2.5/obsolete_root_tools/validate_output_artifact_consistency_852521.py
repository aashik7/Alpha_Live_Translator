"""Regression: authoritative output hash consistency (8.5.25.2.1)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from alpha.utils.canonical_export_writer import (
    set_canonical_export_payload,
    transcript_sha256,
    write_authoritative_outputs_from_payload,
)


def main() -> int:
    lines = ["[Speaker 2] 公定価格の上昇による補助金増額。", "[Speaker 2] 経常利益は11億2800万円となりました。"]
    payload = set_canonical_export_payload(lines)
    with tempfile.TemporaryDirectory() as td:
        run_folder = Path(td)
        result = write_authoritative_outputs_from_payload(run_folder=run_folder)
        hashes = set()
        for p in result.get("written_paths", []):
            path = Path(p)
            if path.exists():
                hashes.add(transcript_sha256(path.read_text(encoding="utf-8")))
        checks = {
            "payload_created": bool(payload.get("canonical_export_payload_sha256")),
            "write_ok": result.get("ok") is True,
            "hash_consistent": result.get("final_output_hash_consistent") is True and len(hashes) <= 1,
            "single_sha": len(hashes) == 1,
        }
    failed = [k for k, ok in checks.items() if not ok]
    lines_out = [
        "VALIDATE_OUTPUT_ARTIFACT_CONSISTENCY_852521",
        f"Result: {'PASSED' if not failed else 'FAILED'}",
        f"sha256={payload.get('canonical_export_payload_sha256', '')[:16]}...",
    ]
    if failed:
        lines_out.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_output_artifact_consistency_852521_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    print("\n".join(lines_out))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
