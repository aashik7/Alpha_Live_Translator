"""Single authoritative Final Alpha writer with seal/verify gates (V25.3.3.1)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from alpha.utils.path_types import ensure_path


class FinalArtifactWriteError(RuntimeError):
    """Raised when a second authoritative Final Alpha write is attempted."""


class FinalArtifactSealedError(RuntimeError):
    """Raised when any write is attempted after the Final Alpha seal."""


class FinalArtifactSealError(RuntimeError):
    """Raised when seal verification fails (fail closed)."""


_lock = threading.RLock()
_state_by_run: dict[str, dict[str, Any]] = {}

WRITER_FUNCTION = "alpha.utils.final_artifact_authority.write_final_once"
AUTHORITATIVE_NAME = "Alpha_output_FINAL.txt"
SIDECAR_NAME = "final_export_records.jsonl"
SEAL_NAME = "FINAL_EXPORT_SEAL.json"
MIRROR_NAME = "Alpha output.txt"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes((text or "").encode("utf-8"))


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _run_key(run_folder: Path | str) -> str:
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    return str(folder.resolve())


def _transcripts_dir(run_folder: Path) -> Path:
    return run_folder / "transcripts"


def _paths(run_folder: Path) -> dict[str, Path]:
    tdir = _transcripts_dir(run_folder)
    return {
        "authoritative": tdir / AUTHORITATIVE_NAME,
        "sidecar": tdir / SIDECAR_NAME,
        "seal": tdir / SEAL_NAME,
        "mirror": tdir / MIRROR_NAME,
    }


def _get_state(run_folder: Path) -> dict[str, Any]:
    key = _run_key(run_folder)
    with _lock:
        state = _state_by_run.get(key)
        if state is None:
            state = {
                "run_folder": str(run_folder),
                "run_id": "",
                "snapshot_id": "",
                "expected_record_count": 0,
                "write_count": 0,
                "sealed": False,
                "seal_verified": False,
                "text_sha256": "",
                "sidecar_sha256": "",
                "record_count": 0,
                "file_size": 0,
                "written_at": "",
                "sealed_at": "",
                "post_seal_write_attempt_count": 0,
                "post_seal_write_attempts": [],
                "alias_hashes": {},
                "begun": False,
            }
            _state_by_run[key] = state
        return state


def reset_final_export_authority(run_folder: Path | str | None = None) -> None:
    """Test helper: clear authority state for one run or all runs."""
    with _lock:
        if run_folder is None:
            _state_by_run.clear()
            return
        key = _run_key(run_folder)
        _state_by_run.pop(key, None)


def begin_final_export(
    run_folder: Path | str,
    run_id: str,
    snapshot_id: str,
    expected_record_count: int,
) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    with _lock:
        state = _get_state(folder)
        if state.get("sealed"):
            raise FinalArtifactSealedError("cannot begin_final_export after seal")
        state.update(
            {
                "run_folder": str(folder),
                "run_id": str(run_id or ""),
                "snapshot_id": str(snapshot_id or ""),
                "expected_record_count": int(expected_record_count or 0),
                "write_count": int(state.get("write_count") or 0),
                "begun": True,
            }
        )
        _jp_log(
            "FINAL_EXPORT_AUTHORITY_BEGIN",
            run_id=state["run_id"],
            snapshot_id=state["snapshot_id"],
            expected_record_count=state["expected_record_count"],
        )
        return dict(state)


def _atomic_write_text(target: Path, text: str) -> dict[str, Any]:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = _sha256_text(text)
    temp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}-{time.time_ns()}")
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, target)
    readback = target.read_text(encoding="utf-8")
    readback_hash = _sha256_text(readback)
    if readback_hash != expected:
        raise FinalArtifactSealError(
            f"readback hash mismatch for {target}: expected={expected} got={readback_hash}"
        )
    return {
        "ok": True,
        "path": str(target),
        "sha256": readback_hash,
        "file_size": target.stat().st_size,
    }


def write_final_once(
    run_folder: Path | str,
    run_id: str,
    snapshot_id: str,
    text: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sole permitted writer of Alpha_output_FINAL.txt."""
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    paths = _paths(folder)
    empty_overwrite_prevented = False
    if not (text or "").strip() and paths["authoritative"].exists():
        existing = paths["authoritative"].read_text(encoding="utf-8")
        if existing.strip():
            empty_overwrite_prevented = True
            _jp_log(
                "FINAL_EMPTY_OVERWRITE_PREVENTED",
                path=str(paths["authoritative"]),
                existing_chars=len(existing),
            )
            return {
                "ok": True,
                "authoritative_path": str(paths["authoritative"]),
                "sidecar_path": str(paths["sidecar"]),
                "text_sha256": _sha256_text(existing),
                "sidecar_sha256": "",
                "record_count": len(records or []),
                "write_count": 0,
                "file_size": paths["authoritative"].stat().st_size,
                "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "empty_overwrite_prevented": True,
                "final_source_character_count": len(existing),
                "final_export_character_count": len(existing),
            }
    if not (text or "").strip():
        _jp_log("FINAL_EMPTY_WRITE_REFUSED", path=str(paths["authoritative"]))
        return {
            "ok": False,
            "error": "empty_final_write_refused",
            "empty_overwrite_prevented": empty_overwrite_prevented,
            "final_source_character_count": 0,
            "final_export_character_count": 0,
        }
    body = text if text.endswith("\n") or not text else text + "\n"
    rows = list(records or [])
    sidecar_lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    sidecar_body = ("\n".join(sidecar_lines) + "\n") if sidecar_lines else ""

    with _lock:
        state = _get_state(folder)
        if state.get("sealed"):
            attempt = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "function": WRITER_FUNCTION,
                "reason": "post_seal_write",
            }
            state["post_seal_write_attempt_count"] = int(
                state.get("post_seal_write_attempt_count") or 0
            ) + 1
            state.setdefault("post_seal_write_attempts", []).append(attempt)
            raise FinalArtifactSealedError(
                "authoritative Final Alpha is sealed; write refused"
            )
        write_count = int(state.get("write_count") or 0)
        if write_count != 0:
            raise FinalArtifactWriteError(
                f"authoritative Final Alpha already written (write_count={write_count})"
            )
        if not state.get("begun"):
            begin_final_export(
                folder,
                run_id=run_id,
                snapshot_id=snapshot_id,
                expected_record_count=len(rows),
            )
            state = _get_state(folder)

        auth = _atomic_write_text(paths["authoritative"], body)
        side = _atomic_write_text(paths["sidecar"], sidecar_body)
        # Twin mirror written in the same authoritative session (not a second FINAL writer).
        _atomic_write_text(paths["mirror"], body)

        written_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        state.update(
            {
                "run_id": str(run_id or state.get("run_id") or ""),
                "snapshot_id": str(snapshot_id or state.get("snapshot_id") or ""),
                "write_count": 1,
                "text_sha256": auth["sha256"],
                "sidecar_sha256": side["sha256"],
                "record_count": len(rows),
                "file_size": int(auth["file_size"]),
                "written_at": written_at,
                "sealed": False,
                "seal_verified": False,
            }
        )
        _jp_log(
            "FINAL_ALPHA_AUTHORITY_WRITE_ONCE",
            path=auth["path"],
            sha256=auth["sha256"],
            record_count=len(rows),
            write_count=1,
        )
        return {
            "ok": True,
            "authoritative_path": auth["path"],
            "sidecar_path": side["path"],
            "text_sha256": auth["sha256"],
            "sidecar_sha256": side["sha256"],
            "record_count": len(rows),
            "write_count": 1,
            "file_size": auth["file_size"],
            "written_at": written_at,
        }


