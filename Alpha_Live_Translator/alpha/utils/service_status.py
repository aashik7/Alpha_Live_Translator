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

# --- Why a Start failed, in words (item 30) ------------------------------------
#
# The reason is decided by `deepgram_client.deepgram_start_refusal` from
# Deepgram's documented answers (developers.deepgram.com/docs/errors):
#
#   401 INVALID_AUTH              a wrong, deleted or EXPIRED key -- the answer
#                                 does not say which
#   401 INSUFFICIENT_PERMISSIONS  the key may not transcribe
#   402 ASR_PAYMENT_REQUIRED      no credit left -- the free trial run out
#   403 INSUFFICIENT_PERMISSIONS  "Project does not have access to the
#                                 requested model."
#   429 TOO_MANY_REQUESTS / 5xx   busy / down
#
# `UNREACHABLE` is the case with no answer at all: DNS, a dropped network, a
# firewall that swallows the connection.
#
# Every sentence is shown through `t()`, so each is ONE literal on ONE line,
# however long: item 88's test looks Japanese keys up verbatim in the source,
# and implicit concatenation is not verbatim.
REFUSAL_KEY_INVALID = "key_invalid"
REFUSAL_KEY_NO_PERMISSION = "key_no_permission"
REFUSAL_CREDIT_EXHAUSTED = "credit_exhausted"
REFUSAL_NO_MODEL_ACCESS = "no_model_access"
REFUSAL_RATE_LIMITED = "rate_limited"
REFUSAL_SERVICE_UNAVAILABLE = "service_unavailable"
REFUSAL_OTHER = "refused"
UNREACHABLE = "unreachable"

_START_FAILURE_TEXT = {
    REFUSAL_KEY_INVALID: (
        "Deepgram API key rejected",
        (
            "Deepgram rejected the API key, so listening could not start.",
            "The key may be mistyped, expired, deleted, or from a different account.",
        ),
    ),
    REFUSAL_KEY_NO_PERMISSION: (
        "Deepgram API key rejected",
        (
            "The Deepgram API key does not have permission to transcribe, so listening could not start.",
            "Create a new key at console.deepgram.com and use that instead.",
        ),
    ),
    REFUSAL_CREDIT_EXHAUSTED: (
        "Deepgram credit used up",
        (
            "Deepgram has no credit left for this account, so listening could not start.",
            "The free trial credit may have run out. Add credit or a payment method at console.deepgram.com, or use a key from an account that has credit.",
        ),
    ),
    REFUSAL_NO_MODEL_ACCESS: (
        "Deepgram model not available",
        (
            "This Deepgram project does not have access to the model Alpha uses (Nova-3), so listening could not start.",
            "Check the project's plan at console.deepgram.com, or use a key from another project.",
        ),
    ),
    REFUSAL_RATE_LIMITED: (
        "Deepgram is busy",
        (
            "Deepgram is receiving too many requests for this key right now, so listening could not start.",
            "Wait a minute, then press Start again. Close any other app that uses the same key.",
        ),
    ),
    REFUSAL_SERVICE_UNAVAILABLE: (
        "Deepgram is unavailable",
        (
            "Deepgram's service is having a problem right now, so listening could not start.",
            "Try again in a few minutes.",
        ),
    ),
    REFUSAL_OTHER: (
        "Deepgram refused the connection",
        ("Deepgram refused the connection, so listening could not start.",),
    ),
    UNREACHABLE: (
        "Cannot reach Deepgram",
        (
            "Alpha could not connect to Deepgram, so listening could not start.",
            "Check the internet connection. On a company network, a firewall or proxy may be blocking api.deepgram.com.",
        ),
    ),
}

# Refusals a different key can cure, so a keyless build offers its key dialog
# for them. Not the model, not a busy or broken service: re-entering a working
# key there sends the operator the wrong way.
KEY_DIALOG_REASONS = frozenset(
    {REFUSAL_KEY_INVALID, REFUSAL_KEY_NO_PERMISSION, REFUSAL_CREDIT_EXHAUSTED}
)

# Shown in place of the socket's own error when the connection simply never
# answered, which leaves nothing more specific to say.
NO_DEEPGRAM_RESPONSE_TEXT = "No response from Deepgram within 30 seconds."

# The failed state, mid-meeting. An already-open socket survives its key
# expiring (Deepgram's forum), so these arrive on the next reconnect.
MID_SESSION_KEY_REJECTED_TEXT = "Deepgram rejected the API key (it may have expired). Transcription has stopped — check the key, then restart the session."
MID_SESSION_CREDIT_EXHAUSTED_TEXT = "Deepgram has no credit left (the free trial may have run out), so transcription has stopped. Add credit at console.deepgram.com, then restart the session."


def start_failure_text(reason: str) -> tuple[str, tuple[str, ...]]:
    """The title and sentences for a failed Start, in English -- `t()` them to show."""
    return _START_FAILURE_TEXT.get(reason, _START_FAILURE_TEXT[REFUSAL_OTHER])


def key_dialog_hint(reason: str) -> str:
    """What to do next on a keyless build, or "" when a new key would not help."""
    if reason == REFUSAL_CREDIT_EXHAUSTED:
        return "To use a different Deepgram key, enter it in the next window."
    if reason in KEY_DIALOG_REASONS:
        return "Enter a valid Deepgram key in the next window."
    return ""


