"""A segment in the wrong language must be flagged, not silently committed.

WHAT WAS BROKEN
---------------
`_evaluate_language_reliability` was a stub:

    return {
        "decision": "commit",
        "reason": "language_gate_disabled",
        "allowed_languages": ["en"],      # hardcoded, ignoring the caller
        "script_warning": None,           # never computed
        ...
    }

Ninety-six varied inputs collapsed to one output tuple. Its single caller used
the result only as logging arguments and never branched, so a segment detected
as Bengali at 0.99 confidence while the operator had selected Japanese was
committed to the transcript and forwarded to DeepL exactly like a clean one.

The four `LANGUAGE_*` constants are not the cause and are not touched here.
`LANGUAGE_GATE_BLOCKING_MODE` has zero readers, `LANGUAGE_GATE_WARNING_ONLY`
has one that only writes it into a startup diagnostic, and the two ENABLED
flags each have exactly one control-flow reader where False is a no-op. The
decision point was the stub.

A working detector, `_language_script_warning`, was already sitting two methods
above it with **zero call sites**, and so were `_log_language_commit_warning`
and `_hold_unstable_language_candidate`.

THE FIX
-------
Un-stub the evaluator using the code already beside it, and branch once at the
call site. Warn-only, deliberately: the manual dropdown stays authoritative and
nothing is ever dropped. The decision recorded in `constants.py` was to stop
FORCING a language after an English-selection regression -- not to stop warning.
The warning went with it by accident.

WHAT THESE TESTS PIN
--------------------
* a detected language outside the selected profile produces `decision="warn"`
* a matching one still produces `decision="commit"` -- a gate that cries wolf
  gets switched off
* the caller's `allowed_languages` is honoured rather than the hardcoded `["en"]`
* the warning is counted, so the session summary stops reporting a hardcoded zero
* nothing is ever dropped or held: `warn` still commits
"""

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.ui.main_window import AlphaApp  # noqa: E402


class Host:
    """Borrows the two real methods and nothing else."""

    _evaluate_language_reliability = AlphaApp._evaluate_language_reliability
    _language_script_warning = AlphaApp._language_script_warning
    _normalize_lang_code = AlphaApp._normalize_lang_code

    def __init__(self):
        self._language_stats = {
            "stable_commit_count": 0,
            "warning_commit_count": 0,
            "unexpected_language_warning_count": 0,
            "low_confidence_warning_count": 0,
            "missing_metadata_count": 0,
        }


def meta(detected, allowed, confidence=0.99):
    return {
        "detected_language": detected,
        "language_confidence": confidence,
        "allowed_languages": list(allowed),
        "selected_profile": "Japanese",
    }


class TheEvaluatorIsNotAStubTest(unittest.TestCase):
    def setUp(self):
        self.host = Host()

    def _eval(self, text, detected, allowed):
        return self.host._evaluate_language_reliability(
            text=text,
            metadata=meta(detected, allowed),
            selected_profile="Japanese",
        )

    def test_a_language_outside_the_profile_warns(self):
        r = self._eval("এটি বাংলা বাক্য", "bn", ["ja"])
        self.assertEqual(
            r["decision"],
            "warn",
            "Bengali at 0.99 confidence while Japanese is selected was "
            "committed with no signal at all",
        )
        self.assertTrue(r.get("script_warning"), "no reason was given for the warning")

    def test_a_matching_language_still_commits(self):
        r = self._eval("本日はよろしくお願いします", "ja", ["ja", "en"])
        self.assertEqual(
            r["decision"], "commit", "a gate that cries wolf gets switched off"
        )
        self.assertIsNone(r.get("script_warning"))

    def test_english_under_an_english_profile_still_commits(self):
        r = self._eval("Good morning everyone", "en", ["en", "ja"])
        self.assertEqual(r["decision"], "commit")

    def test_the_callers_allowed_languages_are_honoured(self):
        """The stub hardcoded ['en'], so the caller's profile was ignored."""
        r = self._eval("本日はよろしくお願いします", "ja", ["ja"])
        self.assertEqual(
            list(r["allowed_languages"]),
            ["ja"],
            "the evaluator is still reporting a hardcoded allow-list",
        )

    def test_the_evaluator_is_not_a_constant(self):
        """The measurement that exposed the stub: many inputs, one output."""
        seen = set()
        for detected in ("ja", "en", "bn", "zh", None):
            for allowed in (["ja"], ["en"], ["ja", "en"]):
                for text in ("本日は", "Good morning", "এটি বাংলা"):
                    r = self._eval(text, detected, allowed)
                    seen.add((r["decision"], r.get("script_warning")))
        self.assertGreater(
            len(seen),
            1,
            "45 varied inputs still collapse to one output -- the evaluator "
            "is a constant",
        )

    def test_a_warning_never_drops_or_holds_the_segment(self):
        """Warn-only is the whole contract. Nothing may be lost."""
        r = self._eval("এটি বাংলা বাক্য", "bn", ["ja"])
        self.assertNotIn(r["decision"], ("drop", "hold", "block", "reject"))


class TheCallerActsOnTheDecisionTest(unittest.TestCase):
    """Walked with the AST: this file and the module both quote the decision
    names in prose, so matching raw text would find the explanation."""

    SRC = PROJECT_ROOT / "alpha" / "ui" / "main_window.py"

    def _tree(self):
        return ast.parse(self.SRC.read_text(encoding="utf-8"))

    def test_some_caller_branches_on_a_warn_decision(self):
        tree = self._tree()
        found = False
        for n in ast.walk(tree):
            if not isinstance(n, ast.If):
                continue
            test = ast.unparse(n.test)
            if "reliability" in test and "warn" in test:
                found = True
        self.assertTrue(
            found,
            "the evaluator's decision is still used only as a logging "
            "argument -- nothing branches on it",
        )

    def test_the_session_summary_no_longer_hardcodes_zero_blocked(self):
        tree = self._tree()
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_log_language_summary"
        )
        for n in ast.walk(fn):
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "blocked_count"
                        and isinstance(v, ast.Constant)
                    ):
                        self.fail(
                            "the summary still reports a hardcoded "
                            "blocked_count, so a post-mortem reading it "
                            "concludes nothing happened"
                        )

    def test_the_warning_logger_is_no_longer_dead_code(self):
        tree = self._tree()
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "_log_language_commit_warning"
        ]
        self.assertTrue(
            calls,
            "_log_language_commit_warning still has zero call sites -- the "
            "warning is computed and then thrown away",
        )


if __name__ == "__main__":
    unittest.main()
