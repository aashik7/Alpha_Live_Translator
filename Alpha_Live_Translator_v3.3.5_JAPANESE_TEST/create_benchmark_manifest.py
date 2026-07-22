"""Create benchmark test manifest for 18-minute business CER tests."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.utils.reference_alpha_hash import file_sha256


def main() -> int:
    parser = argparse.ArgumentParser(description="Create benchmark test manifest")
    parser.add_argument("--name", required=True, help="Benchmark name, e.g. business_18min_test01")
    parser.add_argument("--reference", required=True, help="Path to reference transcript")
    parser.add_argument("--notes", default="", help="Optional notes")
    parser.add_argument("--duration", type=float, default=18.0, help="Expected duration minutes")
    args = parser.parse_args()

    reference_path = Path(args.reference)
    if not reference_path.exists():
        print(f"FAILED reference not found: {reference_path}")
        return 1

    out_dir = Path("troubleshooting/accuracy_benchmark/test_manifests")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "benchmark_name": args.name,
        "expected_language": "ja",
        "expected_domain": "business_meeting",
        "video_duration_minutes": args.duration,
        "selected_section_start": "",
        "selected_section_end": "",
        "reference_path": str(reference_path).replace("\\", "/"),
        "reference_sha256": file_sha256(reference_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": args.notes,
        "app_version": APP_VERSION,
        "intended_metrics": [
            "cer_score",
            "trusted_cer_score",
            "alignment_coverage",
            "boundary_risks",
            "business_term_risks",
            "translation_ready_ratio",
        ],
        "do_not_autocorrect": True,
        "raw_mutation_required_zero": True,
    }
    out_path = out_dir / f"{args.name}_manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
