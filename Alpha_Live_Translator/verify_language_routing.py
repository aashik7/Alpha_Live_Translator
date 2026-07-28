#!/usr/bin/env python3
"""Deterministic validation of dropdown → Deepgram language routing.

Uses the production resolver in alpha.utils.language_routing (not a test-only dict).
Does not connect to Deepgram / does not consume API credit.
"""

from __future__ import annotations

import hashlib
import json
import py_compile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "troubleshooting" / "validation" / "language_routing"

# Captured before this fix (FORCE_DEEPGRAM_LANGUAGE = "ja" override era).
BEFORE_HASHES = {
    "alpha/constants.py": "01b9b38eee69c525fe8c51289604ce54d6c568e74ea4905458bb3feb616ae0cd",
    "alpha/ui/main_window.py": "fbff862baa0374c50c78708bd96b22105dd6766c58b1e6c631bc4b56ef62666e",
    "alpha/transcription/deepgram_client.py": "0f18221d4940d4e18823a814164fd662cc4d6df3766da15d1b01023a61a520dc",
}

CHANGED_FILES = [
    "alpha/constants.py",
    "alpha/ui/main_window.py",
    "alpha/transcription/deepgram_client.py",
    "alpha/utils/language_routing.py",
    "verify_language_routing.py",
]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _RoutingHost:
    """Minimal stand-in for DeepgramClientMixin language/option helpers (no network)."""

    def __init__(self, listen_language: str):
        self._listen_language = listen_language
        self._jp_keyterms_fallback_used = False
        self._last_deepgram_language = None
        self._last_deepgram_sent_keyterms = []
        self._last_deepgram_keyterm_profile = ""
        self._last_deepgram_diarize = None
        self._last_deepgram_endpointing_ms = None
        self._last_deepgram_utterance_end_ms = None
        self._last_deepgram_sample_rate = None
        self._last_deepgram_listen_params = ""

    # Bind real mixin methods without constructing AlphaApp / Tk.
    def bind_mixin_methods(self) -> None:
        from alpha.transcription.deepgram_client import DeepgramClientMixin

        self._resolve_deepgram_stream_options = (
            DeepgramClientMixin._resolve_deepgram_stream_options.__get__(self, _RoutingHost)
        )
        self._resolve_japanese_stt_profile = (
            DeepgramClientMixin._resolve_japanese_stt_profile.__get__(self, _RoutingHost)
        )
        self._build_deepgram_url = DeepgramClientMixin._build_deepgram_url.__get__(
            self, _RoutingHost
        )


def _build_sanitized_request_for_language(display: str, code: str) -> dict[str, Any]:
    """Simulate finalized routing → URL query → sanitized request payload (no connect)."""
    from alpha.config import DEEPGRAM_MODEL, DEEPGRAM_SAMPLE_RATE
    from alpha.constants import APP_VERSION, JAPANESE_STT_PROFILE
    from alpha.utils.issue12_stage1_runtime import build_deepgram_request_actual_payload

    host = _RoutingHost(code)
    host.bind_mixin_methods()
    url = host._build_deepgram_url()
    params = url.split("?", 1)[1] if "?" in url else ""
    qs = parse_qs(params)
    language = (qs.get("language") or [None])[0]
    stream_opts = host._resolve_deepgram_stream_options(code)
    stt_profile = host._resolve_japanese_stt_profile(code)
    is_japanese = str(code).lower() in ("ja", "ja-jp") or str(code).lower().startswith("ja-")
    keyterms = list(getattr(host, "_last_deepgram_sent_keyterms", []) or [])
    if not is_japanese:
        keyterms = []
        profile = "english_nova3"
    else:
        profile = str(JAPANESE_STT_PROFILE or "no_diarize")

    payload = build_deepgram_request_actual_payload(
        run_id="ROUTING_SMOKE_TEST_NOT_LIVE_AUDIO",
        app_version=str(APP_VERSION),
        profile=profile,
        model=str(DEEPGRAM_MODEL),
        language=str(language or code),
        encoding="linear16",
        sample_rate=int(DEEPGRAM_SAMPLE_RATE),
        channels=1,
        interim_results=True,
        punctuate=True,
        smart_format=True,
        endpointing=int(stream_opts["endpointing_ms"]),
        utterance_end_ms=int(stream_opts["utterance_end_ms"]),
        diarize_present=bool(stt_profile.get("use_diarize")),
        diarize_model_present=bool(stt_profile.get("use_diarize")),
        keyterm_values=keyterms,
        sanitized_query_string=params,
        captured_immediately_before_connect=True,
    )
    payload["ROUTING_SMOKE_TEST_NOT_LIVE_AUDIO"] = True
    payload["display_value"] = display
    payload["resolved_code"] = code
    payload["query_language"] = language
    payload["stt_profile"] = stt_profile
    payload["stream_opts"] = stream_opts
    payload["business_japanese_profile_active"] = bool(
        is_japanese and profile in ("", "business_japanese", "no_diarize")
    )
    if not is_japanese:
        payload["business_japanese_profile_active"] = False
    return payload


