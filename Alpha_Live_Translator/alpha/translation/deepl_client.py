"""DeepL client using the official `deepl` Python package.

Auth key is read only from DEEPL_AUTH_KEY (via alpha.config). Never log the key.
"""

from __future__ import annotations

from typing import Optional

from alpha.config import DEEPL_AUTH_KEY, DEEPL_TIMEOUT_SECONDS


class DeepLError(Exception):
    """Raised when DeepL translation fails."""

    def __init__(self, message: str, *, code: str = "deepl_error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class DeepLClient:
    """Persistent session wrapper around deepl.Translator."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout_seconds: float | None = None,
    ):
        self._api_key = (api_key if api_key is not None else DEEPL_AUTH_KEY) or ""
        self._api_key = str(self._api_key).strip()
        self._timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else DEEPL_TIMEOUT_SECONDS
        )
        # Lazy: do not construct deepl.Translator (or touch the network) until first use.
        self._translator = None
        self._init_error: Optional[DeepLError] = None

    def _ensure_translator(self) -> None:
        if self._translator is not None:
            return
        if self._init_error is not None:
            raise self._init_error
        if not self._api_key:
            self._init_error = DeepLError(
                "DeepL auth key missing.", code="auth_missing", retryable=False
            )
            raise self._init_error
        try:
            import deepl  # type: ignore

            kwargs = {}
            if self._api_key.endswith(":fx"):
                kwargs["server_url"] = "https://api-free.deepl.com"
            self._translator = deepl.Translator(self._api_key, **kwargs)
        except Exception as exc:
            self._init_error = DeepLError(
                f"DeepL client init failed: {type(exc).__name__}",
                code="init_failed",
                retryable=False,
            )
            raise self._init_error from exc

    @property
    def available(self) -> bool:
        # Key present is enough for readiness UI; Translator is created on first translate.
        return bool(self._api_key) and self._init_error is None

    @staticmethod
    def _provider_supports_context() -> bool:
        """Does the installed deepl SDK accept `context`? Detected, not assumed.

        Item 50. `context` arrived in deepl-python 1.17; this project currently
        pins 1.30.0, which has it. But passing an unknown keyword to an older
        SDK raises `TypeError`, and `translate_text`'s error mapper classifies
        an unrecognised exception as `retryable=False` -- so a downgrade would
        turn every translation into a permanent failure rather than degrading.
        Checking the signature once removes that whole class of outcome.
        """
        cached = getattr(DeepLClient, "_context_supported_cache", None)
        if cached is not None:
            return bool(cached)
        supported = False
        try:
            import inspect

            import deepl  # type: ignore

            supported = "context" in inspect.signature(
                deepl.Translator.translate_text
            ).parameters
        except Exception:
            supported = False
        DeepLClient._context_supported_cache = supported
        return supported

    def translate_text(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: str = "",
    ) -> str:
        self._ensure_translator()
        if self._translator is None:
            raise DeepLError("DeepL auth key missing.", code="auth_missing", retryable=False)
        cleaned = (text or "").strip()
        if not cleaned:
            raise DeepLError("Cannot translate empty text.", code="empty_text", retryable=False)
        if not source_lang or not target_lang:
            raise DeepLError(
                "Explicit source and target language required.",
                code="lang_required",
                retryable=False,
            )
        try:
            import deepl  # type: ignore

            # Item 50: `context` gives DeepL the preceding lines so it can
            # resolve pronouns, honorifics and topic across an utterance
            # boundary -- which matters here because a meeting transcript is
            # translated line by line, each line stripped of everything said
            # before it. DeepL neither translates nor bills the context; it
            # only reads it. Passed as a keyword ONLY when the installed SDK
            # accepts it, and omitted entirely when empty so behaviour is
            # byte-identical to before for any caller that supplies none.
            kwargs = {"source_lang": source_lang, "target_lang": target_lang}
            hint = (context or "").strip()
            if hint and self._provider_supports_context():
                kwargs["context"] = hint
            result = self._translator.translate_text(cleaned, **kwargs)
            translated = str(getattr(result, "text", "") or "").strip()
            if not translated:
                raise DeepLError("DeepL returned empty text.", code="empty_result", retryable=False)
            return translated
        except DeepLError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            msg = str(exc)
            low = msg.lower()
            # A dropped network is the most retryable failure there is, and it
            # was being classified as permanent. `deepl.ConnectionException`
            # subclasses `DeepLException`, so it fell through to the
            # `retryable=False` branch below and the job was marked
            # `permanently_failed` with `retry_count: 0` -- no backoff, no
            # second chance, translation simply gone for that line. Measured on
            # run `...20260812-142447`: the one and only translation request
            # was issued at 14:30:01, during the WiFi drop, and died there;
            # `failed_translations: 1`, `successful_translations: 0`.
            #
            # Checked on the exception TYPE first, before the message-substring
            # rules below. Those are broad -- `"auth" in low` matches any
            # message that merely contains "auth" -- so a connection error
            # whose text happens to mention authorization would otherwise be
            # classified permanent again.
            if name in (
                "ConnectionException",
                "ConnectionError",
                "ConnectTimeout",
                "ReadTimeout",
                "Timeout",
            ) or "connection" in low:
                raise DeepLError(msg, code="connection_failed", retryable=True) from exc
            # Map common DeepL SDK / HTTP failures
            if "429" in msg or "too many requests" in low:
                raise DeepLError(msg, code="http_429", retryable=True) from exc
            if "456" in msg or "quota" in low:
                raise DeepLError(msg, code="quota_exceeded", retryable=False) from exc
            if "403" in msg or "authorization" in low or "auth" in low:
                raise DeepLError(msg, code="auth_failed", retryable=False) from exc
            if "400" in msg or "unsupported" in low:
                raise DeepLError(msg, code="invalid_request", retryable=False) from exc
            if "500" in msg or "503" in msg or "timeout" in low or "temporar" in low:
                raise DeepLError(msg, code="temporary_server", retryable=True) from exc
            if isinstance(exc, getattr(deepl, "DeepLException", ())):
                raise DeepLError(msg, code=name, retryable=False) from exc
            raise DeepLError(f"{name}", code=name, retryable=False) from exc
