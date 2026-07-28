"""English-only Deepgram live request construction and conflict validation.

Japanese request building remains in deepgram_client._build_deepgram_url and must
not be changed by this module. This module is for English allowlist validation
and experiment harnesses that must mirror production English params.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode

from alpha.stt_settings import (
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_MODEL,
    DEEPGRAM_SAMPLE_RATE,
    DEEPGRAM_UTTERANCE_END_MS,
    clamp_deepgram_utterance_end_ms,
)

# English production now omits diarization params (ENGLISH_DIARIZATION_ENABLED=False).
ENGLISH_DIARIZE_MODE_PRODUCTION = "off"
ENGLISH_DIARIZE_MODE_OFF = "off"
ENGLISH_DIARIZE_MODE_FLAG_ONLY = "diarize_true"  # alternate supported form
ENGLISH_DIARIZE_MODE_MODEL_LATEST = "diarize_model_latest"  # legacy / A-B only

ENGLISH_QUERY_ALLOWLIST = frozenset(
    {
        "model",
        "language",
        "encoding",
        "sample_rate",
        "channels",
        "interim_results",
        "punctuate",
        "smart_format",
        "numerals",
        "profanity_filter",
        "redact",
        "endpointing",
        "utterance_end_ms",
        "diarize",
        "diarize_model",
        "keyterm",
    }
)

FORBIDDEN_ENGLISH_KEYS = frozenset(
    {
        # Japanese-only / unsafe for EN experiments
        "keywords",  # legacy
    }
)


def build_english_live_query_params(
    *,
    endpointing_ms: int | None = None,
    utterance_end_ms: int | None = None,
    diarize_mode: str = ENGLISH_DIARIZE_MODE_PRODUCTION,
    keyterms: list[str] | None = None,
    sample_rate: int | None = None,
) -> dict[str, str]:
    """Build English live query params matching Alpha production semantics."""
    ep = int(endpointing_ms if endpointing_ms is not None else DEEPGRAM_ENDPOINTING_MS)
    raw_ue = int(
        utterance_end_ms if utterance_end_ms is not None else DEEPGRAM_UTTERANCE_END_MS
    )
    ue, _ = clamp_deepgram_utterance_end_ms(raw_ue)
    sr = int(sample_rate if sample_rate is not None else DEEPGRAM_SAMPLE_RATE)
    params: dict[str, str] = {
        "model": str(DEEPGRAM_MODEL),
        "language": "en",
        "encoding": "linear16",
        "sample_rate": str(sr),
        "channels": "1",
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "numerals": "true",
        "profanity_filter": "false",
        "redact": "false",
        "endpointing": str(ep),
        "utterance_end_ms": str(ue),
    }
    mode = str(diarize_mode or ENGLISH_DIARIZE_MODE_PRODUCTION).strip().lower()
    if mode in (ENGLISH_DIARIZE_MODE_MODEL_LATEST, "latest", "on", "diarize_model"):
        # Legacy A/B only — never combine with diarize=true.
        params["diarize_model"] = "latest"
    elif mode in (ENGLISH_DIARIZE_MODE_FLAG_ONLY, "diarize", "diarize_true"):
        params["diarize"] = "true"
    elif mode in (
        ENGLISH_DIARIZE_MODE_OFF,
        ENGLISH_DIARIZE_MODE_PRODUCTION,
        "production",
        "off",
        "none",
        "false",
        "0",
    ):
        pass
    else:
        raise ValueError(f"unsupported English diarize_mode={diarize_mode!r}")

    validate_english_deepgram_query_params(params, keyterms=keyterms)
    return params


def query_string_from_params(
    params: dict[str, str], *, keyterms: list[str] | None = None
) -> str:
    q = urlencode(params)
    for term in keyterms or []:
        t = str(term or "").strip()
        if t:
            q += "&" + urlencode({"keyterm": t})
    return q


def validate_english_deepgram_query_params(
    params: dict[str, Any],
    *,
    keyterms: list[str] | None = None,
) -> dict[str, Any]:
    """Validate English Deepgram live params. Raises ValueError on conflict."""
    flat: dict[str, Any] = {}
    for k, v in (params or {}).items():
        key = str(k)
        if isinstance(v, (list, tuple)) and len(v) == 1:
            flat[key] = v[0]
        else:
            flat[key] = v

    errors: list[str] = []
    unknown = sorted(k for k in flat if k not in ENGLISH_QUERY_ALLOWLIST)
    if unknown:
        errors.append(f"unknown_or_disallowed_keys:{unknown}")

    for bad in FORBIDDEN_ENGLISH_KEYS:
        if bad in flat:
            errors.append(f"forbidden_key:{bad}")

    if str(flat.get("model") or "") != "nova-3":
        errors.append(f"model_expected_nova-3_got:{flat.get('model')}")
    if str(flat.get("language") or "").lower() not in {"en", "en-us", "en-gb"}:
        errors.append(f"language_expected_en_got:{flat.get('language')}")
    if str(flat.get("encoding") or "") != "linear16":
        errors.append(f"encoding_expected_linear16_got:{flat.get('encoding')}")
    if str(flat.get("sample_rate") or "") not in {"16000", "16_000"}:
        errors.append(f"sample_rate_expected_16000_got:{flat.get('sample_rate')}")
    if str(flat.get("channels") or "") != "1":
        errors.append(f"channels_expected_1_got:{flat.get('channels')}")

    has_diarize = "diarize" in flat and str(flat.get("diarize")).lower() in {
        "true",
        "1",
        "yes",
    }
    has_diarize_model = "diarize_model" in flat and str(flat.get("diarize_model") or "").strip()
    has_diarize_version = "diarize_version" in flat and str(
        flat.get("diarize_version") or ""
    ).strip()
    if has_diarize and has_diarize_model:
        errors.append(
            "conflicting_diarization:diarize_and_diarize_model_cannot_both_be_set"
        )
    if has_diarize_model and has_diarize_version:
        errors.append(
            "conflicting_diarization:diarize_model_and_diarize_version_cannot_both_be_set"
        )
    if has_diarize and has_diarize_version:
        errors.append(
            "conflicting_diarization:diarize_and_diarize_version_cannot_both_be_set"
        )

    # No Japanese keyterms on English requests unless explicitly passed for a
    # controlled product-glossary experiment (caller supplies keyterms).
    ja_like = [
        t
        for t in (keyterms or [])
        if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in str(t))
    ]
    if ja_like:
        errors.append(f"japanese_keyterms_forbidden_on_english:{ja_like[:5]}")

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "ok": True,
        "ENGLISH_DEEPGRAM_REQUEST_VALIDATION": "PASSED",
        "params": {k: str(v) for k, v in flat.items()},
        "diarize_present": bool(has_diarize),
        "diarize_model_present": bool(has_diarize_model),
        "diarization_mode": (
            "diarize_model"
            if has_diarize_model
            else ("diarize" if has_diarize else "off")
        ),
    }


def validate_english_query_string(query: str) -> dict[str, Any]:
    parsed = parse_qs(query, keep_blank_values=True)
    flat = {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}
    keyterms = []
    if "keyterm" in parsed:
        keyterms = [str(x) for x in parsed.get("keyterm") or []]
        flat.pop("keyterm", None)
    return validate_english_deepgram_query_params(flat, keyterms=keyterms)


def production_english_live_query_string(
    *,
    endpointing_ms: int | None = None,
    diarize_mode: str = ENGLISH_DIARIZE_MODE_PRODUCTION,
) -> str:
    params = build_english_live_query_params(
        endpointing_ms=endpointing_ms,
        diarize_mode=diarize_mode,
    )
    return query_string_from_params(params)