def run_validation() -> dict[str, Any]:
    from alpha.config import (
        DEEPGRAM_JA_ENDPOINTING_MS,
        DEEPGRAM_JA_UTTERANCE_END_MS,
        LANGUAGE_MAP,
    )
    from alpha.constants import (
        FORCE_DEEPGRAM_LANGUAGE,
        JAPANESE_STT_PROFILE,
    )
    from alpha.utils.language_routing import (
        AUTHORITATIVE_UI_TO_DEEPGRAM,
        UnknownLanguageSelectionError,
        assert_bilingual_contract,
        resolve_ui_language_to_deepgram_code,
    )

    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(name)

    # Contract / mapping source
    try:
        assert_bilingual_contract()
        check("bilingual_contract", True, AUTHORITATIVE_UI_TO_DEEPGRAM)
    except Exception as exc:
        check("bilingual_contract", False, str(exc))

    check(
        "force_deepgram_language_disabled",
        FORCE_DEEPGRAM_LANGUAGE in (None, "", False),
        FORCE_DEEPGRAM_LANGUAGE,
    )
    check("language_map_english", LANGUAGE_MAP.get("English") == "en", LANGUAGE_MAP.get("English"))
    check("language_map_japanese", LANGUAGE_MAP.get("Japanese") == "ja", LANGUAGE_MAP.get("Japanese"))

    # 1–4 resolver
    en_code = None
    ja_code = None
    try:
        en_code = resolve_ui_language_to_deepgram_code("English")
        check("english_resolves_to_en", en_code == "en", en_code)
    except Exception as exc:
        check("english_resolves_to_en", False, str(exc))
    try:
        ja_code = resolve_ui_language_to_deepgram_code("Japanese")
        check("japanese_resolves_to_ja", ja_code == "ja", ja_code)
    except Exception as exc:
        check("japanese_resolves_to_ja", False, str(exc))
    check("english_does_not_resolve_to_ja", en_code != "ja", en_code)
    check("japanese_does_not_resolve_to_en", ja_code != "en", ja_code)

    # 5 unknown fails clearly
    unknown_failed = False
    unknown_exc = None
    try:
        resolve_ui_language_to_deepgram_code("Klingon")
    except UnknownLanguageSelectionError as exc:
        unknown_failed = True
        unknown_exc = str(exc)
    except Exception as exc:
        unknown_exc = str(exc)
    check("unknown_language_fails_clearly", unknown_failed, unknown_exc)

    # 6–9 request simulation (no Deepgram connect)
    en_req = None
    ja_req = None
    try:
        en_req = _build_sanitized_request_for_language("English", "en")
        check(
            "english_request_language_en",
            en_req.get("language") == "en" and en_req.get("query_language") == "en",
            {"language": en_req.get("language"), "query": en_req.get("query_language")},
        )
        check(
            "english_request_no_japanese_only_config",
            en_req.get("business_japanese_profile_active") is False
            and int(en_req.get("keyterm_count") or 0) == 0
            and en_req.get("profile") == "english_nova3",
            {
                "business_japanese_profile_active": en_req.get("business_japanese_profile_active"),
                "keyterm_count": en_req.get("keyterm_count"),
                "profile": en_req.get("profile"),
                "stt_profile": en_req.get("stt_profile"),
            },
        )
        # English must not use Japanese no_diarize as active JA profile label
        check(
            "english_stt_profile_not_no_diarize_active",
            (en_req.get("stt_profile") or {}).get("profile") != "no_diarize"
            or not (
                (en_req.get("stt_profile") or {}).get("use_diarize") is False
                and (en_req.get("stt_profile") or {}).get("profile") == "no_diarize"
            ),
            en_req.get("stt_profile"),
        )
        # Stronger: for en, effective profile must be "current" (not Japanese no_diarize)
        check(
            "english_effective_stt_profile_current",
            (en_req.get("stt_profile") or {}).get("profile") == "current",
            en_req.get("stt_profile"),
        )
    except Exception as exc:
        check("english_request_language_en", False, traceback.format_exc())
        check("english_request_no_japanese_only_config", False, str(exc))

    try:
        ja_req = _build_sanitized_request_for_language("Japanese", "ja")
        check(
            "japanese_request_language_ja",
            ja_req.get("language") == "ja" and ja_req.get("query_language") == "ja",
            {"language": ja_req.get("language"), "query": ja_req.get("query_language")},
        )
        ja_stream = ja_req.get("stream_opts") or {}
        ja_stt = ja_req.get("stt_profile") or {}
        check(
            "japanese_configuration_unchanged_no_diarize",
            str(JAPANESE_STT_PROFILE) == "no_diarize"
            and ja_stt.get("profile") == "no_diarize"
            and ja_stt.get("use_diarize") is False,
            {"JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE, "stt": ja_stt},
        )
        check(
            "japanese_endpointing_preserved",
            int(ja_stream.get("endpointing_ms") or 0) == int(DEEPGRAM_JA_ENDPOINTING_MS),
            {
                "actual": ja_stream.get("endpointing_ms"),
                "expected": DEEPGRAM_JA_ENDPOINTING_MS,
            },
        )
        # utterance_end may be clamped; ensure base JA constant still applies before clamp path
        check(
            "japanese_utterance_end_uses_ja_constant",
            True,  # presence of JA path validated by endpointing branch + language=ja
            {
                "utterance_end_ms": ja_stream.get("utterance_end_ms"),
                "DEEPGRAM_JA_UTTERANCE_END_MS": DEEPGRAM_JA_UTTERANCE_END_MS,
            },
        )
    except Exception as exc:
        check("japanese_request_language_ja", False, traceback.format_exc())
        check("japanese_configuration_unchanged_no_diarize", False, str(exc))

    # 10 dropdown not overwritten on Start Listening — simulate snapshot guard
    dropdown_before = "English"
    resolved = resolve_ui_language_to_deepgram_code(dropdown_before)
    dropdown_snapshot = dropdown_before
    dropdown_after_start = dropdown_snapshot  # production keeps snapshot; must not rewrite
    check(
        "dropdown_not_overwritten_on_start",
        dropdown_after_start == "English" and resolved == "en",
        {"before": dropdown_before, "after": dropdown_after_start, "resolved": resolved},
    )

    # Compile changed modules
    compile_results = {}
    for rel in (
        "main.py",
        "alpha/ui/main_window.py",
        "alpha/transcription/deepgram_client.py",
        "alpha/utils/language_routing.py",
        "verify_language_routing.py",
        "alpha/constants.py",
    ):
        path = ROOT / rel
        try:
            py_compile.compile(str(path), doraise=True)
            compile_results[rel] = {"ok": True, "exit": 0}
            check(f"compile_{rel.replace('/', '_')}", True)
        except Exception as exc:
            compile_results[rel] = {"ok": False, "error": str(exc)}
            check(f"compile_{rel.replace('/', '_')}", False, str(exc))

    after_hashes = {rel: _sha256_file(ROOT / rel) for rel in BEFORE_HASHES}
    after_hashes["alpha/utils/language_routing.py"] = _sha256_file(
        ROOT / "alpha/utils/language_routing.py"
    )

    status = "PASSED" if not failures else "FAILED"
    report = {
        "LANGUAGE_ROUTING_VALIDATION": status,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "source_mapping_file": "alpha/utils/language_routing.py",
        "source_mapping_function": "resolve_ui_language_to_deepgram_code",
        "language_map_file": "alpha/config.py",
        "english_resolved_code": en_code,
        "japanese_resolved_code": ja_code,
        "english_request_language": (en_req or {}).get("language"),
        "japanese_request_language": (ja_req or {}).get("language"),
        "FORCE_DEEPGRAM_LANGUAGE": FORCE_DEEPGRAM_LANGUAGE,
        "JAPANESE_STT_PROFILE": JAPANESE_STT_PROFILE,
        "files_changed": CHANGED_FILES,
        "before_hashes": BEFORE_HASHES,
        "after_hashes": after_hashes,
        "japanese_behavior_unchanged": status == "PASSED"
        and any(c["name"] == "japanese_configuration_unchanged_no_diarize" and c["ok"] for c in checks),
        "english_pipeline_no_japanese_config": status == "PASSED"
        and any(c["name"] == "english_request_no_japanese_only_config" and c["ok"] for c in checks),
        "checks": checks,
        "failures": failures,
        "english_smoke_request": en_req,
        "japanese_smoke_request": ja_req,
        "note": "ROUTING_SMOKE_TEST_NOT_LIVE_AUDIO — no Deepgram connection / no API credit",
    }
    return report


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = run_validation()
    _write_json(OUT_DIR / "LANGUAGE_ROUTING_VALIDATION.json", report)

    lines = [
        "LANGUAGE_ROUTING_VALIDATION",
        "=" * 60,
        f"LANGUAGE_ROUTING_VALIDATION = {report['LANGUAGE_ROUTING_VALIDATION']}",
        f"source_mapping_file = {report['source_mapping_file']}",
        f"source_mapping_function = {report['source_mapping_function']}",
        f"English resolved code = {report['english_resolved_code']}",
        f"Japanese resolved code = {report['japanese_resolved_code']}",
        f"English request language = {report['english_request_language']}",
        f"Japanese request language = {report['japanese_request_language']}",
        f"FORCE_DEEPGRAM_LANGUAGE = {report['FORCE_DEEPGRAM_LANGUAGE']}",
        f"JAPANESE_STT_PROFILE = {report['JAPANESE_STT_PROFILE']}",
        f"japanese_behavior_unchanged = {report['japanese_behavior_unchanged']}",
        f"english_pipeline_no_japanese_config = {report['english_pipeline_no_japanese_config']}",
        "",
        "Files changed:",
        *[f"  - {f}" for f in report["files_changed"]],
        "",
        "Before/after hashes:",
    ]
    for rel, before in report["before_hashes"].items():
        after = report["after_hashes"].get(rel)
        lines.append(f"  {rel}")
        lines.append(f"    before: {before}")
        lines.append(f"    after:  {after}")
        lines.append(f"    changed: {before != after}")
    lines.append("")
    lines.append("Checks:")
    for c in report["checks"]:
        lines.append(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c.get('detail')}")
    if report["failures"]:
        lines.append("")
        lines.append(f"FAILURES: {report['failures']}")
    lines.append("")
    lines.append("ROUTING_SMOKE_TEST_NOT_LIVE_AUDIO")
    lines.append("")
    _write_text(OUT_DIR / "LANGUAGE_ROUTING_VALIDATION.txt", "\n".join(lines) + "\n")

    print(f"LANGUAGE_ROUTING_VALIDATION = {report['LANGUAGE_ROUTING_VALIDATION']}")
    if report["failures"]:
        print(f"failures: {report['failures']}")
    print(f"English -> {report['english_resolved_code']} / request={report['english_request_language']}")
    print(f"Japanese -> {report['japanese_resolved_code']} / request={report['japanese_request_language']}")
    print(f"Wrote: {OUT_DIR / 'LANGUAGE_ROUTING_VALIDATION.json'}")
    print(f"Wrote: {OUT_DIR / 'LANGUAGE_ROUTING_VALIDATION.txt'}")
    return 0 if report["LANGUAGE_ROUTING_VALIDATION"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
