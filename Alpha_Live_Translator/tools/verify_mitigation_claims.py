"""Re-check every structural claim in mitigation.md, mechanically.

Run:  python tools/verify_mitigation_claims.py

Prints PASS/FAIL per claim. Exists because the findings in mitigation.md were
first written up from working memory after the investigation, and the write-up
carried errors the investigation itself did not. Claims that can be checked by a
machine should not rest on anyone recalling them correctly -- including mine.

Every FAIL is either a real change in the code (a finding has been fixed, so
update mitigation.md) or a defect in the check itself. Read the code before
concluding which.
"""
import ast, io, re, subprocess, sys
from pathlib import Path
R = Path(r"C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator")

def src(f): return io.open(R/f, encoding="utf-8").read()
def tree(f): return ast.parse(src(f))
def fn(f, name):
    for n in ast.walk(tree(f)):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name: return n
    return None
def count(pattern, path="alpha", extra=()):
    out = subprocess.run(["grep","-rn",pattern,"--include=*.py",path,*extra],
                         cwd=R, capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if "/build/" not in l]

res=[]
def check(label, ok, detail):
    res.append((label, ok, detail))

# ---- A1 crash_guard ------------------------------------------------------
f="alpha/utils/crash_guard_log.py"; w=fn(f,"_writer_loop")
loops=[n for n in ast.walk(w) if isinstance(n,ast.While)]
has_try=any(isinstance(n,ast.Try) for l in loops for n in ast.walk(l))
check("A1 crash_guard _writer_loop has NO try in its loop", not has_try,
      f"{len(loops)} while loop(s), try present={has_try}")
resets=[l for l in count("_writer_started = False", "alpha/utils/crash_guard_log.py")]
check("A1 _writer_started is never reset (no restart possible)", len(resets)==1 and resets[0].strip().startswith("26:"),
      f"only site: {resets[0].strip() if resets else 'none'}")

# ---- A2 diagnostic_test_log ---------------------------------------------
f="alpha/utils/diagnostic_test_log.py"; w=fn(f,"_writer_loop")
loops=[n for n in ast.walk(w) if isinstance(n,ast.While)]
has_try=any(isinstance(n,ast.Try) and any(h.type is None or "Exception" in ast.dump(h) for h in n.handlers)
            for l in loops for n in l.body if isinstance(n,ast.Try))
check("A2 diagnostic _writer_loop loop body cannot swallow", not has_try, f"swallowing try in loop body={has_try}")
resets=count("_writer_started = False","alpha/utils/diagnostic_test_log.py")
check("A2 _writer_started never reset", len(resets)==1, f"sites={len(resets)}")

# ---- A3 wasapi -----------------------------------------------------------
f="alpha/audio/wasapi.py"; w=fn(f,"_wasapi_device_watch_worker")
top_try=[n for n in w.body if isinstance(n,ast.Try)]
loop_inside_try = any(isinstance(x,ast.While) for t in top_try for x in ast.walk(t))
loop_has_own_try = any(isinstance(x,ast.Try) for t in top_try for wl in ast.walk(t)
                       if isinstance(wl,ast.While) for x in wl.body)
check("A3 wasapi: while sits INSIDE a try, loop body has none",
      loop_inside_try and not loop_has_own_try,
      f"loop inside try={loop_inside_try}, loop body has try={loop_has_own_try}")
check("A3 watch thread has no restart path", len(count("_wasapi_device_watch_thread = threading.Thread","alpha/audio"))==1,
      "single spawn site")

# ---- A4 performance_timeline --------------------------------------------
f="alpha/utils/performance_timeline.py"; s=src(f)
check("A4 start_heartbeat early-returns when thread exists", "if self._heartbeat is not None:" in s, "guard present")
check("A4 _heartbeat_stop is never cleared", "_heartbeat_stop.clear()" not in s, "no .clear() anywhere")

# ---- A5 async logger scheduling -----------------------------------------
callers=count("ensure_async_logger_healthy_non_blocking","alpha",("main.py",))
callers=[c for c in callers if "async_debug_log.py" not in c]
check("A5 repair called from exactly one place, and it is startup",
      len(callers)==2 and all("main.py" in c for c in callers),
      "; ".join(c.split(":")[0]+":"+c.split(":")[1] for c in callers))
s=src("alpha/utils/async_debug_log.py")
check("A5 the dead-writer branch exists and does NOT call the repair",
      'emergency_sync_write("ASYNC_LOG_WRITER_STALLED")' in s
      and "ASYNC_LOG_WRITER_STALLED" in s.split("def _check_queue_health")[1].split("def ")[0]
      and "ensure_async_logger_healthy_non_blocking" not in s.split("def _check_queue_health")[1].split("def ")[0],
      "detection present, repair not called from it")
check("A5 the repair opens with a SYNCHRONOUS write (so per-tick calling is wrong)",
      s.split("def ensure_async_logger_healthy_non_blocking")[1].split("\n")[2].strip().startswith("emergency_sync_write"),
      "first statement is emergency_sync_write")

# ---- A6 stop_finalize watchdog ------------------------------------------
w=fn("alpha/utils/stop_finalize_worker.py","_watchdog_loop")
loops=[n for n in w.body if isinstance(n,ast.While)]
check("A6 stop watchdog loop body has no exception handler",
      loops and not any(isinstance(n,ast.Try) for n in loops[0].body), "checked outermost while")

# ---- B1 degraded mode ----------------------------------------------------
on=count("set_degraded_logging_mode(True)")
off=count("set_degraded_logging_mode(False)")
check("B1 degraded ON has 3 call sites, OFF has 0", len(on)==3 and len(off)==0,
      f"ON={len(on)}  OFF={len(off)}")

# ---- B2 translation quota ------------------------------------------------
s=src("alpha/translation/translation_worker.py")
import inspect
sys.path.insert(0,str(R))
from alpha.translation.translation_worker import TranslationWorker
rearm=[m for m in dir(TranslationWorker) if any(k in m for k in ("resume","rearm","reenable","re_arm"))]
check("B2 no public re-arm exists on TranslationWorker", not rearm, f"candidates={rearm}")

# ---- report --------------------------------------------------------------
print(f"{'RESULT':6} {'CLAIM':62} DETAIL")
bad=0
for label, ok, detail in res:
    if not ok: bad+=1
    print(f"{'PASS ' if ok else 'FAIL '} {label:62.62} {detail}")
print()
print(f"{len(res)-bad}/{len(res)} claims re-verified" + ("" if not bad else f"  --  {bad} FAILED"))
