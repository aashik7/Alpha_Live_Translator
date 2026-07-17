"""Runtime configuration, Deepgram settings, and API credentials."""

import os
from pathlib import Path

from alpha.constants import APP_CODENAME, APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

# Env var overrides default (same default as legacy parent main.py)
DEEPGRAM_API_KEY = os.getenv(
    "DEEPGRAM_API_KEY",
    "77dc0b90793074722dcae7875198e9cb24a68838",
)

DEEPGRAM_MODEL = "nova-3"
DEEPGRAM_SAMPLE_RATE = 16000
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

__all__ = [
    "APP_VERSION",
    "APP_CODENAME",
    "PROJECT_ROOT",
    "ASSETS_DIR",
    "DEEPGRAM_API_KEY",
    "DEEPGRAM_MODEL",
    "DEEPGRAM_SAMPLE_RATE",
    "LANGUAGE_CONFIG",
    "LANGUAGE_MAP",
]
