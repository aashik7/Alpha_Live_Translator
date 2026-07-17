"""Validate that logs and artifact index belong to the same run."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from alpha.constants import APP_VERSION
from alpha.utils.run_identity import (
    RUN_TYPE_LIVE,
    RunIdentity,
    get_current_run_identity,
    sanitize_selected_language,
)

_INDEX_KV_RE = re.compile(r"^([a-zA-Z0-9_]+)=(.*)$")


def _read_index_fields(index_path: Path) -> dict[str, str]:
    if not index_path.exists():
        return {}
    fields: dict[str, str] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = _INDEX_KV_RE.match(line.strip())
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def _file_contains_run_marker(path: Path, *, run_id: str, run_timestamp: str) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    if run_id in text:
        return True
    if f'"run_id": "{run_id}"' in text or f'"run_id":"{run_id}"' in text:
        return True
    if f"run_id={run_id}" in text:
        return True
    if "DIAGNOSTIC_RUN_HEADER" in text and run_id in text:
        return True
    if run_timestamp in text:
        return True
    if f'"app_version": "{APP_VERSION}"' in text or f"app_version={APP_VERSION}" in text:
        return True
    return False


def validate_run_consistency(
    *,
    identity: Optional[RunIdentity] = None,
    host: Any = None,
) -> dict[str, Any]:
    identity = identity or get_current_run_identity()
    mismatches: list[str] = []
    blocking: list[str] = []
    warnings: list[str] = []

    if identity is None:
        return {
            "passed": False,
            "mismatches": ["no_run_identity"],
            "blocking_reasons": ["no_run_identity"],
            "warning_reasons": [],
        }

    try:
        from alpha.utils.async_debug_log import log_runtime_debug_event
        from alpha.utils.freeze_guard_log import freeze_guard_log
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "RUN_CONSISTENCY_CHECK_BEGIN",
            run_id=identity.run_id,
            run_type=identity.run_type,
        )
        freeze_guard_log(
            "RUN_CONSISTENCY_CHECK_BEGIN",
            run_id=identity.run_id,
            run_type=identity.run_type,
        )
        log_runtime_debug_event(
            "RUN_CONSISTENCY_CHECK_BEGIN",
            run_id=identity.run_id,
        )
    except Exception:
        pass

    lang, lang_real = sanitize_selected_language(identity.selected_language)
    if not lang_real:
        mismatches.append("selected_language_not_real_string")
        blocking.append("magicmock_or_invalid_language")

    if identity.run_type == RUN_TYPE_LIVE:
        if "MagicMock" in identity.selected_language:
            mismatches.append("live_run_with_magicmock_language")
            blocking.append("live_run_magicmock_language")
    else:
        if identity.run_type != RUN_TYPE_LIVE:
            warnings.append(f"non_live_run_type={identity.run_type}")

    from alpha.utils.run_artifacts import get_current_index_path

    index_path = get_current_index_path()
    index_fields: dict[str, str] = {}
    if index_path and index_path.exists():
        index_fields = _read_index_fields(index_path)
        idx_run_id = index_fields.get("run_id", "")
        idx_run_type = index_fields.get("RUN_TYPE", "")
        idx_ts = index_fields.get("run_timestamp", "")
        if idx_run_id and idx_run_id != identity.run_id:
            mismatches.append("index_run_id_mismatch")
            warnings.append("run_id_mismatch_in_index")
            try:
                from alpha.utils.run_identity import repair_artifact_run_id_if_safe
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                jp_accuracy_log(
                    "RUN_ID_MISMATCH_DETECTED",
                    expected_run_id=identity.run_id,
                    index_run_id=idx_run_id,
                )
                jp_accuracy_log("RUN_CONSISTENCY_CHECK_AUTO_REPAIR_ATTEMPT")
                if repair_artifact_run_id_if_safe():
                    jp_accuracy_log("RUN_CONSISTENCY_CHECK_AUTO_REPAIR_SUCCESS")
                else:
                    jp_accuracy_log("RUN_CONSISTENCY_CHECK_NON_BLOCKING_FAILURE")
            except Exception:
                pass
        if idx_run_type and idx_run_type != identity.run_type:
            mismatches.append("index_run_type_mismatch")
            blocking.append("run_type_mismatch_in_index")
        if idx_ts and idx_ts != identity.run_timestamp:
            mismatches.append("index_run_timestamp_mismatch")
            warnings.append("run_timestamp_mismatch_in_index")
        if identity.run_type == RUN_TYPE_LIVE and idx_run_type and idx_run_type != RUN_TYPE_LIVE:
            mismatches.append("live_run_index_not_live")
            blocking.append("live_index_run_type_invalid")
    elif identity.run_type == RUN_TYPE_LIVE:
        if not identity.index_created:
            warnings.append("run_artifacts_index_not_yet_created")
        else:
            blocking.append("run_artifacts_index_missing")

    log_checks = []
    try:
        from alpha.utils.japanese_accuracy_log import get_japanese_accuracy_log_path
        from alpha.utils.async_debug_log import get_async_debug_log_path
        from alpha.utils.diagnostic_test_log import get_log_file_path
        from alpha.utils.freeze_guard_log import get_freeze_guard_log_path

        log_checks = [
            ("accuracy", get_japanese_accuracy_log_path()),
            ("debug", get_async_debug_log_path()),
            ("diagnostic", get_log_file_path()),
            ("freeze_guard", get_freeze_guard_log_path()),
        ]
    except Exception:
        pass

    for label, path in log_checks:
        if not path.exists():
            if identity.run_type == RUN_TYPE_LIVE:
                warnings.append(f"{label}_log_missing")
            continue
        if label == "diagnostic":
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            if "DIAGNOSTIC_RUN_HEADER" in text and identity.run_id in text:
                continue
        if not _file_contains_run_marker(
            path,
            run_id=identity.run_id,
            run_timestamp=identity.run_timestamp,
        ):
            mismatches.append(f"{label}_log_version_or_run_marker_missing")
            warnings.append(f"{label}_log_run_marker_weak")

    passed = len(blocking) == 0
    result = {
        "passed": passed,
        "mismatches": mismatches,
        "blocking_reasons": blocking,
        "warning_reasons": warnings,
        "run_id": identity.run_id,
        "run_type": identity.run_type,
        "index_fields": index_fields,
    }

    event = "RUN_CONSISTENCY_CHECK_PASSED" if passed else "RUN_CONSISTENCY_CHECK_FAILED"
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log
        from alpha.utils.freeze_guard_log import freeze_guard_log
        from alpha.utils.async_debug_log import log_runtime_debug_event

        jp_accuracy_log(event, **result)
        freeze_guard_log(event, **{k: v for k, v in result.items() if k != "index_fields"})
        log_runtime_debug_event(event, mismatches=mismatches, passed=passed)
    except Exception:
        pass

    return result
