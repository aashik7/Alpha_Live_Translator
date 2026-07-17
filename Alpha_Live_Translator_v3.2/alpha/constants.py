"""Application-wide constants (non-secret)."""

APP_VERSION = "3.2.8"
APP_CODENAME = "Audio Format Alignment"

# Deepgram linear16 mono 16 kHz stream expectations
DEEPGRAM_BYTES_PER_SECOND = 32000  # 16000 samples/s * 2 bytes
DEEPGRAM_EXPECTED_KBPS = 256
DEEPGRAM_KBPS_MIN = 230
DEEPGRAM_KBPS_MAX = 290

COMPACT_BREAKPOINT = 900

SOURCE_LANGUAGES = [
    "English",
    "Japanese",
    "Chinese (Mandarin)",
    "Russian",
]

TARGET_LANGUAGES = [
    "Japanese",
    "Chinese (Mandarin)",
    "Russian",
    "English",
]
