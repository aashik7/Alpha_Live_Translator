"""End-to-end regression test for the Stop-time interim tail recovery chain.

BUG_FIX_ROADMAP.md Batch 3 put four separate fixes on one code path:

    item 10   _check_stop_tail_duplicate       filter 1: "already committed?"
              (ANY substring of ANY of the last 5 segments -> equality-or-prefix)
    item 11   _should_commit_interim_recovery  filter 2: "already covered?"
              (`norm_interim in norm_final` -> equality-or-prefix)
    item 11b  _check_interim_ghost_watchdog    the *source* the two filters read
              (stash the orphan before the display layer clears it)
    item 12   _should_repair_previous_segment  same anti-pattern, different path

Each has its own unit test (tests/test_check_stop_tail_duplicate_containment.py,
tests/test_should_commit_interim_recovery_containment.py,
tests/test_watchdog_orphan_stop_tail_recovery.py,
tests/test_should_repair_previous_segment_containment.py) and each passes. What
none of them proves is that the pieces work *as a chain*: items 10 and 11 are
two sequential filters that must BOTH pass for uncommitted speech to survive
Stop, and item 11b decides whether either of them ever sees any text at all.

Three consecutive live sessions all reached Stop with an empty interim, so this
whole path has never executed for real -- the one live run that did reach it
(`v3.3.5.5.8.5.26.5.3-20260809-033339`) lost the text to the item 11b defect
before the filters ran. This file closes that gap deterministically: it drives
the real `_recover_interim_tail_on_stop` with a NON-EMPTY interim and asserts on
what ends up in the transcript, not on internal flags.

Style follows the four sibling tests: real AlphaApp methods are bound onto a
minimal stub host rather than constructing a real app, so no Tk root is created
and the logic under test is the production logic, not a paraphrase of it.
"""

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.constants import INTERIM_GHOST_TTL_MS  # noqa: E402
from alpha.ui.main_window import AlphaApp  # noqa: E402

# The live run this file reproduces: a 10-char interim went stale, the ghost
# watchdog cleared it at stale_ms=6039.2, and Stop ran 1.06s later.
LIVE_STALE_SECONDS = 6.0392
LIVE_ORPHAN_TO_STOP_SECONDS = 1.06


class _Segment:
    def __init__(self, text, speaker=1):
        self.text = text
        self.speaker = speaker


class _Store:
    """Just enough TranscriptStore to drive the real chain end to end.

    Only the four methods the Stop path actually calls are implemented:
    `get_all` (item 10's 5-segment window), `get_clean_text`
    (_get_last_final_text_for_recovery's fallback), and the
    `update_last_segment_if_active` / `add_segment` pair the
    append_missing_suffix branch writes through.
    """

    def __init__(self, segments=()):
        self._segments = [_Segment(text, speaker) for text, speaker in segments]
        # Lets a test force the item 17 fallback (update refused -> append
        # rather than silently drop the merged tail).
        self.update_last_segment_succeeds = True
        self.update_calls = []

    def get_all(self):
        return list(self._segments)

    def get_clean_text(self):
        return "\n".join(
            seg.text for seg in self._segments if (seg.text or "").strip()
        )

    def add_segment(self, speaker, text):
        self._segments.append(_Segment(text, speaker))

    def update_last_segment_if_active(self, speaker, text):
        self.update_calls.append((speaker, text))
        if not self.update_last_segment_succeeds:
            return False
        self._segments[-1] = _Segment(text, speaker)
        return True

    def texts(self):
        return [seg.text for seg in self._segments]


