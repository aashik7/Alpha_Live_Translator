"""Item 94 - the commit gate's escape condition was unreachable in production.

WHAT HAPPENED LIVE
------------------
Run ``v3.3.5.5.8.5.26.5.3-20260826-190152`` (client machine, ja -> en, 37.6
minutes). At 19:22:52.773 the assembler logged **one**::

    ASSEMBLER_COMMIT_GATE_FAILED  failure_reason="missing_exact_revision_target"

From 19:22:54 to the end of the run it logged ``ASSEMBLER_COMMIT_GATE_FAILED_REJECT``
**85 times**, every one carrying the same ``gate_utterance_id``
(``jp-utt-89bc96734833``). Deepgram finals kept arriving and were still
``accepted_by_gate: true`` at 19:39:23, so audio/ASR/network/DeepL were all
healthy - 16.5 minutes of transcript and every translation derived from it were
discarded one segment at a time. Run ``...-195613`` died the same way after
1.5 minutes, so the "20 minutes" in the report is not a threshold; the trap is
reachable at any point.

``ASSEMBLER_COMMIT_GATE_CLEARED_NEW_UTTERANCE`` appears **zero** times across
all five recorded runs. The escape had never once executed.

WHY THE ESCAPE COULD NOT FIRE
-----------------------------
Item 60 replaced a latch-until-``reset()`` with a latch-until-the-utterance-changes::

    if gate_utterance and current_utterance == gate_utterance:
        return                                   # _publish_sentence

The only site that mints a new ``_current_canonical_utterance_id`` is
``self._current_canonical_utterance_id = f"jp-utt-{uuid4().hex[:12]}"``, and it
sits *below* that ``return`` in the same method. So clearing the gate required a
new utterance id, and producing a new utterance id required getting past the
gate. Circular: the id never changed, the gate never cleared.

WHY THE ITEM 60 TEST WENT GREEN OVER IT
---------------------------------------
``test_a_new_utterance_clears_the_gate`` hand-assigned
``_current_canonical_utterance_id = "jp-utt-fresh"`` while the gate held
``"jp-utt-broken"`` - it fabricated the precondition that production cannot
produce. The tests below never set the two ids apart by hand; they set the gate
exactly as the production failure site sets it (``gate id == current id``) and
require commits to resume anyway.

WHAT ALSO HAD TO CHANGE
-----------------------
``missing_exact_revision_target`` is a *proposal* rejection ("I wanted to revise
line X, X is not resolvable yet") - the lifecycle's own comment documents the
~100ms window where the canonical record id is not registered yet, and its
bounded 3x60ms retry. Losing that race is recoverable: commit the text as a new
line. Treating it as a broken transaction is what armed the latch in the first
place.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _host():
    class _Host:
        _live_session_id = "sess-item94"
        _listen_language = "ja"
        _is_finalizing = False
        _is_stopping = False
        is_listening = True

        def __init__(self):
            self.published: list[str] = []

        def _publish_final_transcript_segment(
            self, speaker, text, metadata=None, queue_item=None, commit_reason=None
        ):
            self.published.append(text)
            return True

    return _Host()


def _assembler(host):
    from alpha.transcription.japanese_sentence_assembler import (
        get_japanese_continuity_assembler,
    )

    return get_japanese_continuity_assembler(host)


def _trip_gate_the_way_production_does(assembler):
    """Reproduce the failure site exactly.

    Both set sites do the same two lines, and the id they record is the id the
    assembler is *currently* on - never a different one. Any test that sets
    these two to different values is testing a state production cannot reach.
    """
    assembler._current_canonical_utterance_id = "jp-utt-89bc96734833"
    assembler._assembler_commit_gate_failed = True
    assembler._commit_gate_failed_utterance_id = str(
        assembler._current_canonical_utterance_id or ""
    )


class TheGateCannotOutliveTheUtteranceItTrippedOnTest(unittest.TestCase):
    """The live failure, reproduced through the real assembler.

    The fixture host has no real store behind it, so a commit can legitimately
    fail here for reasons unrelated to the gate. What these assert on is the
    thing the live evidence measured: 85 attempts, 85 rejections, 0 escapes.
    The gate must not be able to refuse every attempt forever.
    """

    def _drive(self, attempts=20):
        from unittest.mock import patch

        from alpha.transcription import japanese_sentence_assembler as jsa

        host = _host()
        assembler = _assembler(host)
        _trip_gate_the_way_production_does(assembler)

        events: list[str] = []
        real_log = jsa.jp_accuracy_log

        def _spy(event, *a, **k):
            events.append(str(event))
            return real_log(event, *a, **k)

        with patch.object(jsa, "jp_accuracy_log", _spy):
            for i in range(attempts):
                assembler._publish_sentence(
                    1,
                    f"MCPサーバーの追加確認削除といった操作を簡単に行います。[{i}]",
                    {"channel_index": 0},
                    "hold_timeout_sentence_end_punctuation",
                )
        return assembler, events

    def test_the_gate_cannot_refuse_every_attempt(self):
        attempts = 20
        _, events = self._drive(attempts)
        rejects = events.count("ASSEMBLER_COMMIT_GATE_FAILED_REJECT")
        self.assertLess(
            rejects,
            attempts,
            f"all {attempts} attempts were refused by the gate - this is the "
            "live signature exactly: 85 rejections, 0 commits, 16.5 minutes",
        )

    def test_the_gate_force_clears_itself(self):
        _, events = self._drive(20)
        self.assertIn(
            "ASSEMBLER_COMMIT_GATE_FORCE_CLEARED_BOUNDED",
            events,
            "the gate never released. ASSEMBLER_COMMIT_GATE_CLEARED_NEW_UTTERANCE "
            "appears 0 times across every recorded run for the same reason: its "
            "escape condition was unreachable",
        )

    def test_the_refusal_stays_bounded_per_trip(self):
        from alpha.transcription.japanese_sentence_assembler import (
            _COMMIT_GATE_MAX_CONSECUTIVE_REJECTS,
        )

        _, events = self._drive(20)
        clears = events.count("ASSEMBLER_COMMIT_GATE_FORCE_CLEARED_BOUNDED")
        rejects = events.count("ASSEMBLER_COMMIT_GATE_FAILED_REJECT")
        self.assertLessEqual(
            rejects,
            clears * _COMMIT_GATE_MAX_CONSECUTIVE_REJECTS,
            "more commits were refused than the per-trip bound allows",
        )

    def test_the_gate_still_refuses_the_commit_that_broke(self):
        """The integrity purpose is kept - the block is bounded, not removed."""
        host = _host()
        assembler = _assembler(host)
        _trip_gate_the_way_production_does(assembler)

        assembler._publish_sentence(
            1, "同じ発話の続きです。", {"channel_index": 0}, "test_same_utterance"
        )

        self.assertEqual(
            [],
            host.published,
            "the first attempt straight after a broken transaction must still "
            "be refused - only the unbounded latch is being removed",
        )


class TheEscapeConditionMustBeReachableTest(unittest.TestCase):
    """Structural guard: the escape must not depend on state minted below it."""

    def test_the_gate_does_not_gate_on_the_id_minted_below_it(self):
        source = (
            PROJECT_ROOT
            / "alpha"
            / "transcription"
            / "japanese_sentence_assembler.py"
        ).read_text(encoding="utf-8")

        gate_at = source.index("if self._assembler_commit_gate_failed:")
        mint_at = source.index('self._current_canonical_utterance_id = f"jp-utt-')
        self.assertLess(
            gate_at,
            mint_at,
            "fixture assumption changed: the mint site is no longer below the gate",
        )

        gate_block = source[gate_at : source.index("metadata = dict(metadata or {})", gate_at)]
        gate_code = " ".join(
            line for line in gate_block.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn(
            "_current_canonical_utterance_id",
            gate_code,
            "the gate's escape depends on _current_canonical_utterance_id, which "
            "is only ever assigned further down this same method - past the "
            "gate's own return. That is the item 94 deadlock: clearing the gate "
            "needs a new utterance id, minting one needs to pass the gate.",
        )


class RevisionTargetMissIsRecoverableTest(unittest.TestCase):
    """Item 94 trigger half - through the real lifecycle controller."""

    def test_a_missed_revision_target_does_not_latch_the_gate(self):
        from unittest.mock import patch

        from alpha.transcription import utterance_lifecycle

        host = _host()
        assembler = _assembler(host)
        # A prior committed line is what makes the next commit want to revise.
        assembler._last_stable_line_id = "canon-000102"
        assembler._current_canonical_utterance_id = "jp-utt-89bc96734833"

        real_controller = utterance_lifecycle.get_utterance_lifecycle(host)

        class _RefusesTheRevision:
            """Exactly what the live controller returned at 19:22:52.773."""

            def __init__(self, inner):
                self._inner = inner
                self.calls: list[str] = []

            def accept_boundary_proposal(self, **kwargs):
                self.calls.append(str(kwargs.get("action")))
                if kwargs.get("action") == "revise_previous":
                    return {
                        "success": False,
                        "accepted": False,
                        "reason": "missing_exact_revision_target",
                    }
                # A plain new line is always acceptable - that is the whole
                # point of the fallback.
                return {
                    "success": True,
                    "accepted": True,
                    "reason": "",
                    "record_id": "canon-000103",
                    "applied_action": "append",
                    "metadata": dict(kwargs.get("metadata") or {}),
                }

        stub = _RefusesTheRevision(real_controller)
        with patch.object(
            utterance_lifecycle, "get_utterance_lifecycle", return_value=stub
        ):
            assembler._publish_sentence(
                1,
                "かな?これはインストールペースト予備公式制度、うちじゃなくてはい。こちらの",
                {"channel_index": 0, "stable_layer_update_previous": True},
                "hold_timeout_sentence_end_punctuation",
                stable_layer_update_previous=True,
            )

        self.assertEqual(
            ["revise_previous", "commit_new"],
            stub.calls,
            "the missed revision target was not retried as a new line - it went "
            "straight to arming the gate, which is what killed the live run",
        )
        self.assertFalse(
            assembler._assembler_commit_gate_failed,
            "losing the ~100ms canonical-record-id registration race is a "
            "recoverable proposal rejection, not a broken transaction - it must "
            "not arm the gate that silenced 16.5 minutes of the live run",
        )
        self.assertTrue(
            host.published,
            "the text was dropped instead of being committed as a new line",
        )

    def test_the_downgraded_commit_is_not_still_flagged_as_an_update(self):
        """The retry appends; it must not carry the revise flag with it.

        `post_update_previous` is read from ``stable_layer_update_previous``
        after the proposal. Leaving it True on a downgraded commit is the item
        20b shape: a brand-new line that every downstream consumer treats as a
        revision of the previous one, overwriting it.
        """
        from unittest.mock import patch

        from alpha.transcription import utterance_lifecycle

        host = _host()
        assembler = _assembler(host)
        assembler._last_stable_line_id = "canon-000102"
        assembler._current_canonical_utterance_id = "jp-utt-89bc96734833"

        seen: list[dict] = []

        class _RefusesThenEchoesTheFlagBack:
            def accept_boundary_proposal(self, **kwargs):
                if kwargs.get("action") == "revise_previous":
                    return {
                        "success": False,
                        "accepted": False,
                        "reason": "missing_exact_revision_target",
                    }
                seen.append(dict(kwargs))
                return {
                    "success": True,
                    "accepted": True,
                    "reason": "",
                    "record_id": "canon-000103",
                    "applied_action": "append",
                    # A controller that hands the flag straight back must not be
                    # able to turn the append into an overwrite.
                    "metadata": {"stable_layer_update_previous": True},
                }

        with patch.object(
            utterance_lifecycle,
            "get_utterance_lifecycle",
            return_value=_RefusesThenEchoesTheFlagBack(),
        ):
            assembler._publish_sentence(
                1,
                "かな?これはインストールペースト予備公式制度です。",
                {"channel_index": 0, "stable_layer_update_previous": True},
                "hold_timeout_sentence_end_punctuation",
                stable_layer_update_previous=True,
            )

        self.assertTrue(seen, "the fallback commit_new proposal never happened")
        self.assertEqual(
            "",
            str(seen[-1].get("revision_target_id") or ""),
            "the downgraded commit still points at a revision target",
        )


class StallSummaryMustNotEraseAConfirmedStallTest(unittest.TestCase):
    """Item 94 observability half.

    The live artifact reported ``stall_confirmed_count: 0``,
    ``final_metrics_healthy: true`` and run ``final_status: completed`` while the
    same file recorded ``stable_pipeline.last_runtime_state: "confirmed"`` and
    the exit snapshot showed ``stable_commit_age_ms: 987147.8``. The stop flush
    resets the ages ``_metrics_look_healthy`` reads, so any live stall looks
    healthy by the time the summary is written.
    """

    def _summary_after_a_confirmed_stable_pipeline_stall(self, tmpdir):
        from alpha.utils import component_stall_classifier as csc

        csc.reset_stall_classification()
        # deepgram fresh + stable commits stale + commits exist == the exact
        # detector already in classify_component_stalls.
        stalled = {
            "deepgram_final_age_ms": 1000.0,
            "stable_commit_age_ms": 900_000.0,
            "internal_stable_commit_count": 104,
            "ui_commit_age_ms": 900_000.0,
        }
        for _ in range(4):
            csc.classify_component_stalls(dict(stalled))
        self.assertEqual(
            "confirmed",
            csc.get_component_final_states().get("stable_pipeline"),
            "fixture assumption changed: the stall detector no longer confirms",
        )
        # Stop flush has run: every age is small again.
        healthy_at_stop = {
            "deepgram_final_age_ms": 1144.0,
            "stable_commit_age_ms": 120.0,
            "ui_commit_age_ms": 100.0,
            "internal_stable_commit_count": 104,
        }
        return csc.finalize_stall_classifications(healthy_at_stop, run_folder=tmpdir)

    def test_a_runtime_confirmed_stall_is_still_counted_at_stop(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = self._summary_after_a_confirmed_stable_pipeline_stall(tmpdir)

        self.assertGreater(
            int(summary.get("stall_confirmed_at_runtime_count", 0)),
            0,
            "the summary reports no stall at all, which is what let a 16.5 "
            "minute dead pipeline ship as a completed run",
        )

    def test_a_stalled_component_is_never_summarised_as_healthy(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = self._summary_after_a_confirmed_stable_pipeline_stall(tmpdir)

        self.assertNotEqual(
            "healthy",
            summary["components"]["stable_pipeline"]["final_state"],
            "a component that was confirmed stalled at runtime is reported as "
            "healthy once the stop flush resets the ages it is judged on",
        )


if __name__ == "__main__":
    unittest.main()
