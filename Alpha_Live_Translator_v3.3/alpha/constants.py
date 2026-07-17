"""Application-wide constants (non-secret)."""

APP_VERSION = "3.3.4.5"
APP_CODENAME = "Hard Restore English Pipeline"

# When True, verbose NDJSON diagnostics ([LATENCY], [AUDIO_FORMAT], [INTERIM], etc.)
DEBUG_DIAGNOSTICS = False

# When True, Teams-focused quality diagnostics ([TEAMS_DIAG], etc.)
DEBUG_TEAMS_DIAGNOSTICS = True

# Teams source gate thresholds (RMS scale matches existing mixer readings)
MIC_ACTIVE_RMS_MIN = 80.0
SYSTEM_ACTIVE_RMS_MIN = 80.0
MIC_NOISE_MULTIPLIER = 4.0
SYSTEM_NOISE_MULTIPLIER = 3.0
MIC_TO_SYSTEM_RATIO_MIN = 0.08
OVERLAP_CONFIRM_FRAMES = 3
SOURCE_HOLD_MS = 500

# Meeting segment buffer (disabled in restore mode)
MEETING_SEGMENT_BUFFER_ENABLED = False
MEETING_BUFFER_MAX_GAP_MS = 1500
MEETING_BUFFER_MAX_HOLD_MS = 2500
MEETING_BUFFER_MIN_FRAGMENT_WORDS = 3
MEETING_BUFFER_FLUSH_ON_SPEAKER_CHANGE = True
MEETING_BUFFER_FLUSH_ON_SOURCE_CHANGE = True
MEETING_BUFFER_ENABLE_OVERLAP_MERGE = True

# Retrospective segment repair
MEETING_SEGMENT_REPAIR_ENABLED = False
MEETING_SEGMENT_REPAIR_MAX_GAP_MS = 6000

# Auto-language gate (disabled in V3.3.4.4 restore mode)
AUTO_LANGUAGE_ENABLED = False
DEFAULT_SOURCE_LANGUAGE = "en"
FORCE_DEEPGRAM_LANGUAGE = None
LANGUAGE_CONFIDENCE_SAFE = 0.70
LANGUAGE_CONFIDENCE_UNSTABLE = 0.45
LANGUAGE_CONFIDENCE_REJECT = 0.45
LANGUAGE_GATE_ENABLED = False
LANGUAGE_GATE_BLOCKING_MODE = False
LANGUAGE_GATE_WARNING_ONLY = False

# UI label retained for compatibility, hidden in restore mode
AUTO_SOURCE_LANGUAGE_UI = "Auto: English + Japanese"

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
