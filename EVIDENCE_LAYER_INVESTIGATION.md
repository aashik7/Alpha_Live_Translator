# Evidence Layer Investigation — `stage_capture_complete` / `no_finalizer_exceptions`

Investigation only, per request — no code changes.

## Answer, up front

**No — `stage_capture_complete`, `no_finalizer_exceptions`, and the
related `trusted_for_scoring`/`reference_visible_to_runtime`/
`reference_not_yet_scored` fields do not affect anything the end user
experiences.** They belong to a separate, read-only Japanese accuracy
diagnostic/benchmark layer that runs *after* the live pipeline has
already finished writing everything the user sees. This is the same kind
of finding as the earlier `completed_pending_evidence_package` result —
a "false"/incomplete value here is expected and by-design in plenty of
legitimate runs (English sessions, sessions with the diagnostic disabled,
etc.), not a production defect. Task 14's fix did not need to, and does
not, touch this layer.

---

## 1. Where `stage_manifest.json` is written, and what computes the two named fields

Both fields are computed inside
`alpha/utils/accuracy_stage_capture.py`, a module whose own header
declares its scope precisely:

> `"""Three-stage Japanese accuracy diagnostic capture (8.5.25.3)."""`
> — [accuracy_stage_capture.py:1](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1)

- **`no_finalizer_exceptions`** is computed at
  [accuracy_stage_capture.py:1103](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1103),
  inside `evaluate_stage_capture_critical_checks()`
  ([:1079](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1079)):
  `_check("no_finalizer_exceptions", not finalizer_errors)` — true iff no
  exception was recorded during this diagnostic module's *own* internal
  bookkeeping (`_log_finalizer_exception`, [:528](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:528)),
  not the live Stop sequence's own error tracking.

- **`stage_capture_complete`** is the aggregate of ~15-20 checks run by
  that same function ([:1079-1160](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1079)):
  file-existence checks for each diagnostic stage artifact
  (`raw_stage_exists`, `assembler_events_exist`, ...), audio-counter
  cross-checks, hash-match/coverage checks, and (Japanese-only)
  ledger-lineage-coverage checks. `complete = not failed` at
  [:1153](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1153).
  The result is folded into the larger manifest object built by
  `build_stage_manifest()` at
  [:1688](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1688)
  and [:1824](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1824).

- **Written to disk** as `accuracy_stage_compare/stage_manifest.json` by
  `finalize_accuracy_stage_artifacts()`, via
  `get_accuracy_stage_compare_path("stage_manifest", run_folder)`
  ([:2020](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:2020),
  [:2215](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:2215)).

- **Called from the live Stop sequence** exactly once, but in a way that
  discards its result entirely:
  `stop_finalize_worker.py::_invoke_three_stage_finalize_once()`
  ([:1228-1248](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:1228))
  calls `finalize_three_stage_on_stop(host, run_folder=run_folder)` and
  never reads its return value; that whole call is itself wrapped in
  `run_timed_step(host, "three_stage_finalize", ...)`
  ([stop_finalize_worker.py:1771](Alpha_Live_Translator/alpha/utils/stop_finalize_worker.py:1771))
  whose own boolean result is *also* discarded (a bare statement, not
  assigned to anything). `"three_stage_finalize"` does not appear in
  `_REQUIRED_SYNC_STEPS` and no `_mark_required_step(...)` call anywhere
  in the codebase references it, `stage_capture_complete`, or
  `no_finalizer_exceptions` — confirmed by grep across
  `stop_finalize_worker.py`. This step's outcome cannot influence
  `compute_core_final_status()`, `final_status`, `RUN_MANIFEST.json`, or
  the `STOP_FINALIZE_COMPLETED` log event Task 12/14 hardened.

`finalize_three_stage_on_stop()`'s own docstring confirms it is
observational, not generative:
> `"""Invoke stage finalization after Stop — read-only for sealed Final
> Alpha."""` — [:1914](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1914)

It reads the *already-sealed* `Alpha_output_FINAL.txt` (via
`verify_final_export_seal`, [:1936](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1936))
to cross-check it against the diagnostic capture streams — it never
writes that file, and if the seal can't be verified or the file doesn't
exist yet, it just returns an error dict for its own manifest, it does
not block or alter Stop.

---

## 2. What `reference_visible_to_runtime`, `trusted_for_scoring`, `reference_not_yet_scored` mean

All three are set inside the same `build_stage_manifest()` return object
([accuracy_stage_capture.py:1781-1788](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1781)):

```python
"reference_visible_to_runtime": False,
...
"trusted_for_scoring": trusted_for_scoring,
...
"reference_not_yet_scored": True,
```

- **`reference_visible_to_runtime`**: hard-coded `False` — a fixed
  assertion, written into every manifest, that the live recognition
  runtime never had access to any reference/ground-truth transcript
  while running. This is a guardrail *statement*, not a computed gate on
  anything — it exists so a later scoring pass can trust the run wasn't
  contaminated by test-answer leakage.
