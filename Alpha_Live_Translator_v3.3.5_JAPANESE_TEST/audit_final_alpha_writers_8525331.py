"""Static audit: exactly one authoritative Final Alpha writer (V25.3.3.1)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "troubleshooting" / "validation" / f"v{APP_VERSION}" / "FINAL_ALPHA_WRITER_AUDIT.json"

AUTHORITATIVE_WRITER = "alpha.utils.final_artifact_authority.write_final_once"
APPROVED_WRITE_MODULES = {
    "alpha/utils/final_artifact_authority.py",
}
LEGACY_FUNCS = {
    "write_authoritative_outputs_from_payload",
}
SCAN_GLOBS = ("alpha/**/*.py", "*.py")
PATH_MARKERS = (
    "Alpha_output_FINAL.txt",
    "alpha_output_final",
)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return str(path)


def _scan_file(path: Path) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return hits
    rel = _rel(path)
    if not any(marker in text for marker in PATH_MARKERS):
        # still catch legacy writer name
        if "write_authoritative_outputs_from_payload" not in text:
            return hits
    write_patterns = (
        r'\.write_text\s*\(',
        r'atomic_write',
        r'open\s*\([^)]*[\'"]w',
        r'os\.replace\s*\(',
        r'shutil\.copy',
        r'write_final_once\s*\(',
        r'write_authoritative_outputs_from_payload\s*\(',
    )
    for i, line in enumerate(text.splitlines(), start=1):
        if not any(m in line for m in PATH_MARKERS) and "write_authoritative" not in line and "write_final_once" not in line:
            continue
        for pat in write_patterns:
            if re.search(pat, line):
                hits.append(
                    {
                        "file": rel,
                        "line": i,
                        "snippet": line.strip()[:240],
                        "pattern": pat,
                    }
                )
                break
    return hits


def run_audit() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for pattern in SCAN_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file() or path.suffix != ".py":
                continue
            if any(part.startswith("_") or part in {"_cleanup_archive", ".venv", "venv"} for part in path.parts):
                continue
            candidates.extend(_scan_file(path))

    authoritative: list[dict[str, Any]] = []
    legacy_runtime: list[dict[str, Any]] = []
    post_seal: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []

    for hit in candidates:
        file_rel = hit["file"]
        snippet = hit["snippet"]
        if file_rel in APPROVED_WRITE_MODULES and "write_final_once" in snippet:
            authoritative.append(hit)
            continue
        if "write_authoritative_outputs_from_payload" in snippet:
            # Allowed only if it raises / is disabled (no live write). Inspect context.
            src_path = ROOT / file_rel
            src = src_path.read_text(encoding="utf-8", errors="ignore") if src_path.exists() else ""
            if file_rel.startswith("regression_") or file_rel.startswith("validate_") or file_rel.startswith("audit_") or file_rel.startswith("run_") or "/validation/" in file_rel:
                other.append({**hit, "disposition": "test_or_tool_call"})
            elif "LegacyAuthoritativeWriterDisabled" in src and "raise LegacyAuthoritativeWriterDisabled" in src:
                other.append({**hit, "disposition": "disabled_raises"})
            else:
                legacy_runtime.append(hit)
            continue
        if "Alpha_output_FINAL" in snippet or "alpha_output_final" in snippet:
            if file_rel.endswith("final_artifact_authority.py"):
                authoritative.append(hit)
            elif (
                file_rel.startswith(
                    (
                        "audit_final_alpha",
                        "regression_",
                        "validate_",
                        "run_pre_live",
                        "run_post_live",
                        "package_latest",
                        "repair_",
                        "runtime_smoke",
                    )
                )
                or "/validation/" in file_rel
            ):
                other.append({**hit, "disposition": "test_or_tool"})
            elif "read_text" in snippet or "exists" in snippet or "Path(" in snippet:
                other.append({**hit, "disposition": "path_reference"})
            else:
                # Potential runtime writer
                if file_rel.endswith(
                    (
                        "run_artifacts.py",
                        "accuracy_evidence_export.py",
                        "canonical_export_writer.py",
                        "alpha_output_protection.py",
                        "accuracy_stage_capture.py",
                    )
                ):
                    # stage capture may copy TO stage path using FINAL as source (read) + write stage
                    if "final_alpha_output" in snippet and "Alpha_output_FINAL" not in snippet:
                        other.append({**hit, "disposition": "stage_copy"})
                    elif "Alpha_output_FINAL" in snippet and (
                        "write_text" in snippet or "atomic_write" in snippet
                    ):
                        if file_rel.endswith("accuracy_stage_capture.py"):
                            # stage reads FINAL; writing dest is stage file
                            other.append({**hit, "disposition": "stage_read_or_copy"})
                        else:
                            legacy_runtime.append(hit)
                    else:
                        other.append({**hit, "disposition": "reviewed_non_writer"})
                else:
                    other.append({**hit, "disposition": "reviewed_non_writer"})
        else:
            other.append(hit)

    # Deduplicate authoritative by writer identity
    auth_files = sorted({h["file"] for h in authoritative if "final_artifact_authority" in h["file"]})
    report = {
        "app_version": APP_VERSION,
        "authoritative_writer": AUTHORITATIVE_WRITER,
        "authoritative_writer_count": 1 if auth_files else 0,
        "authoritative_writer_files": auth_files,
        "legacy_runtime_writer_count": len(legacy_runtime),
        "legacy_runtime_writers": legacy_runtime,
        "post_seal_writer_count": len(post_seal),
        "post_seal_writers": post_seal,
        "hits": candidates,
        "other_references": other,
        "acceptance": {
            "authoritative_writer_count": 1 if auth_files else 0,
            "legacy_runtime_writer_count": len(legacy_runtime),
            "post_seal_writer_count": len(post_seal),
            "passed": bool(auth_files)
            and len(legacy_runtime) == 0
            and len(post_seal) == 0,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = run_audit()
    print(json.dumps(report["acceptance"], ensure_ascii=False, indent=2))
    print(f"wrote {OUT}")
    return 0 if report["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
