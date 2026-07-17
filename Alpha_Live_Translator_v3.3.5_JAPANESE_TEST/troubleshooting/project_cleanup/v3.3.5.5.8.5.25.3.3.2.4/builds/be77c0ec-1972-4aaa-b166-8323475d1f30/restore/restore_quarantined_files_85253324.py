"""Restore quarantined files for V25.3.3.2.4 cleanup."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRIES = json.loads((ROOT / "QUARANTINE_RESTORE_ENTRIES.json").read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    failed = 0
    restored = 0
    for e in ENTRIES:
        q = Path(e["quarantine_path"])
        orig = Path(e["original_path"])
        if not q.exists():
            print(f"MISSING_QUARANTINE={q}")
            failed += 1
            continue
        expected = e.get("sha256_before")
        if q.is_file() and expected:
            got = sha256_file(q)
            if got != expected:
                print(f"HASH_MISMATCH={q}")
                failed += 1
                continue
        if orig.exists():
            if orig.is_file() and q.is_file() and sha256_file(orig) == sha256_file(q):
                print(f"ALREADY_PRESENT_SAME={orig}")
                restored += 1
                continue
            print(f"REFUSE_OVERWRITE_DIFFERENT={orig}")
            failed += 1
            continue
        orig.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(q), str(orig))
        print(f"RESTORED={orig}")
        restored += 1
    print(f"restored={restored}")
    print(f"failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
