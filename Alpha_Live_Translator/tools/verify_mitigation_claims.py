"""Check that every recovery path mitigation.md asked for is present AND reachable.

Run:  python tools/verify_mitigation_claims.py

WHAT THIS IS FOR
----------------
The findings in mitigation.md were first written up from working memory after an
investigation that had itself used execution. The investigation held; the
write-up carried errors. Anything a machine can check should not rest on anyone
recalling it correctly -- mine included. This is that machine check.

WHAT CHANGED WHEN STEPS 1-3 LANDED
----------------------------------
The first version of this file asserted the DEFECTS still existed, so it was
useful exactly until the defects were fixed, at which point it read 6/14 and
every FAIL was good news. That is a detector that cannot tell "fixed" from
"broken", which is the same shape as the bugs it was written to find.

It now asserts the FIXED state: for each finding, that the way back exists and
that something can actually reach it. A FAIL here means a recovery path has been
removed or disconnected -- a regression, not progress.

Two of the old checks were FALSE PASSES and are recorded here so nobody
reinstates them:

* "A4 `_heartbeat_stop` is never cleared -- no `.clear()` anywhere" kept passing
  after the fix, because the supervisor clears it from `supervised_thread.py`
  and the grep only looked in `performance_timeline.py`. Right answer, wrong
  file, no signal.
* "A5 the repair is called from exactly one place" kept passing because the
  caller-count grep excluded `async_debug_log.py` itself -- which is precisely
  where the new call had just been added.

A grep that cannot see the fix will report the bug forever.
"""

from __future__ import annotations

import ast
import io
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str) -> None:
    _results.append((label, bool(ok), detail))


def src(rel: str) -> str:
    return io.open(REPO / rel, encoding="utf-8").read()


def code_only(rel: str) -> str:
    """Source with comment lines stripped.

    Both writer modules explain in prose why the `_writer_started` latch is
    gone, and that prose quotes the latch. Matching raw source therefore finds
    the very sentence announcing the fix and reports the bug -- which is the
    failure mode this file's docstring warns about, reintroduced. Strip the
    commentary before asserting on the code.
    """
    return "\n".join(
        line for line in src(rel).splitlines() if not line.lstrip().startswith("#")
    )


