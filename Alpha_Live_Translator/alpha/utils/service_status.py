"""Credential preflight and connection status — sprint items 46 and 47.

`CLIENT_DELIVERY_SPRINT_v5.md` problem D: *"No behaviour defined for network
drop, DeepL quota exhaustion, device change, or invalid credentials."*

Both halves live here as **pure functions**, deliberately. The UI renders what
these return; it does not compute status itself. That keeps the rules testable
without a Tk display (`SKIP_TK_INTEGRATION_TESTS` is set in this suite) and
keeps one authority for "what state are we in" rather than the answer being
re-derived at each call site.

* **Item 46** — `preflight_credentials()` turns a missing, placeholder or
  rejected key into a sentence a non-technical user can act on, at Start,
  instead of a stack trace mid-session.
* **Item 47** — `describe_connection(...)` collapses the live signals into one
  of four states: connected / reconnecting / degraded / failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

CONNECTED = "connected"
RECONNECTING = "reconnecting"
DEGRADED = "degraded"
FAILED = "failed"

# Severity order matters: several signals are routinely true at once and the
# worst must win.
#
# `reconnecting` deliberately outranks `degraded`, which is not the order the
# sprint lists them in. `degraded` means translation is failing while the
# transcript keeps being produced; `reconnecting` means the transcript itself
# has stopped and audio is being lost. Losing the words is worse than losing
# the translation of them, so a reconnect must not be hidden behind a
# translation warning.
_SEVERITY = {CONNECTED: 0, DEGRADED: 1, RECONNECTING: 2, FAILED: 3}


@dataclass
class CredentialProblem:
    """One actionable credential problem, phrased for the person running the app."""

    service: str
    code: str
    message: str
    blocks_start: bool


@dataclass
class ConnectionStatus:
    """What the indicator should show right now."""

    state: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        return self.state == CONNECTED


def _worst(*states: str) -> str:
    return max(states, key=lambda s: _SEVERITY.get(s, 0))


def preflight_credentials(
    *,
    deepgram_status: Optional[str] = None,
    deepl_configured: Optional[bool] = None,
    translation_enabled: bool = True,
) -> list[CredentialProblem]:
    """Check both providers' credentials before a session starts (item 46).

    Returns an empty list when everything is usable. Arguments default to the
    real configuration; they are injectable so the rules can be tested without
    touching the environment.

    Deepgram is required — without it there is no transcript at all, so its
    problems set `blocks_start`. DeepL is not: a session with no translation is
    degraded, not broken, and refusing to start would be worse than running
    transcript-only. That asymmetry is the whole point of returning structured
    problems rather than raising.
    """
    if deepgram_status is None:
        from alpha.config import get_deepgram_key_status

        deepgram_status = get_deepgram_key_status()
    if deepl_configured is None:
        from alpha.config import has_deepl_api_key

        deepl_configured = has_deepl_api_key()

    problems: list[CredentialProblem] = []

    if deepgram_status == "missing":
        problems.append(
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_missing",
                message=(
                    "No Deepgram API key found. Set DEEPGRAM_API_KEY in your "
                    "environment or .env file, then start again. Without it "
                    "there is no transcription."
                ),
                blocks_start=True,
            )
        )
    elif deepgram_status == "placeholder":
        problems.append(
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_placeholder",
                message=(
                    "The Deepgram API key is still the example placeholder. "
                    "Replace it with your real key, then start again."
                ),
                blocks_start=True,
            )
        )

    if translation_enabled and not deepl_configured:
        problems.append(
            CredentialProblem(
                service="DeepL",
                code="deepl_key_missing",
                message=(
                    "No DeepL auth key found, so this session will transcribe "
                    "but not translate. Set DEEPL_AUTH_KEY to enable "
                    "translation."
                ),
                blocks_start=False,
            )
        )

    return problems


def blocking_problems(problems: list[CredentialProblem]) -> list[CredentialProblem]:
    return [p for p in problems if p.blocks_start]


def preflight_summary(problems: list[CredentialProblem]) -> str:
    """One human-readable line for the Start dialog."""
    if not problems:
        return "Credentials OK."
    blocking = blocking_problems(problems)
    if blocking:
        return blocking[0].message
    return problems[0].message


def describe_connection(
    *,
    listening: bool,
    deepgram_connected: bool,
    deepgram_reconnecting: bool = False,
    deepgram_auth_failed: bool = False,
    translation_degraded: bool = False,
    translation_status_message: str = "",
    gap_seconds: float = 0.0,
) -> ConnectionStatus:
    """Collapse the live signals into one indicator state (item 47).

    Ordering is severity-based rather than first-match, because more than one
    signal is routinely true at once — a rejected key while a reconnect is in
    flight is a *failure*, not a reconnect, and showing the milder of the two
    would tell the operator to wait for a recovery that cannot happen.

    `deepgram_auth_failed` is the runtime half of item 46: a key that was valid
    at Start and is later rejected (expired, revoked, quota-cancelled) must
    surface as a clear failed state, not an endless reconnect loop.
    """
    if not listening:
        return ConnectionStatus(
            state=CONNECTED,
            message="Idle.",
            detail={"listening": False},
        )

    state = CONNECTED
    message = "Connected."

    if translation_degraded:
        state = _worst(state, DEGRADED)
        message = translation_status_message or "Translation degraded."

    if deepgram_reconnecting or (listening and not deepgram_connected):
        candidate = RECONNECTING
        candidate_message = "Reconnecting to Deepgram…"
        if gap_seconds >= 1.0:
            candidate_message = (
                f"Reconnecting to Deepgram… {int(gap_seconds)}s of audio not captured."
            )
        if _SEVERITY[candidate] >= _SEVERITY[state]:
            state, message = candidate, candidate_message

    if deepgram_auth_failed:
        state = FAILED
        message = (
            "Deepgram rejected the API key. Transcription has stopped — check "
            "the key, then restart the session."
        )

    return ConnectionStatus(
        state=state,
        message=message,
        detail={
            "listening": listening,
            "deepgram_connected": deepgram_connected,
            "deepgram_reconnecting": deepgram_reconnecting,
            "deepgram_auth_failed": deepgram_auth_failed,
            "translation_degraded": translation_degraded,
            "gap_seconds": round(float(gap_seconds), 1),
        },
    )


__all__ = [
    "CONNECTED",
    "RECONNECTING",
    "DEGRADED",
    "FAILED",
    "CredentialProblem",
    "ConnectionStatus",
    "preflight_credentials",
    "blocking_problems",
    "preflight_summary",
    "describe_connection",
]
