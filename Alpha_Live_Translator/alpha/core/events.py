"""Application event type constants for the EventBus."""

from enum import Enum


class EventType(str, Enum):
    """Structured event identifiers for backend ↔ UI communication."""

    APP_STATUS_CHANGED = "app_status_changed"
    LISTENING_STARTED = "listening_started"
    LISTENING_STOPPED = "listening_stopped"
    TRANSCRIPT_RECEIVED = "transcript_received"
    TRANSLATION_STARTED = "translation_started"
    TRANSLATION_RECEIVED = "translation_received"
    TRANSLATION_ERROR = "translation_error"
    SPEAKER_DETECTED = "speaker_detected"
    ERROR_OCCURRED = "error_occurred"
    SUMMARY_UPDATED = "summary_updated"
