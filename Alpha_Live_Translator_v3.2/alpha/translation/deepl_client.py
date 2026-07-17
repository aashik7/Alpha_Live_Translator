"""DeepL REST API client for text translation."""

from __future__ import annotations

from typing import Optional

import requests


class DeepLError(Exception):
    """Raised when DeepL translation fails."""


class DeepLClient:
    """Thin wrapper around DeepL POST /v2/translate."""

    def __init__(
        self,
        api_key: Optional[str],
        api_plan: str = "auto",
        timeout_seconds: float = 10,
    ):
        self._api_key = (api_key or "").strip()
        self._api_plan = (api_plan or "auto").lower()
        self._timeout_seconds = timeout_seconds
        self._base_url = self._resolve_base_url()

    def _resolve_base_url(self) -> str:
        plan = self._api_plan
        if plan == "free":
            return "https://api-free.deepl.com"
        if plan == "pro":
            return "https://api.deepl.com"
        if plan == "auto":
            if self._api_key.endswith(":fx"):
                return "https://api-free.deepl.com"
            return "https://api.deepl.com"
        raise DeepLError(f"Unsupported DEEPL_API_PLAN value: {self._api_plan!r}")

    def translate_text(
        self,
        text: str,
        source_lang: Optional[str],
        target_lang: str,
    ) -> str:
        """Translate text and return the translated string."""
        if not self._api_key:
            raise DeepLError("DeepL API key is missing.")

        cleaned = (text or "").strip()
        if not cleaned:
            raise DeepLError("Cannot translate empty text.")

        if not target_lang:
            raise DeepLError("Target language code is required.")

        url = f"{self._base_url}/v2/translate"
        headers = {
            "Authorization": f"DeepL-Auth-Key {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict = {
            "text": [cleaned],
            "target_lang": target_lang,
        }
        if source_lang:
            body["source_lang"] = source_lang

        try:
            response = requests.post(
                url,
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except requests.Timeout as exc:
            raise DeepLError("DeepL request timed out.") from exc
        except requests.RequestException as exc:
            raise DeepLError(f"DeepL network error: {exc}") from exc

        if not response.ok:
            detail = self._extract_error_message(response)
            raise DeepLError(f"DeepL HTTP {response.status_code}: {detail}")

        try:
            payload = response.json()
            translations = payload.get("translations") or []
            if not translations:
                raise DeepLError("DeepL returned no translations.")
            translated = translations[0].get("text", "").strip()
            if not translated:
                raise DeepLError("DeepL returned an empty translation.")
            return translated
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise DeepLError("DeepL returned a malformed response.") from exc

    @staticmethod
    def _extract_error_message(response: requests.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("message") or payload.get("error", {}).get("message")
            if message:
                return str(message)
        except (ValueError, TypeError):
            pass
        text = (response.text or "").strip()
        return text[:200] if text else "Unknown error"