def env_file_hint(reason: str) -> str:
    """What to do next on a keyed build; the `.env` path is appended by the UI."""
    if reason == REFUSAL_CREDIT_EXHAUSTED:
        return "To use a different Deepgram key, put it on the DEEPGRAM_API_KEY line of this file, then close Alpha and start it again:"
    if reason in KEY_DIALOG_REASONS:
        return "Put a valid Deepgram key on the DEEPGRAM_API_KEY line of this file, then close Alpha and start it again:"
    return ""


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
    deepl_status: Optional[str] = None,
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
    if deepl_status is None:
        if deepl_configured is None:
            from alpha.config import get_deepl_key_status

            deepl_status = get_deepl_key_status()
        else:
            # Callers that predate `deepl_status` pass a bool. Keep their
            # meaning exactly: True is a usable key, False is an absent one.
            deepl_status = "configured" if deepl_configured else "missing"
    if deepl_configured is None:
        deepl_configured = deepl_status == "configured"

    problems: list[CredentialProblem] = []

    if deepgram_status == "missing":
        problems.append(
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_missing",
                # One literal on one line, so `t()` finds it (item 30).
                message="No Deepgram API key found. Set DEEPGRAM_API_KEY in your environment or .env file, then start again. Without it there is no transcription.",
                blocks_start=True,
            )
        )
    elif deepgram_status == "placeholder":
        problems.append(
            CredentialProblem(
                service="Deepgram",
                code="deepgram_key_placeholder",
                message="The Deepgram API key is still the example placeholder. Replace it with your real key, then start again.",
                blocks_start=True,
            )
        )

    if translation_enabled and deepl_status == "placeholder":
        problems.append(
            CredentialProblem(
                service="DeepL",
                code="deepl_key_placeholder",
                message=(
                    "The DeepL auth key is still the example placeholder, so "
                    "this session will transcribe but not translate. Replace "
                    "it with your real key in the .env file."
                ),
                # Not blocking, deliberately: the Deepgram/DeepL asymmetry
                # above is the point. A session without translation is
                # degraded, not broken.
                blocks_start=False,
            )
        )
    elif translation_enabled and not deepl_configured:
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
    deepgram_credit_exhausted: bool = False,
    translation_degraded: bool = False,
    translation_status_message: str = "",
    gap_seconds: float = 0.0,
    audio_device_changed: bool = False,
    audio_capture_device: str = "",
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

    # Item 73's detector, folded in rather than left to paint the indicator
    # itself. Windows moving the default output does not stop the socket, so
    # every signal above still reads healthy while the capture device goes on
    # recording a device nothing is routed to -- silence, with a green light.
    #
    # Ranked at `reconnecting` severity because the consequence is the same,
    # the transcript stops, and checked AFTER the reconnect branch so it wins a
    # tie: a reconnect resolves itself, whereas this one needs the operator to
    # do something, and telling them to wait would be wrong.
    if audio_device_changed:
        candidate = RECONNECTING
        # The advice is deliberately ordered "stop and start" FIRST, and the
        # captured device is NAMED, because the previous wording was misleading
        # in the field.
        #
        # Capture binds to whatever was default at Start and never follows a
        # change. Measured on the live runs of 2026-08-21: loopback goes to
        # EXACT digital silence (rms 0.0, 100% of samples) the moment the
        # default moves, and stays there for the rest of the session -- 85 s in
        # one run, 65 s in the other. "Switch back" therefore only helps if the
        # user switches back to the device THIS session bound, which the old
        # message never said. In the second run the user switched the default
        # to the device the FIRST session had used, which was exactly the wrong
        # move for the session that was actually running.
        device = (audio_capture_device or "").strip()
        if device:
            candidate_message = (
                "Windows changed the default audio output. This session "
                f"captures “{device}” and cannot follow the change, so nothing "
                "is being recorded now. Stop and start the session to capture "
                f"the new device, or make “{device}” the default again."
            )
        else:
            candidate_message = (
                "Windows changed the default audio output. This session cannot "
                "follow the change, so nothing is being recorded now. Stop and "
                "start the session to capture the new device, or make the "
                "previous device the default again."
            )
        if _SEVERITY[candidate] >= _SEVERITY[state]:
            state, message = candidate, candidate_message

    # Item 30. A 402 on reconnect was not recognised at all, so the indicator
    # read "Reconnecting" for the rest of the meeting -- a wait for a recovery
    # that only a top-up can bring. Failed, like a rejected key, and checked
    # BEFORE it so a key problem keeps the last word: the two cannot come from
    # one answer, and the key is the more fundamental of the two.
    if deepgram_credit_exhausted:
        state = FAILED
        message = MID_SESSION_CREDIT_EXHAUSTED_TEXT

    if deepgram_auth_failed:
        state = FAILED
        message = MID_SESSION_KEY_REJECTED_TEXT

    return ConnectionStatus(
        state=state,
        message=message,
        detail={
            "listening": listening,
            "deepgram_connected": deepgram_connected,
            "deepgram_reconnecting": deepgram_reconnecting,
            "deepgram_auth_failed": deepgram_auth_failed,
            "deepgram_credit_exhausted": deepgram_credit_exhausted,
            "translation_degraded": translation_degraded,
            "audio_device_changed": audio_device_changed,
            "audio_capture_device": audio_capture_device,
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
    "REFUSAL_KEY_INVALID",
    "REFUSAL_KEY_NO_PERMISSION",
    "REFUSAL_CREDIT_EXHAUSTED",
    "REFUSAL_NO_MODEL_ACCESS",
    "REFUSAL_RATE_LIMITED",
    "REFUSAL_SERVICE_UNAVAILABLE",
    "REFUSAL_OTHER",
    "UNREACHABLE",
    "KEY_DIALOG_REASONS",
    "NO_DEEPGRAM_RESPONSE_TEXT",
    "MID_SESSION_KEY_REJECTED_TEXT",
    "MID_SESSION_CREDIT_EXHAUSTED_TEXT",
    "start_failure_text",
    "key_dialog_hint",
    "env_file_hint",
]