- **`trusted_for_scoring`** ([:1741-1748](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1741)):
  `True` only if `stage_capture_complete` AND all three diagnostic stages
  individually completed AND the final-output hash matches AND the run
  wasn't offline-repaired. This is the flag a *separate accuracy-scoring
  pass* checks before trusting this run's artifacts as input to a
  reference-transcript comparison (word-error-rate style benchmarking).
- **`reference_not_yet_scored`**: hard-coded `True` — a placeholder
  stating no scoring pass has run against this manifest yet; it exists
  for that same later, offline scoring workflow to update.

**Who actually reads these fields?** Grepped every consumer repo-wide:
- `canonical_acceptance_state.py:406` and `multidomain_gate_evidence.py:1650-1655`
  read `stage_capture_complete` from the persisted manifest file.
- `multidomain_gate_evidence.py` itself states its scope in its own
  header: *"Benchmark-only instrumentation and post-run analysis helpers.
  Must not load reference/truth during live recognition."*
  ([:1-4](Alpha_Live_Translator/alpha/utils/multidomain_gate_evidence.py:1)).
  The specific function that reads `stage_capture_complete`,
  `build_stop_evidence_reconciliation()`
  ([:1546](Alpha_Live_Translator/alpha/utils/multidomain_gate_evidence.py:1546)),
  has exactly three callers in the whole repository, and every one is a
  standalone top-level script, not part of the live app:
  `regression_multidomain_evidence_repair_85264.py`,
  `run_multidomain_evidence_repair_85264.py`,
  `run_multidomain_gate_85262.py`.
- `main_window.py` and `deepgram_client.py` do import from
  `multidomain_gate_evidence.py`, but only the observational recorder
  functions (`record_lifecycle_event`, `note_normalized_chunk_queued`,
  `note_queue_drop_discard_pending`, `activate_/deactivate_benchmark_evidence`),
  every call site gated behind `is_multidomain_benchmark_mode()` — none
  of them read `stage_capture_complete`/`trusted_for_scoring` back, and
  none feed a decision into the live pipeline.

**Conclusion for Q2**: this is a reference-transcript accuracy-scoring
workflow, run entirely offline/separately from the live user pipeline. It
does not gate UI display, translation delivery, or file output.

---

## 3. Does any user-facing behavior depend on `stage_capture_complete`?

**No — confirmed by tracing every consumer, not just by absence of
evidence:**

- **UI display**: `accuracy_stage_capture.py` has no import of, or
  reference to, any Tk widget, `main_window.py` display method, or the UI
  event bus. The only cross-references from `main_window.py` go the other
  way (into `multidomain_gate_evidence.py`'s benchmark recorders, which
  themselves don't touch `stage_capture_complete`).
- **`Alpha_output_FINAL.txt`**: written by `final_artifact_authority.py`/
  `run_artifacts.py::write_final_alpha_output_from_snapshot`, the same
  pipeline Task 14 investigated — entirely independent of
  `accuracy_stage_capture.py`. `finalize_three_stage_on_stop` only reads
  that file after the fact, seal-verified, and never writes it.
- **Translation delivery**: governed by `translation_worker.py`/the
  canonical ledger/`stop_finalize_worker.py`'s own required-step tracking
  (`translation_drain`, `translation_reconciliation`) — no code path in
  `accuracy_stage_capture.py` touches translation state.
- **`final_status` / `RUN_MANIFEST.json` / `STOP_FINALIZE_COMPLETED`**:
  as shown in section 1, the one live call site
  (`_invoke_three_stage_finalize_once`) discards this module's result
  entirely, and `_REQUIRED_SYNC_STEPS` contains no entry that reads it.

**This is purely an internal/offline accuracy-measurement concern (Q4).**
A `stage_capture_complete: false` (or `no_finalizer_exceptions: false`)
value in `accuracy_stage_compare/stage_manifest.json` is expected and
by-design whenever: the session is English (many of the Japanese-specific
checks don't even apply, though the function still runs and can fail
generic checks), `THREE_STAGE_ACCURACY_DIAGNOSTIC_ENABLED` is off
([:1979](Alpha_Live_Translator/alpha/utils/accuracy_stage_capture.py:1979)),
a diagnostic-only artifact (e.g. `deepgram_request_snapshot`) wasn't
captured for an unrelated reason, or the run was short enough that some
diagnostic stage file genuinely has nothing to compare. None of these
represent a production correctness problem, exactly analogous to the
already-confirmed `completed_pending_evidence_package` finding from
Task 9-12's work.

## 4. Nothing in category 5 applies

No later repair/audit workflow *inside the live app* depends on this
field; the only consumers are the three standalone offline scripts named
above, which are QA/benchmark tooling run manually/separately, not part
of what a user's Start→Stop session executes or waits on.

## Conclusion

**Task 14's fix was already sufficient for production readiness.**
`stage_capture_complete`/`no_finalizer_exceptions` living outside the
required-step gate, and being allowed to be `false`, is correct,
intentional design — this diagnostic layer exists to support a separate,
optional, offline Japanese-accuracy benchmarking workflow, and was
explicitly built (per its own module docstrings) to never influence live
recognition, translation, or export behavior.