class _StopHost:
    """Drives the real Stop-time recovery chain over a fake transcript store.

    Everything on the decision path is the production implementation --
    including `_normalize_compare`, whose punctuation stripping all four fixes
    depend on. Only the UI/IO edges are stubbed.
    """

    # --- the chain under test ---
    _recover_interim_tail_on_stop = AlphaApp._recover_interim_tail_on_stop
    _check_interim_ghost_watchdog = AlphaApp._check_interim_ghost_watchdog  # 11b
    _check_stop_tail_duplicate = AlphaApp._check_stop_tail_duplicate  # item 10
    _should_commit_interim_recovery = AlphaApp._should_commit_interim_recovery  # 11
    # --- real helpers the chain calls into ---
    _get_last_final_text_for_recovery = AlphaApp._get_last_final_text_for_recovery
    _normalize_compare = AlphaApp._normalize_compare
    _merge_text_with_overlap_info = AlphaApp._merge_text_with_overlap_info
    _text_looks_english_or_romaji = AlphaApp._text_looks_english_or_romaji
    _clear_interim_tail = AlphaApp._clear_interim_tail
    _discard_watchdog_orphaned_interim = AlphaApp._discard_watchdog_orphaned_interim
    _reset_segment_repair_state = AlphaApp._reset_segment_repair_state

    def __init__(
        self,
        segments=(),
        interim="",
        interim_speaker=2,
        interim_utterance_id="u-77",
        last_final_text=None,
    ):
        self.transcript_store = _Store(segments)
        self._latest_interim_text = interim
        self._latest_interim_speaker = interim_speaker
        self._latest_interim_utterance_id = interim_utterance_id
        self._latest_interim_committed = False
        # A live interim by default: fresh enough that the ghost watchdog does
        # not fire unless a test deliberately ages it.
        self._last_interim_ui_at = time.perf_counter()
        # The app keeps `_last_final_text` pointed at the most recent committed
        # final, which is normally also the store's last row.
        if last_final_text is None:
            stored = self.transcript_store.texts()
            last_final_text = stored[-1] if stored else ""
        self._last_final_text = last_final_text
        self._watchdog_orphaned_interim_text = ""
        self._watchdog_orphaned_interim_speaker = 1
        self._watchdog_orphaned_interim_utterance_id = ""
        self._watchdog_orphaned_interim_at = 0.0
        self._teams_pending_commit_override = None
        # observation
        self.committed = []
        self.store_updates = []
        self.display_removals = 0
        self.logged = []

    # --- stubs: language mode -------------------------------------------
    def _is_japanese_manual_mode(self):
        # False keeps `_normalize_compare` on its English branch and skips
        # `_apply_japanese_final_cleanup`, matching the four sibling tests.
        # Japanese manual mode is decided by module-level constants, so
        # flipping it here would require patching those, not the host.
        return False

    # --- stubs: display / IO edges --------------------------------------
    def _remove_interim_line_from_display(self):
        self.display_removals += 1

    def _interim_log(self, message, data=None):
        self.logged.append((message, data or {}))

    def _display_transcript_item(self, item):
        # The real method funnels through duplicate protection into
        # TranscriptStore and then into Tk widgets. The observable outcome
        # this file cares about is "the text reached the transcript", so the
        # store write is mirrored here and the widget work is skipped.
        self.committed.append(dict(item))
        self.transcript_store.add_segment(
            speaker=item.get("speaker", 1), text=item.get("text", "")
        )

    def _on_store_segment_updated(
        self, speaker, text, *, canonical_utterance_id="", source_version=1,
        source_record_id="",
    ):
        self.store_updates.append((speaker, text))

    def _track_committed_segment_meta(self, item, text):
        pass

    # --- observation helpers --------------------------------------------
    def committed_texts(self):
        return [item.get("text") for item in self.committed]

    def skip_reasons(self):
        return [
            data.get("reason")
            for message, data in self.logged
            if "stop tail skipped" in message
        ]

    def messages(self):
        return [message for message, _ in self.logged]

    def has_message(self, fragment):
        return any(fragment in message for message in self.messages())

    def age_interim(self, seconds=LIVE_STALE_SECONDS):
        """Make the pending interim look stale enough for the watchdog."""
        self._last_interim_ui_at = time.perf_counter() - seconds


class _ChainCase(unittest.TestCase):
    """Shared assertions phrased in terms of the transcript, not flags."""

    def assertCommittedOnce(self, host, text, speaker=None):
        self.assertEqual(
            host.committed_texts(),
            [text],
            f"expected exactly this tail to be committed; log={host.logged}",
        )
        self.assertIn(
            text,
            host.transcript_store.texts(),
            "a committed tail must actually reach the transcript store",
        )
        if speaker is not None:
            self.assertEqual(host.committed[0]["speaker"], speaker)
        self.assertTrue(host.committed[0]["is_final"])
        # The interim must not stay pending after it was committed.
        self.assertEqual(host._latest_interim_text, "")
        self.assertTrue(host._latest_interim_committed)

    def assertNothingCommitted(self, host, store_texts_before):
        self.assertEqual(
            host.committed_texts(),
            [],
            f"nothing should have been committed; log={host.logged}",
        )
        self.assertEqual(
            host.transcript_store.texts(),
            store_texts_before,
            "the transcript must be byte-identical to before Stop",
        )
        self.assertEqual(host._latest_interim_text, "")
        self.assertEqual(host._watchdog_orphaned_interim_text, "")


