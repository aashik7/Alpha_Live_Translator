"""Item 50: give DeepL the preceding lines as `context`.

A meeting transcript is translated line by line, each line stripped of
everything said before it, so pronouns, honorifics and topic have nothing to
resolve against. DeepL's `context` parameter takes that background and neither
translates nor bills it.

The whole risk in this item is the SDK: passing an unknown keyword to a version
without `context` raises `TypeError`, and `translate_text`'s error mapper
classifies an unrecognised exception as `retryable=False` -- so a downgrade
would turn every translation into a permanent failure rather than degrading
quietly. Support is therefore detected from the signature, not assumed, and
these tests pin that.

The rest is bounds. Context grows for the length of a session and is sent on
every request, so it is capped by line count AND characters, and the buffer
behind it cannot grow with the session.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import (  # noqa: E402
    TRANSLATION_CONTEXT_LINES,
    TRANSLATION_CONTEXT_MAX_CHARS,
)
from alpha.translation.deepl_client import DeepLClient  # noqa: E402
from alpha.translation.translation_worker import TranslationWorker  # noqa: E402


def _worker():
    return TranslationWorker(enabled=False)


class ContextIsBuiltFromRecentLinesTest(unittest.TestCase):
    def test_no_context_before_anything_is_translated(self):
        self.assertEqual(_worker()._translation_context("EN"), "")

    def test_only_the_configured_number_of_lines(self):
        w = _worker()
        for i in range(10):
            w._remember_source_line("EN", f"Line {i}.")
        ctx = w._translation_context("EN")
        self.assertEqual(len(ctx.split(".")) - 1, TRANSLATION_CONTEXT_LINES)
        self.assertIn("Line 9.", ctx)
        self.assertNotIn("Line 5.", ctx)

    def test_other_languages_are_excluded(self):
        """A Japanese line is noise in the context of an English one."""
        w = _worker()
        w._remember_source_line("EN", "An English line.")
        w._remember_source_line("JA", "日本語の行です。")
        self.assertNotIn("日本語", w._translation_context("EN"))
        self.assertNotIn("English", w._translation_context("JA"))

    def test_character_cap_is_enforced(self):
        w = _worker()
        w._remember_source_line("EN", "X" * 5000)
        self.assertLessEqual(
            len(w._translation_context("EN")), TRANSLATION_CONTEXT_MAX_CHARS
        )

    def test_the_buffer_cannot_grow_with_the_session(self):
        w = _worker()
        for i in range(500):
            w._remember_source_line("EN", f"line {i}")
        self.assertLessEqual(len(w._recent_source_lines), TRANSLATION_CONTEXT_LINES * 4)

    def test_empty_lines_are_not_remembered(self):
        w = _worker()
        w._remember_source_line("EN", "   ")
        self.assertEqual(w._translation_context("EN"), "")

    def test_building_context_never_raises(self):
        w = _worker()
        w._recent_source_lines = None  # force the failure path
        self.assertEqual(w._translation_context("EN"), "")

    def test_remembering_never_raises(self):
        w = _worker()
        w._recent_source_lines = None
        w._remember_source_line("EN", "text")  # must not raise


class SdkSupportIsDetectedNotAssumedTest(unittest.TestCase):
    """The one way this item could break translation outright."""

    def setUp(self):
        if hasattr(DeepLClient, "_context_supported_cache"):
            del DeepLClient._context_supported_cache
        self.addCleanup(
            lambda: hasattr(DeepLClient, "_context_supported_cache")
            and delattr(DeepLClient, "_context_supported_cache")
        )

    def test_support_is_read_from_the_installed_signature(self):
        import inspect

        import deepl

        expected = "context" in inspect.signature(
            deepl.Translator.translate_text
        ).parameters
        self.assertEqual(DeepLClient._provider_supports_context(), expected)

    def test_the_installed_sdk_supports_it(self):
        """Documents the pinned version's capability; if this ever fails the
        context is simply omitted, not an error."""
        self.assertTrue(DeepLClient._provider_supports_context())

    def test_context_is_only_passed_when_supported(self):
        import inspect

        src = inspect.getsource(DeepLClient.translate_text)
        self.assertIn("_provider_supports_context()", src)
        self.assertIn('kwargs["context"]', src)

    def test_empty_context_is_omitted_entirely(self):
        """Behaviour must be byte-identical for callers that supply none."""
        import inspect

        src = inspect.getsource(DeepLClient.translate_text)
        self.assertIn("if hint and", src)


class WorkerPassesContextTest(unittest.TestCase):
    def test_the_translate_call_supplies_context(self):
        import inspect

        from alpha.translation import translation_worker

        src = inspect.getsource(translation_worker.TranslationWorker)
        self.assertIn("_client_accepts_context()", src)
        self.assertIn('translate_kwargs["context"]', src)

    def test_a_client_without_context_is_not_broken(self):
        """The regression this caught: five clients in this repo take only
        (text, source_lang, target_lang). Passing context to those raises
        TypeError inside the translate loop and DROPS the translation."""

        class OldClient:
            def translate_text(self, text, source_lang, target_lang):
                return "ok"

        w = _worker()
        w._client = OldClient()
        self.assertFalse(w._client_accepts_context())

    def test_a_client_with_context_is_offered_it(self):
        class NewClient:
            def translate_text(self, text, source_lang, target_lang, context=""):
                return "ok"

        w = _worker()
        w._client = NewClient()
        self.assertTrue(w._client_accepts_context())

    def test_a_client_taking_kwargs_is_offered_it(self):
        class KwargsClient:
            def translate_text(self, text, **kw):
                return "ok"

        w = _worker()
        w._client = KwargsClient()
        self.assertTrue(w._client_accepts_context())

    def test_a_line_is_remembered_only_after_success(self):
        """A line that never reached the provider must not pollute the next
        request's context."""
        import inspect

        from alpha.translation import translation_worker

        src = inspect.getsource(translation_worker.TranslationWorker)
        success = src.index('status = "success"')
        remember = src.index("_remember_source_line(job.source_language", success)
        self.assertGreater(remember, success)


if __name__ == "__main__":
    unittest.main()
