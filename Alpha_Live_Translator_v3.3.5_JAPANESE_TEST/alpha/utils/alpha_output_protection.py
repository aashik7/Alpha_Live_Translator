"""Protect real live Alpha outputs from smoke-test and validation overwrites."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    EVIDENCE_PROTECTION_85232_ENABLED,
    REAL_LIVE_ALPHA_PROTECTION_ENABLED,
    SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED,
    VALIDATION_MAY_WRITE_LATEST_LIVE_OUTPUT,
)

RUN_TYPE_LIVE = "live"
RUN_TYPE_SMOKE_TEST = "smoke_test"
RUN_TYPE_VALIDATION = "validation"

_PLACEHOLDER_MARKERS = (
    "no transcript captured during this session",
    "# note: no transcript",
)

_run_type_lock = threading.Lock()
_alpha_export_run_type = RUN_TYPE_LIVE


def set_alpha_export_run_type(run_type: str) -> None:
    global _alpha_export_run_type
    with _run_type_lock:
        _alpha_export_run_type = run_type


def get_alpha_export_run_type() -> str:
    with _run_type_lock:
        return _alpha_export_run_type


def reset_alpha_export_run_type() -> None:
    global _alpha_export_run_type
    with _run_type_lock:
        _alpha_export_run_type = RUN_TYPE_LIVE


def is_live_alpha_write_allowed() -> bool:
    if not VALIDATION_MAY_WRITE_LATEST_LIVE_OUTPUT and get_alpha_export_run_type() != RUN_TYPE_LIVE:
        _log("VALIDATION_LATEST_LIVE_WRITE_BLOCKED", run_type=get_alpha_export_run_type())
        return False
    if not EVIDENCE_PROTECTION_85232_ENABLED:
        return get_alpha_export_run_type() == RUN_TYPE_LIVE
    return get_alpha_export_run_type() == RUN_TYPE_LIVE


def hash_latest_live_output() -> str:
    path = get_latest_live_alpha_path()
    if not path.exists():
        return ""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_latest_live_unchanged(before_sha: str, after_sha: str) -> bool:
    if not before_sha or not after_sha:
        return before_sha == after_sha
    unchanged = before_sha == after_sha
    if unchanged:
        _log("SMOKE_TEST_LATEST_LIVE_OUTPUT_UNCHANGED", sha256=after_sha)
    else:
        _log(
            "SMOKE_TEST_LATEST_LIVE_OUTPUT_OVERWRITE_DETECTED",
            before=before_sha,
            after=after_sha,
        )
    return unchanged


def is_placeholder_text(text: str) -> bool:
    body = (text or "").strip().lower()
    if not body:
        return True
    return any(marker in body for marker in _PLACEHOLDER_MARKERS)


def count_transcript_lines(text: str) -> int:
    count = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count += 1
    return count


def count_speaker_lines(text: str) -> int:
    count = 0
    for raw in (text or "").splitlines():
        if re.match(r"^\[Speaker \d+\]", raw.strip()):
            count += 1
    return count


def _log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def get_latest_live_alpha_path() -> Path:
    from alpha.utils.troubleshooting_paths import get_latest_dir

    return get_latest_dir() / "latest_live_alpha_output.txt"


def write_smoke_test_alpha_outputs(
    text: str,
    *,
    run_type: str,
    status: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write smoke/validation outputs only under troubleshooting/smoke_tests/."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path("troubleshooting/smoke_tests") / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    alpha_path = out_dir / "Alpha_smoke_test.txt"
    status_path = out_dir / "smoke_test_status.json"

    if not text.strip() or is_placeholder_text(text):
        body = (
            "# Alpha Live Translator Smoke Test Export\n"
            f"# run_type: {run_type}\n"
            f"# timestamp: {stamp}\n"
            "# note: no transcript captured during this session\n"
        )
    else:
        body = text

    alpha_path.write_text(body, encoding="utf-8")
    status_payload = {
        "run_type": run_type,
        "timestamp": stamp,
        "alpha_smoke_test_path": str(alpha_path),
        "line_count": count_transcript_lines(body),
        "placeholder": is_placeholder_text(body),
        **(status or {}),
    }
    status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED:
        _log("SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED", run_type=run_type)
    _log("SMOKE_TEST_ALPHA_WRITE_REDIRECTED", path=str(alpha_path), run_type=run_type)
    try:
        latest = Path("troubleshooting/latest")
        latest.mkdir(parents=True, exist_ok=True)
        smoke_pointer = {
            "run_type": run_type,
            "timestamp": stamp,
            "smoke_alpha_path": str(alpha_path),
            "smoke_status_path": str(status_path),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (latest / "LATEST_SMOKE_RUN_POINTER.json").write_text(
            json.dumps(smoke_pointer, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _log("LATEST_SMOKE_RUN_POINTER_UPDATED", path=str(alpha_path))
        _log("SMOKE_RUN_DID_NOT_OVERRIDE_LIVE_POINTER")
    except Exception:
        pass
    if is_placeholder_text(body):
        _log("ALPHA_PLACEHOLDER_REDIRECTED_TO_SMOKE_TEST_FOLDER", path=str(alpha_path))
    _log("ALPHA_OUTPUT_PROTECTION_COMPLETED", run_type=run_type)

    return {
        "ok": True,
        "smoke_alpha_path": str(alpha_path),
        "smoke_status_path": str(status_path),
        "alpha_output_line_count": count_speaker_lines(body) or count_transcript_lines(body),
        "run_type": run_type,
        "live_paths_updated": False,
    }


def write_protected_live_alpha_outputs(
    text: str,
    *,
    run_id: str,
    run_timestamp: str,
    per_run_alpha: Path,
    troubleshooting_root: Path,
    latest_dir: Path,
    accuracy_copy_path: Path,
    audio_ref: str = "",
    header_fn: Any = None,
    skip_per_run_alpha_if_sealed: bool = False,
) -> dict[str, Any]:
    """Write approved live Alpha alias paths — never Alpha_output_FINAL.txt after seal."""
    line_count = count_speaker_lines(text) or count_transcript_lines(text)
    has_content = line_count > 0 and not is_placeholder_text(text)

    per_run_alpha = Path(per_run_alpha)
    # Hard guard: never write the authoritative FINAL filename.
    if per_run_alpha.name == "Alpha_output_FINAL.txt":
        _log("ALPHA_OUTPUT_FINAL_WRITE_BLOCKED_IN_PROTECTION", path=str(per_run_alpha))
        return {
            "ok": False,
            "error": "Alpha_output_FINAL.txt writes are reserved for final_artifact_authority",
            "live_paths_updated": False,
            "alpha_output_line_count": line_count,
        }

    sealed = False
    try:
        from alpha.utils.final_artifact_authority import get_final_export_authority_state

        run_folder = per_run_alpha.parent.parent if per_run_alpha.parent.name == "transcripts" else None
        if run_folder is not None:
            state = get_final_export_authority_state(run_folder)
            sealed = bool(state.get("sealed"))
            if sealed and per_run_alpha.name in ("Alpha_output_FINAL.txt", "Alpha output.txt"):
                # Authoritative/mirror already written by authority; skip overwrite.
                skip_per_run_alpha_if_sealed = True
    except Exception:
        sealed = False

    if not (skip_per_run_alpha_if_sealed and sealed and per_run_alpha.exists()):
        per_run_alpha.parent.mkdir(parents=True, exist_ok=True)
        per_run_alpha.write_text(text, encoding="utf-8")

    result: dict[str, Any] = {
        "ok": True,
        "per_run_alpha_path": str(per_run_alpha),
        "alpha_output_line_count": line_count,
        "live_paths_updated": False,
        "authoritative_final_touched": False,
    }

    if not has_content:
        _log("REAL_LIVE_ALPHA_WRITE_ALLOWED", run_id=run_id, has_content=False)
        return result

    if not is_live_alpha_write_allowed():
        _log("VALIDATION_LATEST_LIVE_WRITE_BLOCKED", run_id=run_id)
        result["live_paths_updated"] = False
        return result

    latest_alpha = troubleshooting_root / "Alpha.txt"
    latest_alias = troubleshooting_root / "latest_alpha_output.txt"
    latest_mirror = latest_dir / "latest_alpha_output.txt"
    latest_live = get_latest_live_alpha_path()

    latest_alpha.write_text(text, encoding="utf-8")
    latest_alias.write_text(text, encoding="utf-8")
    latest_mirror.write_text(text, encoding="utf-8")
    latest_live.write_text(text, encoding="utf-8")

    accuracy_copy_path.parent.mkdir(parents=True, exist_ok=True)
    if callable(header_fn):
        accuracy_body = header_fn(run_id=run_id, run_timestamp=run_timestamp, audio_ref=audio_ref)
        accuracy_body += text
    else:
        accuracy_body = text
    accuracy_copy_path.write_text(accuracy_body, encoding="utf-8")

    size_bytes = latest_live.stat().st_size
    _log("REAL_LIVE_ALPHA_WRITE_ALLOWED", run_id=run_id, line_count=line_count)
    _log("LATEST_LIVE_ALPHA_OUTPUT_WRITTEN", path=str(latest_live), size_bytes=size_bytes)
    _log("LATEST_LIVE_ALPHA_OUTPUT_SIZE_VERIFIED", size_bytes=size_bytes)
    _log("LATEST_LIVE_ALPHA_OUTPUT_PROTECTED_FROM_SMOKE_TEST")
    _log("LATEST_LIVE_ALPHA_PRESERVED", path=str(latest_live))
    _log("ALPHA_OUTPUT_PROTECTION_COMPLETED", run_type=RUN_TYPE_LIVE)

    result.update(
        {
            "alpha_txt_path": str(latest_alpha),
            "latest_alpha_txt_path": str(latest_alias),
            "latest_live_alpha_output_path": str(latest_live),
            "latest_live_alpha_output_size_bytes": size_bytes,
            "latest_live_alpha_protected": REAL_LIVE_ALPHA_PROTECTION_ENABLED,
            "latest_live_alpha_updated_by_run_id": run_id,
            "latest_live_alpha_updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "accuracy_alpha_txt_path": str(accuracy_copy_path),
            "live_paths_updated": True,
        }
    )
    return result


__all__ = [
    "RUN_TYPE_LIVE",
    "RUN_TYPE_SMOKE_TEST",
    "RUN_TYPE_VALIDATION",
    "count_speaker_lines",
    "count_transcript_lines",
    "get_alpha_export_run_type",
    "get_latest_live_alpha_path",
    "is_live_alpha_write_allowed",
    "is_placeholder_text",
    "reset_alpha_export_run_type",
    "set_alpha_export_run_type",
    "hash_latest_live_output",
    "verify_latest_live_unchanged",
    "write_smoke_test_alpha_outputs",
]