def seal_final_export(
    run_folder: Path | str,
    run_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    paths = _paths(folder)
    with _lock:
        state = _get_state(folder)
        if int(state.get("write_count") or 0) != 1:
            raise FinalArtifactSealError(
                f"cannot seal without exactly one write (write_count={state.get('write_count')})"
            )
        if not paths["authoritative"].exists():
            raise FinalArtifactSealError("authoritative Final Alpha missing before seal")
        if not paths["sidecar"].exists():
            raise FinalArtifactSealError("final_export_records.jsonl missing before seal")

        text = paths["authoritative"].read_text(encoding="utf-8")
        text_hash = _sha256_text(text)
        sidecar = paths["sidecar"].read_text(encoding="utf-8")
        sidecar_hash = _sha256_text(sidecar)
        if text_hash != state.get("text_sha256"):
            raise FinalArtifactSealError("authoritative text hash changed before seal")
        if sidecar_hash != state.get("sidecar_sha256"):
            raise FinalArtifactSealError("sidecar hash changed before seal")

        sealed_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        seal = {
            "run_id": str(run_id or state.get("run_id") or ""),
            "snapshot_id": str(snapshot_id or state.get("snapshot_id") or ""),
            "authoritative_path": str(paths["authoritative"]),
            "sidecar_path": str(paths["sidecar"]),
            "writer_function": WRITER_FUNCTION,
            "write_count": 1,
            "text_sha256": text_hash,
            "sidecar_sha256": sidecar_hash,
            "record_count": int(state.get("record_count") or 0),
            "file_size": int(paths["authoritative"].stat().st_size),
            "written_at": state.get("written_at") or sealed_at,
            "sealed_at": sealed_at,
            "sealed": True,
            "post_seal_write_attempt_count": int(
                state.get("post_seal_write_attempt_count") or 0
            ),
            "post_seal_write_attempts": list(state.get("post_seal_write_attempts") or []),
            "seal_verified": False,
        }
        _atomic_write_text(
            paths["seal"],
            json.dumps(seal, ensure_ascii=False, indent=2) + "\n",
        )
        state.update(
            {
                "sealed": True,
                "sealed_at": sealed_at,
                "run_id": seal["run_id"],
                "snapshot_id": seal["snapshot_id"],
                "seal_verified": False,
            }
        )
        _jp_log(
            "FINAL_EXPORT_SEALED",
            path=str(paths["seal"]),
            text_sha256=text_hash,
            write_count=1,
        )
        return dict(seal)


def verify_final_export_seal(
    run_folder: Path | str,
    run_id: str = "",
    snapshot_id: str = "",
) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    paths = _paths(folder)
    if not paths["seal"].exists():
        raise FinalArtifactSealError("FINAL_EXPORT_SEAL.json missing")
    seal = json.loads(paths["seal"].read_text(encoding="utf-8"))
    if not seal.get("sealed"):
        raise FinalArtifactSealError("seal marker sealed=false")
    if run_id and str(seal.get("run_id") or "") != str(run_id):
        raise FinalArtifactSealError("seal run_id mismatch")
    if snapshot_id and str(seal.get("snapshot_id") or "") != str(snapshot_id):
        raise FinalArtifactSealError("seal snapshot_id mismatch")
    if not paths["authoritative"].exists():
        raise FinalArtifactSealError("authoritative file missing during verify")
    text_hash = _sha256_text(paths["authoritative"].read_text(encoding="utf-8"))
    sidecar_hash = _sha256_text(paths["sidecar"].read_text(encoding="utf-8"))
    if text_hash != seal.get("text_sha256"):
        raise FinalArtifactSealError(
            f"seal text hash mismatch: seal={seal.get('text_sha256')} disk={text_hash}"
        )
    if sidecar_hash != seal.get("sidecar_sha256"):
        raise FinalArtifactSealError("seal sidecar hash mismatch")
    if int(seal.get("write_count") or 0) != 1:
        raise FinalArtifactSealError("seal write_count must be 1")
    seal["seal_verified"] = True
    _atomic_write_text(
        paths["seal"],
        json.dumps(seal, ensure_ascii=False, indent=2) + "\n",
    )
    with _lock:
        state = _get_state(folder)
        state["seal_verified"] = True
        state["sealed"] = True
        state["text_sha256"] = text_hash
        state["post_seal_write_attempt_count"] = int(
            seal.get("post_seal_write_attempt_count") or 0
        )
    _jp_log("FINAL_EXPORT_SEAL_VERIFIED", text_sha256=text_hash)
    return dict(seal)


def sync_non_authoritative_aliases_from_sealed_final(
    run_folder: Path | str,
    run_id: str = "",
) -> dict[str, Any]:
    """Update approved latest/alias paths from sealed Final Alpha only."""
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    seal = verify_final_export_seal(folder, run_id=run_id)
    paths = _paths(folder)
    text = paths["authoritative"].read_text(encoding="utf-8")
    text_hash = _sha256_text(text)
    if text_hash != seal.get("text_sha256"):
        raise FinalArtifactSealError("alias sync refused: sealed hash mismatch")

    from alpha.utils.alpha_output_protection import get_latest_live_alpha_path
    from alpha.utils.troubleshooting_paths import get_latest_dir, get_troubleshooting_root

    troubleshooting_root = get_troubleshooting_root()
    latest_dir = get_latest_dir()
    alias_targets = [
        paths["mirror"],
        get_latest_live_alpha_path(),
        troubleshooting_root / "latest_alpha_output.txt",
        troubleshooting_root / "Alpha.txt",
        latest_dir / "latest_alpha_output.txt",
    ]
    written: list[str] = []
    hashes: dict[str, str] = {}
    for target in alias_targets:
        result = _atomic_write_text(Path(target), text)
        written.append(result["path"])
        hashes[result["path"]] = result["sha256"]
    with _lock:
        state = _get_state(folder)
        state["alias_hashes"] = hashes
    _jp_log(
        "NON_AUTHORITATIVE_ALIASES_SYNCED_FROM_SEALED_FINAL",
        paths=len(written),
        text_sha256=text_hash,
    )
    return {
        "ok": True,
        "written_paths": written,
        "alias_hashes": hashes,
        "text_sha256": text_hash,
        "authoritative_path": str(paths["authoritative"]),
        "seal_verified": True,
    }


def get_final_export_authority_state(run_folder: Path | str) -> dict[str, Any]:
    folder = ensure_path(run_folder)
    if folder is None:
        raise ValueError("run_folder is required")
    with _lock:
        state = dict(_get_state(folder))
    paths = _paths(folder)
    seal_path = paths["seal"]
    if seal_path.exists():
        try:
            disk_seal = json.loads(seal_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "sealed": bool(disk_seal.get("sealed")),
                    "seal_verified": bool(disk_seal.get("seal_verified")),
                    "write_count": int(disk_seal.get("write_count") or state.get("write_count") or 0),
                    "text_sha256": str(disk_seal.get("text_sha256") or ""),
                    "post_seal_write_attempt_count": int(
                        disk_seal.get("post_seal_write_attempt_count") or 0
                    ),
                    "post_seal_write_attempts": list(
                        disk_seal.get("post_seal_write_attempts") or []
                    ),
                }
            )
        except Exception:
            pass
    state["authoritative_path"] = str(paths["authoritative"])
    state["sidecar_path"] = str(paths["sidecar"])
    state["seal_path"] = str(paths["seal"])
    state["writer_function"] = WRITER_FUNCTION
    return state


