"""Canonical Deepgram / STT connection settings (Phase 1 reconciliation authority).

Runtime Deepgram client imports these values via alpha.config re-exports.
Japanese live timing must remain endpointing=500 / utterance_end=1500.
Non-Japanese defaults remain endpointing=1200 / utterance_end=5000 (clamped at connect).
"""

from __future__ import annotations

# Model / stream
DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_SAMPLE_RATE = 16000

# Non-Japanese defaults (historical config.py values — preserve effective behavior)
DEEPGRAM_ENDPOINTING_MS = 1200
DEEPGRAM_UTTERANCE_END_MS = 5000

# Japanese manual mode — proven effective on authoritative run
DEEPGRAM_JA_ENDPOINTING_MS = 500
DEEPGRAM_JA_UTTERANCE_END_MS = 1500

# Clamp policy (unchanged)
DEEPGRAM_UTTERANCE_END_CLAMP_MIN_MS = 1000
DEEPGRAM_UTTERANCE_END_CLAMP_MAX_MS = 3000
DEEPGRAM_UTTERANCE_END_SAFE_DEFAULT_MS = 1500

# Diagnostic / validator aliases: Japanese effective timing used by constants diagnostics
DEEPGRAM_JA_DIAGNOSTIC_ENDPOINTING_MS = DEEPGRAM_JA_ENDPOINTING_MS
DEEPGRAM_JA_DIAGNOSTIC_UTTERANCE_END_MS = DEEPGRAM_JA_UTTERANCE_END_MS


def clamp_deepgram_utterance_end_ms(value: int) -> tuple[int, bool]:
    """Clamp utterance_end_ms to Deepgram-supported range; return (value, was_clamped)."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return DEEPGRAM_UTTERANCE_END_SAFE_DEFAULT_MS, True
    if ms < DEEPGRAM_UTTERANCE_END_CLAMP_MIN_MS or ms > DEEPGRAM_UTTERANCE_END_CLAMP_MAX_MS:
        return DEEPGRAM_UTTERANCE_END_SAFE_DEFAULT_MS, True
    return ms, False


def effective_stream_timing(*, language: str) -> dict[str, int | bool]:
    """Return the timing that deepgram_client would apply for the given language."""
    lang = (language or "en").lower().replace("_", "-")
    is_ja = lang.startswith("ja")
    if is_ja:
        endpointing = int(DEEPGRAM_JA_ENDPOINTING_MS)
        raw_ue = int(DEEPGRAM_JA_UTTERANCE_END_MS)
    else:
        endpointing = int(DEEPGRAM_ENDPOINTING_MS)
        raw_ue = int(DEEPGRAM_UTTERANCE_END_MS)
    utterance_end, clamped = clamp_deepgram_utterance_end_ms(raw_ue)
    return {
        "language": lang,
        "is_japanese": is_ja,
        "endpointing_ms": endpointing,
        "utterance_end_ms_raw": raw_ue,
        "utterance_end_ms": utterance_end,
        "utterance_end_clamped": clamped,
    }