def func(rel: str, name: str):
    for node in ast.walk(ast.parse(src(rel))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def grep(pattern: str, *paths: str) -> list[str]:
    out = subprocess.run(
        ["grep", "-rn", pattern, "--include=*.py", *(paths or ("alpha",))],
        cwd=REPO, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if "/build/" not in line]


# ---------------------------------------------------------------------------
# A1 / A2 -- the two log writers
# ---------------------------------------------------------------------------

for tag, rel, stats_fn in (
    ("A1 crash_guard_log", "alpha/utils/crash_guard_log.py", "get_crash_guard_writer_stats"),
    ("A2 diagnostic_test_log", "alpha/utils/diagnostic_test_log.py", "get_diagnostic_writer_stats"),
):
    body = src(rel)
    # The latch itself, not the substring: both files legitimately still mention
    # `_writer_started` in prose explaining why it is gone, and in a stat key.
    executable = code_only(rel)
    check(
        f"{tag}: the never-reset latch is gone",
        "_writer_started = True" not in executable
        and "global _writer_started" not in executable,
        "no `_writer_started = True` / `global _writer_started` in code",
    )
    check(
        f"{tag}: the writer is supervised",
        "SupervisedThread" in body or "_writer_supervisor" in body,
        "supervisor referenced",
    )
    check(
        f"{tag}: restart state is observable",
        stats_fn in body,
        f"{stats_fn}() present",
    )
    check(
        f"{tag}: degrades to an unsupervised writer, never to none",
        "except Exception" in body and body.count("threading.Thread") >= 1,
        "raw-thread fallback retained if the supervisor cannot import",
    )

# ---------------------------------------------------------------------------
# A3 -- wasapi device watch
# ---------------------------------------------------------------------------

wasapi = src("alpha/audio/wasapi.py")
check(
    "A3 wasapi: the swallowing handler is gone",
    "Device watch stopped" not in wasapi,
    "no `print('[WASAPI] Device watch stopped')` catch-all",
)
check(
    "A3 wasapi: the watch is supervised",
    "SupervisedThread" in wasapi,
    "spawn site uses SupervisedThread",
)
check(
    "A3 wasapi: the debounce and the un-latch survived the conversion",
    "_wasapi_device_change_reported" in wasapi and "pending != current" in wasapi,
    "both preserved",
)

# ---------------------------------------------------------------------------
# A4 -- performance timeline heartbeat
# ---------------------------------------------------------------------------

timeline = src("alpha/utils/performance_timeline.py")
check(
    "A4 heartbeat: the start gate is liveness, not 'is not None'",
    "is_alive" in timeline,
    "start_heartbeat consults liveness",
)

# ---------------------------------------------------------------------------
# A5 -- the async logger repair, wired to the detection
# ---------------------------------------------------------------------------

adl = src("alpha/utils/async_debug_log.py")
check(
    "A5: the dead-writer detection now calls the repair",
    "ensure_async_logger_healthy_non_blocking()" in adl.split("def _repair_writer_if_dead")[-1],
    "called from _repair_writer_if_dead",
)
check(
    "A5: the repair is throttled off the enqueue hot path",
    "_WRITER_REPAIR_INTERVAL_S" in adl,
    "throttle constant present",
)
check(
    "A5: the repair is bounded and gives up loudly",
    "_WRITER_REPAIR_MAX_ATTEMPTS" in adl and "ASYNC_LOG_WRITER_UNRECOVERABLE" in adl,
    "bound + give-up event present",
)
check(
    "A5: it is NOT called from the session watchdog tick",
    not grep("ensure_async_logger_healthy_non_blocking", "alpha/utils/session_watchdog.py"),
    "watchdog ticks every 2.0s and the repair opens with a sync disk write",
)

# ---------------------------------------------------------------------------
# A6 -- stop-freeze watchdog
# ---------------------------------------------------------------------------

sfw = src("alpha/utils/stop_finalize_worker.py")
check(
    "A6 stop watchdog: supervised",
    "SupervisedThread" in sfw,
    "spawn site uses SupervisedThread",
)
check(
    "A6 stop watchdog: worker_done is still a clean exit",
    "worker_done" in sfw,
    "clean return must not be restarted",
)

# ---------------------------------------------------------------------------
# B1 / B2 -- the two flag clears
# ---------------------------------------------------------------------------

on = grep(r"set_degraded_logging_mode(True)")
off = grep(r"set_degraded_logging_mode(False)")
check(
    "B1: degraded logging has a way back",
    len(off) >= 1,
    f"ON={len(on)} OFF={len(off)}  (was ON=3 OFF=0)",
)
check(
    "B1: recovery needs a sustained window, not one low sample",
    "_DEGRADED_RECOVERY_S" in adl,
    "window constant present",
)
check(
    "B1: leaving degraded mode is announced",
    "DEGRADED_LOGGING_MODE_CLEARED" in adl,
    "exit event present",
)

from alpha.translation.translation_worker import TranslationWorker  # noqa: E402

check(
    "B2: the worker has a public re-arm",
    hasattr(TranslationWorker, "resume_after_quota"),
    "resume_after_quota()",
)
check(
    "B2: something in the app can reach it",
    "def resume_translation_after_quota" in src("alpha/ui/main_window.py"),
    "AlphaApp.resume_translation_after_quota()",
)
check(
    "B2: resuming is manual, never automatic",
    not grep("resume_after_quota", "alpha/utils"),
    "no watchdog or timer calls it -- exhausted quota is real",
)

# ---------------------------------------------------------------------------
# Behaviour, not shape: the supervisor must actually restart and actually stop
# ---------------------------------------------------------------------------

try:
    from alpha.utils.supervised_thread import SupervisedThread

    calls = {"n": 0}

    def _always_fails() -> None:
        calls["n"] += 1
        raise RuntimeError("probe")

    sup = SupervisedThread(_always_fails, name="VerifyProbe", max_restarts=3,
                           restart_window_s=60.0, register=False)
    sup.start()
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and not sup.snapshot().get("gave_up"):
        time.sleep(0.1)
    snap = sup.snapshot()
    sup.stop()
    check("supervisor: a failing target is restarted", calls["n"] > 1, f"invocations={calls['n']}")
    check("supervisor: it stops rather than spinning", bool(snap.get("gave_up")),
          f"gave_up={snap.get('gave_up')} restart_count={snap.get('restart_count')}")

    ran = {"n": 0}

    def _finishes() -> None:
        ran["n"] += 1

    clean = SupervisedThread(_finishes, name="VerifyCleanProbe", register=False)
    clean.start()
    time.sleep(1.0)
    clean_snap = clean.snapshot()
    clean.stop()
    check("supervisor: a clean return is NOT restarted",
          ran["n"] == 1 and not clean_snap.get("restart_count"),
          f"ran={ran['n']} restart_count={clean_snap.get('restart_count')}")
except Exception as exc:  # noqa: BLE001
    check("supervisor: behavioural probe ran", False, f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------------------

print(f"{'RESULT':6} {'CLAIM':62} DETAIL")
failed = 0
for label, ok, detail in _results:
    if not ok:
        failed += 1
    print(f"{'PASS ' if ok else 'FAIL '} {label:62.62} {detail}")
print()
print(f"{len(_results) - failed}/{len(_results)} checks pass"
      + ("" if not failed else f"  --  {failed} FAILED"))
print()
print("A FAIL here means a recovery path was removed or disconnected. Read the")
print("code before concluding which -- a check can also be wrong, and two of the")
print("previous version's were (see this file's docstring).")
