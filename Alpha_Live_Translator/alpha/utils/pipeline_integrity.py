"""Pipeline integrity errors for fail-closed transcript handling (V25.3.2)."""

from __future__ import annotations


class PipelineIntegrityError(RuntimeError):
    """Raised when applied transcript action contradicts downstream metadata or export."""