def record_post_seal_write_attempt(
    run_folder: Path | str,
    *,
    function_name: str,
    reason: str = "",
) -> None:
    folder = ensure_path(run_folder)
    if folder is None:
        return
    with _lock:
        state = _get_state(folder)
        if not state.get("sealed"):
            return
        attempt = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "function": function_name,
            "reason": reason,
        }
        state["post_seal_write_attempt_count"] = int(
            state.get("post_seal_write_attempt_count") or 0
        ) + 1
        state.setdefault("post_seal_write_attempts", []).append(attempt)
        paths = _paths(folder)
        if paths["seal"].exists():
            seal = json.loads(paths["seal"].read_text(encoding="utf-8"))
            seal["post_seal_write_attempt_count"] = state["post_seal_write_attempt_count"]
            seal["post_seal_write_attempts"] = list(state["post_seal_write_attempts"])
            paths["seal"].write_text(
                json.dumps(seal, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


__all__ = [
    "FinalArtifactWriteError",
    "FinalArtifactSealedError",
    "FinalArtifactSealError",
    "WRITER_FUNCTION",
    "begin_final_export",
    "write_final_once",
    "seal_final_export",
    "verify_final_export_seal",
    "sync_non_authoritative_aliases_from_sealed_final",
    "get_final_export_authority_state",
    "reset_final_export_authority",
    "record_post_seal_write_attempt",
]
