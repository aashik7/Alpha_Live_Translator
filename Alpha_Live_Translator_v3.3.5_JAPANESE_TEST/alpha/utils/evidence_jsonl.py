"""Async non-blocking JSONL evidence writer with optional rotation."""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import LOG_MAX_FILE_MB, LOG_ROTATION_BACKUPS, LOG_ROTATION_ENABLED

_lock = threading.Lock()
_writers: dict[str, "_JsonlWriter"] = {}


class _JsonlWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._queue: queue.SimpleQueue[Optional[dict[str, Any]]] = queue.SimpleQueue()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._loop, name=f"JsonlWriter-{self.path.name}", daemon=True
        )
        self._thread.start()

    def post(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(payload)
        except Exception:
            pass

    def _rotate_if_needed(self) -> None:
        if not LOG_ROTATION_ENABLED:
            return
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size < LOG_MAX_FILE_MB * 1024 * 1024:
                return
            for i in range(LOG_ROTATION_BACKUPS - 1, 0, -1):
                src = self.path.with_suffix(self.path.suffix + f".{i}")
                dst = self.path.with_suffix(self.path.suffix + f".{i + 1}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
            backup = self.path.with_suffix(self.path.suffix + ".1")
            if backup.exists():
                backup.unlink()
            self.path.rename(backup)
        except Exception:
            pass

    def _loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except Exception:
                item = None
            if item is None:
                continue
            if item.get("__shutdown__"):
                return
            try:
                self._rotate_if_needed()
                line = json.dumps(item, ensure_ascii=False, default=str)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except Exception:
                pass


def _get_writer(path: Path) -> _JsonlWriter:
    key = str(path)
    with _lock:
        writer = _writers.get(key)
        if writer is None:
            writer = _JsonlWriter(path)
            _writers[key] = writer
            writer.start()
        return writer


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    payload.setdefault("timestamp", time.time())
    _get_writer(path).post(payload)


def append_jsonl_named(category: str, name: str, payload: dict[str, Any]) -> None:
    from alpha.utils.troubleshooting_paths import (
        get_accuracy_path,
        get_health_path,
        get_log_path,
        get_transcript_path,
    )

    resolvers = {
        "log": get_log_path,
        "transcript": get_transcript_path,
        "accuracy": get_accuracy_path,
        "health": get_health_path,
    }
    resolver = resolvers.get(category)
    if resolver is None:
        return
    append_jsonl(resolver(name), payload)


def rebind_runtime_log_writer() -> None:
    with _lock:
        _writers.clear()


def shutdown_jsonl_writers() -> None:
    with _lock:
        for writer in _writers.values():
            writer.post({"__shutdown__": True})
        _writers.clear()
