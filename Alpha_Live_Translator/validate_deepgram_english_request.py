#!/usr/bin/env python3
"""Validate English Deepgram live request construction (no Japanese options/conflicts)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.utils.english_deepgram_request import (  # noqa: E402
    ENGLISH_DIARIZE_MODE_OFF,
    ENGLISH_DIARIZE_MODE_PRODUCTION,
    build_english_live_query_params,
    production_english_live_query_string,
    query_string_from_params,
    validate_english_query_string,
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    out_dir = ROOT / "troubleshooting" / "validation" / "english_only_improvement"
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict] = []
    errors: list[str] = []

    # Production English (diarize_model only)
    try:
        q = production_english_live_query_string()
        v = validate_english_query_string(q)
        checks.append({"name": "production_english", "ok": True, "query": q, "validation": v})
    except Exception as exc:
        errors.append(f"production_english:{exc}")
        checks.append({"name": "production_english", "ok": False, "error": str(exc)})

    # Diarization off
    try:
        p = build_english_live_query_params(diarize_mode=ENGLISH_DIARIZE_MODE_OFF)
        q = query_string_from_params(p)
        v = validate_english_query_string(q)
        checks.append({"name": "english_diarize_off", "ok": True, "query": q, "validation": v})
    except Exception as exc:
        errors.append(f"english_diarize_off:{exc}")
        checks.append({"name": "english_diarize_off", "ok": False, "error": str(exc)})

    # Conflict must FAIL validation
    conflict_ok = False
    try:
        validate_english_query_string(
            "model=nova-3&language=en&encoding=linear16&sample_rate=16000&channels=1"
            "&interim_results=true&punctuate=true&smart_format=true&numerals=true"
            "&endpointing=1200&utterance_end_ms=1500&diarize=true&diarize_model=latest"
        )
        errors.append("conflict_case_should_have_failed")
    except ValueError:
        conflict_ok = True
        checks.append({"name": "conflict_diarize_and_diarize_model", "ok": True, "rejected": True})

    # Endpointing variants
    for ep in (500, 800, 1200):
        try:
            p = build_english_live_query_params(
                endpointing_ms=ep, diarize_mode=ENGLISH_DIARIZE_MODE_PRODUCTION
            )
            q = query_string_from_params(p)
            v = validate_english_query_string(q)
            checks.append({"name": f"endpointing_{ep}", "ok": True, "query": q, "validation": v})
        except Exception as exc:
            errors.append(f"endpointing_{ep}:{exc}")
            checks.append({"name": f"endpointing_{ep}", "ok": False, "error": str(exc)})

    # Mirror of last live English request (from evidence) must pass
    live_q = (
        "model=nova-3&language=en&punctuate=true&smart_format=true&diarize_model=latest"
        "&numerals=true&profanity_filter=false&redact=false&endpointing=1200"
        "&utterance_end_ms=1500&encoding=linear16&sample_rate=16000&channels=1"
        "&interim_results=true"
    )
    try:
        v = validate_english_query_string(live_q)
        checks.append({"name": "live_run_057f111e_mirror", "ok": True, "validation": v})
    except Exception as exc:
        errors.append(f"live_mirror:{exc}")
        checks.append({"name": "live_run_057f111e_mirror", "ok": False, "error": str(exc)})

    passed = (not errors) and conflict_ok and all(c.get("ok") for c in checks)
    payload = {
        "generated_at_utc": _utc(),
        "ENGLISH_DEEPGRAM_REQUEST_VALIDATION": "PASSED" if passed else "FAILED",
        "conflict_rejection_verified": conflict_ok,
        "checks": checks,
        "errors": errors,
        "notes": [
            "Production English uses diarize_model=latest without diarize=true.",
            "Japanese request building is not exercised by this validator.",
        ],
    }
    out = out_dir / "ENGLISH_DEEPGRAM_REQUEST_VALIDATION.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"ENGLISH_DEEPGRAM_REQUEST_VALIDATION = {payload['ENGLISH_DEEPGRAM_REQUEST_VALIDATION']}")
    print(f"Wrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
