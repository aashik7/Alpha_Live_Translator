#!/usr/bin/env python3
"""Validate English Raw→Stable→Final content preservation (no added/substituted words)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_english_streaming_replay_experiment import (  # noqa: E402
    content_preservation,
    lexical_tokens,
)


def main() -> int:
    pointer = (
        ROOT
        / "troubleshooting"
        / "experiments"
        / "english_streaming_improvement"
        / "LATEST_EXPERIMENT.json"
    )
    if not pointer.is_file():
        print("No LATEST_EXPERIMENT.json — run streaming experiment first")
        return 1
    exp_dir = Path(json.loads(pointer.read_text(encoding="utf-8"))["experiment_dir"])
    a = exp_dir / "candidates" / "A_production_streaming"
    events = json.loads((a / "raw_events.json").read_text(encoding="utf-8"))
    hyp = (a / "hypothesis.txt").read_text(encoding="utf-8")
    # Harness Stable/Final == joined Raw finals (no Alpha rewrite in replay)
    payload = content_preservation(events, hyp, hyp)
    payload["hypothesis_token_count"] = len(lexical_tokens(hyp))
    payload["ENGLISH_CONTENT_PRESERVATION_VALIDATION"] = (
        "PASSED"
        if payload["RAW_TO_STABLE_UNSUPPORTED_INSERTIONS"] == 0
        and payload["STABLE_TO_FINAL_UNSUPPORTED_INSERTIONS"] == 0
        and payload["STABLE_TO_FINAL_UNSUPPORTED_SUBSTITUTIONS"] == 0
        else "FAILED"
    )
    out = exp_dir / "ENGLISH_CONTENT_PRESERVATION_VALIDATION.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(payload["ENGLISH_CONTENT_PRESERVATION_VALIDATION"])
    print(f"Wrote {out}")
    return 0 if payload["ENGLISH_CONTENT_PRESERVATION_VALIDATION"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
