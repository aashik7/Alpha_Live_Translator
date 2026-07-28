"""Abstract language pipeline contract — Tk-free, UIEventBus output only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LanguagePipelineBase(ABC):
    """Contract for Japanese / English / Chinese / future pipelines."""

    @abstractmethod
    def ingest_raw_final(self, raw_event: dict[str, Any]) -> None:
        """Ingest a final STT segment from a background thread."""

    @abstractmethod
    def ingest_interim(self, raw_event: dict[str, Any]) -> None:
        """Ingest interim STT (optional)."""

    @abstractmethod
    def request_flush(self, reason: str) -> None:
        """Request buffered text flush without Tk scheduling."""

    @abstractmethod
    def stop_flush(self) -> None:
        """Flush on stop — no Tk calls."""

    @abstractmethod
    def get_snapshot_nonblocking(self) -> dict[str, Any]:
        """UI-safe snapshot; must not block on lock."""

    @abstractmethod
    def emit_events_to_ui_bus(self) -> None:
        """Post pending UI events after lock release."""
