"""Export lineage coverage pairs each exported line with the commits whose words it holds.

WHAT WAS BROKEN (PENDING_TASKS.md section 0i)
---------------------------------------------
`build_registry_from_export_lines` gave export line i the source commits of
chain entry i -- by POSITION. Any difference in count between the stable
commits and the exported lines (a merged revision, a line the dedupe sweep
removed, a stop-tail line) shifted every pairing after it:

* `...140417` reported 16 commits (`stable-213`..`stable-228`) as
  `valid_segment_loss`; all 16 are in `Alpha_output_FINAL.txt` verbatim, and the
  record-level `export_coverage_report.json` shows 204/204.
* Replaying the real function on the retained runs with one exported line
  REMOVED: in `...140417` it blamed `stable-228` (the last commit) for line
  103; in `...101440` it reported no loss at all.
* Lines beyond the chain were all given the chain's LAST id, and
  `select_final_export_canonical_lines` keeps one line per lineage group -- so
  those lines could be dropped from the export by the lineage lock itself.

WHAT THESE TESTS PIN
--------------------
* every commit whose words are exported is represented, however the counts
  differ -- no false loss
* a genuinely missing line is reported, and as ITS commit
* a line built from several commits carries all of them
* no two exported lines share a commit, so the lineage lock never drops a line
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alpha.transcription import transcript_lineage as tl  # noqa: E402

COMMITS = [
    ("stable-1", "今日は会議にありがとうございます。"),
    ("stable-2", "まず資料を確認しましょう。"),
    ("stable-3", "デザインドキュメントを"),
    ("stable-4", "あら、どれを直さないと駄目だ。"),
    ("stable-5", "明日も十時からなのでお願いします。"),
]


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name)
        (self.run_dir / "transcripts").mkdir()
        (self.run_dir / "accuracy").mkdir()

    def write_commits(self, commits):
        path = self.run_dir / "transcripts" / "stable_commits.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for cid, text in commits:
                fh.write(json.dumps({"stable_commit_id": cid, "stable_text": text}, ensure_ascii=False) + "\n")
        return path

    def coverage(self, lines, commits=COMMITS):
        stable = self.write_commits(commits)
        registry = tl.build_registry_from_export_lines(lines, stable_commits_path=stable)
        report = tl.analyze_lineage_export_coverage(registry, lines, stable_commits_path=stable)
        return registry, report

    def lost(self, report):
        return [i["source_commit_id"] for i in report["valid_segment_loss_items"]]


class NoFalseLossTest(_Case):
    def test_every_exported_commit_is_represented(self):
        _, report = self.coverage([f"Speaker: {t}" for _, t in COMMITS])
        self.assertEqual(self.lost(report), [])

    def test_two_commits_exported_as_one_line(self):
        lines = [
            "Speaker: " + COMMITS[0][1],
            "Speaker: " + COMMITS[1][1],
            "Speaker: " + COMMITS[2][1] + COMMITS[3][1],  # merged in the export
            "Speaker: " + COMMITS[4][1],
        ]
        registry, report = self.coverage(lines)
        self.assertEqual(self.lost(report), [], "the merged line holds both commits' words")
        merged = [r for r in registry.get_active_lines() if COMMITS[3][1] in r["text"]][0]
        self.assertEqual(sorted(merged["source_commit_ids"]), ["stable-3", "stable-4"])


class ARealLossIsNamedTest(_Case):
    def test_a_missing_line_is_reported_as_its_own_commit(self):
        lines = [f"Speaker: {t}" for cid, t in COMMITS if cid != "stable-2"]
        _, report = self.coverage(lines)
        self.assertEqual(self.lost(report), ["stable-2"])


class TheLineageLockNeverDropsALineTest(_Case):
    def test_lines_beyond_the_commits_are_all_exported(self):
        lines = [f"Speaker: {t}" for _, t in COMMITS] + [
            "Speaker: 停止の直前の一言です。",
            "Speaker: もう一つの言葉です。",
        ]
        registry, _ = self.coverage(lines)
        exported = tl.format_export_lines(tl.select_final_export_canonical_lines(registry))
        self.assertEqual(len(exported), len(lines), "the lineage lock dropped exported lines")
        ids = [sid for r in registry.get_active_lines() for sid in r["source_commit_ids"]]
        self.assertEqual(len(ids), len(set(ids)), "two lines share a commit")


if __name__ == "__main__":
    unittest.main()
