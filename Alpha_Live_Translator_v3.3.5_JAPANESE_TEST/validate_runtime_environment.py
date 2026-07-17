"""Validate runtime environment against runtime_environment_contract.json (offline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    contract_path = root / "runtime_environment_contract.json"
    if not contract_path.exists():
        print("FAILED: runtime_environment_contract.json missing")
        return 1
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    py_file = root / ".python-version"
    if not py_file.exists():
        errors.append("missing_.python-version")
    else:
        declared = py_file.read_text(encoding="utf-8").strip()
        actual = f"{sys.version_info.major}.{sys.version_info.minor}"
        if declared != actual and declared != sys.version.split()[0]:
            # Allow major.minor match
            if not actual.startswith(declared.split(".")[0]):
                errors.append(f"python_mismatch:declared={declared}:actual={actual}")

    lock = root / (contract.get("requirements_lock") or "requirements-lock.txt")
    if not lock.exists():
        errors.append("missing_requirements-lock")
    req = root / (contract.get("requirements") or "requirements.txt")
    if not req.exists():
        errors.append("missing_requirements")

    # Import smoke for direct deps listed in requirements.txt
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split(">")[0].split("=")[0].split("!")[0].split("~")[0].strip()
        mod = "PIL" if name == "Pillow" else name.replace("-", "_")
        if name == "websocket-client":
            mod = "websocket"
        if name == "python-dotenv":
            mod = "dotenv"
        if name == "pyaudiowpatch":
            continue  # optional on some hosts
        try:
            __import__(mod)
        except Exception as exc:
            errors.append(f"import_failed:{name}:{exc}")

    if errors:
        print("STATUS=FAILED")
        for e in errors:
            print(f"error={e}")
        return 1
    print("STATUS=PASSED")
    print(f"python={sys.version.split()[0]}")
    print(f"contract={contract_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
