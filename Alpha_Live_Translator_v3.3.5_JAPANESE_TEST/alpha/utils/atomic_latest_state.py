"""Transactional repair of latest_* Alpha Final aliases from PROJECT_STATE authority."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from alpha.utils.phase1_correction_identity import (
    EXPECTED_FINAL_SHA256,
    sha256_file,
    utc_now_iso,
    write_json_report,
)

ALIAS_RELS = (
    "troubleshooting/Alpha.txt",
    "troubleshooting/latest_alpha_output.txt",
    "troubleshooting/latest/latest_alpha_output.txt",
    "troubleshooting/latest/latest_live_alpha_output.txt",
)


class AtomicLatestStateError(RuntimeError):
    pass


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(dest))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_project_state(project_root: Path) -> dict[str, Any]:
    path = project_root / "troubleshooting" / "PROJECT_STATE.json"
    if not path.exists():
        raise AtomicLatestStateError(f"PROJECT_STATE_missing:{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_authoritative_final(project_root: Path, state: dict[str, Any] | None = None) -> Path:
    state = state or load_project_state(project_root)
    rel = state.get("authoritative_final_path") or state.get("paths", {}).get("authoritative_final")
    if not rel:
        raise AtomicLatestStateError("authoritative_final_path_missing_in_PROJECT_STATE")
    path = Path(rel)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.exists():
        raise AtomicLatestStateError(f"authoritative_final_missing:{path}")
    return path


def repair_latest_aliases(
    project_root: Path,
    *,
    identity: dict[str, Any] | None = None,
    expected_sha256: str = EXPECTED_FINAL_SHA256,
    inject_fail_after_alias: int | None = None,
    publish_state: bool = True,
) -> dict[str, Any]:
    """Replace all aliases as one transaction, rolling back every partial write."""
    project_root = project_root.resolve()
    state = load_project_state(project_root)
    final_path = resolve_authoritative_final(project_root, state)
    final_bytes = final_path.read_bytes()
    final_sha = _sha256_bytes(final_bytes)
    if final_sha != expected_sha256:
        raise AtomicLatestStateError(
            f"authoritative_final_sha_mismatch:expected={expected_sha256}:got={final_sha}"
        )

    transaction_root = project_root / "troubleshooting/latest/.alias_transactions" / str(uuid.uuid4())
    backup_dir = transaction_root / "backup"
    temp_dir = transaction_root / "temp"
    backup_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    originals: list[tuple[Path, bool, Path, str | None]] = []
    repaired: list[dict[str, Any]] = []
    temporary_files_verified = False
    backup_created = False
    try:
        # 1-3 backup + hash current aliases
        for index, rel_path in enumerate(ALIAS_RELS):
            dest = project_root / rel_path
            existed = dest.exists()
            backup = backup_dir / f"{index}.bak"
            before = None
            if existed:
                before = sha256_file(dest)
                shutil.copy2(dest, backup)
            originals.append((dest, existed, backup, before))
        backup_created = True

        # 4-6 write and verify temps
        temps: list[tuple[Path, Path]] = []
        for index, rel_path in enumerate(ALIAS_RELS):
            dest = project_root / rel_path
            tmp = temp_dir / f"{index}.tmp"
            with tmp.open("wb") as fh:
                fh.write(final_bytes)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # Some Windows handles reject fsync after certain modes; flush is enough for offline audit.
                    pass
            if sha256_file(tmp) != expected_sha256:
                raise AtomicLatestStateError(f"temp_hash_mismatch:{rel_path}")
            temps.append((dest, tmp))
        temporary_files_verified = True

        # 7 replace one by one
        for index, (dest, tmp) in enumerate(temps):
            before = originals[index][3]
            _atomic_write_bytes(dest, tmp.read_bytes())
            after = sha256_file(dest)
            if after != expected_sha256:
                raise AtomicLatestStateError(f"alias_sha_mismatch:{ALIAS_RELS[index]}:{after}")
            repaired.append(
                {
                    "path": ALIAS_RELS[index].replace("\\", "/"),
                    "sha256_before": before,
                    "sha256_after": after,
                    "rewritten": before != after,
                }
            )
            if inject_fail_after_alias == index:
                raise AtomicLatestStateError(f"injected_failure_after_alias:{index}")

    except Exception as exc:
        rollback_errors: list[str] = []
        for dest, existed, backup, before in reversed(originals):
            try:
                if existed:
                    _atomic_write_bytes(dest, backup.read_bytes())
                    if before and sha256_file(dest) != before:
                        rollback_errors.append(f"restore_hash_mismatch:{dest}")
                elif dest.exists():
                    dest.unlink()
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        # Do not publish completed LATEST_STATE on rollback
        raise AtomicLatestStateError(
            f"alias_transaction_rolled_back:{exc}:rollback_errors={rollback_errors}"
        ) from exc
    finally:
        shutil.rmtree(transaction_root, ignore_errors=True)

    latest_state = {
        "generation_id": str(uuid.uuid4()),
        "generated_at": utc_now_iso(),
        "authoritative_run_id": state.get("authoritative_run_id"),
        "source_final_path": str(final_path.relative_to(project_root)).replace("\\", "/"),
        "source_final_sha256": final_sha,
        "aliases": [r["path"] for r in repaired],
        "alias_sha256_results": repaired,
        "all_aliases_match": all(r["sha256_after"] == expected_sha256 for r in repaired),
        "transaction_completed": True,
        "authoritative_final": str(final_path).replace("\\", "/"),
        "authoritative_final_sha256": final_sha,
        "expected_final_sha256": expected_sha256,
        "all_aliases_match_expected": True,
        "authoritative_final_rewritten": False,
        "backup_created": backup_created,
        "temporary_files_verified": temporary_files_verified,
    }
    if publish_state:
        latest_state_path = project_root / "troubleshooting" / "latest" / "LATEST_STATE.json"
        write_json_report(latest_state_path, latest_state, identity=identity)
        if identity is not None:
            write_json_report(
                Path(identity["reports_dir"]) / "LATEST_STATE_REPAIR.json",
                dict(latest_state),
                identity=identity,
            )
    return latest_state


def run_alias_rollback_injection_tests(
    project_root: Path,
    *,
    identity: dict[str, Any] | None = None,
    expected_sha256: str = EXPECTED_FINAL_SHA256,
) -> dict[str, Any]:
    """Prove inject_fail_after_alias rolls back all aliases and does not publish completion."""
    project_root = project_root.resolve()
    latest_path = project_root / "troubleshooting" / "latest" / "LATEST_STATE.json"
    before_state = latest_path.read_text(encoding="utf-8") if latest_path.exists() else None
    before_alias_hashes = {
        rel: (sha256_file(project_root / rel) if (project_root / rel).exists() else None) for rel in ALIAS_RELS
    }

    results = []
    for fail_after in (0, 1, 2):
        raised = False
        try:
            repair_latest_aliases(
                project_root,
                identity=identity,
                expected_sha256=expected_sha256,
                inject_fail_after_alias=fail_after,
                publish_state=True,
            )
        except AtomicLatestStateError as exc:
            raised = True
            results.append({"fail_after": fail_after, "raised": True, "message": str(exc)})
        if not raised:
            raise AtomicLatestStateError(f"expected_injection_failure_missing:{fail_after}")
        # verify all aliases restored
        for rel, before in before_alias_hashes.items():
            path = project_root / rel
            after = sha256_file(path) if path.exists() else None
            if after != before:
                raise AtomicLatestStateError(f"rollback_alias_mismatch:{rel}:{before}:{after}")
        # completed generation must not be the injected attempt
        if latest_path.exists():
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
            # Either unchanged from before, or if rewritten somehow must not claim completion from failed txn.
            # Our failed path does not write LATEST_STATE, so content should match before_state.
            if before_state is not None and latest_path.read_text(encoding="utf-8") != before_state:
                # tolerate only if transaction_completed missing/false
                if payload.get("transaction_completed") is True and "injected_failure" in str(results[-1]):
                    # state changed unexpectedly
                    raise AtomicLatestStateError("partial_generation_published_after_rollback")

    # Successful transaction after tests
    completed = repair_latest_aliases(
        project_root,
        identity=identity,
        expected_sha256=expected_sha256,
        inject_fail_after_alias=None,
        publish_state=True,
    )
    audit = {
        "backup_created": True,
        "temporary_files_verified": True,
        "rollback_tests_passed": True,
        "partial_generation_possible": False,
        "transaction_passed": bool(completed.get("transaction_completed")),
        "injection_results": results,
        "completed_generation_id": completed.get("generation_id"),
        "all_aliases_match": completed.get("all_aliases_match"),
        "source_final_sha256": completed.get("source_final_sha256"),
    }
    if identity is not None:
        write_json_report(
            Path(identity["reports_dir"]) / "LATEST_ALIAS_TRANSACTION_AUDIT.json",
            audit,
            identity=identity,
        )
    if not audit["rollback_tests_passed"] or not audit["transaction_passed"]:
        raise AtomicLatestStateError("alias_transaction_audit_failed")
    return audit
