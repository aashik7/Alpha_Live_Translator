"""mitigation.md step 4 — stop the unrecoverable-failure class from coming back.

Steps 1-3 fixed eight instances. This is the part that stops the ninth: the audit
that found them (`tools/audit_unrecoverable_latches.py`) now runs as a test, so a
new one-way flag, or a new thread loop whose outermost body cannot swallow an
exception, turns the suite red instead of waiting for a client to find it.

THE ALLOWLIST IS THE HARD PART
------------------------------
The scan is deliberately over-inclusive: some one-way state is correct (a
one-shot startup guard), and some loops are meant to end. So an allowlist is
unavoidable — and an allowlist is exactly how a guard like this rots into a
blanket suppression that passes forever while the codebase decays under it.

Three rules keep it honest, and all three are enforced below rather than
described:

1. **Every finding must be allowlisted or the test fails.** A new latch is a
   failure, not a diff to review later.
2. **Every allowlist entry must still match a real finding.** An entry whose
   target was deleted or fixed is removed, not left behind "just in case" —
   otherwise the list only ever grows and stops meaning anything.
3. **Every entry carries an EXECUTABLE reason, not prose.** `still_true()` is
   the actual thing that makes the entry safe. If someone deletes the clear
   site, the setter, or the supervisor that the reason names, the entry stops
   being true and the test fails — even though the scanner's own output has not
   changed by one character.

Rule 3 is the one that matters, and it exists because of a recurring failure in
this work: **a checker that cannot see the fix reports the bug forever, and a
checker with a blind spot reports nothing at all.** Both happened here.

`_writer_started` in `japanese_accuracy_log` is the live example. It is the same
latch that cost item 94's sibling findings A1 and A2 their restart path — set
once, never reset, `_start_writer()` a permanent no-op afterwards. It is safe
ONLY because that module's loop body swallows every exception, so the thread
cannot die and the latch is never consulted after a death. That is a property of
one `try/except` twenty lines away, and prose saying "this one is fine" would
outlive its deletion. The entry asserts the swallow instead.

`TheAuditToolItselfStillWorksTest` below earned its place on first run: its
planted-latch probe failed, because the scanner counted a module-level
`_flag = False` initialiser as a live-path clear and therefore skipped exactly
the shape A1 had. crash_guard_log's latch had been found by the thread scan, not
the state scan — had it not also been a thread, this tool would have reported
nothing. Fixing that surfaced four findings that had been invisible, including
the `japanese_accuracy_log` one above.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT = PROJECT_ROOT / "tools" / "audit_unrecoverable_latches.py"


def src(rel: str) -> str:
    return io.open(PROJECT_ROOT / rel, encoding="utf-8").read()


def code_only(rel: str) -> str:
    """Source with comment lines stripped.

    Modules here explain in prose why a latch is gone, and that prose quotes the
    latch. Matching raw source finds the sentence announcing the fix and reports
    the bug. Hit three times across this work; stripped once, here.
    """
    return "\n".join(
        line for line in src(rel).splitlines() if not line.lstrip().startswith("#")
    )


def calls(symbol: str, *paths: str) -> int:
    out = subprocess.run(
        ["grep", "-rn", symbol, "--include=*.py", *(paths or ("alpha",))],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    return len([l for l in out.stdout.splitlines() if "/build/" not in l])


@dataclass(frozen=True)
class Allowed:
    kind: str            # "state" or "thread"
    file: str
    name: str            # attribute/global name, or function name for a thread
    reason: str          # for a human reading the failure
    still_true: Callable[[], bool]   # for the machine

    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.file, self.name)


# ---------------------------------------------------------------------------
# The allowlist. Every entry was checked against the code before being added.
# ---------------------------------------------------------------------------

ALLOWLIST: tuple[Allowed, ...] = (
    # --- one-way state -----------------------------------------------------
    Allowed(
        "state", "alpha/audio/timeline_mixer.py", "self._sys_source_available",
        "Latches ON, not off. A source becoming available is not a failure, and "
        "`reset()` clears it between sessions.",
        lambda: "_sys_source_available = False" in src("alpha/audio/timeline_mixer.py"),
    ),
    Allowed(
        "state", "alpha/audio/timeline_mixer.py", "self._mic_source_available",
        "Same as the system-source flag: latches ON, cleared by `reset()`.",
        lambda: "_mic_source_available = False" in src("alpha/audio/timeline_mixer.py"),
    ),
    Allowed(
        "state", "alpha/transcription/japanese_sentence_assembler.py",
        "self._stop_boundary_active",
        "Set only in `flush()`, and both wrappers that reach it "
        "(`flush_japanese_assembler_on_stop`, `flush_japanese_final_stabilizer`) are "
        "stop-only — they close the transcript gate first, and the first calls "
        "`assembler.reset()`, which clears it. Cannot latch mid-session.",
        lambda: (
            "def flush_japanese_assembler_on_stop"
            in src("alpha/transcription/japanese_final_chunk_stabilizer.py")
            and "assembler.reset()"
            in src("alpha/transcription/japanese_final_chunk_stabilizer.py")
        ),
    ),
    Allowed(
        "state", "alpha/ui/main_window.py", "self.translation_enabled",
        "Re-derived from config at the start of every session "
        "(`_start_translation_session`). The scanner only tracks literal True/False "
        "assignments, so it cannot see the non-literal ones that clear it.",
        lambda: "self.translation_enabled = bool(" in src("alpha/ui/main_window.py"),
    ),
    Allowed(
        "state", "alpha/utils/japanese_accuracy_log.py", "_writer_started",
        "The SAME latch A1 and A2 had -- set once, never reset, `_start_writer()` a "
        "no-op afterwards. Safe here only because this module's `_writer_loop` body "
        "wraps everything in `try/except Exception: continue`, with a comment saying "
        "it must not silently kill the writer thread. The thread cannot die, so the "
        "latch is never consulted after a death. Delete that swallow and this "
        "becomes a real latch in the evidence logger.",
        lambda: "except Exception:" in src("alpha/utils/japanese_accuracy_log.py").split(
            "def _writer_loop"
        )[-1].split("def ")[0],
    ),
    Allowed(
        "state", "alpha/utils/crash_guard_log.py", "_shutdown_requested",
        "Terminal by design: once shutdown is requested the writer must NOT be "
        "restarted, and `_start_writer()` consults it for exactly that reason. The "
        "session is ending, so there is no 'rest of the session' to strand.",
        lambda: "_shutdown_requested" in code_only("alpha/utils/crash_guard_log.py").split(
            "def _start_writer"
        )[-1].split("def ")[0],
    ),
    Allowed(
        "state", "alpha/utils/diagnostic_test_log.py", "_shutdown_requested",
        "Same as the crash-guard shutdown flag: terminal on purpose, consulted so a "
        "late log line cannot resurrect a writer the shutdown just stopped.",
        lambda: "_shutdown_requested" in code_only("alpha/utils/diagnostic_test_log.py"),
    ),
    Allowed(
        "state", "alpha/transcription/japanese_accuracy_cleaner.py",
        "_live_skip_second_idempotency_pass",
        "A one-shot startup decision, not failure state: "
        "`run_business_cleanup_selftest_once()` proves the cleanup is idempotent at "
        "boot and records that the per-segment second pass can be skipped. Nothing "
        "degrades when it is set -- that is the fast path, and it is the 'one-shot "
        "startup guard' the audit tool's own docstring names as legitimate.",
        lambda: "def run_business_cleanup_selftest_once"
        in src("alpha/transcription/japanese_accuracy_cleaner.py"),
    ),

    Allowed(
        "state", "alpha/utils/supervised_thread.py", "self._gave_up",
        "This is the supervisor's own bounded give-up, and `start()` clears it -- "
        "the re-arm is the whole point. If this ever stops being cleared in "
        "`start()`, the thing built to fix the latch class has become an instance "
        "of it.",
        lambda: "self._gave_up = False" in code_only("alpha/utils/supervised_thread.py"),
    ),
    Allowed(
        "state", "alpha/utils/supervised_thread.py", "self._finished_cleanly",
        "Bookkeeping for 'the target returned normally, do not restart it'. Cleared "
        "in `start()` alongside `_gave_up`, so a deliberate stop can be undone.",
        lambda: "self._finished_cleanly = False" in code_only("alpha/utils/supervised_thread.py"),
    ),

    # --- thread loops ------------------------------------------------------
    Allowed(
        "thread", "alpha/utils/async_debug_log.py", "_writer_loop",
        "mitigation.md A5, and deliberately NOT supervised: this module already "
        "had its own repair, and step 3 wired it to the dead-writer detection that "
        "already ran on every enqueue. Wrapping it in a supervisor as well would "
        "duplicate working code. The reason holds only while that wiring exists.",
        lambda: (
            "def _repair_writer_if_dead" in src("alpha/utils/async_debug_log.py")
            and "ensure_async_logger_healthy_non_blocking()"
            in src("alpha/utils/async_debug_log.py").split("def _repair_writer_if_dead")[-1]
        ),
    ),
    Allowed(
        "thread", "alpha/utils/stop_finalize_worker.py", "_watchdog_loop",
        "Still literally true — the loop body has no handler — but it no longer "
        "means death: the handler is the supervisor OUTSIDE the loop, which is the "
        "correct shape. The scanner cannot see a spawn site from a loop body, so "
        "the reason asserts the supervision instead.",
        lambda: "SupervisedThread" in src("alpha/utils/stop_finalize_worker.py"),
    ),
)


def run_audit() -> dict:
    result = subprocess.run(
        [sys.executable, str(AUDIT), "--json"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"the audit tool failed to run:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )
    return json.loads(result.stdout)


def findings() -> set[tuple[str, str, str]]:
    data = run_audit()
    keys = {("state", row["file"], row["name"]) for row in data["one_way_state"]}
    keys |= {("thread", row["file"], row["function"]) for row in data["unsupervised_threads"]}
    return keys


class NoNewUnrecoverableLatchesTest(unittest.TestCase):
    """Rule 1 — a new latch is a build failure, not a review comment."""

    def test_every_finding_is_accounted_for(self):
        allowed = {entry.key() for entry in ALLOWLIST}
        unexplained = sorted(findings() - allowed)
        self.assertEqual(
            [], unexplained,
            "New state or a new thread loop that cannot recover from one failure:\n"
            + "\n".join(f"    {kind:6} {path}  {name}" for kind, path, name in unexplained)
            + "\n\nThis is the item 94 class: a component disables itself and the "
              "path back is unreachable, absent, or wired where it cannot run.\n"
              "Either give it a reachable way back, or add it to ALLOWLIST in this "
              "file WITH an executable reason -- prose alone is how an allowlist "
              "becomes a blanket suppression.",
        )


class TheAllowlistCannotRotTest(unittest.TestCase):
    """Rules 2 and 3 — the list stays exact, and the reasons stay true."""

    def test_no_stale_entries(self):
        current = findings()
        stale = sorted(entry.key() for entry in ALLOWLIST if entry.key() not in current)
        self.assertEqual(
            [], stale,
            "These allowlist entries no longer match anything the audit reports:\n"
            + "\n".join(f"    {kind:6} {path}  {name}" for kind, path, name in stale)
            + "\n\nThe code was fixed or deleted. Remove the entry -- an allowlist "
              "that only ever grows stops meaning anything, and a stale entry will "
              "silently cover a future regression at the same location.",
        )

    def test_every_reason_still_holds(self):
        broken = []
        for entry in ALLOWLIST:
            try:
                ok = bool(entry.still_true())
            except Exception as exc:  # noqa: BLE001
                ok, exc_note = False, f" ({type(exc).__name__}: {exc})"
            else:
                exc_note = ""
            if not ok:
                broken.append(f"    {entry.file}  {entry.name}{exc_note}\n"
                              f"        reason: {entry.reason}")
        self.assertEqual(
            [], broken,
            "These entries are allowlisted for a reason that is no longer true:\n"
            + "\n".join(broken)
            + "\n\nThe finding was safe BECAUSE of what the reason asserts. That "
              "assertion now fails, so the entry is no longer justified -- the "
              "latch it covers may now be real.",
        )

    def test_every_entry_carries_a_reason_a_human_can_act_on(self):
        for entry in ALLOWLIST:
            with self.subTest(entry=entry.name):
                self.assertGreaterEqual(
                    len(entry.reason), 60,
                    f"{entry.file}:{entry.name} is allowlisted with a reason too "
                    "short to be checkable by the next reader",
                )

    def test_the_allowlist_has_no_duplicates(self):
        keys = [entry.key() for entry in ALLOWLIST]
        self.assertEqual(
            len(keys), len(set(keys)),
            "a duplicated allowlist key hides which entry is actually in force",
        )


class TheAuditToolItselfStillWorksTest(unittest.TestCase):
    """A guard whose scanner silently stopped scanning would pass forever.

    This is the same failure as item 94's stall detector, which classified the
    stall correctly and then could not report it.
    """

    def test_the_audit_finds_something(self):
        data = run_audit()
        total = len(data["one_way_state"]) + len(data["unsupervised_threads"])
        self.assertGreater(
            total, 0,
            "the audit reports nothing at all -- it has stopped scanning, and a "
            "guard that finds nothing passes forever",
        )

    def test_it_still_recognises_a_planted_latch(self):
        """End-to-end on a synthetic module, so a broken scanner cannot pass."""
        import tempfile
        import textwrap

        planted = textwrap.dedent(
            '''
            import threading

            _blown = False


            def trip() -> None:
                global _blown
                _blown = True


            def consult() -> bool:
                return _blown


            def _loop() -> None:
                while True:
                    consult()


            threading.Thread(target=_loop).start()
            '''
        )
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "alpha" / "utils"
            pkg.mkdir(parents=True)
            (pkg / "planted_latch.py").write_text(planted, encoding="utf-8")
            (Path(tmp) / "tools").mkdir()
            probe = Path(tmp) / "tools" / "audit_unrecoverable_latches.py"
            probe.write_text(AUDIT.read_text(encoding="utf-8"), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(probe), "--json"],
                cwd=tmp, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(0, result.returncode, result.stderr[-2000:])
            data = json.loads(result.stdout)

        states = {row["name"] for row in data["one_way_state"]}
        threads = {row["function"] for row in data["unsupervised_threads"]}
        self.assertIn(
            "_blown", states,
            "the scanner missed a global set True on a live path, read elsewhere, "
            "and never cleared -- the exact shape it exists to find",
        )
        self.assertIn(
            "_loop", threads,
            "the scanner missed a thread loop whose body cannot swallow an "
            "exception",
        )


if __name__ == "__main__":
    unittest.main()
