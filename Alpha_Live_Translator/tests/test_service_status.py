"""Tests for `alpha/utils/service_status.py` — sprint items 46 and 47.

Item 46: a bad credential must become a sentence the operator can act on, at
Start, rather than a stack trace mid-session — and a Deepgram problem must
block Start while a DeepL one must not, because a session with no translation
is degraded rather than broken.

Item 47: the four-state indicator. The rules are severity-ordered rather than
first-match on purpose, because several signals are routinely true at once;
`SeverityOrderingTest` is the part that would silently rot if someone
reordered the checks.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.utils.service_status import (  # noqa: E402
    CONNECTED,
    DEGRADED,
    FAILED,
    RECONNECTING,
    blocking_problems,
    describe_connection,
    preflight_credentials,
    preflight_summary,
)


class CredentialPreflightTest(unittest.TestCase):
    """Item 46."""

    def test_everything_configured_reports_no_problems(self):
        problems = preflight_credentials(
            deepgram_status="configured", deepl_configured=True
        )
        self.assertEqual([], problems)
        self.assertEqual("Credentials OK.", preflight_summary(problems))

    def test_missing_deepgram_key_blocks_start_with_an_actionable_message(self):
        problems = preflight_credentials(
            deepgram_status="missing", deepl_configured=True
        )
        self.assertEqual(1, len(problems))
        self.assertTrue(problems[0].blocks_start)
        self.assertIn("DEEPGRAM_API_KEY", problems[0].message)
        self.assertNotIn("Traceback", problems[0].message)

    def test_placeholder_key_is_distinguished_from_a_missing_one(self):
        """They need different advice: one is 'set a key', the other is
        'you left the example in'."""
        problems = preflight_credentials(
            deepgram_status="placeholder", deepl_configured=True
        )
        self.assertEqual("deepgram_key_placeholder", problems[0].code)
        self.assertIn("placeholder", problems[0].message.lower())
        self.assertTrue(problems[0].blocks_start)

    def test_missing_deepl_key_does_not_block_start(self):
        """Transcript-only is degraded, not broken. Refusing to start would be
        worse than running without translation."""
        problems = preflight_credentials(
            deepgram_status="configured", deepl_configured=False
        )
        self.assertEqual(1, len(problems))
        self.assertFalse(problems[0].blocks_start)
        self.assertEqual([], blocking_problems(problems))

    def test_deepl_is_not_flagged_when_translation_is_switched_off(self):
        problems = preflight_credentials(
            deepgram_status="configured",
            deepl_configured=False,
            translation_enabled=False,
        )
        self.assertEqual([], problems)

    def test_the_blocking_problem_wins_the_summary_line(self):
        problems = preflight_credentials(
            deepgram_status="missing", deepl_configured=False
        )
        self.assertEqual(2, len(problems))
        self.assertIn("Deepgram", preflight_summary(problems))


class ConnectionStateTest(unittest.TestCase):
    """Item 47."""

    def test_not_listening_is_idle_not_an_error(self):
        status = describe_connection(listening=False, deepgram_connected=False)
        self.assertEqual(CONNECTED, status.state)
        self.assertTrue(status.is_healthy)

    def test_healthy_session_is_connected(self):
        status = describe_connection(listening=True, deepgram_connected=True)
        self.assertEqual(CONNECTED, status.state)

    def test_reconnecting_is_reported(self):
        status = describe_connection(
            listening=True, deepgram_connected=False, deepgram_reconnecting=True
        )
        self.assertEqual(RECONNECTING, status.state)

    def test_listening_but_disconnected_counts_as_reconnecting(self):
        status = describe_connection(listening=True, deepgram_connected=False)
        self.assertEqual(RECONNECTING, status.state)

    def test_reconnect_message_carries_the_lost_audio_duration(self):
        status = describe_connection(
            listening=True,
            deepgram_connected=False,
            deepgram_reconnecting=True,
            gap_seconds=14.0,
        )
        self.assertIn("14", status.message)

    def test_translation_trouble_is_degraded_not_failed(self):
        """The transcript is still being produced."""
        status = describe_connection(
            listening=True,
            deepgram_connected=True,
            translation_degraded=True,
            translation_status_message="Translation degraded (provider failing).",
        )
        self.assertEqual(DEGRADED, status.state)
        self.assertIn("degraded", status.message.lower())

    def test_rejected_key_mid_session_is_failed_with_advice(self):
        """The runtime half of item 46 — a key valid at Start and later
        revoked must not present as an endless reconnect."""
        status = describe_connection(
            listening=True, deepgram_connected=False, deepgram_auth_failed=True
        )
        self.assertEqual(FAILED, status.state)
        self.assertIn("key", status.message.lower())


class SeverityOrderingTest(unittest.TestCase):
    """Several signals are routinely true at once; the worst must win."""

    def test_auth_failure_outranks_reconnecting(self):
        status = describe_connection(
            listening=True,
            deepgram_connected=False,
            deepgram_reconnecting=True,
            deepgram_auth_failed=True,
        )
        self.assertEqual(
            FAILED,
            status.state,
            "a rejected key shown as 'reconnecting' tells the operator to wait "
            "for a recovery that cannot happen",
        )

    def test_reconnecting_outranks_translation_degraded(self):
        status = describe_connection(
            listening=True,
            deepgram_connected=False,
            deepgram_reconnecting=True,
            translation_degraded=True,
        )
        self.assertEqual(RECONNECTING, status.state)

    def test_auth_failure_outranks_everything(self):
        status = describe_connection(
            listening=True,
            deepgram_connected=False,
            deepgram_reconnecting=True,
            deepgram_auth_failed=True,
            translation_degraded=True,
        )
        self.assertEqual(FAILED, status.state)

    def test_detail_reports_every_signal_for_diagnosis(self):
        status = describe_connection(
            listening=True, deepgram_connected=True, translation_degraded=True
        )
        for key in (
            "listening",
            "deepgram_connected",
            "deepgram_reconnecting",
            "deepgram_auth_failed",
            "translation_degraded",
            "gap_seconds",
        ):
            self.assertIn(key, status.detail)


if __name__ == "__main__":
    unittest.main()
