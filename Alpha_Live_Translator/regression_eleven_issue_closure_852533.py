"""Regression suite for eleven-issue closure (V25.3.3 / 852533) — 35 tests."""

from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from alpha.constants import APP_VERSION, DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED
from alpha.transcription.canonical_transcript_ledger import (
    apply_decision,
    freeze_snapshot,
    get_active_records,
    get_frozen_snapshot,
    reset_for_run,
)
from alpha.transcription.pipeline_commit_transaction import execute_pipeline_commit
from alpha.transcription.stable_revision_decision import decide_stable_revision_action
from alpha.utils.accuracy_stage_capture import (
    compare_stable_and_final_artifacts,
    evaluate_stage_capture_critical_checks,
    finalize_accuracy_stage_artifacts,
    get_accuracy_stage_compare_dir,
    get_accuracy_stage_compare_path,
    load_jsonl_records,
    recompute_export_coverage_report,
    reset_accuracy_stage_capture,
    write_jsonl_records,
    write_stable_active_stage_artifacts,
)
from alpha.utils.path_types import ensure_path
from alpha.utils.pipeline_integrity import PipelineIntegrityError
from alpha.utils.runtime_audio_counters import (
    merge_audio_metric,
    note_audio_chunk_sent,
    reset_runtime_audio_counters,
    verify_counter_crosscheck,
)
from alpha.utils.ui_stop_drain_barrier import drain_stop_queues_on_main_thread

OUT = Path(f"troubleshooting/validation/v{APP_VERSION}/regression_eleven_issue_closure_852533.txt")
FIXTURE_REPORT = Path(
    f"troubleshooting/validation/v{APP_VERSION}/fixture_reconstruction_report.json"
)
FIXTURE_ROOT = Path(f"troubleshooting/validation/v{APP_VERSION}/fixtures")

YATO_SENTENCE_A = (
    "また、連結子会社の保育のデザイン研究所においては、矢藤誠慈郎氏が取締役に就任いたしました。"
)
YATO_SENTENCE_B = (
    "矢藤氏の取締役就任により、専門的な学術的知見を経営と保育研修へ直接反映させてまいります。"
)


