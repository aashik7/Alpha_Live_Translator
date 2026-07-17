"""Canonical content normalization and hashing (V25.3.3.2).

Separate byte_sha256 from normalized_content_sha256. Never use byte hashes
alone to decide transcript-content equality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional


_VOLATILE_RECORD_KEYS = frozenset(
    {
        "timestamp",
        "written_at",
        "sealed_at",
        "created_at",
        "updated_at",
        "completed_at",
        "snapshot_id",
        "run_id",
        "content_sha256",
        "canonical_record_hash",
    }
)


def normalize_text_content(text: str) -> str:
    """Normalize text for content comparison without changing Japanese meaning."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    s = str(text)
    if s.startswith("\ufeff"):
        s = s[1:]
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Platform-specific final newline difference only
    if s.endswith("\n"):
        s = s[:-1]
    return s


def normalized_text_sha256(text: str) -> str:
    payload = normalize_text_content(text).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def byte_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def byte_sha256_file(path: Path | str) -> str:
    p = Path(path)
    return byte_sha256_bytes(p.read_bytes())


def normalized_file_sha256(path: Path | str) -> str:
    p = Path(path)
    raw = p.read_bytes()
    # Decode as UTF-8 for text normalization; binary-safe fallback
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return byte_sha256_bytes(raw)
    return normalized_text_sha256(text)


def hash_pair_for_file(path: Path | str) -> dict[str, str]:
    p = Path(path)
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
        norm = normalized_text_sha256(text)
    except UnicodeDecodeError:
        norm = ""
    return {
        "byte_sha256": byte_sha256_bytes(raw),
        "normalized_content_sha256": norm,
    }


def canonicalize_record(
    record: dict[str, Any],
    *,
    exclude_volatile: bool = True,
) -> dict[str, Any]:
    """Return a content-focused canonical record dict."""
    src = dict(record or {})
    text = str(src.get("text") or src.get("final_text") or src.get("assembler_text") or "")
    lineage = list(src.get("source_raw_event_ids") or [])
    out: dict[str, Any] = {
        "record_id": str(src.get("record_id") or ""),
        "sequence_number": int(src.get("sequence_number") or src.get("sequence") or 0),
        "speaker": int(src.get("speaker") or 2),
        "text": normalize_text_content(text),
        "source_raw_event_ids": [str(x) for x in lineage],
    }
    if not exclude_volatile:
        for k, v in src.items():
            if k not in out:
                out[k] = v
    return out


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_record_sha256(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(canonicalize_record(record))).hexdigest()


def canonical_record_list_sha256(records: Iterable[dict[str, Any]]) -> str:
    rows = [canonicalize_record(r) for r in records]
    return hashlib.sha256(_canonical_json_bytes(rows)).hexdigest()


def compare_normalized_text_files(path_a: Path | str, path_b: Path | str) -> dict[str, Any]:
    a = Path(path_a)
    b = Path(path_b)
    a_bytes = a.read_bytes() if a.exists() else b""
    b_bytes = b.read_bytes() if b.exists() else b""
    a_norm = normalize_text_content(a_bytes.decode("utf-8", errors="replace"))
    b_norm = normalize_text_content(b_bytes.decode("utf-8", errors="replace"))
    return {
        "byte_sha256_a": byte_sha256_bytes(a_bytes),
        "byte_sha256_b": byte_sha256_bytes(b_bytes),
        "normalized_content_sha256_a": hashlib.sha256(a_norm.encode("utf-8")).hexdigest(),
        "normalized_content_sha256_b": hashlib.sha256(b_norm.encode("utf-8")).hexdigest(),
        "byte_identical": a_bytes == b_bytes,
        "normalized_content_identical": a_norm == b_norm,
    }


def atomic_write_bytes(path: Path, data: bytes) -> dict[str, str]:
    """Atomic binary write with read-back verification."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            import os

            os.fsync(fh.fileno())
        except Exception:
            pass
    tmp.replace(path)
    read_back = path.read_bytes()
    if read_back != data:
        raise IOError(f"atomic_write_bytes verification failed: {path}")
    return {
        "byte_sha256": byte_sha256_bytes(read_back),
        "normalized_content_sha256": normalized_text_sha256(
            read_back.decode("utf-8", errors="replace")
        ),
    }


def atomic_copy_bytes(src: Path, dest: Path) -> dict[str, str]:
    src = Path(src)
    dest = Path(dest)
    data = src.read_bytes()
    result = atomic_write_bytes(dest, data)
    if byte_sha256_file(src) != result["byte_sha256"]:
        raise IOError(f"stage copy byte mismatch: {src} -> {dest}")
    return result


def atomic_write_text_utf8(path: Path, text: str) -> dict[str, str]:
    data = text.encode("utf-8")
    return atomic_write_bytes(Path(path), data)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> dict[str, str]:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return atomic_write_text_utf8(Path(path), text)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    text = ("\n".join(lines) + ("\n" if lines else ""))
    return atomic_write_text_utf8(Path(path), text)
