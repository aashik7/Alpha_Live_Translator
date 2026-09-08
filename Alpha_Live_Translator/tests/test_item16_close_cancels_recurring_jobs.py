"""Item 16: the close path must cancel the four self-rescheduling `after` jobs.

`main_window.py` schedules 31 `after`/`after_idle` calls; 16 store the id so it
can be cancelled. Of the 13 stored job attributes, four were never passed to
`after_cancel` anywhere in the module, and `_on_close` contained no
`after_cancel` call at all:

    _jp_pipeline_hb_after_id
    _transcript_ui_batch_after_id
    _ui_event_bus_after_id
    _ui_queue_defer_after_id

All four are self-rescheduling loops, so each keeps re-arming until the
interpreter goes away. The observable form is the
`invalid command name "..." while executing ("after" script)` noise this
project's own test runs produce on teardown.

WHY THIS IS NOT FOLDED INTO `_stop_ui_loops`
--------------------------------------------
`_stop_ui_loops` runs at every SESSION stop, not only at close.
`_ui_event_bus_after_id` is app-lifetime -- it is armed by
`_start_ui_event_bus_drain_loop` from `_deferred_post_show_init`, not from
Start -- so cancelling it there would kill the UI event bus drain for the rest
of the run. The cancellation therefore belongs on the close path only.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

JOB_ATTRS = (
    "_jp_pipeline_hb_after_id",
    "_transcript_ui_batch_after_id",
    "_ui_event_bus_after_id",
    "_ui_queue_defer_after_id",
)


def _host_with_all_four_armed():
    """A stub carrying the real method, with every job id armed."""
    import alpha.ui.main_window as mw

    class Host:
        _cancel_recurring_ui_jobs = mw.AlphaApp._cancel_recurring_ui_jobs

        def __init__(self):
            self.cancelled: list[str] = []
            for index, attr in enumerate(JOB_ATTRS):
                setattr(self, attr, f"after#{index}")

        def after_cancel(self, job_id):
            self.cancelled.append(str(job_id))

    return Host()


class TheCloseCancellationCancelsAllFour(unittest.TestCase):
    def setUp(self):
        self.host = _host_with_all_four_armed()
        self.host._cancel_recurring_ui_jobs()

    def test_every_armed_job_is_cancelled(self):
        self.assertEqual(
            sorted(self.host.cancelled),
            sorted(f"after#{i}" for i in range(len(JOB_ATTRS))),
            "not every recurring job id reached after_cancel",
        )

    def test_each_attribute_is_cleared(self):
        """A stale id left behind is a cancellation that cannot be repeated."""
        for attr in JOB_ATTRS:
            self.assertIsNone(
                getattr(self.host, attr), f"{attr} still holds a stale job id"
            )


class ItSurvivesTheStatesCloseActuallySees(unittest.TestCase):
    """Close runs on half-initialised and already-torn-down windows too."""

    def test_a_host_with_no_job_attributes_at_all(self):
        import alpha.ui.main_window as mw

        class Bare:
            _cancel_recurring_ui_jobs = mw.AlphaApp._cancel_recurring_ui_jobs

            def after_cancel(self, job_id):
                raise AssertionError("nothing was armed; nothing should be cancelled")

        Bare()._cancel_recurring_ui_jobs()

    def test_a_job_id_that_tk_already_forgot(self):
        """`after_cancel` on a fired id raises; that must not abort the close."""
        import alpha.ui.main_window as mw

        class Angry:
            _cancel_recurring_ui_jobs = mw.AlphaApp._cancel_recurring_ui_jobs

            def __init__(self):
                self.seen = 0
                for attr in JOB_ATTRS:
                    setattr(self, attr, "stale")

            def after_cancel(self, job_id):
                self.seen += 1
                raise RuntimeError('invalid command name "after#0"')

        host = Angry()
        host._cancel_recurring_ui_jobs()
        self.assertEqual(host.seen, len(JOB_ATTRS), "it stopped at the first raise")
        for attr in JOB_ATTRS:
            self.assertIsNone(getattr(host, attr))


class TheCancellationIsActuallyWiredIntoTheClosePath(unittest.TestCase):
    """This project has shipped eight rules with no caller. Not a ninth."""

    def _source(self):
        return (PROJECT_ROOT / "alpha" / "ui" / "main_window.py").read_text(
            encoding="utf-8", errors="replace"
        )

    def test_the_teardown_funnel_calls_it(self):
        import ast

        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_shutdown_and_destroy"
            ):
                body = ast.unparse(node)
                self.assertIn(
                    "_cancel_recurring_ui_jobs",
                    body,
                    "_shutdown_and_destroy does not cancel the recurring jobs",
                )
                return
        self.fail("_shutdown_and_destroy not found")

    def test_the_early_destroy_path_calls_it_too(self):
        """`_on_close` destroys directly when a close is already pending."""
        import ast

        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_on_close":
                body = ast.unparse(node)
                self.assertIn(
                    "_cancel_recurring_ui_jobs",
                    body,
                    "the early destroy path in _on_close cancels nothing",
                )
                return
        self.fail("_on_close not found")


if __name__ == "__main__":
    unittest.main()
