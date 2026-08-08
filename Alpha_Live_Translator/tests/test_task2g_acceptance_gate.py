"""Task 2G — final QA validation for the Task 2F manual-mode Japanese fix.

Deterministic only: no real audio, no live Deepgram/DeepL calls, no
timing-based flakiness. Two layers:

  Set A: direct unit tests against TranscriptStore's new
         get_last_segment_if_active / update_last_segment_if_active
         (the actual fix from Task 2F).
  Set B: integration tests against AlphaApp._commit_transcript_item_to_store
         itself (the real, unmodified production entry point containing the
         two Task 2F edits), using a lightweight method-borrowed host
         (same pattern as test_task3c_acceptance_gate.py) instead of
         constructing the full CustomTkinter GUI.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.summary.transcript_store import TranscriptStore  # noqa: E402
from alpha.transcription import canonical_transcript_ledger as ctl  # noqa: E402
from alpha.transcription.canonical_identity_registry import (  # noqa: E402
    reset_for_session,
)
from alpha.transcription.duplicate_protection import (  # noqa: E402
    DuplicateProtectionMixin,
)
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    reset_utterance_lifecycle,
)
from alpha.ui.main_window import AlphaApp  # noqa: E402

_manual_mode_host_counter = 0


# ---------------------------------------------------------------------------
# Set A: TranscriptStore-level tests (the core Task 2F fix)
# ---------------------------------------------------------------------------
class TranscriptStoreHardBoundaryTests(unittest.TestCase):
    def test_get_last_segment_if_active_refuses_cross_speaker_jump(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        store.add_segment(2, "speaker two interjects")
        # True last row belongs to speaker 2 -- must NOT reach back to
        # speaker 1's earlier row the way the old get_last_segment(1) would.
        self.assertIsNone(store.get_last_segment_if_active(1))
        # Old method's bug, still reproducible on purpose -- proves this is
        # a real behavior delta. BUG_FIX_ROADMAP.md Batch 3 item 17 renamed
        # it from get_last_segment(speaker) to make the unsafe scan
        # impossible to reach by reflex; no production caller uses it now,
        # and it is retained only so this comparison keeps working.
        self.assertIsNotNone(store.get_last_segment_unsafe_speaker_scan(1))
        self.assertEqual(
            store.get_last_segment_unsafe_speaker_scan(1).text,
            "speaker one first line",
        )

    def test_get_last_segment_if_active_matches_true_last(self):
        store = TranscriptStore()
        store.add_segment(1, "A")
        store.add_segment(2, "B")
        store.add_segment(1, "C")
        result = store.get_last_segment_if_active(1)
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "C")

    def test_update_last_segment_if_active_refuses_cross_speaker_write(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        store.add_segment(2, "speaker two interjects")
        updated = store.update_last_segment_if_active(1, "corrupted merge")
        self.assertFalse(updated)
        segments = store.get_all()
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].text, "speaker one first line")
        self.assertEqual(segments[1].text, "speaker two interjects")

    def test_update_last_segment_if_active_updates_true_last(self):
        store = TranscriptStore()
        store.add_segment(1, "speaker one first line")
        store.add_segment(2, "speaker two interjects")
        updated = store.update_last_segment_if_active(2, "speaker two extended")
        self.assertTrue(updated)
        segments = store.get_all()
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].text, "speaker one first line")
        self.assertEqual(segments[1].text, "speaker two extended")

    def test_unknown_speaker_never_confirmed_same(self):
        store = TranscriptStore()
        store.add_segment(None, "unattributed line")
        self.assertIsNone(store.get_last_segment_if_active(None))
        self.assertFalse(store.update_last_segment_if_active(None, "x"))


# ---------------------------------------------------------------------------
# Set B: integration tests against the real _commit_transcript_item_to_store
# ---------------------------------------------------------------------------
class ManualModeCommitHost(DuplicateProtectionMixin):
    """Method-borrowed host: real AlphaApp Japanese manual-mode commit code,
    no CustomTkinter GUI construction. Borrows every method actually reached
    by _commit_transcript_item_to_store for a Japanese manual-mode item.
    """

    _JAPANESE_NATURAL_SHORT_REPEATS = AlphaApp._JAPANESE_NATURAL_SHORT_REPEATS
    _JAPANESE_SHORT_REPEAT_MIN_LEN = AlphaApp._JAPANESE_SHORT_REPEAT_MIN_LEN
    _JAPANESE_PARTIAL_OVERLAP_MIN_LEN = AlphaApp._JAPANESE_PARTIAL_OVERLAP_MIN_LEN
    _JAPANESE_TAIL_STITCH_MAX_COMPACT_LEN = AlphaApp._JAPANESE_TAIL_STITCH_MAX_COMPACT_LEN
    _JAPANESE_TAIL_INCOMPLETE_ENDINGS = AlphaApp._JAPANESE_TAIL_INCOMPLETE_ENDINGS
    _JAPANESE_PARTICLE_ENDINGS = AlphaApp._JAPANESE_PARTICLE_ENDINGS
    _JAPANESE_CONTINUATION_PREFIXES = AlphaApp._JAPANESE_CONTINUATION_PREFIXES
    _JAPANESE_PREFIX_REPEAT_MIN_LEN = AlphaApp._JAPANESE_PREFIX_REPEAT_MIN_LEN

    _commit_transcript_item_to_store = AlphaApp._commit_transcript_item_to_store
    _diag_transcript_item_fields = AlphaApp._diag_transcript_item_fields
    _diag_store_segment_count = AlphaApp._diag_store_segment_count
    _should_accept_transcript_commit = AlphaApp._should_accept_transcript_commit
    _teams_log_commit_decision = AlphaApp._teams_log_commit_decision
    _is_japanese_manual_mode = AlphaApp._is_japanese_manual_mode
    _selected_source_language_ui = AlphaApp._selected_source_language_ui
    _strip_language_flag = AlphaApp._strip_language_flag
    _track_committed_segment_meta = AlphaApp._track_committed_segment_meta
    _item_to_segment_meta = AlphaApp._item_to_segment_meta
    _apply_final_interim_comparison = AlphaApp._apply_final_interim_comparison
    _normalize_compare = AlphaApp._normalize_compare
    _interim_log = AlphaApp._interim_log

    _evaluate_japanese_cross_segment_merge = AlphaApp._evaluate_japanese_cross_segment_merge
    _evaluate_japanese_tail_stitch = AlphaApp._evaluate_japanese_tail_stitch
    _evaluate_japanese_particle_continuation = AlphaApp._evaluate_japanese_particle_continuation
    _evaluate_japanese_compound_continuation = AlphaApp._evaluate_japanese_compound_continuation
    _evaluate_japanese_commit_dedup = AlphaApp._evaluate_japanese_commit_dedup
    _commit_japanese_update_previous_segment = AlphaApp._commit_japanese_update_previous_segment
    _japanese_recent_compact_segments = AlphaApp._japanese_recent_compact_segments
    _previous_segment_ends_incomplete_japanese = AlphaApp._previous_segment_ends_incomplete_japanese
    _normalize_japanese_tail_fragment = AlphaApp._normalize_japanese_tail_fragment
    _is_japanese_standalone_phrase = AlphaApp._is_japanese_standalone_phrase
    _is_japanese_standalone_no_merge = AlphaApp._is_japanese_standalone_no_merge
    _previous_blocks_particle_merge = AlphaApp._previous_blocks_particle_merge
    _current_starts_japanese_continuation = AlphaApp._current_starts_japanese_continuation
    _apply_cjk_post_merge_cleanup = AlphaApp._apply_cjk_post_merge_cleanup
    _log_tail_stitch_skipped = AlphaApp._log_tail_stitch_skipped
    _log_particle_merge_blocked = AlphaApp._log_particle_merge_blocked
    _log_compound_continuation_skipped = AlphaApp._log_compound_continuation_skipped
    _trim_segment_to_compact_prefix = AlphaApp._trim_segment_to_compact_prefix

    _apply_japanese_final_cleanup = AlphaApp._apply_japanese_final_cleanup
    _apply_japanese_known_term_corrections = AlphaApp._apply_japanese_known_term_corrections
    _remove_internal_japanese_repeat = AlphaApp._remove_internal_japanese_repeat
    _remove_short_internal_japanese_repeat = AlphaApp._remove_short_internal_japanese_repeat
    _remove_japanese_prefix_repeat = AlphaApp._remove_japanese_prefix_repeat
    _remove_japanese_prefix_repeat_once = AlphaApp._remove_japanese_prefix_repeat_once
    _remove_japanese_partial_overlap_repeat = AlphaApp._remove_japanese_partial_overlap_repeat
    _remove_japanese_partial_overlap_once = AlphaApp._remove_japanese_partial_overlap_once
    _cjk_log_fn = AlphaApp._cjk_log_fn
    _cjk_log = AlphaApp._cjk_log
    _cjk_language_code = AlphaApp._cjk_language_code
    _is_cjk_pipeline_active = AlphaApp._is_cjk_pipeline_active
    _is_japanese_pipeline_active = AlphaApp._is_japanese_pipeline_active
    _compact_japanese_for_compare = AlphaApp._compact_japanese_for_compare
    _normalize_japanese_display_text = AlphaApp._normalize_japanese_display_text

    record_latency_commit = AlphaApp.record_latency_commit
    log_latency_transcript_committed = AlphaApp.log_latency_transcript_committed
    _latency_elapsed_sec = AlphaApp._latency_elapsed_sec

    def __init__(self, listen_language: str = "ja") -> None:
        # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 2/3: manual-mode commits
        # now route through the real execute_pipeline_commit/identity
        # registry (previously always trusted via _jp_continuity_assembler
        # without verification) -- this host must reset that shared,
        # module-level state per instance exactly like every other test
        # host in this suite (IdentityTestHost/EnglishTestHost/
        # JapaneseTestHost), and use a unique session/run id so parallel
        # instances across the four tests in this class never collide.
        global _manual_mode_host_counter
        _manual_mode_host_counter += 1
        session_id = f"sess-2g-{_manual_mode_host_counter}"
        run_id = f"run-2g-{_manual_mode_host_counter}"
        self.transcript_store = TranscriptStore()
        self.translation_worker = None
        self.translation_enabled = False
        self.is_listening = True
        self._is_finalizing = False
        self._listen_language = listen_language
        self._live_session_id = session_id
        self._on_store_segment_updated_calls = []
        ctl.reset_for_run(run_id)
        reset_for_session(session_id)
        reset_utterance_lifecycle(self, session_id=session_id)

    def _on_store_segment_updated(self, speaker, text, **kwargs):
        # Deliberately NOT AlphaApp's own version (that touches Tk widgets
        # and translation submission, both irrelevant to this test) --
        # records the call so tests can assert on it instead.
        self._on_store_segment_updated_calls.append((speaker, text))

    def commit(self, speaker, text, *, jp_continuity_assembler=True):
        item = {
            "speaker": speaker,
            "text": text,
            "is_final": True,
            "speech_final": True,
            "_jp_cleaned": True,  # skip the CJK cleanup cascade -- irrelevant here
        }
        if jp_continuity_assembler:
            # Marks this item as already having passed through the canonical
            # Japanese assembler, matching block_rogue_japanese_direct_commit's
            # real gate (TASK_2E_FINDINGS.md item 1) -- lets the "commit_new"
            # fallback skip the full canonical-registry machinery, which is
            # untouched by Task 2F and out of scope for this test.
            item["_jp_continuity_assembler"] = True
        self._commit_transcript_item_to_store(item)
        return item


class ManualModeIntegrationTests(unittest.TestCase):
    def test_1_cross_speaker_interjection_prevents_bypass_merge(self):
        # fixes TASK_2E_FINDINGS.md item 1/3: the manual-mode merge path
        # previously reached backward across a different speaker's turn
        # (TranscriptStore.update_last_segment's positional scan) and could
        # silently overwrite an unrelated, non-adjacent segment. Confirms
        # that bypass is closed.
        host = ManualModeCommitHost()
        host.commit(1, "これはテストの音声")
        host.commit(2, "はい、そうですね、よろしくお願いします")
        host.commit(1, "認識をテストしています")

        segments = host.transcript_store.get_all()
        self.assertEqual(
            len(segments), 3,
            f"expected 3 independent segments, got: {[s.text for s in segments]}",
        )
        self.assertEqual(segments[0].speaker, 1)
        self.assertEqual(segments[0].text, "これはテストの音声")
        self.assertEqual(segments[1].speaker, 2)
        self.assertEqual(segments[2].speaker, 1)
        self.assertEqual(segments[2].text, "認識をテストしています")
        # The old-bug outcome would have been 2 segments with segment[0]
        # silently rewritten to a merged compound string -- explicitly
        # assert that did NOT happen.
        self.assertNotIn("音声認識", segments[0].text)

    def test_2_wrong_speaker_merge_rejected_like_task2d_pattern(self):
        # Reuses the Task 2D two-speaker rejection pattern directly against
        # this path: speaker 2's utterance must never be merged into
        # speaker 1's row merely because it is store-adjacent in time.
        host = ManualModeCommitHost()
        host.commit(1, "これはテストの音声")
        before = host.transcript_store.get_last_segment_if_active(1).text
        host.commit(2, "音声認識のテストです")  # would satisfy the compound
        # continuation pattern (ends/starts match JAPANESE_COMPOUND_ENDINGS/
        # STARTS) if speaker were ignored entirely.
        segments = host.transcript_store.get_all()
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].text, before)
        self.assertEqual(segments[0].speaker, 1)
        self.assertEqual(segments[1].speaker, 2)

    def test_3_legitimate_same_speaker_continuation_still_merges(self):
        # Regression guard (TASK_2E_FINDINGS.md item 4): the manual-mode
        # merge path is genuinely reachable and does real, currently
        # necessary work for Japanese sessions -- same-speaker compound
        # continuation must still work exactly as before Task 2F.
        host = ManualModeCommitHost()
        host.commit(1, "これはテストの音声")
        host.commit(1, "認識をテストしています")

        segments = host.transcript_store.get_all()
        self.assertEqual(
            len(segments), 1,
            f"expected same-speaker continuation to merge into one segment, got: {[s.text for s in segments]}",
        )
        self.assertIn("音声", segments[0].text)
        self.assertIn("認識", segments[0].text)
        self.assertEqual(
            host._on_store_segment_updated_calls[-1][0], 1,
            "merge commit must notify via _on_store_segment_updated for the merged speaker",
        )

    def test_4_particle_continuation_same_speaker_still_merges(self):
        # A second legitimate-merge shape (particle continuation, distinct
        # heuristic from compound continuation) -- broader regression
        # coverage for item 4's "real necessary work" claim.
        host = ManualModeCommitHost()
        # "previous" must compact to >= 8 chars and end in a particle
        # (_previous_blocks_particle_merge's own minimum-length guard);
        # "current" starts with "確認", one of _JAPANESE_CONTINUATION_PREFIXES.
        host.commit(1, "本日の会議の資料を")
        host.commit(1, "確認してください")
        segments = host.transcript_store.get_all()
        self.assertEqual(
            len(segments), 1,
            f"expected particle continuation to merge, got: {[s.text for s in segments]}",
        )


if __name__ == "__main__":
    unittest.main()
