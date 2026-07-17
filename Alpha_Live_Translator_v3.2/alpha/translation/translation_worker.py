"""Background translation worker — runs DeepL calls off the UI thread."""

from __future__ import annotations

import queue
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

from alpha.translation.deepl_client import DeepLClient, DeepLError
from alpha.translation.language_map import (
    get_deepl_source_code,
    get_deepl_target_code,
    is_same_language,
)


@dataclass
class TranslationResult:
    """Result payload delivered to the UI callback."""

    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    speaker: Optional[int] = None
    timestamp: Optional[str] = None
    from_cache: bool = False


@dataclass
class _TranslationJob:
    text: str
    speaker: Optional[int]
    source_language: str
    target_language: str
    timestamp: Optional[str]


class TranslationWorker:
    """Daemon worker thread that processes translation jobs from a queue."""

    QUEUE_MAXSIZE = 100
    CACHE_MAXSIZE = 200

    def __init__(
        self,
        deepl_client: DeepLClient,
        on_result: Optional[Callable[[TranslationResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self._client = deepl_client
        self._on_result = on_result
        self._on_error = on_error
        self._queue: queue.Queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cache: OrderedDict[tuple, str] = OrderedDict()

    def is_available(self) -> bool:
        return self._client is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="TranslationWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def submit(
        self,
        text: str,
        speaker: Optional[int] = None,
        source_language: Optional[str] = None,
        target_language: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return

        source_language = source_language or ""
        target_language = target_language or ""

        job = _TranslationJob(
            text=cleaned,
            speaker=speaker,
            source_language=source_language,
            target_language=target_language,
            timestamp=timestamp,
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            try:
                self._queue.get_nowait()
                print("[Translation] Queue full — dropped oldest translation job.")
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                print("[Translation] Queue full — rejected translation job.")

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if job is None:
                break

            self._process_job(job)

    def _process_job(self, job: _TranslationJob) -> None:
        try:
            if is_same_language(job.source_language, job.target_language):
                result = TranslationResult(
                    original_text=job.text,
                    translated_text=job.text,
                    source_language=job.source_language,
                    target_language=job.target_language,
                    speaker=job.speaker,
                    timestamp=job.timestamp,
                )
                self._emit_result(result)
                return

            source_code = get_deepl_source_code(job.source_language)
            target_code = get_deepl_target_code(job.target_language)
            if source_code is None:
                self._emit_error(
                    f"Unsupported source language: {job.source_language!r}"
                )
                return
            if target_code is None:
                self._emit_error(
                    f"Unsupported target language: {job.target_language!r}"
                )
                return

            cache_key = (source_code, target_code, job.text)
            if cache_key in self._cache:
                translated = self._cache[cache_key]
                self._cache.move_to_end(cache_key)
                self._emit_result(
                    TranslationResult(
                        original_text=job.text,
                        translated_text=translated,
                        source_language=job.source_language,
                        target_language=job.target_language,
                        speaker=job.speaker,
                        timestamp=job.timestamp,
                        from_cache=True,
                    )
                )
                return

            translated = self._client.translate_text(
                job.text,
                source_lang=source_code,
                target_lang=target_code,
            )
            self._cache[cache_key] = translated
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self.CACHE_MAXSIZE:
                self._cache.popitem(last=False)

            self._emit_result(
                TranslationResult(
                    original_text=job.text,
                    translated_text=translated,
                    source_language=job.source_language,
                    target_language=job.target_language,
                    speaker=job.speaker,
                    timestamp=job.timestamp,
                )
            )
        except DeepLError as exc:
            self._emit_error(str(exc))
        except Exception as exc:
            self._emit_error(f"Translation failed: {exc}")

    def _emit_result(self, result: TranslationResult) -> None:
        if self._on_result:
            try:
                self._on_result(result)
            except Exception as exc:
                print(f"[Translation] Result callback error: {exc}")

    def _emit_error(self, message: str) -> None:
        if self._on_error:
            try:
                self._on_error(message)
            except Exception as exc:
                print(f"[Translation] Error callback error: {exc}")
