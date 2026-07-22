"""Strict package identity — presence alone is not enough."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from alpha.utils.path_types import ensure_path
from alpha.utils.persisted_run_evidence import load_run_identity
from alpha.utils.validation_version import VALIDATION_PATCH_VERSION


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def verify_exact_run_id(
    *,
    embedded_run_id: Any,
    selected_run_id: str,
    field_name: str = "run_id",
) -> dict[str, Any]:
    """Existence alone does not pass — must equal selected_run_id."""
    ok = (
        isinstance(embedded_run_id, str)
        and bool(embedded_run_id.strip())
        and embedded_run_id == selected_run_id
    )
    return {
        "field": field_name,
        "embedded": embedded_run_id,
        "selected": selected_run_id,
        "passed": ok,
        "existence_only_rejected": bool(embedded_run_id) and embedded_run_id != selected_run_id,
    }


def build_package_identity_audit(
    *,
    run_folder: Path | str,
    validation_patch_version: str = VALIDATION_PATCH_VERSION,
    prepared_reference_dir: Path | str | None = None,
    package_paths: Iterable[str] | None = None,
    validation_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    assert folder is not None
    identity = load_run_identity(folder)
    selected_run_id = str(identity.get("run_id") or "")
    selected_app = str(identity.get("app_version") or "")
    selected_folder = str(folder.resolve())

    runtime_checks: list[dict[str, Any]] = []
    run_id_mismatches: list[str] = []
    run_version_mismatches: list[str] = []

    runtime_files = {
        "RUN_MANIFEST.json": _read_json(folder / "RUN_MANIFEST.json"),
        "artifacts/LIVE_RUN_STATUS.json": _read_json(folder / "artifacts" / "LIVE_RUN_STATUS.json"),
        "transcripts/FINAL_EXPORT_SEAL.json": _read_json(
            folder / "transcripts" / "FINAL_EXPORT_SEAL.json"
        ),
    }
    for rel, data in runtime_files.items():
        rid = data.get("run_id")
        if rid is not None or "run_id" in data:
            check = verify_exact_run_id(
                embedded_run_id=rid, selected_run_id=selected_run_id, field_name=f"{rel}:run_id"
            )
            runtime_checks.append(check)
            if not check["passed"]:
                run_id_mismatches.append(f"{rel}:run_id")
        app = data.get("app_version")
        if app is not None:
            ok = str(app) == selected_app
            runtime_checks.append(
                {
                    "field": f"{rel}:app_version",
                    "embedded": app,
                    "selected": selected_app,
                    "passed": ok,
                }
            )
            if not ok:
                run_version_mismatches.append(f"{rel}:app_version")

    validation_checks: list[dict[str, Any]] = []
    validation_version_mismatches: list[str] = []
    meta = dict(validation_metadata or {})
    if meta:
        v = meta.get("validation_patch_version") or meta.get("validation_version")
        ok = str(v) == validation_patch_version
        validation_checks.append(
            {
                "field": "validation_metadata.validation_patch_version",
                "embedded": v,
                "expected": validation_patch_version,
                "passed": ok,
            }
        )
        if not ok:
            validation_version_mismatches.append("validation_metadata.validation_patch_version")

    reference_checks: list[dict[str, Any]] = []
    if prepared_reference_dir is not None:
        pref = Path(prepared_reference_dir)
        snap = _read_json(pref / "reference_snapshot.json")
        if snap:
            reference_checks.append(
                {
                    "field": "prepared_reference.preparation_version",
                    "embedded": snap.get("preparation_version")
                    or snap.get("output_version")
                    or snap.get("validation_patch_version"),
                    "source_sha256": snap.get("source_sha256"),
                    "snapshot_sha256": snap.get("snapshot_sha256"),
                    "normalized_sha256_match": snap.get("normalized_sha256_match"),
                    "passed": bool(snap.get("source_sha256") and snap.get("snapshot_sha256")),
                }
            )

    # Package path identity
    if package_paths:
        for p in package_paths:
            if "troubleshooting/runs/" in p.replace("\\", "/"):
                parts = p.replace("\\", "/").split("troubleshooting/runs/")
                if len(parts) > 1:
                    run_part = parts[1].split("/", 1)[0]
                    if run_part and run_part != folder.name:
                        run_id_mismatches.append(f"package_path:{p}")

    passed = (
        not run_id_mismatches
        and not run_version_mismatches
        and not validation_version_mismatches
        and bool(selected_run_id)
        and bool(selected_app)
    )
    return {
        "selected_run_id": selected_run_id,
        "selected_run_folder": selected_folder,
        "selected_run_app_version": selected_app,
        "validation_patch_version": validation_patch_version,
        "runtime_file_identity_checks": runtime_checks,
        "validation_file_version_checks": validation_checks,
        "reference_identity_checks": reference_checks,
        "run_id_mismatches": run_id_mismatches,
        "run_version_mismatches": run_version_mismatches,
        "validation_version_mismatches": validation_version_mismatches,
        "package_identity_passed": passed,
    }


def audit_current_run_only_zip(
    zip_path: Path | str,
    *,
    selected_run_id: str,
    selected_run_folder_name: str,
    validation_patch_version: str = VALIDATION_PATCH_VERSION,
) -> dict[str, Any]:
    zp = Path(zip_path)
    names: list[str] = []
    embedded_run_ids: set[str] = set()
    validation_versions: set[str] = set()
    unexpected: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()

    allowed_roots = ("run/", "validation/", "reference/", "source/", "test_outputs/", "analysis_inputs/")
    # Also allow troubleshooting/runs/<selected>/ and prepared/<version>/ etc.
    forbidden_tokens = (
        "/external/",
        "/smoke",
        "/preflight",
        "/.env",
        "/.git/",
        "/.venv/",
        ".wav",
        ".mp3",
    )

    with zipfile.ZipFile(zp, "r") as zf:
        names = list(zf.namelist())
        for n in names:
            norm = n.replace("\\", "/")
            if norm in seen:
                duplicates.append(norm)
            seen.add(norm)
            low = f"/{norm.lower()}"
            if any(tok in low for tok in forbidden_tokens):
                unexpected.append(norm)
            if "troubleshooting/runs/" in norm:
                part = norm.split("troubleshooting/runs/", 1)[1].split("/", 1)[0]
                if part and part != selected_run_folder_name:
                    unexpected.append(norm)
                    embedded_run_ids.add(part)
                else:
                    embedded_run_ids.add(selected_run_id)
            if "/validation/v" in norm or norm.startswith("validation/v"):
                # extract version folder
                idx = norm.find("validation/v")
                if idx >= 0:
                    rest = norm[idx + len("validation/v") :]
                    ver = rest.split("/", 1)[0]
                    if ver:
                        validation_versions.add(ver)
            # Try read JSON for run_id / validation version
            if norm.endswith(".json"):
                try:
                    data = json.loads(zf.read(n).decode("utf-8"))
                except Exception:
                    data = None
                if isinstance(data, dict):
                    rid = data.get("run_id") or data.get("selected_run_id")
                    if isinstance(rid, str) and rid.strip():
                        embedded_run_ids.add(rid)
                    for key in (
                        "validation_patch_version",
                        "validation_version",
                        "VALIDATION_PATCH_VERSION",
                    ):
                        if key in data and data[key]:
                            validation_versions.add(str(data[key]).lstrip("vV"))

    foreign_run_ids = sorted(r for r in embedded_run_ids if r and r != selected_run_id and selected_run_folder_name not in r)
    # selected_run_folder_name may appear as folder id but not run_id — exclude folder name from foreign
    foreign_run_ids = [r for r in foreign_run_ids if r != selected_run_folder_name]
    foreign_validation = sorted(
        v for v in validation_versions if v and v != validation_patch_version
    )
    # Allow empty validation_versions if no versioned paths
    current_run_only_passed = (
        not foreign_run_ids
        and not foreign_validation
        and not unexpected
        and not duplicates
    )
    return {
        "selected_run_id": selected_run_id,
        "all_embedded_run_ids": sorted(embedded_run_ids),
        "foreign_run_ids": foreign_run_ids,
        "all_validation_versions": sorted(validation_versions),
        "foreign_validation_versions": foreign_validation,
        "unexpected_paths": sorted(set(unexpected)),
        "duplicate_paths": sorted(set(duplicates)),
        "current_run_only_passed": current_run_only_passed,
        "zip_path": str(zp),
        "entry_count": len(names),
    }
