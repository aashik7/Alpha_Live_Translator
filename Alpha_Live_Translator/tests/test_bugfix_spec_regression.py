"""Regression tests for ALPHA_BUGFIX_SPEC_FOR_CLAUDE_CODE.md (BUG-A..G1).

BUG-A (deepgram_client.py) and BUG-B (main_window.py) are trivial,
localized fixes verified via the existing full suite (no regressions) and
by direct source inspection matching the spec's before/after diff; they
are not re-tested in isolation here.

BUG-C, BUG-D, BUG-E, BUG-F, and BUG-G1 get dedicated tests below, since
each has behavior that is meaningfully different before vs. after the fix
and is cheap to exercise without a live Deepgram session.

BUG-G2 and DIAGNOSTIC-H are not covered by automated tests here: the
spec's own regression checklist only requests unit tests for D/E/F, and
G2/H's own "Verification" sections are explicitly live-session-log-based
(applied_action counts in japanese_accuracy.log, new gate-mismatch
events) rather than asking for a new unit test. G2 lives in
duplicate_protection.py's DuplicateProtectionMixin, which needs a fuller
host (transcript_store, canonical ledger, execute_pipeline_commit) to
exercise meaningfully -- out of proportion for what the spec asked for
here. DIAGNOSTIC-H is log-only instrumentation with no behavior to assert
on beyond "still returns the same bool", already covered by BUG-D/E's
existing tests continuing to pass unchanged.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import canonical_identity_registry as cir  # noqa: E402
from alpha.transcription.utterance_lifecycle import (  # noqa: E402
    UtteranceLifecycleOwner,
    _text_related,
)


class BugCPureInterimArmsFallbackTimeoutTests(unittest.TestCase):
    """BUG-C: a pure-interim (is_final=False) chunk must arm the
    inactivity-fallback timer, so an utterance that never receives an
    is_final=True chunk can still eventually commit instead of staying
    stuck on screen forever."""

    def setUp(self) -> None:
        self.owner = UtteranceLifecycleOwner(host=None, commit_fallback_ms=250)
        self.owner.reset_for_session("sess-bugc")

    def test_interim_only_chunk_arms_timeout(self) -> None:
        token_before = self.owner._timeout_token
        d = self.owner.on_final_chunk(
            text="This is a pure interim fragment",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=False,
            speech_final=False,
            event_id="ev-interim-1",
            metadata={},
        )
        self.assertIsNotNone(d)
        self.assertNotEqual(
            self.owner._timeout_token,
            token_before,
            "a pure-interim chunk must arm (increment) the fallback timeout "
            "token (BUG-C) -- before the fix, Case A never called "
            "_arm_timeout_locked() so this token never moved",
        )

    def test_incompatible_interim_against_held_active_does_not_arm(self) -> None:
        # First interim establishes an active utterance.
        self.owner.on_final_chunk(
            text="Speaker one talking",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=False,
            speech_final=False,
            event_id="ev-1",
            metadata={},
        )
        token_after_first = self.owner._timeout_token
        # A wildly incompatible interim (different channel) must be
        # ignored, not merged and not (re)arming on this rejected path --
        # this exercises the untouched IGNORE_DUPLICATE branch right above
        # the fix, confirming it still short-circuits before reaching the
        # new self._arm_timeout_locked() call.
        d = self.owner.on_final_chunk(
            text="Completely unrelated speaker two talking now",
            speaker=2,
            channel=1,
            start=50.0,
            end=51.0,
            is_final=False,
            speech_final=False,
            event_id="ev-2",
            metadata={},
        )
        self.assertEqual(d.reason, "interim_incompatible_with_active_utterance")
        # Token identity is allowed to be the same object (rejected path
        # never re-arms); just confirm the held utterance's own timer
        # wasn't clobbered by the rejected update.
        self.assertIs(self.owner._timeout_token, token_after_first)


class BugDPostCommitCorrectionSupersedesTests(unittest.TestCase):
    """BUG-D (primary): once the previously-committed utterance's identity
    is registered in canonical_identity_registry (the real production
    path — normally done by duplicate_protection.py's commit handler,
    simulated directly here), a same-channel, timing/text-related
    follow-up final must now resolve via the owner's own tracked
    prev.utterance_id fallback and produce SUPERSEDE_PREVIOUS with the
    SAME utterance_id and a populated superseded_record_id — instead of
    silently starting an unrelated second utterance (CREATE_NEW/
    COMMIT_ACTIVE with a fresh id), which is what happened before the fix
    for every real English/generic session (118/119 utterance-boundary
    transitions in the largest recorded live test).
    """

    def setUp(self) -> None:
        self.session_id = "sess-bugd"
        cir.reset_for_session(self.session_id)
        self.owner = UtteranceLifecycleOwner(host=None, commit_fallback_ms=250)
        self.owner.reset_for_session(self.session_id)

    def _register_commit_identity(self, decision) -> str:
        """Simulates duplicate_protection.py's commit path: the ONLY
        current place canonical_identity_registry actually gets populated
        for a real commit."""
        record_id = f"rec-{decision.utterance_id}"
        cir.observe_identity(
            session_id=self.session_id,
            channel_index=0,
            canonical_utterance_id=decision.utterance_id,
            provider_utterance_id="",
            source_version=1,
            decision="CREATE_NEW",
            text=decision.text,
            lifecycle_state="COMMITTED",
            translation_eligible=True,
        )
        cir.assign_canonical_record_id(
            session_id=self.session_id,
            channel_index=0,
            canonical_utterance_id=decision.utterance_id,
            canonical_record_id=record_id,
        )
        return record_id

    def test_no_metadata_identity_hints_reproduces_real_pipeline_shape(self) -> None:
        # No revision_target_id / canonical_utterance_id in metadata --
        # exactly what raw Deepgram English/generic finals actually carry
        # (Part 1 of the root-cause chain: Deepgram has no such concept).
        d1 = self.owner.on_final_chunk(
            text="Tariqul is joining the call.",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=True,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(d1.decision, "COMMIT_ACTIVE")
        first_utterance_id = d1.utterance_id
        self._register_commit_identity(d1)

        d2 = self.owner.on_final_chunk(
            text="Tariqul is joining the call for the demo.",
            speaker=1,
            channel=0,
            start=1.2,
            end=2.2,
            is_final=True,
            speech_final=True,
            event_id="ev-2",
            metadata={},
        )
        self.assertEqual(
            d2.decision,
            "SUPERSEDE_PREVIOUS",
            f"expected the identity-linked fallback to supersede, got "
            f"{d2.decision!r} reason={d2.reason!r}",
        )
        self.assertEqual(
            d2.utterance_id,
            first_utterance_id,
            "a superseding correction must keep the SAME canonical utterance id",
        )
        self.assertTrue(
            d2.superseded_record_id,
            "superseded_record_id must be populated, not left empty",
        )

    def test_unrelated_new_utterance_still_creates_new_not_forced_supersede(self) -> None:
        """Safety check: the fallback must not make everything a
        correction. A genuinely new, textually- and timing-unrelated
        utterance must still create a new canonical id."""
        d1 = self.owner.on_final_chunk(
            text="Tariqul is joining the call.",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=True,
            event_id="ev-1",
            metadata={},
        )
        self._register_commit_identity(d1)

        d2 = self.owner.on_final_chunk(
            text="Let's move on to discuss the quarterly budget numbers now.",
            speaker=1,
            channel=0,
            start=45.0,
            end=48.0,
            is_final=True,
            speech_final=True,
            event_id="ev-2",
            metadata={},
        )
        self.assertNotEqual(d2.decision, "SUPERSEDE_PREVIOUS")
        self.assertNotEqual(d2.utterance_id, d1.utterance_id)

    def test_spec_example_text_pair_is_a_known_text_related_gap_not_this_fix(self) -> None:
        """Documents a real gap found while validating BUG-D: the spec's
        own suggested example pair ("Tarikur is joining the call." ->
        "Tariqul is joining the call.") does NOT satisfy the pre-existing,
        untouched _text_related() heuristic (the divergent "kur"/"qul"
        syllable falls inside its fixed prefix-comparison window), so it
        would still fall through to CREATE_NEW even with BUG-D's identity
        fallback correctly resolving. This is unrelated to BUG-D's fix
        (which only supplies the identity link — _text_related is
        untouched, per the spec's own explicit scope restriction) and is
        flagged here rather than silently adjusted away.
        """
        self.assertFalse(
            _text_related(
                "Tarikur is joining the call.", "Tariqul is joining the call."
            ),
            "if this ever becomes True, the note above is stale and the "
            "other tests in this class should be revisited for realism",
        )


class BugEPrematureContinuationExtendTests(unittest.TestCase):
    """BUG-E (primary, dominant real-world pattern): a low-text-similarity
    follow-up final, arriving shortly after a commit that was only the
    app's own uncertain inactivity-timeout guess (not a confident
    Deepgram/boundary signal), must be APPENDED to the previous utterance
    (SUPERSEDE_PREVIOUS, same utterance_id, merged text) instead of
    silently starting an unrelated new utterance -- this is what fixes
    the 116/118 "continuation" cases (as opposed to BUG-D's 2/118
    "correction" cases) that caused fragmented translations and
    word-level content loss at artificial cut points in the real test
    data.
    """

    def setUp(self) -> None:
        self.session_id = "sess-buge"
        cir.reset_for_session(self.session_id)
        self.owner = UtteranceLifecycleOwner(host=None, commit_fallback_ms=250)
        self.owner.reset_for_session(self.session_id)

    def _register_commit_identity(self, decision) -> str:
        """Simulates duplicate_protection.py's commit path -- the only
        current place canonical_identity_registry actually gets populated
        for a real commit."""
        record_id = f"rec-{decision.utterance_id}"
        cir.observe_identity(
            session_id=self.session_id,
            channel_index=0,
            canonical_utterance_id=decision.utterance_id,
            provider_utterance_id="",
            source_version=1,
            decision="CREATE_NEW",
            text=decision.text,
            lifecycle_state="COMMITTED",
            translation_eligible=True,
        )
        cir.assign_canonical_record_id(
            session_id=self.session_id,
            channel_index=0,
            canonical_utterance_id=decision.utterance_id,
            canonical_record_id=record_id,
        )
        return record_id

    def test_continuation_after_fallback_commit_is_appended_not_dropped(self) -> None:
        # First chunk held as incomplete (speech_final=False), then forced
        # through the real inactivity-fallback path via on_timeout() --
        # this is the actual production trigger for commit_reason ==
        # "inactivity_timeout_fallback", not a hand-set field.
        held = self.owner.on_final_chunk(
            text="Although it was raining heavily outside",
            speaker=1,
            channel=0,
            start=0.0,
            end=2.0,
            is_final=True,
            speech_final=False,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(held.decision, "HOLD_FINAL_CHUNK")
        fallback_commit = self.owner.on_timeout(token=self.owner._timeout_token)
        self.assertIsNotNone(fallback_commit)
        self.assertEqual(fallback_commit.reason, "inactivity_timeout_fallback")
        self.assertEqual(
            self.owner._last_committed.commit_reason, "inactivity_timeout_fallback"
        )
        self._register_commit_identity(fallback_commit)

        # Deliberately dissimilar wording -- the real-world pattern (a
        # genuine sentence continuation, not a same-word correction).
        continuation = self.owner.on_final_chunk(
            text="we decided to stay in the cozy living room, drink hot tea",
            speaker=1,
            channel=0,
            start=2.2,
            end=4.5,
            is_final=True,
            speech_final=True,
            event_id="ev-2",
            metadata={},
        )
        self.assertEqual(continuation.decision, "SUPERSEDE_PREVIOUS")
        self.assertEqual(
            continuation.utterance_id,
            fallback_commit.utterance_id,
            "a continuation-extend must keep the SAME canonical utterance id",
        )
        self.assertIn(
            continuation.reason,
            ("premature_continuation_extend", "extend_then_commit"),
            "reason should reflect the new BUG-E extend path, not a plain new commit",
        )
        self.assertEqual(
            continuation.text,
            "Although it was raining heavily outside, we decided to stay in "
            "the cozy living room, drink hot tea",
            "text must be the two chunks merged via _merge_lexical, not "
            "just the second chunk alone (which would silently drop the "
            "first half of the sentence)",
        )
        self.assertTrue(continuation.superseded_record_id)

    def test_continuation_after_confident_commit_starts_new_utterance(self) -> None:
        """Negative case: if the previous commit was a CONFIDENT boundary
        (speech_final=True, not a fallback guess), a dissimilar follow-up
        must NOT be merged -- it's a genuinely new, separate utterance.
        Proves the commit_reason gate actually discriminates."""
        confident = self.owner.on_final_chunk(
            text="Although it was raining heavily outside",
            speaker=1,
            channel=0,
            start=0.0,
            end=2.0,
            is_final=True,
            speech_final=True,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(
            self.owner._last_committed.commit_reason,
            "speech_final",
        )
        self._register_commit_identity(confident)

        next_utterance = self.owner.on_final_chunk(
            text="we decided to stay in the cozy living room, drink hot tea",
            speaker=1,
            channel=0,
            start=2.2,
            end=4.5,
            is_final=True,
            speech_final=True,
            event_id="ev-2",
            metadata={},
        )
        self.assertNotEqual(next_utterance.decision, "SUPERSEDE_PREVIOUS")
        self.assertNotEqual(next_utterance.utterance_id, confident.utterance_id)


class BugFLockFreeDuringPublishCallbackTests(unittest.TestCase):
    """BUG-F (critical): commit/interim publish callbacks must run with
    self._lock genuinely free, for every public entry point that can
    produce one -- confirmed via a real thread dump that a background
    thread calling into Tkinter while holding this lock could deadlock
    against the main thread's own lock-needing work. The callback itself
    attempts a non-blocking acquire of the SAME lock object the owner
    uses internally; if it succeeds, the lock was genuinely free at the
    moment the callback ran (the strongest possible proof short of an
    actual concurrent repro).
    """

    def setUp(self) -> None:
        self.session_id = "sess-bugf"
        self.owner = UtteranceLifecycleOwner(
            host=None,
            commit_fallback_ms=250,
            on_commit=self._on_commit,
            on_interim_update=self._on_interim,
        )
        self.owner.reset_for_session(self.session_id)
        self.lock_was_free_on_commit: list[bool] = []
        self.lock_was_free_on_interim: list[bool] = []

    def _on_commit(self, decision) -> None:
        acquired = self.owner._lock.acquire(blocking=False)
        self.lock_was_free_on_commit.append(bool(acquired))
        if acquired:
            self.owner._lock.release()

    def _on_interim(self, decision) -> None:
        acquired = self.owner._lock.acquire(blocking=False)
        self.lock_was_free_on_interim.append(bool(acquired))
        if acquired:
            self.owner._lock.release()

    def test_on_final_chunk_commit_path_runs_with_lock_free(self) -> None:
        decision = self.owner.on_final_chunk(
            text="hello world testing the lock",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=True,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(decision.decision, "COMMIT_ACTIVE")
        self.assertTrue(
            self.lock_was_free_on_commit,
            "on_commit callback was never invoked",
        )
        self.assertTrue(
            all(self.lock_was_free_on_commit),
            "self._lock must be free (acquirable) during every on_commit "
            "callback -- BUG-F's confirmed deadlock happened because a "
            "publish callback ran while the lock was still held",
        )

    def test_on_timeout_commit_path_runs_with_lock_free(self) -> None:
        # This is the exact call site from the confirmed thread dump: the
        # main thread's .after()-scheduled callback re-entering on_timeout.
        held = self.owner.on_final_chunk(
            text="a held utterance awaiting fallback commit",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=False,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(held.decision, "HOLD_FINAL_CHUNK")
        fired = self.owner.on_timeout(token=self.owner._timeout_token)
        self.assertIsNotNone(fired)
        self.assertEqual(fired.decision, "COMMIT_ACTIVE")
        self.assertTrue(
            self.lock_was_free_on_commit,
            "on_commit callback was never invoked from on_timeout",
        )
        self.assertTrue(
            all(self.lock_was_free_on_commit),
            "self._lock must be free during the on_timeout-triggered "
            "commit callback -- this is the exact deadlocking call site "
            "confirmed in the real thread dump",
        )

    def test_held_final_chunk_interim_callback_runs_with_lock_free(self) -> None:
        held = self.owner.on_final_chunk(
            text="a held utterance triggers an interim update",
            speaker=1,
            channel=0,
            start=0.0,
            end=1.0,
            is_final=True,
            speech_final=False,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(held.decision, "HOLD_FINAL_CHUNK")
        self.assertTrue(
            self.lock_was_free_on_interim,
            "on_interim_update callback was never invoked",
        )
        self.assertTrue(all(self.lock_was_free_on_interim))


class BugG1ExtendFallsBackInsteadOfDroppingTextTests(unittest.TestCase):
    """BUG-G1 (critical, silent content loss): when _extend_committed_locked
    can't resolve the previous utterance's identity in the canonical
    registry (a real timing race BUG-D's own spec already flagged as
    possible), the candidate text must be preserved as its own committed
    utterance -- never silently discarded. Deliberately does NOT register
    the fallback commit's identity in canonical_identity_registry, which
    reproduces the exact registry-miss condition BUG-G1 addresses.
    """

    def setUp(self) -> None:
        self.session_id = "sess-bugg1"
        cir.reset_for_session(self.session_id)
        self.owner = UtteranceLifecycleOwner(host=None, commit_fallback_ms=250)
        self.owner.reset_for_session(self.session_id)

    def test_unresolvable_continuation_is_preserved_as_new_utterance(self) -> None:
        held = self.owner.on_final_chunk(
            text="Although it was raining heavily outside",
            speaker=1,
            channel=0,
            start=0.0,
            end=2.0,
            is_final=True,
            speech_final=False,
            event_id="ev-1",
            metadata={},
        )
        self.assertEqual(held.decision, "HOLD_FINAL_CHUNK")
        fallback_commit = self.owner.on_timeout(token=self.owner._timeout_token)
        self.assertIsNotNone(fallback_commit)
        self.assertEqual(fallback_commit.reason, "inactivity_timeout_fallback")
        # Deliberately NOT registering fallback_commit's identity in
        # canonical_identity_registry -- reproduces the registry-miss race
        # BUG-G1 is about. Before the fix, this made the continuation
        # vanish entirely (IGNORE_DUPLICATE, text discarded, never
        # committed under any id).

        continuation = self.owner.on_final_chunk(
            text="we decided to stay in the cozy living room, drink hot tea",
            speaker=1,
            channel=0,
            start=2.2,
            end=4.5,
            is_final=True,
            speech_final=True,
            event_id="ev-2",
            metadata={},
        )
        self.assertNotEqual(
            continuation.decision,
            "IGNORE_DUPLICATE",
            "the spoken text must not be silently discarded when the "
            "identity registry lookup can't resolve the extend target",
        )
        self.assertEqual(
            continuation.text,
            "we decided to stay in the cozy living room, drink hot tea",
            "the candidate text must survive as its own committed "
            "utterance, not vanish with no trace",
        )
        self.assertNotEqual(
            continuation.utterance_id,
            fallback_commit.utterance_id,
            "since the extend couldn't resolve, this must be a genuinely "
            "new (unmerged, but preserved) utterance, not falsely chained "
            "onto the previous one",
        )


if __name__ == "__main__":
    unittest.main()
