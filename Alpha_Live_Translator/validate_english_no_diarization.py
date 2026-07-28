#!/usr/bin/env python3
"""Validate English Deepgram request has no diarization parameters."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.constants import ENGLISH_DIARIZATION_ENABLED  # noqa: E402
from alpha.stt_settings import (  # noqa: E402
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_UTTERANCE_END_MS,
    clamp_deepgram_utterance_end_ms,
)
from alpha.utils.english_deepgram_request import (  # noqa: E402
    production_english_live_query_string,
    validate_english_query_string,
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _inspect_query(query: str) -> dict:
    parsed = parse_qs(query, keep_blank_values=True)
    flat = {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}
    return {
        "query": query,
        "diarize_absent": "diarize" not in flat,
        "diarize_model_absent": "diarize_model" not in flat,
        "language": flat.get("language"),
        "model": flat.get("model"),
        "endpointing": flat.get("endpointing"),
        "utterance_end_ms": flat.get("utterance_end_ms"),
        "encoding": flat.get("encoding"),
        "sample_rate": flat.get("sample_rate"),
        "channels": flat.get("channels"),
    }


def main() -> int:
    errors: list[str] = []
    q = production_english_live_query_string()
    try:
        validate_english_query_string(q)
    except Exception as exc:
        errors.append(f"validate_english_query_string:{exc}")
    snap = _inspect_query(q)

    # Also inspect live builder path used by DeepgramClientMixin when possible
    live_snap = None
    try:
        from alpha.transcription.deepgram_client import DeepgramClientMixin

        class _Host(DeepgramClientMixin):
            def __init__(self):
                self._listen_language = "en"
                self.deepgram_socket = None

        host = _Host()
        url = host._build_deepgram_url()
        qs = urlparse(str(url)).query
        live_snap = _inspect_query(qs)
        if not live_snap.get("diarize_absent") or not live_snap.get(
            "diarize_model_absent"
        ):
            errors.append("live_url_still_contains_diarization")
    except Exception as exc:
        errors.append(f"live_url_build:{exc}")

    checks = {
        "ENGLISH_DIARIZATION_ENABLED": ENGLISH_DIARIZATION_ENABLED,
        "production_builder": snap,
        "live_deepgram_url": live_snap,
    }

    def _ok(s: dict | None) -> bool:
        if not s:
            return False
        return (
            s.get("diarize_absent") is True
            and s.get("diarize_model_absent") is True
            and str(s.get("language") or "").lower() == "en"
            and str(s.get("model") or "") == "nova-3"
            and str(s.get("endpointing") or "") == str(int(DEEPGRAM_ENDPOINTING_MS))
            and str(s.get("utterance_end_ms") or "")
            == str(int(clamp_deepgram_utterance_end_ms(int(DEEPGRAM_UTTERANCE_END_MS))[0]))
        )

    passed = (
        ENGLISH_DIARIZATION_ENABLED is False
        and _ok(snap)
        and (live_snap is None or _ok(live_snap))
        and not errors
    )
    payload = {
        "generated_at_utc": _utc(),
        "ENGLISH_NO_DIARIZATION_VALIDATION": "PASSED" if passed else "FAILED",
        "errors": errors,
        "checks": checks,
    }
    out_dir = ROOT / "troubleshooting" / "validation" / "translation_beta"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ENGLISH_NO_DIARIZATION_VALIDATION.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # also write english_request_no_diarization snapshot
    (out_dir / "english_request_no_diarization.json").write_text(
        json.dumps({"query": q, "snapshot": snap, "live": live_snap}, indent=2),
        encoding="utf-8",
    )
    print(f"ENGLISH_NO_DIARIZATION_VALIDATION = {'PASSED' if passed else 'FAILED'}")
    print(f"wrote {out_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
