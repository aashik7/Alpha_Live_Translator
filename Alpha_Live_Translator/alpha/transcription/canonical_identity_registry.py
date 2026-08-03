"""Session-scoped canonical identity authority for transcript mutations."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _norm_channel(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


@dataclass
class IdentityEntry:
    session_id: str
    channel_index: str
    canonical_utterance_id: str
    provider_utterance_id: str = ""
    source_version: int = 0
    canonical_record_id: str = ""
    lifecycle_state: str = ""
    translation_eligible: bool = False
    last_text_hash: str = ""
    last_text: str = ""
    last_decision: str = ""


@dataclass(frozen=True)
class IdentityObservation:
    accepted: bool
    duplicate: bool = False
    rejected: bool = False
    stale: bool = False
    reason: str = ""
    entry: dict[str, Any] | None = None


_lock = threading.RLock()
_session_id = ""
_entries: dict[tuple[str, str, str], IdentityEntry] = {}
_decision_keys: dict[tuple[str, str, str, int, str], str] = {}


def reset_for_session(session_id: str) -> None:
    global _session_id, _entries, _decision_keys
    with _lock:
        _session_id = str(session_id or "")
        _entries = {}
        _decision_keys = {}
    _jp_log("CANONICAL_IDENTITY_REGISTRY_RESET", session_id=_session_id)


def _entry_key(session_id: str, channel_index: Any, canonical_utterance_id: str) -> tuple[str, str, str]:
    return (
        str(session_id or ""),
        _norm_channel(channel_index),
        str(canonical_utterance_id or "").strip(),
    )


def observe_identity(
    *,
    session_id: str,
    channel_index: Any,
    canonical_utterance_id: str,
    provider_utterance_id: str = "",
    source_version: int,
    decision: str,
    text: str,
    lifecycle_state: str,
    translation_eligible: bool,
) -> IdentityObservation:
    key = _entry_key(session_id, channel_index, canonical_utterance_id)
    text_value = str(text or "")
    text_digest = _text_hash(text_value)
    version = max(1, int(source_version or 1))
    decision_name = str(decision or "").strip().upper()

    if not key[0] or not key[2]:
        _jp_log(
            "IDENTITY_REJECTION",
            reason="missing_identity_key",
            session_id=key[0],
            channel_index=key[1],
            canonical_utterance_id=key[2],
            source_version=version,
            decision=decision_name,
        )
        return IdentityObservation(
            accepted=False,
            rejected=True,
            reason="missing_identity_key",
        )

    with _lock:
        if _session_id and key[0] != _session_id:
            _jp_log(
                "IDENTITY_REJECTION",
                reason="session_mismatch",
                registry_session_id=_session_id,
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
            )
            return IdentityObservation(
                accepted=False,
                rejected=True,
                reason="session_mismatch",
            )

        entry = _entries.get(key)
        if entry is None:
            entry = IdentityEntry(
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
            )
            _entries[key] = entry

        decision_key = (key[0], key[1], key[2], version, decision_name)
        seen_hash = _decision_keys.get(decision_key)
        if seen_hash is not None:
            if seen_hash == text_digest:
                payload = _entry_dict(entry)
                if (
                    not payload.get("canonical_record_id")
                    and decision_name in ("TERMINAL_COMMIT", "SUPERSEDE")
                ):
                    entry.provider_utterance_id = str(
                        provider_utterance_id or entry.provider_utterance_id or ""
                    )
                    entry.lifecycle_state = str(
                        lifecycle_state or entry.lifecycle_state or ""
                    )
                    entry.translation_eligible = bool(translation_eligible)
                    return IdentityObservation(
                        accepted=True,
                        reason="awaiting_canonical_commit",
                        entry=_entry_dict(entry),
                    )
                _jp_log(
                    "DUPLICATE_IGNORE",
                    reason="idempotent_replay",
                    session_id=key[0],
                    channel_index=key[1],
                    canonical_utterance_id=key[2],
                    source_version=version,
                    decision=decision_name,
                    canonical_record_id=payload.get("canonical_record_id"),
                )
                return IdentityObservation(
                    accepted=True,
                    duplicate=True,
                    reason="idempotent_replay",
                    entry=payload,
                )
            _jp_log(
                "IDENTITY_REJECTION",
                reason="conflicting_idempotency_replay",
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
                source_version=version,
                decision=decision_name,
            )
            return IdentityObservation(
                accepted=False,
                rejected=True,
                reason="conflicting_idempotency_replay",
            )

        if version < int(entry.source_version or 0):
            _jp_log(
                "STALE_VERSION_REJECTED",
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
                source_version=version,
                current_source_version=entry.source_version,
                decision=decision_name,
            )
            return IdentityObservation(
                accepted=False,
                rejected=True,
                stale=True,
                reason="stale_version",
                entry=_entry_dict(entry),
            )

        if version == int(entry.source_version or 0) and entry.source_version > 0:
            if entry.last_text_hash == text_digest:
                if decision_name != str(entry.last_decision or ""):
                    entry.provider_utterance_id = str(provider_utterance_id or entry.provider_utterance_id or "")
                    entry.lifecycle_state = str(lifecycle_state or entry.lifecycle_state or "")
                    entry.translation_eligible = bool(translation_eligible)
                    entry.last_decision = decision_name
                    _decision_keys[decision_key] = text_digest
                    return IdentityObservation(
                        accepted=True,
                        reason="same_version_state_promotion",
                        entry=_entry_dict(entry),
                    )
                _decision_keys[decision_key] = text_digest
                payload = _entry_dict(entry)
                _jp_log(
                    "DUPLICATE_IGNORE",
                    reason="same_version_same_text",
                    session_id=key[0],
                    channel_index=key[1],
                    canonical_utterance_id=key[2],
                    source_version=version,
                    decision=decision_name,
                    canonical_record_id=payload.get("canonical_record_id"),
                )
                return IdentityObservation(
                    accepted=True,
                    duplicate=True,
                    reason="same_version_same_text",
                    entry=payload,
                )
            _jp_log(
                "IDENTITY_REJECTION",
                reason="conflicting_same_version_text",
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
                source_version=version,
                decision=decision_name,
            )
            return IdentityObservation(
                accepted=False,
                rejected=True,
                reason="conflicting_same_version_text",
                entry=_entry_dict(entry),
            )

        entry.provider_utterance_id = str(provider_utterance_id or entry.provider_utterance_id or "")
        entry.source_version = version
        entry.lifecycle_state = str(lifecycle_state or entry.lifecycle_state or "")
        entry.translation_eligible = bool(translation_eligible)
        entry.last_text_hash = text_digest
        entry.last_text = text_value
        entry.last_decision = decision_name
        _decision_keys[decision_key] = text_digest
        return IdentityObservation(
            accepted=True,
            reason="accepted",
            entry=_entry_dict(entry),
        )


def resolve_canonical_record_id(
    *,
    session_id: str,
    channel_index: Any,
    canonical_utterance_id: str,
) -> str:
    with _lock:
        entry = _entries.get(_entry_key(session_id, channel_index, canonical_utterance_id))
        if entry is None:
            return ""
        return str(entry.canonical_record_id or "")


def assign_canonical_record_id(
    *,
    session_id: str,
    channel_index: Any,
    canonical_utterance_id: str,
    canonical_record_id: str,
) -> IdentityObservation:
    key = _entry_key(session_id, channel_index, canonical_utterance_id)
    record_id = str(canonical_record_id or "").strip()
    if not key[0] or not key[2] or not record_id:
        return IdentityObservation(
            accepted=False,
            rejected=True,
            reason="missing_record_assignment_key",
        )
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            _jp_log(
                "IDENTITY_REJECTION",
                reason="missing_identity_for_record_assignment",
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
                canonical_record_id=record_id,
            )
            return IdentityObservation(
                accepted=False,
                rejected=True,
                reason="missing_identity_for_record_assignment",
            )
        if entry.canonical_record_id and entry.canonical_record_id != record_id:
            _jp_log(
                "IDENTITY_REJECTION",
                reason="canonical_record_id_immutable",
                session_id=key[0],
                channel_index=key[1],
                canonical_utterance_id=key[2],
                canonical_record_id=entry.canonical_record_id,
                attempted_record_id=record_id,
            )
            return IdentityObservation(
                accepted=False,
                rejected=True,
                reason="canonical_record_id_immutable",
                entry=_entry_dict(entry),
            )
        entry.canonical_record_id = record_id
        payload = _entry_dict(entry)
    _jp_log(
        "COMMIT_APPLIED",
        session_id=key[0],
        channel_index=key[1],
        canonical_utterance_id=key[2],
        canonical_record_id=record_id,
        source_version=payload.get("source_version"),
        lifecycle_state=payload.get("lifecycle_state"),
    )
    return IdentityObservation(
        accepted=True,
        reason="record_assigned",
        entry=payload,
    )


def get_identity_entry(
    *,
    session_id: str,
    channel_index: Any,
    canonical_utterance_id: str,
) -> dict[str, Any]:
    with _lock:
        entry = _entries.get(_entry_key(session_id, channel_index, canonical_utterance_id))
        return _entry_dict(entry) if entry is not None else {}


def _entry_dict(entry: IdentityEntry | None) -> dict[str, Any]:
    if entry is None:
        return {}
    return {
        "session_id": entry.session_id,
        "channel_index": entry.channel_index,
        "canonical_utterance_id": entry.canonical_utterance_id,
        "provider_utterance_id": entry.provider_utterance_id,
        "source_version": int(entry.source_version or 0),
        "canonical_record_id": entry.canonical_record_id,
        "lifecycle_state": entry.lifecycle_state,
        "translation_eligible": bool(entry.translation_eligible),
        "last_decision": entry.last_decision,
    }
