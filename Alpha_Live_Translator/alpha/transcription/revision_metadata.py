"""Normalize downstream metadata from authoritative applied revision action (V25.3.2)."""

from __future__ import annotations

from typing import Any

from alpha.constants import SINGLE_REVISION_AUTHORITY_ENABLED
from alpha.utils.pipeline_integrity import PipelineIntegrityError


def normalize_applied_metadata(
    metadata: dict[str, Any],
    *,
    applied_action: str,
    revision_target_id: str = "",
    requested_update_previous: bool = False,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    applied = str(applied_action or "append")
    meta["requested_update_previous"] = bool(requested_update_previous)
    meta["requested_replaces_previous"] = bool(requested_update_previous)

    if applied == "append":
        meta["applied_action"] = "append"
        meta["update_previous"] = False
        meta["replaces_previous"] = False
        meta["revise_previous_line"] = False
        meta["stable_layer_update_previous"] = False
        meta["boundary_should_revise"] = False
        meta["revision_target_id"] = None
        meta["force_update_previous"] = False
        stab = dict(meta.get("boundary_stab_result") or {})
        stab["should_revise"] = False
        stab["update_previous"] = False
        meta["boundary_stab_result"] = stab
    elif applied in ("revise", "revise_previous"):
        meta["applied_action"] = "revise"
        meta["update_previous"] = True
        meta["replaces_previous"] = True
        meta["revise_previous_line"] = True
        meta["stable_layer_update_previous"] = True
        meta["boundary_should_revise"] = True
        meta["revision_target_id"] = revision_target_id or None
        meta["force_update_previous"] = True
        stab = dict(meta.get("boundary_stab_result") or {})
        stab["should_revise"] = True
        stab["update_previous"] = True
        meta["boundary_stab_result"] = stab
    elif applied == "no_op":
        meta["applied_action"] = "no_op"
        meta["update_previous"] = False
        meta["replaces_previous"] = False
        meta["revise_previous_line"] = False
        meta["stable_layer_update_previous"] = False
        meta["boundary_should_revise"] = False
        meta["revision_target_id"] = None
        meta["force_update_previous"] = False
    elif applied in ("suppress", "suppressed_stop_tail"):
        meta["applied_action"] = "suppress"
        meta["update_previous"] = False
        meta["replaces_previous"] = False
        meta["revise_previous_line"] = False
        meta["stable_layer_update_previous"] = False
        meta["boundary_should_revise"] = False
        meta["revision_target_id"] = None
        meta["force_update_previous"] = False
    elif applied in ("suppress_candidate", "suppressed_stop_tail_candidate"):
        meta["applied_action"] = "suppress_candidate"
        meta["update_previous"] = False
        meta["replaces_previous"] = False
        meta["revise_previous_line"] = False
        meta["stable_layer_update_previous"] = False
        meta["boundary_should_revise"] = False
        meta["revision_target_id"] = None
        meta["canonical_record_id"] = None
        meta["force_update_previous"] = False
        meta["stop_tail_candidate"] = True
        meta["stop_tail_candidate_suppressed"] = True
        meta["previous_active_record_preserved"] = True
        stab = dict(meta.get("boundary_stab_result") or {})
        stab["should_revise"] = False
        stab["update_previous"] = False
        meta["boundary_stab_result"] = stab

    if SINGLE_REVISION_AUTHORITY_ENABLED:
        assert_metadata_consistency(meta, applied_action=applied)
    return meta


def assert_metadata_consistency(metadata: dict[str, Any], *, applied_action: str) -> None:
    applied = str(applied_action or metadata.get("applied_action") or "append")
    if applied == "append":
        if metadata.get("update_previous") or metadata.get("replaces_previous") or metadata.get("revise_previous_line"):
            raise PipelineIntegrityError("append action with replace metadata true")
        if metadata.get("force_update_previous"):
            raise PipelineIntegrityError("append action with force_update_previous true")
    elif applied in ("revise", "revise_previous"):
        if not metadata.get("revision_target_id"):
            raise PipelineIntegrityError("revise action without revision_target_id")
        if not (metadata.get("update_previous") and metadata.get("replaces_previous")):
            raise PipelineIntegrityError("revise action with inconsistent replace flags")