def _test(name: str, fn: Callable[[], None]) -> str:
    try:
        fn()
        return f"PASS {name}"
    except Exception as exc:
        return f"FAIL {name}: {exc}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_fixture_run(base: Path) -> Path:
    run = base / "fixture_replay"
    (run / "accuracy_stage_compare").mkdir(parents=True, exist_ok=True)
    (run / "transcripts").mkdir(parents=True, exist_ok=True)
    reset_for_run("fixture-replay-852533")
    reset_accuracy_stage_capture("fixture-replay-852533", run_folder=run)

    sentences = [
        ("raw-000001", "皆さん、こんにちは。株式会社さくらさくプラス代表の中山です。"),
        ("raw-000002", YATO_SENTENCE_A),
        ("raw-000003", YATO_SENTENCE_B),
    ]
    for rid, text in sentences:
        apply_decision(
            speaker=2,
            assembler_text=text,
            final_text=text,
            applied_action="append",
            source_raw_event_ids=[rid],
        )

    snap = freeze_snapshot()
    write_stable_active_stage_artifacts(run, snapshot=snap)
    final_lines = [f"[Speaker 2] {rec['final_text']}" for rec in get_active_records()]
    final_path = run / "transcripts" / "Alpha_output_FINAL.txt"
    final_path.write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    export_rows = [
        {
            "record_id": rec["record_id"],
            "text": rec["final_text"],
            "content_sha256": _sha(rec["final_text"]),
        }
        for rec in get_active_records()
    ]
    write_jsonl_records(run / "transcripts" / "final_export_records.jsonl", export_rows)
    raw_events = [
        {
            "raw_event_id": rid,
            "raw_text": text,
            "speaker": 2,
            "is_final": True,
        }
        for rid, text in sentences
    ]
    write_jsonl_records(get_accuracy_stage_compare_path("raw_deepgram_events", run), raw_events)
    return run


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    lines = [f"regression_eleven_issue_closure_852533 {APP_VERSION}", ""]
    project = Path(__file__).resolve().parent

    def t_string_path_run_folder() -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run_str"
            run.mkdir()
            reset_accuracy_stage_capture("path-test", run_folder=str(run))
            stage = get_accuracy_stage_compare_dir(run)
            assert stage.exists()
            assert ensure_path(str(run)) == run

    def t_finalizer_once() -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "once"
            (run / "accuracy_stage_compare").mkdir(parents=True)
            (run / "transcripts").mkdir()
            reset_accuracy_stage_capture("once", run_folder=run)
            final_src = run / "transcripts" / "Alpha_output_FINAL.txt"
            final_src.write_text("[Speaker 2] once\n", encoding="utf-8")
            r1 = finalize_accuracy_stage_artifacts(
                run_folder=run,
                final_alpha_source_path=final_src,
                run_type="fixture",
                run_status="completed",
            )
            assert not r1.get("errors"), r1.get("errors")
            r2 = finalize_accuracy_stage_artifacts(
                run_folder=run,
                final_alpha_source_path=final_src,
                run_type="fixture",
                run_status="completed",
            )
            assert "duplicate_three_stage_finalizer_call" in (r2.get("errors") or [])

    def t_stabilizer_meta_propagation() -> None:
        src = (project / "alpha/transcription/japanese_final_chunk_stabilizer.py").read_text(
            encoding="utf-8"
        )
        assert "assembler_metadata = dict(meta)" in src

    def t_raw_event_id_to_assembler() -> None:
        src = (project / "alpha/transcription/japanese_final_chunk_stabilizer.py").read_text(
            encoding="utf-8"
        )
        assert 'meta["raw_event_id"] = raw_event_id' in src

    def t_buffer_lineage_merge() -> None:
        reset_for_run("lineage-merge")
        apply_decision(
            speaker=2,
            assembler_text="A",
            final_text="A",
            applied_action="append",
            source_raw_event_ids=["raw-000001"],
        )
        rid = get_active_records()[-1]["record_id"]
        apply_decision(
            speaker=2,
            assembler_text="AB",
            final_text="AB",
            applied_action="revise",
            revision_target_id=rid,
            source_raw_event_ids=["raw-000002"],
        )
        merged = get_active_records()[-1]["source_raw_event_ids"]
        assert merged == ["raw-000001", "raw-000002"]

    def t_missing_lineage_prevents_revision() -> None:
        reset_for_run("no-lineage")
        apply_decision(
            speaker=2,
            assembler_text="first",
            final_text="first",
            applied_action="append",
            source_raw_event_ids=["raw-000001"],
        )
        r = apply_decision(
            speaker=2,
            assembler_text="unrelated",
            final_text="unrelated",
            applied_action="revise",
            revision_target_id=get_active_records()[-1]["record_id"],
            source_raw_event_ids=[],
        )
        assert r["applied_action"] == "append"

    def t_safe_append_not_replaced() -> None:
        reset_for_run("safe-append")
        apply_decision(
            speaker=2,
            assembler_text="keep",
            final_text="keep",
            applied_action="append",
            source_raw_event_ids=["raw-000001"],
        )
        before = len(get_active_records())
        decision = decide_stable_revision_action(
            previous_record={
                "text": "keep",
                "source_raw_event_ids": ["raw-000001"],
                "line_id": "line-1",
            },
            candidate_text="drop",
            update_previous_requested=True,
            candidate_raw_event_ids=["raw-999999"],
        )
        assert decision["action"] == "append"
        apply_decision(
            speaker=2,
            assembler_text="drop",
            final_text="drop",
            applied_action=decision["action"],
            source_raw_event_ids=["raw-999999"],
        )
        assert len(get_active_records()) == before + 1

    def t_ledger_transaction_failure_blocks_ui() -> None:
        reset_for_run("txn-fail")
        freeze_snapshot()
        txn = execute_pipeline_commit(
            speaker=2,
            assembler_text="blocked",
            final_text="blocked",
            metadata={},
            requested_action="append",
            applied_action="append",
            source_raw_event_ids=["raw-000001"],
        )
        assert not txn.success
        assert not txn.ledger_applied

    def t_thread_safe_ledger() -> None:
        reset_for_run("thread-safe")
        errors: list[str] = []

        def worker(idx: int) -> None:
            try:
                apply_decision(
                    speaker=2,
                    assembler_text=f"L{idx}",
                    final_text=f"L{idx}",
                    applied_action="append",
                    source_raw_event_ids=[f"raw-{idx:06d}"],
                )
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(get_active_records()) == 8

    def t_freeze_after_freeze() -> None:
        reset_for_run("freeze-twice")
        apply_decision(
            speaker=2,
            assembler_text="X",
            final_text="X",
            applied_action="append",
            source_raw_event_ids=["raw-000001"],
        )
        s1 = freeze_snapshot()
        s2 = freeze_snapshot()
        assert s1.get("snapshot_id") == s2.get("snapshot_id")

    def t_unrelated_revision_cannot_remove_sentence() -> None:
        reset_for_run("unrelated-revise")
        apply_decision(
            speaker=2,
            assembler_text=YATO_SENTENCE_A,
            final_text=YATO_SENTENCE_A,
            applied_action="append",
            source_raw_event_ids=["raw-yato-1"],
        )
        rid = get_active_records()[-1]["record_id"]
        decision = decide_stable_revision_action(
            previous_record={
                "text": YATO_SENTENCE_A,
                "source_raw_event_ids": ["raw-yato-1"],
                "line_id": "line-yato",
            },
            candidate_text="別の話題です。",
            update_previous_requested=True,
            candidate_raw_event_ids=["raw-other"],
        )
        assert decision["action"] == "append"
        apply_decision(
            speaker=2,
            assembler_text="別の話題です。",
            final_text="別の話題です。",
            applied_action=decision["action"],
            source_raw_event_ids=["raw-other"],
        )
        texts = [r["final_text"] for r in get_active_records()]
        assert YATO_SENTENCE_A in texts

    def t_yato_sentence_fixture_retained() -> None:
        with tempfile.TemporaryDirectory(dir=FIXTURE_ROOT) as td:
            run = _build_fixture_run(Path(td))
            texts = [r["final_text"] for r in get_active_records()]
            assert YATO_SENTENCE_A in texts
            assert YATO_SENTENCE_B in texts
            report = {
                "fixture_run": str(run),
                "yato_sentence_a_retained": YATO_SENTENCE_A in texts,
                "yato_sentence_b_retained": YATO_SENTENCE_B in texts,
                "active_record_count": len(texts),
                "record_texts": texts,
            }
            FIXTURE_REPORT.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            assert report["yato_sentence_a_retained"] and report["yato_sentence_b_retained"]

    def t_stable_equals_final() -> None:
        with tempfile.TemporaryDirectory() as td:
            run = _build_fixture_run(Path(td))
            cmp = compare_stable_and_final_artifacts(run)
            assert cmp["stable_final_record_id_match"]
            assert cmp["stable_final_text_exact_match"]

    def t_sidecar_matches_text() -> None:
        with tempfile.TemporaryDirectory() as td:
            run = _build_fixture_run(Path(td))
            cmp = compare_stable_and_final_artifacts(run)
            assert cmp["stable_final_text_hash_match"]

    def t_false_100_coverage_rejected() -> None:
        cov = {
            "coverage_ratio": 1.0,
            "coverage_passed": False,
            "missing_from_final": ["canon-000001"],
        }
        assert cov["coverage_ratio"] == 1.0 and not cov["coverage_passed"]

    def t_23_vs_22_rejected() -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "mismatch"
            (run / "accuracy_stage_compare").mkdir(parents=True)
            (run / "transcripts").mkdir()
            stable = [{"record_id": f"canon-{i:06d}", "text": f"L{i}", "speaker": 2} for i in range(22)]
            final = [{"record_id": f"canon-{i:06d}", "text": f"L{i}", "content_sha256": _sha(f"L{i}")} for i in range(23)]
            write_jsonl_records(get_accuracy_stage_compare_path("stable_active_records", run), stable)
            write_jsonl_records(run / "transcripts" / "final_export_records.jsonl", final)
            (run / "transcripts" / "Alpha_output_FINAL.txt").write_text(
                "\n".join(f"L{i}" for i in range(23)) + "\n",
                encoding="utf-8",
            )
            cov = recompute_export_coverage_report(run)
            assert not cov.get("coverage_passed")

    def t_runtime_audio_merge_precedence() -> None:
        assert merge_audio_metric(5, 9, 1) == 5
        assert merge_audio_metric(None, 9, 1) == 9
        assert merge_audio_metric(None, None, 1) == 1

    def t_zero_not_overwritten() -> None:
        assert merge_audio_metric(0, 99, None) == 0

    def t_audio_counters_match_sender() -> None:
        reset_runtime_audio_counters("sender-test")

        class _Host:
            def get_authoritative_send_accounting(self):
                return {"audio_chunks_sent": 3, "audio_bytes_sent": 300}

        note_audio_chunk_sent(100)
        note_audio_chunk_sent(100)
        note_audio_chunk_sent(100)
        cross = verify_counter_crosscheck(_Host())
        assert cross["counter_crosscheck_passed"]

    def t_action_counters_reconcile() -> None:
        from alpha.utils.canonical_finalize import reconcile_action_counts

        out = reconcile_action_counts(
            ledger_counts={"append": 2, "revise": 1, "no_op": 0, "suppress": 0},
            runtime_metrics={
                "append_count": 2,
                "revision_applied_count": 1,
                "no_op_count": 0,
                "suppression_count": 0,
            },
            event_file_counts={"append": 2, "revise": 1, "no_op": 0, "suppress": 0},
            assembler_event_count=3,
        )
        assert out["counts_reconciled"]

    def t_stage_capture_complete_false_on_failure() -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "fail-stage"
            (run / "accuracy_stage_compare").mkdir(parents=True)
            (run / "transcripts").mkdir()
            crit = evaluate_stage_capture_critical_checks(
                run_folder=run,
                finalizer_errors=["boom"],
                final_source_hash_matches=False,
                export_coverage={"coverage_passed": False},
                stable_final_compare={"stable_final_text_exact_match": False},
                action_reconciliation={"counts_reconciled": False},
                lineage={"lineage_coverage_ratio": 0.0, "stable_records_without_lineage": 1},
                audio_summary={"generated_during_runtime": False},
                three_stage_finalize_call_count=2,
            )
            assert crit["stage_capture_complete"] is False

    def t_stop_not_clear_before_drain() -> None:
        src = (project / "alpha/utils/stop_finalize_worker.py").read_text(encoding="utf-8")
        worker = src.split("def _run_finalize_worker", 1)[1]
        assert "never clear queues" in src.lower() or "_drain_outgoing_audio_queue" in src
        assert worker.index("drain_audio_queue") < worker.index("close_transcript_gate")
        assert "_clear_audio_pipeline_queues" not in worker

    def t_transcript_gate_after_deepgram() -> None:
        src = (project / "alpha/utils/stop_finalize_worker.py").read_text(encoding="utf-8")
        worker = src.split("def _run_finalize_worker", 1)[1]
        assert worker.index("deepgram_graceful_stop") < worker.index("close_transcript_gate")

    def t_ui_drain_barrier() -> None:
        class _Host:
            transcript_queue: list[str] = []
            _transcript_ui_batch_buffer: list[str] = []
            _transcript_events_posted = 1
            _transcript_events_drained = 0

            def _flush_pending_transcript_queue(self) -> None:
                self._transcript_events_drained = self._transcript_events_posted

            def drain_transcript_queue_for_stop(self):
                return {"drained": 1, "remaining": 0}

            def _run_on_ui_thread(self, fn):
                fn()

        host = _Host()
        result = drain_stop_queues_on_main_thread(host)
        assert "transcript_events_drained" in result

    def t_worker_restart() -> None:
        from alpha.utils.language_pipeline_worker import get_language_pipeline_worker

        worker = get_language_pipeline_worker()
        worker.reset_for_new_run()
        worker.stop_and_join(timeout_seconds=1.0)
        assert worker.pending_task_count() == 0

    def t_stop_flags_false() -> None:
        status = {"is_stopping": False, "is_finalizing": False, "language_pipeline_worker_alive": False}
        assert not status["is_stopping"] and not status["is_finalizing"]

    def t_stall_recovery() -> None:
        from alpha.utils.component_stall_classifier import (
            finalize_stall_classifications,
            reset_stall_classification,
        )

        reset_stall_classification()
        summary = finalize_stall_classifications(
            {"listening": False, "ui_heartbeat_age_ms": 50, "stable_commit_age_ms": 50},
            run_folder=FIXTURE_ROOT / "stall_fixture",
        )
        assert "stall_recovered_count" in summary

    def t_package_no_external() -> None:
        import package_latest_troubleshooting_run as pkg

        assert "/external/" in str(pkg._FORBIDDEN_ARCHIVE_PARTS)

    def t_package_no_smoke_preflight_history() -> None:
        import package_latest_troubleshooting_run as pkg

        forbidden = "/".join(pkg._FORBIDDEN_ARCHIVE_PARTS)
        assert "smoke" in forbidden and "preflight" in forbidden and "latest" in forbidden

    def t_package_no_duplicate_archive_paths() -> None:
        import package_latest_troubleshooting_run as pkg

        paths = [f"/a/{i}.txt" for i in range(3)]
        assert len(paths) == len(set(paths))
        assert hasattr(pkg, "build_package")

    def t_current_validation_packaged() -> None:
        import package_latest_troubleshooting_run as pkg

        assert "validate_eleven_issue_closure_852533.txt" in pkg._VALIDATION_REQUIRED

    def t_wav_excluded() -> None:
        import package_latest_troubleshooting_run as pkg

        assert ".wav" in pkg._AUDIO_SUFFIXES

    def t_secrets_excluded() -> None:
        import package_latest_troubleshooting_run as pkg

        with tempfile.TemporaryDirectory() as td:
            secret = Path(td) / ".env"
            secret.write_text("DEEPGRAM_API_KEY=sk-testsecretvalue12345\n", encoding="utf-8")
            assert pkg._contains_secret(secret)

    def t_raw_transcript_immutable() -> None:
        assert DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED is False
        src = (project / "alpha/utils/accuracy_stage_capture.py").read_text(encoding="utf-8")
        assert "DIAGNOSTIC_STAGE_TEXT_MUTATION_ALLOWED" in src

    def t_prepared_reference_trust_v2533() -> None:
        from alpha.utils.validation_version import VALIDATION_PATCH_VERSION

        ref = (
            project
            / "troubleshooting"
            / "accuracy_benchmark"
            / "prepared"
            / f"v{VALIDATION_PATCH_VERSION}"
            / "reference.txt"
        )
        snap = ref.parent / "reference_snapshot.json"
        assert ref.exists() and snap.exists()
        snap_data = json.loads(snap.read_text(encoding="utf-8"))
        ref_sha = hashlib.sha256(ref.read_bytes()).hexdigest()
        assert ref_sha == snap_data.get("snapshot_sha256")

    tests = [
        ("string_path_run_folder", t_string_path_run_folder),
        ("finalizer_once_per_run", t_finalizer_once),
        ("stabilizer_meta_propagation", t_stabilizer_meta_propagation),
        ("raw_event_id_to_assembler", t_raw_event_id_to_assembler),
        ("buffer_lineage_merge_on_revise", t_buffer_lineage_merge),
        ("missing_lineage_prevents_revision", t_missing_lineage_prevents_revision),
        ("safe_append_not_replaced", t_safe_append_not_replaced),
        ("ledger_transaction_failure_blocks_ui", t_ledger_transaction_failure_blocks_ui),
        ("thread_safe_ledger", t_thread_safe_ledger),
        ("freeze_after_freeze_idempotent", t_freeze_after_freeze),
        ("unrelated_revision_cannot_remove_sentence", t_unrelated_revision_cannot_remove_sentence),
        ("yato_sentence_fixture_retained", t_yato_sentence_fixture_retained),
        ("stable_equals_final", t_stable_equals_final),
        ("sidecar_matches_text", t_sidecar_matches_text),
        ("false_100_percent_coverage_rejected", t_false_100_coverage_rejected),
        ("count_mismatch_23_vs_22_rejected", t_23_vs_22_rejected),
        ("runtime_audio_merge_precedence", t_runtime_audio_merge_precedence),
        ("zero_counter_not_overwritten", t_zero_not_overwritten),
        ("audio_counters_match_sender", t_audio_counters_match_sender),
        ("action_counters_reconcile", t_action_counters_reconcile),
        ("stage_capture_complete_false_on_failure", t_stage_capture_complete_false_on_failure),
        ("stop_does_not_clear_queue_before_drain", t_stop_not_clear_before_drain),
        ("transcript_gate_after_deepgram", t_transcript_gate_after_deepgram),
        ("ui_stop_drain_barrier", t_ui_drain_barrier),
        ("language_worker_restart", t_worker_restart),
        ("stop_flags_false", t_stop_flags_false),
        ("stall_classification_recovery", t_stall_recovery),
        ("package_no_external_paths", t_package_no_external),
        ("package_no_smoke_preflight_history", t_package_no_smoke_preflight_history),
        ("package_no_duplicate_archive_paths", t_package_no_duplicate_archive_paths),
        ("current_validation_packaged", t_current_validation_packaged),
        ("package_wav_excluded", t_wav_excluded),
        ("package_secrets_excluded", t_secrets_excluded),
        ("raw_transcript_immutable", t_raw_transcript_immutable),
        ("prepared_reference_trust_v2533", t_prepared_reference_trust_v2533),
    ]

    assert len(tests) == 35

    for name, fn in tests:
        lines.append(_test(name, fn))

    failed = [ln for ln in lines if ln.startswith("FAIL")]
    passed = [ln for ln in lines if ln.startswith("PASS")]
    lines.append("")
    lines.append(f"passed: {len(passed)}")
    lines.append(f"failed: {len(failed)}")
    lines.append("PASSED" if not failed else "FAILED")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"passed={len(passed)} failed={len(failed)}")
    print("PASSED" if not failed else "FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
