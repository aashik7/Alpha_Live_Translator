"""Issue 12 Stage 1 runtime helpers — benchmark mode + meeting-context profile.

Activated only via orchestrator environment. Production defaults remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ACCURACY_PROFILE_TARGET_85 = "target_85_meeting_context"
BENCHMARK_AUDIO_SYSTEM_ONLY = "system_audio_only"
FROZEN_INFRASTRUCTURE_BASELINE = "3.3.5.5.8.5.25.3.3.2.8"
ISSUE12_STAGE1_VERSION = "3.3.5.5.8.5.26.1"
MEETING_CONTEXT_GLOSSARY_REL = Path(
    "troubleshooting/accuracy_benchmark/glossaries/test01_meeting_context.json"
)
MEETING_CONTEXT_REPORT_REL = Path(
    "troubleshooting/accuracy_benchmark/glossaries/test01_meeting_context_report.json"
)
MAX_MEETING_CONTEXT_TERMS = 40

# High-confidence terms extracted from authoritative test01.txt only.
# Exact spellings preserved; categories constrained to the Stage 1 allow-list.
_MEETING_CONTEXT_SEED: list[tuple[str, str]] = [
    ("株式会社さくらさくプラス", "company_name"),
    ("さくらさくプラス", "company_name"),
    ("中山", "participant_name"),
    ("石川祐介", "participant_name"),
    ("矢藤誠慈郎", "participant_name"),
    ("さくらさくみらい", "company_name"),
    ("さくらさくみらい東平", "product_name"),
    ("さくらさくみらい晴海", "product_name"),
    ("保育のデザイン研究所", "company_name"),
    ("YELL", "company_name"),
    ("経営管理本部長", "job_title"),
    ("第3四半期", "date"),
    ("2026年7月期", "date"),
    ("2027年7月期", "date"),
    ("売上高", "financial_term"),
    ("営業利益", "financial_term"),
    ("経常利益", "financial_term"),
    ("当期純利益", "financial_term"),
    ("営業利益率", "financial_term"),
    ("進捗率", "financial_term"),
    ("貸借対照表", "financial_term"),
    ("自己資本比率", "financial_term"),
    ("未収入金", "financial_term"),
    ("有形固定資産", "financial_term"),
    ("短期借入金", "financial_term"),
    ("長期借入金", "financial_term"),
    ("公定価格", "financial_term"),
    ("補助金", "financial_term"),
    ("保育サービス", "business_term"),
    ("不動産事業", "business_term"),
    ("マンション開発事業", "business_term"),
    ("買取再販事業", "business_term"),
    ("ホテル再生事業", "business_term"),
    ("フェムケア", "product_name"),
    ("フェムテック", "product_name"),
    ("葉酸サプリメント", "product_name"),
    ("送迎バス", "product_name"),
    ("児童定員数", "business_term"),
    ("在籍率", "business_term"),
    ("大阪市中央区", "department_name"),
]

_REJECTED_CANDIDATES: list[dict[str, str]] = [
    {
        "term": "皆さん、こんにちは。株式会社さくらさくプラス代表の中山です。",
        "reason": "complete_sentence",
    },
    {"term": "は", "reason": "ordinary_grammar_particle"},
    {"term": "を", "reason": "ordinary_grammar_particle"},
    {"term": "に", "reason": "ordinary_grammar_particle"},
    {"term": "さくらさく・プラス", "reason": "alternative_spelling_not_in_reference"},
    {"term": "サクラサクプラス", "reason": "guessed_katakana_variant"},
    {"term": "お願いいたします", "reason": "ordinary_politeness_phrase"},
]


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def get_active_japanese_accuracy_profile() -> str:
    env = os.environ.get("JAPANESE_ACCURACY_PROFILE", "").strip()
    if env:
        return env
    try:
        from alpha.constants import JAPANESE_ACCURACY_PROFILE

        return str(JAPANESE_ACCURACY_PROFILE or "").strip()
    except Exception:
        return ""


def is_target_85_meeting_context_active() -> bool:
    return get_active_japanese_accuracy_profile() == ACCURACY_PROFILE_TARGET_85


def is_issue12_stage1_benchmark_active() -> bool:
    return _truthy_env("ISSUE12_STAGE1_BENCHMARK") or _truthy_env(
        "ALPHA_ISSUE12_STAGE1_BENCHMARK"
    )


def get_benchmark_audio_source() -> str:
    return os.environ.get("BENCHMARK_AUDIO_SOURCE", "").strip()


def is_system_audio_only_benchmark() -> bool:
    """True only when Stage 1 orchestrator activates system_audio_only."""
    if not is_issue12_stage1_benchmark_active():
        return False
    return get_benchmark_audio_source() == BENCHMARK_AUDIO_SYSTEM_ONLY


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def category_to_keyterm_class(category: str) -> str:
    mapping = {
        "participant_name": "proper_noun",
        "company_name": "proper_noun",
        "department_name": "proper_noun",
        "job_title": "domain_term",
        "product_name": "proper_noun",
        "number": "domain_term",
        "date": "domain_term",
        "financial_term": "domain_term",
        "business_term": "meeting_term",
    }
    return mapping.get(str(category), "proper_noun")


def build_meeting_context_glossary(
    *,
    project_root: Path,
    reference_path: Path,
) -> dict[str, Any]:
    """Create glossary + report from reference. term_count must be <= 40."""
    project_root = Path(project_root)
    reference_path = Path(reference_path)
    if not reference_path.is_absolute():
        reference_path = project_root / reference_path
    ref_text = reference_path.read_text(encoding="utf-8")
    ref_sha = sha256_file(reference_path)

    terms: list[dict[str, str]] = []
    rejected = list(_REJECTED_CANDIDATES)
    seen: set[str] = set()
    for term, category in _MEETING_CONTEXT_SEED:
        if term in seen:
            rejected.append({"term": term, "reason": "duplicate"})
            continue
        if term not in ref_text:
            rejected.append({"term": term, "reason": "not_found_in_reference"})
            continue
        if "。" in term or "、" in term and len(term) > 20:
            rejected.append({"term": term, "reason": "looks_like_sentence"})
            continue
        seen.add(term)
        terms.append({"term": term, "category": category})
        if len(terms) >= MAX_MEETING_CONTEXT_TERMS:
            break

    if len(terms) > MAX_MEETING_CONTEXT_TERMS:
        raise ValueError(f"term_count {len(terms)} exceeds max {MAX_MEETING_CONTEXT_TERMS}")

    categories: dict[str, list[str]] = {}
    for item in terms:
        categories.setdefault(item["category"], []).append(item["term"])

    glossary: dict[str, Any] = {
        "profile_name": ACCURACY_PROFILE_TARGET_85,
        "reference_path": str(reference_path.relative_to(project_root)).replace("\\", "/"),
        "reference_sha256": ref_sha,
        "maximum_term_count": MAX_MEETING_CONTEXT_TERMS,
        "term_count": len(terms),
        "terms": terms,
        "categories": categories,
    }

    report: dict[str, Any] = {
        "reference_path": glossary["reference_path"],
        "reference_sha256": ref_sha,
        "terms": [t["term"] for t in terms],
        "term_count": len(terms),
        "categories": categories,
        "rejected_candidates": rejected,
        "maximum_term_count": MAX_MEETING_CONTEXT_TERMS,
        "profile_name": ACCURACY_PROFILE_TARGET_85,
    }

    gloss_path = project_root / MEETING_CONTEXT_GLOSSARY_REL
    report_path = project_root / MEETING_CONTEXT_REPORT_REL
    gloss_path.parent.mkdir(parents=True, exist_ok=True)
    gloss_path.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "glossary": glossary,
        "report": report,
        "glossary_path": gloss_path,
        "report_path": report_path,
    }


def load_meeting_context_terms(project_root: Path | None = None) -> list[dict[str, str]]:
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
    path = root / MEETING_CONTEXT_GLOSSARY_REL
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    terms = data.get("terms") or []
    out: list[dict[str, str]] = []
    for item in terms:
        if isinstance(item, dict):
            term = str(item.get("term") or "").strip()
            cat = str(item.get("category") or "business_term").strip()
        else:
            term = str(item).strip()
            cat = "business_term"
        if term:
            out.append({"term": term, "category": cat})
    if len(out) > MAX_MEETING_CONTEXT_TERMS:
        out = out[:MAX_MEETING_CONTEXT_TERMS]
    return out


def load_meeting_context_keyterm_strings(project_root: Path | None = None) -> list[str]:
    return [item["term"] for item in load_meeting_context_terms(project_root)]


def register_meeting_context_keyterm_classes(project_root: Path | None = None) -> None:
    """Ensure meeting terms classify into Deepgram-sendable classes."""
    try:
        from alpha.constants import JAPANESE_KEYTERM_CLASSES
    except Exception:
        return
    for item in load_meeting_context_terms(project_root):
        JAPANESE_KEYTERM_CLASSES[item["term"]] = category_to_keyterm_class(item["category"])


def sanitize_deepgram_query_string(query: str) -> str:
    """Strip API keys / authorization material from a query or URL fragment."""
    text = str(query or "")
    lower = text.lower()
    for marker in ("token=", "authorization=", "api_key=", "apikey="):
        idx = lower.find(marker)
        while idx >= 0:
            end = text.find("&", idx)
            if end < 0:
                text = text[:idx]
                lower = text.lower()
                break
            text = text[:idx] + text[end + 1 :]
            lower = text.lower()
            idx = lower.find(marker)
    # Drop credential-bearing URL schemes if a full URL was passed
    if "://" in text:
        try:
            from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

            parsed = urlparse(text)
            pairs = [
                (k, v)
                for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k.lower() not in ("token", "api_key", "apikey", "authorization")
            ]
            text = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    "",
                    urlencode(pairs, doseq=True),
                    "",
                )
            )
            if "Token" in text:
                text = text.split("Token", 1)[0].rstrip("?&")
        except Exception:
            if "Token" in text:
                text = text.split("Token", 1)[0]
    return text


def build_deepgram_request_actual_payload(
    *,
    run_id: str,
    app_version: str,
    profile: str,
    model: str,
    language: str,
    encoding: str,
    sample_rate: int,
    channels: int,
    interim_results: bool,
    punctuate: bool,
    smart_format: bool,
    endpointing: int,
    utterance_end_ms: int,
    diarize_present: bool,
    diarize_model_present: bool,
    keyterm_values: list[str],
    sanitized_query_string: str,
    captured_immediately_before_connect: bool = True,
) -> dict[str, Any]:
    sanitized = sanitize_deepgram_query_string(sanitized_query_string)
    keyterm_values = [str(t) for t in keyterm_values if str(t).strip()]
    payload = {
        "run_id": run_id,
        "app_version": app_version,
        "profile": profile,
        "model": model,
        "language": language,
        "encoding": encoding,
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "interim_results": bool(interim_results),
        "punctuate": bool(punctuate),
        "smart_format": bool(smart_format),
        "endpointing": int(endpointing),
        "utterance_end_ms": int(utterance_end_ms),
        "diarize_present": bool(diarize_present),
        "diarize_model_present": bool(diarize_model_present),
        "keyterm_parameter_present": bool(keyterm_values),
        "keyterm_count": len(keyterm_values),
        "keyterm_values": keyterm_values,
        "sanitized_query_string": sanitized,
        "captured_immediately_before_connect": bool(captured_immediately_before_connect),
    }
    payload["request_sha256"] = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return payload


def build_benchmark_audio_source_record(
    *,
    run_id: str,
    system_audio_enabled: bool,
    microphone_mix_enabled: bool,
    benchmark_mode: bool,
    audio_format: str = "linear16",
    sample_rate: int = 16000,
    channels: int = 1,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "BENCHMARK_AUDIO_SOURCE": BENCHMARK_AUDIO_SYSTEM_ONLY if benchmark_mode else "",
        "BENCHMARK_AUDIO_SOURCE_ACTIVE": bool(
            benchmark_mode and system_audio_enabled and not microphone_mix_enabled
        ),
        "system_audio_enabled": bool(system_audio_enabled),
        "microphone_mix_enabled": bool(microphone_mix_enabled),
        "benchmark_mode": bool(benchmark_mode),
        "audio_format": audio_format,
        "sample_rate": int(sample_rate),
        "channels": int(channels),
    }
