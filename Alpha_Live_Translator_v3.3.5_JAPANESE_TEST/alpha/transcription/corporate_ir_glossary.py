"""Corporate IR glossary loader and validator (8.5.25)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from alpha.constants import (
    CORPORATE_IR_GLOSSARY_PATH,
    GLOSSARY_KEYTERM_MAX,
)

_LIST_FIELDS = (
    "company_names",
    "product_service_names",
    "person_names",
    "locations",
    "financial_terms",
    "business_terms",
    "formal_phrases",
    "do_not_correct",
)

_cached_glossary: dict[str, Any] | None = None
_cached_path: str = ""
_glossary_enabled_runtime: bool | None = None


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def default_glossary_path() -> Path:
    return Path(CORPORATE_IR_GLOSSARY_PATH)


def load_corporate_ir_glossary(path: str | Path | None = None) -> dict[str, Any]:
    global _cached_glossary, _cached_path, _glossary_enabled_runtime
    p = Path(path) if path else default_glossary_path()
    pstr = str(p).replace("\\", "/")
    if _cached_glossary is not None and _cached_path == pstr:
        return _cached_glossary
    _jp_log("GLOSSARY_LOAD_STARTED", path=pstr)
    if not p.exists():
        _glossary_enabled_runtime = False
        _jp_log(
            "GLOSSARY_VALIDATION_WARNING",
            reason="file_missing",
            path=pstr,
            glossary_enabled=False,
        )
        _cached_glossary = {}
        _cached_path = pstr
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    validate_corporate_ir_glossary(data)
    _jp_log("CORPORATE_IR_GLOSSARY_FILE_CREATED", path=pstr, note="loaded_existing")
    _cached_glossary = data
    _cached_path = pstr
    _glossary_enabled_runtime = True
    _jp_log("CORPORATE_IR_GLOSSARY_LOADED", path=pstr, name=data.get("glossary_name", ""))
    _jp_log("GLOSSARY_LOAD_COMPLETED", path=pstr)
    return data


def is_glossary_enabled_runtime() -> bool:
    """Fail-safe: False when glossary file is missing or never successfully loaded."""
    from alpha.constants import CORPORATE_IR_GLOSSARY_ENABLED

    if not CORPORATE_IR_GLOSSARY_ENABLED:
        return False
    if _glossary_enabled_runtime is not None:
        return bool(_glossary_enabled_runtime)
    # Probe once
    load_corporate_ir_glossary()
    return bool(_glossary_enabled_runtime)


def validate_corporate_ir_glossary(data: dict[str, Any]) -> dict[str, Any]:
    _jp_log("GLOSSARY_VALIDATION_STARTED")
    warnings: list[str] = []
    if data.get("correction_mode") != "high_confidence_only":
        raise ValueError("correction_mode must be high_confidence_only")
    for field in _LIST_FIELDS:
        items = data.get(field) or []
        if not isinstance(items, list):
            raise ValueError(f"{field} must be a list")
        cleaned = [str(x).strip() for x in items if str(x).strip()]
        if len(cleaned) != len(set(cleaned)):
            warnings.append(f"duplicate_in_{field}")
        data[field] = cleaned
        for item in cleaned:
            if not item:
                raise ValueError(f"empty entry in {field}")

    numbers = data.get("expected_numbers") or []
    if not isinstance(numbers, list):
        raise ValueError("expected_numbers must be a list")
    if len(numbers) > 100:
        raise ValueError("expected_numbers exceeds max 100")
    labels: set[str] = set()
    for row in numbers:
        if not row.get("label") or not row.get("expected"):
            raise ValueError("expected_numbers entry missing label or expected")
        labels.add(row["label"])
        if row.get("correction_allowed") is not True:
            warnings.append(f"number_not_allowed:{row.get('label')}")

    person_count = len(data.get("person_names") or [])
    if person_count > 30:
        raise ValueError("person_names exceeds max 30")

    keyterms = build_deepgram_keyterms_from_glossary(data)
    if len(keyterms) > GLOSSARY_KEYTERM_MAX:
        raise ValueError(f"keyterm count {len(keyterms)} exceeds max {GLOSSARY_KEYTERM_MAX}")

    result = {"ok": True, "warnings": warnings, "keyterm_count": len(keyterms)}
    if warnings:
        _jp_log("GLOSSARY_VALIDATION_WARNING", warnings=warnings)
    _jp_log("CORPORATE_IR_GLOSSARY_VALIDATED")
    _jp_log("GLOSSARY_VALIDATION_COMPLETED", warnings=len(warnings))
    return result


def _all_glossary_terms(data: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for field in _LIST_FIELDS:
        if field == "do_not_correct":
            continue
        terms.extend(data.get(field) or [])
    return terms


def build_deepgram_keyterms_from_glossary(data: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in _all_glossary_terms(data):
        t = term.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    out = out[:GLOSSARY_KEYTERM_MAX]
    _jp_log("GLOSSARY_KEYTERM_LIST_BUILT", count=len(out))
    return out


def build_stable_correction_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    """High-confidence variant rules derived from glossary + known IR misrecognitions."""
    candidates: list[dict[str, Any]] = []
    do_not = set(data.get("do_not_correct") or [])

    def add(
        before: str,
        after: str,
        category: str,
        *,
        context_any: list[str] | None = None,
        confidence: str = "high",
    ) -> None:
        if before in do_not or after in do_not:
            return
        candidates.append(
            {
                "before": before,
                "after": after,
                "category": category,
                "context_any": context_any or [],
                "confidence": confidence,
            }
        )

    known_variants = [
        ("さくら作プラス", "さくらさくプラス", "company_names", []),
        ("既存円", "既存園", "business_terms", ["既存", "園", "保育"]),
        ("工程価格", "公定価格", "business_terms", ["価格", "公定", "補助"]),
        ("第3市販期", "第3四半期", "financial_terms", ["四半期", "決算"]),
        ("経利益", "経常利益", "financial_terms", ["利益", "経常"]),
        ("当期準利益", "当期純利益", "financial_terms", ["当期", "純利益"]),
        ("貸借対象表", "貸借対照表", "financial_terms", ["貸借", "対照"]),
        ("有計工程資産", "有形固定資産", "financial_terms", ["固定資産", "有形"]),
        ("大阪市中国", "大阪市中央区", "locations", ["大阪", "中央"]),
        ("解説", "開設", "business_terms", ["保育所", "施設", "新規", "開設", "園"]),
        ("蔵書", "増床", "business_terms", ["園", "保育施設", "晴海園", "増床", "施設"]),
        ("量産サプリメント", "葉酸サプリメント", "business_terms", ["サプリメント", "葉酸"]),
        ("教えの程", "ご支援のほど", "formal_phrases", ["支援", "よろしく", "お願い"]),
        ("再三事業", "不採算事業", "business_terms", ["事業", "不採算", "採算"]),
        ("投資反機累計機関", "当四半期累計期間", "financial_terms", ["四半期", "累計", "期間"]),
        ("覚悟会社", "各社", "business_terms", ["各社", "会社"]),
    ]
    for before, after, cat, ctx in known_variants:
        add(before, after, cat, context_any=ctx)

    for term in _all_glossary_terms(data):
        if term in do_not:
            continue
        candidates.append(
            {"before": term, "after": term, "category": "glossary_hit", "context_any": [], "confidence": "audit"}
        )

    _jp_log("GLOSSARY_STABLE_RULES_BUILT", count=len(candidates))
    return candidates


def build_expected_number_rules(data: dict[str, Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for row in data.get("expected_numbers") or []:
        if not row.get("correction_allowed"):
            continue
        rules.append(
            {
                "label": row.get("label", ""),
                "expected": row.get("expected", ""),
                "aliases": list(row.get("aliases") or []),
                "context_terms": list(row.get("context_terms") or []),
            }
        )
    return rules


def glossary_entry_counts(data: dict[str, Any]) -> dict[str, int]:
    counts = {f: len(data.get(f) or []) for f in _LIST_FIELDS}
    counts["expected_numbers"] = len(data.get("expected_numbers") or [])
    counts["total_keyterms"] = len(build_deepgram_keyterms_from_glossary(data))
    _jp_log("CORPORATE_IR_GLOSSARY_ENTRY_COUNT_LOGGED", **counts)
    return counts


def merge_keyterms_with_glossary(
    base_keyterms: list[str],
    glossary_data: dict[str, Any] | None = None,
) -> tuple[list[str], int]:
    glossary_data = glossary_data or load_corporate_ir_glossary()
    if not glossary_data or not glossary_data.get("keyterm_boost_enabled", True):
        return base_keyterms, 0
    glossary_terms = build_deepgram_keyterms_from_glossary(glossary_data)
    seen = {t for t in base_keyterms}
    merged = list(base_keyterms)
    added = 0
    for term in glossary_terms:
        if term in seen:
            continue
        merged.append(term)
        seen.add(term)
        added += 1
        if len(merged) >= GLOSSARY_KEYTERM_MAX:
            break
    return merged[:GLOSSARY_KEYTERM_MAX], added
