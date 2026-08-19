"""Item 74(c): the batch flush must be bounded by TIME, not only by item count.

WHAT WAS MISSING
----------------
`_flush_transcript_ui_batch` capped work at `max_inserts` (8, or 12 under
backpressure) and captured `start = time.perf_counter()`, but never read that
clock inside the loop -- only afterwards, to report `duration_ms`. So the cap
bounded HOW MANY items were rendered, never how long the tick took. A rise in
per-item cost turned into an unbounded freeze rather than a deferral, and the
backpressure branch makes it worse by raising the drain's `max_per_poll` to 24
exactly when the queue is already deep.

MEASURED, SO THE SEVERITY IS HONEST
-----------------------------------
This is LATENT today, not live. The real per-segment widget insert is
**0.009 ms**, so 8 items cost about 0.07 ms against a 10 ms budget -- roughly
2700x of headroom. It starts to bite around 25 ms per item, where 8 items
exceed the 200 ms flush interval and the buffer stops draining. A per-entry
widget model lands squarely in that region: item 75's measurement was 35.6 ms
per card.

WHAT THIS FILE PINS
-------------------
That the budget is enforced, that nothing is dropped when it fires, and that
ordering survives -- using an artificially slow renderer, because at the real
cost the branch is unreachable.
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import UI_QUEUE_TIME_BUDGET_MS  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402


class _Host:
    """Borrows the real flush; renders through a caller-supplied stub."""

    _flush_transcript_ui_batch = AlphaApp._flush_transcript_ui_batch

    def __init__(self, per_item_ms=0.0):
        self._transcript_ui_batch_buffer = []
        self._transcript_ui_batch_after_id = None
        self._transcript_ui_last_flush_mono = 0.0
        self._ui_queue_backpressure_active = False
        self.rendered = []
        self._per_item_s = per_item_ms / 1000.0
        self.reschedules = 0

    def _display_transcript_item(self, item):
        if self._per_item_s:
            end = time.perf_counter() + self._per_item_s
            while time.perf_counter() < end:
                pass
        self.rendered.append(item.get("text"))
        return None

    def _schedule_transcript_ui_batch_flush(self):
        self.reschedules += 1


def _items(n):
    return [{"text": f"item-{i}"} for i in range(n)]


class FlushTimeBudgetTest(unittest.TestCase):
    def test_a_slow_renderer_stops_at_the_budget(self):
        host = _Host(per_item_ms=UI_QUEUE_TIME_BUDGET_MS)
        host._transcript_ui_batch_buffer = _items(8)
        host._flush_transcript_ui_batch(force=True)
        self.assertLess(
            len(host.rendered),
            8,
            "the whole batch rendered despite each item costing a full budget",
        )
        self.assertGreaterEqual(len(host.rendered), 1, "nothing rendered at all")

    def test_deferred_items_are_kept_not_dropped(self):
        host = _Host(per_item_ms=UI_QUEUE_TIME_BUDGET_MS)
        host._transcript_ui_batch_buffer = _items(8)
        host._flush_transcript_ui_batch(force=True)
        recovered = host.rendered + [i["text"] for i in host._transcript_ui_batch_buffer]
        self.assertEqual(
            recovered,
            [f"item-{i}" for i in range(8)],
            "an item was lost or reordered by the time budget",
        )

    def test_a_deferral_is_rescheduled(self):
        host = _Host(per_item_ms=UI_QUEUE_TIME_BUDGET_MS)
        host._transcript_ui_batch_buffer = _items(8)
        host._flush_transcript_ui_batch(force=True)
        self.assertGreater(host.reschedules, 0, "deferred work was never rescheduled")

    def test_a_deferral_is_logged_never_silent(self):
        logged = []
        host = _Host(per_item_ms=UI_QUEUE_TIME_BUDGET_MS)
        host._transcript_ui_batch_buffer = _items(8)
        with patch(
            "alpha.utils.japanese_accuracy_log.jp_accuracy_log",
            side_effect=lambda event, **kw: logged.append(event),
        ):
            host._flush_transcript_ui_batch(force=True)
        self.assertIn("TRANSCRIPT_UI_FLUSH_TIME_BUDGET_EXCEEDED", logged)

    def test_the_fast_path_is_unaffected(self):
        """At the real per-item cost the budget branch must never fire, or this
        change would alter behaviour it was not meant to touch."""
        host = _Host(per_item_ms=0.0)
        host._transcript_ui_batch_buffer = _items(8)
        host._flush_transcript_ui_batch(force=True)
        self.assertEqual(host.rendered, [f"item-{i}" for i in range(8)])
        self.assertEqual(host._transcript_ui_batch_buffer, [])

    def test_over_cap_items_still_defer_normally(self):
        host = _Host(per_item_ms=0.0)
        host._transcript_ui_batch_buffer = _items(20)
        host._flush_transcript_ui_batch(force=True)
        self.assertEqual(len(host.rendered), 8)
        self.assertEqual(len(host._transcript_ui_batch_buffer), 12)

    def test_at_least_one_item_always_makes_progress(self):
        """A single item slower than the whole budget must still be rendered,
        or a slow item would wedge the buffer forever."""
        host = _Host(per_item_ms=UI_QUEUE_TIME_BUDGET_MS * 3)
        host._transcript_ui_batch_buffer = _items(4)
        host._flush_transcript_ui_batch(force=True)
        self.assertEqual(len(host.rendered), 1)
        self.assertEqual(len(host._transcript_ui_batch_buffer), 3)


if __name__ == "__main__":
    unittest.main()