# ---------------------------------------------------------------------------
# Case A -- the case three live sessions failed to produce
# ---------------------------------------------------------------------------


class TestPendingClosingSentenceIsCommitted(_ChainCase):
    """A genuinely new closing sentence, never committed, must survive Stop.

    This is the whole reason the path exists. Every other case in this file is
    a way of NOT losing it or NOT duplicating it; this one is the happy path,
    and it has never been observed executing in a real session.
    """

    def test_new_closing_sentence_reaches_the_transcript(self):
        host = _StopHost(
            segments=(
                ("Right, so that covers the migration timeline.", 1),
                ("Any questions before we finish?", 1),
            ),
            interim="Thanks everyone for making the time today, talk soon.",
            interim_speaker=2,
        )

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(
            host, "Thanks everyone for making the time today, talk soon.", speaker=2
        )
        # It must arrive as a NEW line, not folded into the previous one.
        self.assertEqual(len(host.transcript_store.texts()), 3)
        self.assertEqual(host.store_updates, [], "this is an append, not a merge")
        self.assertEqual(host.skip_reasons(), [])
        committed = [d for m, d in host.logged if "stop tail committed" in m]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["reason"], "new_missing_tail")
        # `_last_final_text` must advance, otherwise a later recovery would
        # compare the next tail against a stale final.
        self.assertEqual(
            host._last_final_text,
            "Thanks everyone for making the time today, talk soon.",
        )

    def test_pending_sentence_with_no_prior_final_is_committed(self):
        # Stop pressed during the very first utterance: nothing committed yet,
        # so both filters run with an empty comparison target.
        host = _StopHost(
            segments=(),
            interim="Okay, I think that is everything for the moment.",
            interim_speaker=1,
        )

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(
            host, "Okay, I think that is everything for the moment.", speaker=1
        )
        committed = [d for m, d in host.logged if "stop tail committed" in m]
        self.assertEqual(committed[0]["reason"], "no_prior_final")


# ---------------------------------------------------------------------------
# Case B -- the duplicate side: no second copy of the closing line
# ---------------------------------------------------------------------------


class TestAlreadyCommittedTailIsNotDuplicated(_ChainCase):
    """The narrowing in items 10/11 must not have opened a duplicate hole.

    An interim that is a genuine equality-or-prefix of what was committed is
    the ordinary shape (the interim was mid-utterance when its final landed).
    It must still be dropped, or every session ends with a repeated last line.
    """

    def test_prefix_of_the_last_committed_final_is_dropped(self):
        host = _StopHost(
            segments=(("Well that is all we had on the agenda for today.", 1),),
            interim="Well that is all we had",
        )
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertNothingCommitted(host, before)
        # Filter 1 (item 10) is what catches this shape -- it runs first and
        # sees the store directly. Filter 2's mirror of the same rule is pinned
        # by test_should_commit_interim_recovery_containment.py and, on this
        # chain, by test_prefix_of_a_buffered_final_is_dropped below.
        self.assertEqual(host.skip_reasons(), ["skip_already_committed"])
        self.assertEqual(
            host._segment_repair_stats["stop_tail_duplicate_skipped_count"],
            1,
            "a skipped duplicate must be counted, not silently swallowed",
        )

    def test_exact_repeat_of_the_last_committed_final_is_dropped(self):
        host = _StopHost(
            segments=(
                ("Some earlier line.", 1),
                ("This is the last sentence of the session.", 1),
            ),
            interim="This is the last sentence of the session.",
        )
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertNothingCommitted(host, before)
        self.assertEqual(host.skip_reasons(), ["skip_already_committed"])

    def test_prefix_of_a_buffered_final_is_dropped(self):
        # Reaches filter 2 (item 11) rather than filter 1: the final has been
        # recorded in `_last_final_text` but the meeting segment buffer has not
        # flushed it into the store yet, so item 10's window cannot see it.
        # This is the real window in which filter 2 is the only guard.
        host = _StopHost(
            segments=(("Let us go over the numbers one final time.", 1),),
            interim="This is the closing sentence",
            last_final_text="This is the closing sentence of the whole session today.",
        )
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertNothingCommitted(host, before)
        self.assertEqual(host.skip_reasons(), ["interim_in_final"])


