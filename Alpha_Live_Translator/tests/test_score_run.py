"""Regression tests for `tools/score_run.py`'s §7 gate checks.

`CLIENT_DELIVERY_SPRINT_v5.md` item 39. Each gate is built against a
minimal on-disk fixture rather than a real run folder, so these stay fast
and do not depend on `troubleshooting/runs/` contents changing.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.score_run import (  # noqa: E402
    FAIL,
    HEURISTIC,
    NOT_MEASURABLE,
    PASS,
    commit_latency_percentiles,
    gate_2_duplicate_export_lines,
    gate_3_untranslated_records,
    gate_5_quarantine_review,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


class _TempRun(unittest.TestCase):
    def setUp(self):
        self.run_folder = Path(tempfile.mkdtemp(prefix="alpha_score_run_test_"))

    def tearDown(self):
        shutil.rmtree(self.run_folder, ignore_errors=True)


class Gate2DuplicateExportLinesTest(_TempRun):
    def test_no_duplicates_passes(self):
        path = self.run_folder / "transcripts" / "Alpha_output_FINAL.txt"
        path.parent.mkdir(parents=True)
        path.write_text("Speaker: a\nSpeaker: b\n", encoding="utf-8")
        result = gate_2_duplicate_export_lines(self.run_folder)
        self.assertEqual(PASS, result["status"])

    def test_exact_duplicate_fails(self):
        """The shape seen on run ...155922: a degenerate short line repeated."""
        path = self.run_folder / "transcripts" / "Alpha_output_FINAL.txt"
        path.parent.mkdir(parents=True)
        path.write_text("Speaker: 。\nSpeaker: b\nSpeaker: 。\n", encoding="utf-8")
        result = gate_2_duplicate_export_lines(self.run_folder)
        self.assertEqual(FAIL, result["status"])
        self.assertEqual({"Speaker: 。": 2}, result["duplicate_lines"])

    def test_missing_export_is_not_measurable_not_a_pass(self):
        result = gate_2_duplicate_export_lines(self.run_folder)
        self.assertEqual(NOT_MEASURABLE, result["status"])

    def test_blank_lines_are_not_counted_as_duplicates(self):
        path = self.run_folder / "transcripts" / "Alpha_output_FINAL.txt"
        path.parent.mkdir(parents=True)
        path.write_text("Speaker: a\n\n\nSpeaker: b\n", encoding="utf-8")
        result = gate_2_duplicate_export_lines(self.run_folder)
        self.assertEqual(PASS, result["status"])
        self.assertEqual(2, result["total_lines"])


class Gate3UntranslatedRecordsTest(_TempRun):
    def test_every_record_translated_passes(self):
        _write_jsonl(
            self.run_folder / "evidence_streams" / "canonical_commits.jsonl",
            [{"canonical_utterance_id": "u1", "text": "a", "synthetic_record": False}],
        )
        _write_jsonl(
            self.run_folder / "evidence_streams" / "translation_jobs.jsonl",
            [{"canonical_utterance_id": "u1", "accepted": True}],
        )
        result = gate_3_untranslated_records(self.run_folder)
        self.assertEqual(PASS, result["status"])

    def test_missing_translation_fails_and_names_the_record(self):
        """The shape seen on one English run: a ledger record with zero
        translation_jobs rows referencing it at all."""
        _write_jsonl(
            self.run_folder / "evidence_streams" / "canonical_commits.jsonl",
            [{"canonical_utterance_id": "u1", "text": "hello", "synthetic_record": False}],
        )
        _write_jsonl(self.run_folder / "evidence_streams" / "translation_jobs.jsonl", [])
        result = gate_3_untranslated_records(self.run_folder)
        self.assertEqual(FAIL, result["status"])
        self.assertEqual(["u1"], [m["uid"] for m in result["missing"]])

    def test_rejected_translation_job_still_counts_as_untranslated(self):
        _write_jsonl(
            self.run_folder / "evidence_streams" / "canonical_commits.jsonl",
            [{"canonical_utterance_id": "u1", "text": "a", "synthetic_record": False}],
        )
        _write_jsonl(
            self.run_folder / "evidence_streams" / "translation_jobs.jsonl",
            [{"canonical_utterance_id": "u1", "accepted": False}],
        )
        result = gate_3_untranslated_records(self.run_folder)
        self.assertEqual(FAIL, result["status"])

    def test_synthetic_records_are_excluded(self):
        _write_jsonl(
            self.run_folder / "evidence_streams" / "canonical_commits.jsonl",
            [{"canonical_utterance_id": "u1", "text": "a", "synthetic_record": True}],
        )
        _write_jsonl(self.run_folder / "evidence_streams" / "translation_jobs.jsonl", [])
        result = gate_3_untranslated_records(self.run_folder)
        self.assertEqual(PASS, result["status"])
        self.assertEqual(0, result["ledger_records"])


class Gate5QuarantineReviewTest(_TempRun):
    def _write_log(self, events: list[dict]) -> None:
        path = self.run_folder / "logs" / "japanese_accuracy.log"
        path.parent.mkdir(parents=True)
        lines = [f"2026-08-11 00:00:00.000 | {json.dumps(e, ensure_ascii=False)}" for e in events]
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_no_quarantine_events_passes(self):
        self._write_log([])
        result = gate_5_quarantine_review(self.run_folder, None)
        self.assertEqual(PASS, result["status"])

    def test_quarantine_without_reference_is_heuristic_not_a_pass(self):
        """Must never silently read as PASS just because there is no
        reference transcript to check against."""
        self._write_log([{"event": "NOISE_FRAGMENT_QUARANTINED", "raw_text": "寝れた、幸せ、"}])
        result = gate_5_quarantine_review(self.run_folder, None)
        self.assertEqual(HEURISTIC, result["status"])
        self.assertEqual(1, result["quarantine_count"])

    def test_quarantined_text_found_in_reference_fails(self):
        self._write_log([{"event": "NOISE_FRAGMENT_QUARANTINED", "raw_text": "real speech here"}])
        result = gate_5_quarantine_review(self.run_folder, "...real speech here...")
        self.assertEqual(FAIL, result["status"])
        self.assertEqual(["real speech here"], result["confirmed_real_speech_lost"])

    def test_quarantined_text_absent_from_reference_stays_heuristic(self):
        self._write_log([{"event": "NOISE_FRAGMENT_QUARANTINED", "raw_text": "genuine noise"}])
        result = gate_5_quarantine_review(self.run_folder, "nothing matching in here")
        self.assertEqual(HEURISTIC, result["status"])
        self.assertEqual([], result["confirmed_real_speech_lost"])

    def test_pipe_delimited_log_format_is_parsed(self):
        """logs/japanese_accuracy.log is `timestamp | {json}`, not plain
        JSONL -- a naive json.loads(line) would fail every line."""
        path = self.run_folder / "logs" / "japanese_accuracy.log"
        path.parent.mkdir(parents=True)
        path.write_text(
            "2026-08-11T00:00:00 | LOG_INITIALIZED\n"
            '2026-08-11 00:00:00.100 | {"event": "NOISE_FRAGMENT_QUARANTINED", "raw_text": "x"}\n',
            encoding="utf-8",
        )
        result = gate_5_quarantine_review(self.run_folder, None)
        self.assertEqual(1, result["quarantine_count"])


class CommitLatencyPercentilesTest(_TempRun):
    def _write_ingress(self, rows: list[dict]) -> None:
        _write_jsonl(self.run_folder / "evidence_streams" / "provider_events.jsonl", rows)

    def _write_ledger(self, rows: list[dict]) -> None:
        _write_jsonl(self.run_folder / "evidence_streams" / "canonical_commits.jsonl", rows)

    def test_latency_is_commit_time_minus_last_matched_ingress(self):
        self._write_ingress(
            [
                {
                    "raw_event_id": "raw-1",
                    "timestamp": 100.0,
                    "confidence": 0.9,
                    "metadata": {"raw_deepgram_text": "x"},
                }
            ]
        )
        self._write_ledger([{"canonical_utterance_id": "u1", "committed_at": 103.5, "source_raw_event_ids": ["raw-1"]}])
        result = commit_latency_percentiles(self.run_folder)
        self.assertEqual(1, result["count"])
        self.assertEqual(3.5, result["p50_s"])

    def test_assembler_re_emission_rows_are_not_used_for_timing(self):
        """A re-emission row (no raw_deepgram_text/confidence) sharing a raw
        id must not be mistaken for a real ingress timestamp."""
        self._write_ingress(
            [
                {"raw_event_id": "raw-1", "timestamp": 999.0, "metadata": {}},  # re-emission, no confidence
            ]
        )
        self._write_ledger([{"canonical_utterance_id": "u1", "committed_at": 103.5, "source_raw_event_ids": ["raw-1"]}])
        result = commit_latency_percentiles(self.run_folder)
        self.assertEqual(0, result["count"])

    def test_negative_latency_is_reported_not_hidden(self):
        """A data-quality anomaly must surface, not be silently dropped
        from the percentile computation."""
        self._write_ingress(
            [{"raw_event_id": "raw-1", "timestamp": 200.0, "confidence": 0.9, "metadata": {"raw_deepgram_text": "x"}}]
        )
        self._write_ledger([{"canonical_utterance_id": "u1", "committed_at": 100.0, "source_raw_event_ids": ["raw-1"]}])
        result = commit_latency_percentiles(self.run_folder)
        self.assertEqual(1, result["count"])
        self.assertEqual(1, len(result["negative_latency_records"]))
        self.assertEqual("u1", result["negative_latency_records"][0]["uid"])

    def test_no_matched_timestamps_reports_zero_count_not_a_crash(self):
        self._write_ingress([])
        self._write_ledger([{"canonical_utterance_id": "u1", "committed_at": 100.0, "source_raw_event_ids": ["raw-missing"]}])
        result = commit_latency_percentiles(self.run_folder)
        self.assertEqual(0, result["count"])


if __name__ == "__main__":
    unittest.main()
