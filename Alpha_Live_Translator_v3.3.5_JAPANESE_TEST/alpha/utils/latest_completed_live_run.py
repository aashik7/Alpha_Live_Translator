"""Authoritative resolver for latest completed live run (V25.3.2.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from alpha.utils.path_types import ensure_path

_EXCLUDE_NAME_PARTS = (
    "smoke",
    "validation",
    "repair",
    "previous_run",
    "previous-run",
    "_pending",
    "fixture",
)


def normalize_app_version(version: str) -> str:
    v = str(version or "").strip()
    if v.upper().startswith("V"):
        v = v[1:].strip()
    return v


def versions_match(a: str, b: str) -> bool:
    na = normalize_app_version(a)
    nb = normalize_app_version(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Patch suffix: 3.3.5.5.8.5.25.3.2.1 matches base 3.3.5.5.8.5.25.3.2
    return na.startswith(nb + ".") or nb.startswith(na + ".")


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_index_version(index_path: Path) -> str:
    if not index_path.exists():
        return ""
    for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("app_version="):
            return line.split("=", 1)[1].strip()
    return ""


def _version_from_folder_name(folder: Path) -> str:
    m = re.match(r"^(v)?([\d.]+)-\d{8}-\d{6}$", folder.name, re.I)
    if m:
        return normalize_app_version(m.group(2))
    return ""


def _collect_version_sources(run_folder: Path) -> dict[str, str]:
    manifest = _read_json(run_folder / "RUN_MANIFEST.json")
    live_status = _read_json(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
    index_txt = run_folder / "artifacts" / "RUN_ARTIFACTS_INDEX.txt"
    stage_manifest = _read_json(run_folder / "accuracy_stage_compare" / "stage_manifest.json")
    return {
        "RUN_MANIFEST.json": normalize_app_version(str(manifest.get("app_version", ""))),
        "artifacts/LIVE_RUN_STATUS.json": normalize_app_version(str(live_status.get("app_version", ""))),
        "artifacts/RUN_ARTIFACTS_INDEX.txt": normalize_app_version(_parse_index_version(index_txt)),
        "run_folder_name": _version_from_folder_name(run_folder),
        "accuracy_stage_compare/stage_manifest.json": normalize_app_version(
            str(stage_manifest.get("app_version", ""))
        ),
    }


def _resolve_authoritative_version(version_values: dict[str, str]) -> tuple[str, bool]:
    priority = [
        "RUN_MANIFEST.json",
        "artifacts/LIVE_RUN_STATUS.json",
        "artifacts/RUN_ARTIFACTS_INDEX.txt",
        "run_folder_name",
        "accuracy_stage_compare/stage_manifest.json",
    ]
    non_empty = {k: v for k, v in version_values.items() if v}
    if not non_empty:
        return "", False
    counts: dict[str, int] = {}
    for v in non_empty.values():
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.items(), key=lambda x: (x[1], -priority.index(next(k for k in priority if non_empty.get(k) == x[0]))))[0]
    conflict = len(set(non_empty.values())) > 1
    # Accept if at least two authoritative sources agree on best (or only one source)
    agreeing = sum(1 for v in non_empty.values() if versions_match(v, best))
    if agreeing >= 2 or len(non_empty) == 1:
        return best, conflict
    return best, True


def _is_excluded_folder(name: str) -> bool:
    lower = name.lower()
    return any(part in lower for part in _EXCLUDE_NAME_PARTS)


def _manifest_run_status(manifest: dict[str, Any]) -> str:
    """Prefer status, then final_status (either is authoritative for completion)."""
    for key in ("status", "final_status"):
        value = str(manifest.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _run_status_is_completed(status: str) -> bool:
    return status == "completed" or status.startswith("completed")


def _run_completed(
    run_folder: Path,
    *,
    expected_version: str = "",
) -> bool:
    """Completed live run per stop-finalize invariant (status or final_status)."""
    manifest = _read_json(run_folder / "RUN_MANIFEST.json")
    if str(manifest.get("run_type", "")).strip() != "live":
        return False
    if expected_version:
        app_ver = normalize_app_version(str(manifest.get("app_version", "")))
        if not versions_match(app_ver, expected_version):
            return False
    status = _manifest_run_status(manifest)
    if not status:
        live = _read_json(run_folder / "artifacts" / "LIVE_RUN_STATUS.json")
        status = str(live.get("status", "") or "").strip()
    if not _run_status_is_completed(status):
        return False
    if not str(manifest.get("completed_at", "") or "").strip():
        return False
    if manifest.get("stop_finalize_completed") is not True:
        return False
    if manifest.get("stop_finalize_failed") is True:
        return False
    return True


def _completed_sort_key(run_folder: Path) -> tuple[str, str]:
    manifest = _read_json(run_folder / "RUN_MANIFEST.json")
    completed_at = str(manifest.get("completed_at", ""))
    run_ts = str(manifest.get("run_timestamp", ""))
    if not run_ts:
        m = re.search(r"-(\d{8}-\d{6})$", run_folder.name)
        run_ts = m.group(1) if m else ""
    return (completed_at, run_ts)


def resolve_latest_completed_live_run(
    expected_version: Optional[str] = None,
    explicit_run_folder: Optional[str | Path] = None,
    *,
    project_root: Optional[Path] = None,
) -> dict[str, Any]:
    root = ensure_path(project_root) or Path(__file__).resolve().parents[2]
    runs_dir = root / "troubleshooting" / "runs"
    expected = normalize_app_version(expected_version or "")

    result: dict[str, Any] = {
        "resolved_run_folder": "",
        "resolved_run_id": "",
        "resolved_run_type": "",
        "resolved_run_status": "",
        "resolved_app_version": "",
        "expected_app_version": expected,
        "version_sources": {},
        "version_values": {},
        "version_match": False,
        "version_conflict": False,
        "ok": False,
        "error": "",
    }

    if explicit_run_folder:
        folder = ensure_path(explicit_run_folder)
        if folder is None:
            result["error"] = "explicit_run_folder_missing"
            return result
        if not folder.is_absolute():
            folder = root / folder
        if not folder.exists():
            result["error"] = "explicit_run_folder_missing"
            return result
        # Exact-folder mode: validate only this run; never search siblings.
        # Do not require expected_version match here — offline patches may bump APP_VERSION
        # while re-closing a completed live run folder from an earlier patch build.
        if not _run_completed(folder, expected_version=""):
            result["error"] = "run_not_completed"
            result["resolved_run_folder"] = str(folder)
            result["run_folder"] = str(folder)
            return result
        chosen = folder
    else:
        if not runs_dir.exists():
            result["error"] = "runs_dir_missing"
            return result
        candidates = [
            p
            for p in runs_dir.iterdir()
            if p.is_dir() and not _is_excluded_folder(p.name) and (p / "RUN_MANIFEST.json").exists()
        ]
        live_completed = [
            p for p in candidates if _run_completed(p, expected_version=expected)
        ]
        if not live_completed:
            result["error"] = "no_completed_live_run"
            return result
        live_completed.sort(key=_completed_sort_key, reverse=True)
        chosen = live_completed[0]

    version_values = _collect_version_sources(chosen)
    resolved_version, conflict = _resolve_authoritative_version(version_values)
    manifest = _read_json(chosen / "RUN_MANIFEST.json")
    live = _read_json(chosen / "artifacts" / "LIVE_RUN_STATUS.json")
    resolved_status = _manifest_run_status(manifest) or str(live.get("status", "") or "")

    result.update(
        {
            "resolved_run_folder": str(chosen),
            "run_folder": str(chosen),
            "resolved_run_id": str(manifest.get("run_id", "")),
            "resolved_run_type": str(manifest.get("run_type", "")),
            "resolved_run_status": resolved_status,
            "resolved_app_version": resolved_version,
            "version_sources": {
                "RUN_MANIFEST.json": "RUN_MANIFEST.json",
                "artifacts/LIVE_RUN_STATUS.json": "artifacts/LIVE_RUN_STATUS.json",
                "artifacts/RUN_ARTIFACTS_INDEX.txt": "artifacts/RUN_ARTIFACTS_INDEX.txt",
                "run_folder_name": "run_folder_name",
            },
            "version_values": version_values,
            "version_conflict": conflict,
            "ok": True,
        }
    )

    if expected:
        result["version_match"] = versions_match(resolved_version, expected)
        if not result["version_match"] and not explicit_run_folder:
            # Non-explicit search must match the requested version.
            result["ok"] = False
            result["error"] = "no_completed_live_run_for_version"
        elif not result["version_match"] and explicit_run_folder:
            # Explicit folder: completion already verified; version mismatch is informational.
            result["error"] = ""
    else:
        result["version_match"] = bool(resolved_version)

    _jp_log("LIVE_RUN_VERSION_NORMALIZED", resolved=resolved_version, expected=expected)
    if conflict:
        _jp_log("LIVE_RUN_VERSION_SOURCE_CONFLICT", **version_values)
    if result["version_match"]:
        _jp_log("LIVE_RUN_VERSION_MATCHED", run_folder=str(chosen))
    elif expected:
        _jp_log("LIVE_RUN_VERSION_MISMATCH", resolved=resolved_version, expected=expected)
    _jp_log(
        "LATEST_COMPLETED_LIVE_RUN_RESOLVED",
        run_folder=str(chosen),
        run_id=result["resolved_run_id"],
        version=resolved_version,
    )
    return result