# ---------------------------------------------------------------------------
# Case C -- the containment fix, verified through the whole chain
# ---------------------------------------------------------------------------


class TestCoincidentalSubstringSurvivesTheWholeChain(_ChainCase):
    """Items 10 and 11 fixed the same anti-pattern one filter apart.

    Fixing only one leaves the text lost at the other, so the property that
    matters is not "each predicate returns the right value" but "a
    coincidental interior match reaches the transcript". That is what these
    assert -- through `_recover_interim_tail_on_stop`, not on the predicates.
    """

    def test_interior_substring_of_the_last_final_is_still_committed(self):
        # The speaker used the phrase mid-sentence earlier, then repeated it
        # alone as a closing remark that never got a final. Pre-fix this hit
        # `norm_interim in norm_seg` at filter 1 AND `norm_interim in
        # norm_final` at filter 2 -- either one loses it permanently.
        last_line = "Okay so let us review the quarterly revenue numbers one more time."
        interim = "review the quarterly revenue numbers"
        host = _StopHost(segments=((last_line, 1),), interim=interim)

        # Guard: this must genuinely be interior-not-prefix, or the test would
        # pass against the pre-fix code for the wrong reason.
        norm_interim = host._normalize_compare(interim)
        norm_last = host._normalize_compare(last_line)
        self.assertIn(norm_interim, norm_last)
        self.assertFalse(norm_last.startswith(norm_interim))

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(host, interim, speaker=2)
        self.assertEqual(
            host.transcript_store.texts(), [last_line, interim],
            "the coincidental match must be appended, not merged or dropped",
        )

    def test_interior_substring_of_an_older_segment_is_still_committed(self):
        # Filter 1 scans the last FIVE segments, so a coincidence anywhere in
        # that window used to be fatal even when the last final was unrelated.
        older = "Well, thank you for joining us today, everyone."
        interim = "thank you for joining us"
        host = _StopHost(
            segments=(
                (older, 1),
                ("Let us get started with the first agenda item.", 1),
                ("That wraps up the budget discussion.", 1),
            ),
            interim=interim,
        )

        norm_interim = host._normalize_compare(interim)
        norm_older = host._normalize_compare(older)
        self.assertIn(norm_interim, norm_older)
        self.assertFalse(norm_older.startswith(norm_interim))

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(host, interim, speaker=2)
        self.assertEqual(host.skip_reasons(), [])


# ---------------------------------------------------------------------------
# Case D -- the watchdog hand-off (item 11b feeding items 10/11)
# ---------------------------------------------------------------------------


