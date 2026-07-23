"""Runtime configuration, Deepgram settings, and API credentials."""

import os
from pathlib import Path

from dotenv import load_dotenv

from alpha.constants import APP_CODENAME, APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

# Load .env from project root (no-op if missing; does not crash on import)
load_dotenv(PROJECT_ROOT / ".env")

# API keys from environment / .env only — never hardcoded in source
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY") or None
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") or None
DEEPL_API_PLAN = os.getenv("DEEPL_API_PLAN", "auto").lower()
try:
    DEEPL_TIMEOUT_SECONDS = float(os.getenv("DEEPL_TIMEOUT_SECONDS", "10"))
except ValueError:
    DEEPL_TIMEOUT_SECONDS = 10.0

DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_SAMPLE_RATE = 16000
DEEPGRAM_ENDPOINTING_MS = 1200  # silence before finalizing; higher = fewer mid-phrase cuts
DEEPGRAM_UTTERANCE_END_MS = 5000  # ms of silence before UtteranceEnd event
TRANSCRIPT_MERGE_WINDOW_S = 3.5  # merge same-speaker finals within this window
AUDIO_BLOCKSIZE = 4000  # silence padding chunk size at 16 kHz
WASAPI_FRAMES_PER_BUFFER = 2048
MIC_BLOCKSIZE = 1024  # microphone capture block size at 16 kHz
MIC_NOISE_GATE_INITIAL_RMS = 200.0  # CHANGED: adaptive gate seed for first 2s (fix 7)
MIC_RMS_ROLLING_WINDOW_S = 2.0  # CHANGED: rolling mic RMS window (fix 7)
MAX_AUDIO_QUEUE_SIZE = 100
MAX_TRANSCRIPT_HASH_HISTORY = 200  # CHANGED: prune dedup set size (fix 8)
FUZZY_DEDUP_JACCARD_THRESHOLD = 0.85  # CHANGED: fuzzy duplicate threshold (fix 8)
FUZZY_DEDUP_WINDOW_S = 3.0  # CHANGED: fuzzy duplicate time window (fix 8)
DG_KEEPALIVE_INTERVAL_S = 8.0  # CHANGED: WebSocket keepalive interval (fix 5/6)
DG_RECONNECT_BACKOFF_MAX_S = 30.0  # CHANGED: reconnect backoff cap (fix 5)
AUDIO_PROCESS_WARN_MS = 50
HEALTH_MONITOR_INTERVAL_MS = 5000

LANGUAGE_CONFIG = {
    "en": {
        "name": "English",
        "keyterms": ["Nova-3", "Alpha"],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
    "ja": {
        "name": "Japanese",
        "keyterms": [],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
    "zh-CN": {
        "name": "Chinese (Mandarin)",
        "keyterms": [],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
    "ru": {
        "name": "Russian",
        "keyterms": [],  # CHANGED: keyterm boosting (fix 11)
        "boost_phrases": [],
        "expected_wer": 0.03,
    },
}

LANGUAGE_MAP = {cfg["name"]: code for code, cfg in LANGUAGE_CONFIG.items()}

PLACEHOLDER_API_KEYS = {
    "your_deepgram_api_key_here",
    "your_deepl_api_key_here",
    "your_api_key_here",
    "replace_with_your_key",
    "paste_your_key_here",
}


def get_deepgram_key_status() -> str:
    """Return Deepgram key state: missing, placeholder, or configured."""
    key = (DEEPGRAM_API_KEY or "").strip()
    if not key:
        return "missing"
    if key.lower() in PLACEHOLDER_API_KEYS:
        return "placeholder"
    return "configured"


def has_deepgram_api_key() -> bool:
    """Return True when a real (non-placeholder) Deepgram API key is configured."""
    return get_deepgram_key_status() == "configured"


def has_deepl_api_key() -> bool:
    """Return True when a non-empty DeepL API key is configured."""
    return bool(DEEPL_API_KEY and DEEPL_API_KEY.strip())


__all__ = [
    "APP_VERSION",
    "APP_CODENAME",
    "PROJECT_ROOT",
    "ASSETS_DIR",
    "DEEPGRAM_API_KEY",
    "DEEPL_API_KEY",
    "DEEPL_API_PLAN",
    "DEEPL_TIMEOUT_SECONDS",
    "has_deepgram_api_key",
    "get_deepgram_key_status",
    "has_deepl_api_key",
    "PLACEHOLDER_API_KEYS",
    "DEEPGRAM_MODEL",
    "DEEPGRAM_SAMPLE_RATE",
    "LANGUAGE_CONFIG",
    "LANGUAGE_MAP",
]
