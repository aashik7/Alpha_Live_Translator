"""CER regression for V25.3.2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.utils.cer_backtracking import levenshtein_operation_counts

OUT = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.2/regression_cer_852532.txt")
PREPARED = Path("troubleshooting/accuracy_benchmark/prepared/v3.3.5.5.8.5.25.3.2/reference_snapshot.json")


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"regression_cer {APP_VERSION}", ""]
    r0 = levenshtein_operation_counts("abc", "abc")
    assert r0["substitutions"] == r0["deletions"] == r0["insertions"] == 0
    lines.append("PASS perfect match")
    r1 = levenshtein_operation_counts("abc", "abd")
    assert r1["substitutions"] == 1
    lines.append("PASS substitution")
    r2 = levenshtein_operation_counts("abc", "ac")
    assert r2["deletions"] == 1
    lines.append("PASS deletion")
    r3 = levenshtein_operation_counts("abc", "abcd")
    assert r3["insertions"] == 1
    lines.append("PASS insertion")
    if PREPARED.exists():
        snap = json.loads(PREPARED.read_text(encoding="utf-8"))
        lines.append(f"reference_snapshot_sha256={snap.get('snapshot_sha256')}")
        lines.append(f"valid_for_cer={snap.get('valid_for_cer')}")
    lines.append("")
    lines.append("PASSED")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