class TestWatchdogOrphanIsHandedToTheFilters(_ChainCase):
    """The live failure, reproduced: watchdog clears, then Stop runs.

    Shape taken from run `v3.3.5.5.8.5.26.5.3-20260809-033339`:

        +261.15s  [INTERIM] received                text_len=10
        +267.19s  [INTERIM] ghost watchdog cleared  text_len=10 stale_ms=6039.2
        +268.25s  [INTERIM] stop tail ...           latest_interim_len=0
                  [INTERIM] stop tail skipped       reason=empty_interim

    The 1.06s gap needs no real sleep: the supersession check compares two
    *stamps* (`_watchdog_orphaned_interim_at` vs `_last_interim_ui_at`), and
    what made this run recoverable is that no new interim arrived in the gap,
    so both stamps stay equal. Wall-clock time is irrelevant to the decision.
    """

    def _stale_orphan_host(self, interim, segments):
        host = _StopHost(segments=segments, interim=interim, interim_speaker=2)
        host.age_interim(LIVE_STALE_SECONDS)

        host._check_interim_ghost_watchdog()

        # Precondition of the whole case: the display layer really did clear
        # the live interim, and really did preserve the orphan.
        self.assertEqual(host._latest_interim_text, "")
        self.assertEqual(host.display_removals, 1)
        self.assertEqual(host._watchdog_orphaned_interim_text, interim)
        return host

    def test_orphaned_tail_is_committed_after_the_watchdog_cleared_it(self):
        interim = "and please remember to file your reports before then"
        host = self._stale_orphan_host(
            interim, segments=(("So the migration window opens on Monday.", 1),)
        )

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(host, interim, speaker=2)
        self.assertTrue(
            host.has_message("stop tail using watchdog orphan"),
            "the orphan hand-off must be visible in the log",
        )
        self.assertNotIn("empty_interim", host.skip_reasons())
        self.assertEqual(
            host._watchdog_orphaned_interim_text,
            "",
            "a consumed orphan must not be left available to a later Stop",
        )

    def test_the_literal_live_tail_now_reaches_the_filters_but_is_too_short(self):
        """Honest record of what item 11b did and did not recover.

        The live orphan was `思って-何、何、何、` -- 10 characters, which
        `_normalize_compare` reduces to 6 once `-` and `、` are stripped.
        `_should_commit_interim_recovery` refuses anything under 20 normalized
        characters, so this specific tail is still not committed.

        That is not a hole in item 11b: pre-11b the tail was destroyed before
        any filter ran and the log said `empty_interim`; now it reaches the
        decision and is refused on its merits, with the hand-off recorded. The
        assertions below pin exactly that difference. If the 20-char floor is
        ever lowered, this tail becomes recoverable and this test must be
        updated deliberately rather than relaxed.
        """
        interim = "思って-何、何、何、"
        self.assertEqual(len(interim), 10)
        host = self._stale_orphan_host(
            interim, segments=(("そうですね、それでは始めましょう。", 1),)
        )
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertTrue(
            host.has_message("stop tail using watchdog orphan"),
            "item 11b's whole point: the filters must at least SEE the orphan",
        )
        self.assertNothingCommitted(host, before)
        self.assertEqual(
            host.skip_reasons(),
            ["too_short"],
            "the refusal must now come from the length rule, not from the text "
            "having been destroyed by the display layer (reason=empty_interim)",
        )

    def test_a_fresh_interim_is_untouched_by_the_watchdog_and_still_commits(self):
        # Guard on the other side: the watchdog must not have become eager
        # enough to interfere with an ordinary Stop.
        interim = "and that is the last thing I wanted to mention today"
        host = _StopHost(
            segments=(("We covered the rollout plan already.", 1),), interim=interim
        )

        host._check_interim_ghost_watchdog()
        self.assertEqual(host._latest_interim_text, interim)
        self.assertEqual(host.display_removals, 0)

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(host, interim, speaker=2)


# ---------------------------------------------------------------------------
# Case E -- supersession: a stale orphan must not be resurrected
# ---------------------------------------------------------------------------


class TestSupersededOrphanIsNotResurrected(_ChainCase):
    """The orphan fallback must not append minutes-old text at the end.

    If the speaker carried on after the watchdog fired, the orphan is stale:
    later speech has already been committed above it, so committing it now
    puts old words at the BOTTOM of the transcript. The supersession check is
    what keeps item 11b from trading a loss bug for a corruption bug.
    """

    def test_orphan_is_dropped_when_a_newer_interim_arrived_afterwards(self):
        host = _StopHost(
            segments=(("First we reviewed the incident timeline.", 1),),
            interim="an old fragment that was left hanging mid sentence",
            interim_speaker=2,
        )
        host.age_interim(LIVE_STALE_SECONDS)
        host._check_interim_ghost_watchdog()
        self.assertTrue(host._watchdog_orphaned_interim_text)

        # The speaker kept talking: a newer interim was rendered, and its
        # utterance completed and committed normally, clearing the live
        # interim again. Only the stale orphan is left over at Stop.
        host._last_interim_ui_at = host._watchdog_orphaned_interim_at + 5.0
        host.transcript_store.add_segment(
            speaker=2, text="Then we agreed on the follow-up actions."
        )
        host._last_final_text = "Then we agreed on the follow-up actions."
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertNothingCommitted(host, before)
        self.assertTrue(
            host.has_message("stop tail orphan superseded"),
            "dropping an orphan must be logged, not silent",
        )
        self.assertEqual(host.skip_reasons(), ["empty_interim"])

    def test_orphan_is_kept_when_the_stamps_are_equal(self):
        # The boundary the live run sits on: the watchdog stamps the orphan
        # with the same `_last_interim_ui_at` it fired on, so "nothing has
        # happened since" is stamp equality, not a time window. A `>=`
        # supersession test here would lose exactly the case 11b exists for.
        interim = "and one final point before we close the meeting"
        host = _StopHost(segments=(("Earlier unrelated line.", 1),), interim=interim)
        host.age_interim(LIVE_STALE_SECONDS)
        host._check_interim_ghost_watchdog()
        self.assertEqual(
            host._watchdog_orphaned_interim_at, host._last_interim_ui_at
        )

        host._recover_interim_tail_on_stop()

        self.assertCommittedOnce(host, interim, speaker=2)


