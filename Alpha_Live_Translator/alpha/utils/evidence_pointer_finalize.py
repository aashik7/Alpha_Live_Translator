"""Finalize latest run pointers and status after Stop or offline package (8.5.22.2)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_VERSION,
    EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED,
    LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED,
    LATEST_UPLOAD_ZIP_POINTER_FIX_ENABLED,
)


def _log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def finalize_evidence_pointers_completed(
    host: Any = None,
    *,
    upload_zip_path: str = "",
    reason: str = "after_stop",
    app_close_status: str = "normal",
) -> dict[str, Any]:
    """Update latest pointers and per-run status to completed — non-blocking safe."""
    result: dict[str, Any] = {"ok": False}
    if not EVIDENCE_POINTER_FINALIZATION_FIX_ENABLED:
        return result

    try:
        from alpha.utils.run_identity import get_current_run_identity
        from alpha.utils.stop_finalize_worker import build_stop_finalize_summary

        identity = get_current_run_identity()
        if identity is None:
            return result
        folder = Path(identity.run_folder) if identity.run_folder else None
        if folder is None or not folder.exists():
            return result

        _log("LATEST_RUN_POINTER_FINALIZATION_BEGIN", reason=reason, run_id=identity.run_id)

        stop_summary = {}
        try:
            stop_summary = build_stop_finalize_summary(host) if host is not None else {}
        except Exception:
            pass

        # fixes TASK_4A_FINDINGS.md items 1/3: this background pass IS the
        # tenth REPAIR_PLAN.md required step ("evidence package") -- its own
        # success can only be known here, not synchronously during Stop.
        # final_status can become "completed" only when BOTH the nine
        # synchronous required steps already tracked in stop_summary
        # succeeded (compute_core_final_status(), threaded through
        # build_stop_finalize_summary) AND this pass's own artifact writes
        # succeed below. A missing stop_summary (no host) is fail-closed,
        # not treated as success.
        core_failed = bool(stop_summary.get("stop_finalize_failed", True))
        core_reason = str(stop_summary.get("failure_reason") or "")
        evidence_package_ok = True
        evidence_failure_reason = ""

        # Provisional value for the writes below, upgraded once this pass's
        # own outcome is known (see the final computation further down) --
        # a write cannot describe its own success inside its own content,
        # the same bootstrapping limit stop_finalize_worker.py's run_manifest
        # step accepts.
        provisional_status = "failed" if core_failed else "completed"

        evidence_flags = {
            "alpha_output_written": bool(stop_summary.get("alpha_output_written", False)),
            "run_artifacts_index_written": False,
            "live_run_status_written": False,
        }
        if upload_zip_path:
            evidence_flags["upload_package_zip_created"] = True
            evidence_flags["upload_package_index_written"] = True

        if LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED:
            try:
                from alpha.utils.run_artifacts import finalize_live_run_status_completed

                written = finalize_live_run_status_completed(
                    host,
                    stop_summary=stop_summary,
                    evidence_flags=evidence_flags,
                )
                evidence_flags["live_run_status_written"] = written is not None
                if written is None:
                    evidence_package_ok = False
                    evidence_failure_reason = "live_run_status_write_failed"
                _log("LIVE_RUN_STATUS_FINALIZED_COMPLETED", status=provisional_status)
            except Exception as exc:
                evidence_package_ok = False
                evidence_failure_reason = f"live_run_status_write_exception:{exc}"

            index_written = _finalize_run_artifacts_index_status(folder, status=provisional_status)
            evidence_flags["run_artifacts_index_written"] = bool(index_written)
            if not index_written:
                evidence_package_ok = False
                evidence_failure_reason = evidence_failure_reason or "run_artifacts_index_write_failed"

        # fixes TASK_4A_FINDINGS.md items 1/3: the true final status, now
        # that this pass's own writes so far are known, combined with the
        # synchronous required-step result.
        if core_failed or not evidence_package_ok:
            final_status = "failed"
            failure_reason = core_reason or evidence_failure_reason
        else:
            final_status = "completed"
            failure_reason = ""
        final_stop_summary = {
            **stop_summary,
            "stop_finalize_failed": core_failed or not evidence_package_ok,
            "failure_reason": failure_reason,
        }

        if LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED:
            try:
                from alpha.utils.troubleshooting_paths import finalize_run_manifest

                finalize_run_manifest(
                    folder,
                    status=final_status,
                    artifact_flags=evidence_flags,
                    stop_summary=final_stop_summary,
                )
                _log("RUN_MANIFEST_FINALIZED_COMPLETED", status=final_status)
            except Exception as exc:
                evidence_package_ok = False
                evidence_failure_reason = evidence_failure_reason or f"run_manifest_write_exception:{exc}"

        if LATEST_UPLOAD_ZIP_POINTER_FIX_ENABLED or LATEST_POINTER_COMPLETED_STATUS_FIX_ENABLED:
            from alpha.utils.troubleshooting_paths import finalize_latest_pointers

            finalize_latest_pointers(
                folder,
                run_id=identity.run_id,
                status=final_status,
                upload_zip_path=upload_zip_path,
                app_close_status=app_close_status,
            )
            if upload_zip_path:
                _log(
                    "UPLOAD_PACKAGE_ZIP_PATH_FINALIZED",
                    path=upload_zip_path,
                )
                _log("LATEST_POINTER_UPLOAD_ZIP_PATH_UPDATED", path=upload_zip_path)
                _log(
                    "LATEST_ARTIFACTS_INDEX_UPLOAD_ZIP_PATH_UPDATED",
                    path=upload_zip_path,
                )

        _update_accuracy_evidence_index_pointer_fields(folder, upload_zip_path=upload_zip_path)

        _log("LATEST_POINTER_FINALIZATION_COMPLETED", status=final_status, reason=reason)
        result.update({"ok": True, "status": final_status, "upload_zip_path": upload_zip_path})
    except Exception as exc:
        _log(
            "LATEST_POINTER_FINALIZATION_FAILED_NON_BLOCKING",
            error=str(exc),
            reason=reason,
        )
        result["error"] = str(exc)
    return result


def _finalize_run_artifacts_index_status(run_folder: Path, *, status: str) -> bool:
    """fixes TASK_4A_FINDINGS.md item 1 (evidence package): report real
    write success instead of always implicitly succeeding (the previous
    -> None return meant a caller could never tell this failed)."""
    ok = True
    found_any = False
    for name in ("RUN_ARTIFACTS_INDEX.txt", "RUN_ARTIFACTS_INDEX.partial.txt"):
        path = run_folder / "artifacts" / name
        if not path.exists():
            continue
        found_any = True
        try:
            text = path.read_text(encoding="utf-8")
            lines = []
            replaced = False
            for line in text.splitlines():
                if line.startswith("status="):
                    lines.append(f"status={status}")
                    replaced = True
                else:
                    lines.append(line)
            if not replaced:
                lines.insert(0, f"status={status}")
            lines.append(f"finalized_at={datetime.now().isoformat(timespec='seconds')}")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            ok = False
    _log("RUN_ARTIFACTS_INDEX_FINALIZED_COMPLETED", status=status, ok=ok, found_any=found_any)
    # RUN_ARTIFACTS_INDEX.txt is written synchronously earlier in Stop
    # (stop_finalize_worker._write_minimal_runtime_artifacts); if neither
    # file exists here, that earlier write never happened -- fail closed.
    return ok and found_any


def _update_accuracy_evidence_index_pointer_fields(
    run_folder: Path, *, upload_zip_path: str = ""
) -> None:
    index_path = run_folder / "accuracy" / "ACCURACY_EVIDENCE_INDEX.json"
    if not index_path.exists():
        return
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["status"] = "completed"
        index["upload_package_zip_path"] = upload_zip_path
        index["upload_package_created"] = bool(upload_zip_path)
        if upload_zip_path:
            zp = Path(upload_zip_path)
            if zp.exists():
                index["upload_package_size_bytes"] = zp.stat().st_size
        index["upload_package_wav_excluded"] = True
        index["updated_at"] = datetime.now().isoformat(timespec="seconds")
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def schedule_evidence_pointer_finalization_background(
    host: Any = None, *, reason: str = "after_stop"
) -> None:
    def _worker() -> None:
        try:
            finalize_evidence_pointers_completed(host, reason=reason)
        except Exception:
            pass

    threading.Thread(
        target=_worker,
        name="EvidencePointerFinalize",
        daemon=True,
    ).start()


def finalize_upload_package_pointer(upload_zip_path: str) -> dict[str, Any]:
    """Called after offline package script creates zip."""
    result = finalize_evidence_pointers_completed(
        None,
        upload_zip_path=upload_zip_path,
        reason="after_offline_package",
    )
    return result
