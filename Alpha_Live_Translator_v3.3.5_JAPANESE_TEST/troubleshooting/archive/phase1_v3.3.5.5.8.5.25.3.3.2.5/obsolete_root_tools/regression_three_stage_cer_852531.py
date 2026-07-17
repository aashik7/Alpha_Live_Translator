"""CER backtracking regression tests for V25.3.1."""

from __future__ import annotations

import json
from pathlib import Path

from alpha.constants import APP_VERSION, CER_OPERATION_ACCOUNTING_STRICT
from alpha.utils.cer_backtracking import levenshtein_operation_counts, stage_metrics_from_normalized

VALIDATION_DIR = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.1")
OUTPUT_PATH = VALIDATION_DIR / "regression_three_stage_cer_852531.txt"


def _assert_counts(ref: str, hyp: str, *, s: int, d: int, i: int) -> dict:
    result = levenshtein_operation_counts(ref, hyp)
    assert result["substitutions"] == s, result
    assert result["deletions"] == d, result
    assert result["insertions"] == i, result
    assert result["edit_distance"] == s + d + i, result
    return result


def _run_latest_fixture_check() -> dict:
    project = Path(__file__).resolve().parent
    run_folder = project / "troubleshooting" / "runs" / "v3.3.5.5.8.5.25.3-20260713-123233"
    reference = project / "troubleshooting" / "accuracy_benchmark" / "reference_transcripts" / "test01.txt"
    if not run_folder.exists() or not reference.exists():
        return {"skipped": True}
    import re

    def norm(text: str) -> str:
        lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
            if m:
                line = m.group(1).strip()
            lines.append(line)
        return re.sub(r"\s+", "", "".join(lines))

    ref = norm(reference.read_text(encoding="utf-8"))
    results = {}
    for label, name in (
        ("raw_deepgram", "raw_deepgram.txt"),
        ("stable_assembler", "stable_assembler_only.txt"),
    ):
        path = run_folder / "accuracy_stage_compare" / name
        if not path.exists():
            continue
        hyp = norm(path.read_text(encoding="utf-8"))
        metrics = stage_metrics_from_normalized(hyp, ref)
        cer = float(metrics.get("cer") or 0)
        s = int(metrics.get("substitution_count") or 0)
        d = int(metrics.get("deletion_count") or 0)
        i = int(metrics.get("insertion_count") or 0)
        assert cer == 0 or (s + d + i) > 0, metrics
        results[label] = metrics
    return {"skipped": False, "results": results}


def main() -> int:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"CER regression {APP_VERSION}", f"CER_OPERATION_ACCOUNTING_STRICT={CER_OPERATION_ACCOUNTING_STRICT}", ""]

    r0 = _assert_counts("abc", "abc", s=0, d=0, i=0)
    lines.append(f"perfect match: CER={r0['cer']} S={r0['substitutions']} D={r0['deletions']} I={r0['insertions']}")

    r1 = _assert_counts("abc", "abd", s=1, d=0, i=0)
    lines.append(f"one substitution: S={r1['substitutions']}")

    r2 = _assert_counts("abc", "ac", s=0, d=1, i=0)
    lines.append(f"one deletion: D={r2['deletions']}")

    r3 = _assert_counts("abc", "abcd", s=0, d=0, i=1)
    lines.append(f"one insertion: I={r3['insertions']}")

    r4 = levenshtein_operation_counts("本日は決算概要について", "本日は決算概要についてご説明いたします。")
    assert r4["edit_distance"] == r4["substitutions"] + r4["deletions"] + r4["insertions"]
    lines.append(
        f"mixed Japanese extension: edit={r4['edit_distance']} S+D+I={r4['substitutions']+r4['deletions']+r4['insertions']}"
    )

    fixture = _run_latest_fixture_check()
    lines.append("")
    lines.append(f"latest-run fixture: {json.dumps(fixture, ensure_ascii=False)}")
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print("PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