# ---------------------------------------------------------------------------
# Case F -- append_missing_suffix: extend, do not duplicate
# ---------------------------------------------------------------------------


class TestInterimExtendingTheLastSegmentIsMerged(_ChainCase):
    """The third outcome of filter 1, between "drop" and "commit new line".

    When the interim starts with the last committed segment it is the same
    utterance carrying on, so the tail is merged onto that row. The failure
    mode this guards is a transcript ending with the same words twice.
    """

    def test_missing_suffix_is_merged_onto_the_last_segment(self):
        host = _StopHost(
            segments=(("I think we should start", 2),),
            interim="I think we should start the review meeting now please",
        )

        host._recover_interim_tail_on_stop()

        self.assertEqual(
            host.transcript_store.texts(),
            ["I think we should start the review meeting now please"],
            "the tail must extend the existing row, not add a second one",
        )
        self.assertEqual(
            host.transcript_store.texts()[0].count("I think we should start"),
            1,
            "the overlapping prefix must not appear twice",
        )
        self.assertEqual(
            host.committed, [], "a merge must not also emit a new transcript item"
        )
        self.assertEqual(
            host.store_updates,
            [(2, "I think we should start the review meeting now please")],
            "the merge must be written back through the segment-updated hook",
        )
        self.assertTrue(host.has_message("stop tail appended suffix"))
        self.assertEqual(host._latest_interim_text, "")
        self.assertTrue(host._latest_interim_committed)
        self.assertEqual(
            host._last_final_text,
            "I think we should start the review meeting now please",
        )

    def test_merged_tail_is_appended_when_the_row_is_no_longer_updatable(self):
        # Item 17's fallback. On the Stop last-chance path a refused update
        # used to drop the merged tail silently; a visible extra line is
        # recoverable, a lost one is not.
        host = _StopHost(
            segments=(("I think we should start", 2),),
            interim="I think we should start the review meeting now please",
        )
        host.transcript_store.update_last_segment_succeeds = False

        host._recover_interim_tail_on_stop()

        self.assertEqual(
            host.transcript_store.texts(),
            [
                "I think we should start",
                "I think we should start the review meeting now please",
            ],
            "a refused update must append the merged tail, never discard it",
        )
        self.assertTrue(host.has_message("stop tail appended suffix"))


# ---------------------------------------------------------------------------
# Guards on the entry conditions of the chain
# ---------------------------------------------------------------------------


class TestChainEntryGuards(_ChainCase):
    def test_already_committed_interim_is_not_committed_twice(self):
        host = _StopHost(
            segments=(("Some committed line.", 1),),
            interim="this tail was already committed on the normal path",
        )
        host._latest_interim_committed = True
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertEqual(host.committed_texts(), [])
        self.assertEqual(host.transcript_store.texts(), before)
        self.assertEqual(host.skip_reasons(), ["already_committed"])

    def test_nothing_pending_at_all_is_a_clean_no_op(self):
        # The shape all three recent live sessions actually reached.
        host = _StopHost(segments=(("The only committed line.", 1),), interim="")
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertNothingCommitted(host, before)
        self.assertEqual(host.skip_reasons(), ["empty_interim"])

    def test_a_two_character_tail_is_still_refused_by_filter_one(self):
        host = _StopHost(segments=(("Anything at all.", 1),), interim="ok")
        before = host.transcript_store.texts()

        host._recover_interim_tail_on_stop()

        self.assertNothingCommitted(host, before)
        self.assertEqual(host.skip_reasons(), ["skip_too_short"])


if __name__ == "__main__":
    unittest.main()
