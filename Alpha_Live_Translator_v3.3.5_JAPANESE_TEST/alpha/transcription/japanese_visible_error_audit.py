"""Visible error audit — detect likely transcript issues without auto-correcting."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    ANTI_OVERFIT_MODE_ENABLED,
    APP_VERSION,
    AUTO_BUSINESS_CORRECTION_LEVEL,
    BENCHMARK_BASELINE_LOCK_ENABLED,
    VISIBLE_ERROR_AUDIT_EXPANDED,
)

_SPEAKER_LINE = re.compile(r"^\[Speaker (\d+)\]\s*(.+)$")

# (pattern_id, needle_or_regex, category, severity, suspected_issue, likely_expected, requires_glossary)
_STRING_PATTERNS: tuple[tuple[str, str, str, str, str, str, bool], ...] = (
    ("doudouzo", "どうどうぞ", "polite_artifact", "high", "duplicate_douzo_prefix", "どうぞ", False),
    ("double_osewa", "おお世話", "polite_artifact", "high", "double_osewa_prefix", "お世話", False),
    ("triple_koko", "こここ", "polite_artifact", "high", "triple_koko_prefix", "ここ", False),
    ("yoroshiku_double_i", "よろしくお願いいします", "polite_artifact", "medium", "extra_i", "よろしくお願いいたします", False),
    ("yoroshiku_missing_shi", "よろしくお願いたします", "polite_artifact", "medium", "missing_shi", "よろしくお願いいたします", False),
    ("osewa_nate", "お世話になております", "polite_artifact", "high", "missing_te", "お世話になっております", False),
    ("goaisatsu_typo", "ご愛挨拶", "polite_artifact", "high", "aisatsu_kanji_typo", "ご挨拶", False),
    ("yatte_kimashi", "やってきまし", "particle_ending", "medium", "incomplete_verb_ending", "やってきました", False),
    ("watasite_kudai", "渡してくだい", "particle_ending", "medium", "missing_sai", "渡してください", False),
    ("nate_orimasu", "なております", "particle_ending", "high", "missing_tte", "なっております", False),
    ("shite_ikimashi", "していきまし", "particle_ending", "medium", "incomplete_mashou", "していきましょう", False),
    ("renshuu_shi", "練習しいきましょう", "particle_ending", "medium", "missing_te", "練習していきましょう", False),
    ("itayouni", "いたように", "particle_ending", "low", "possible_iitayouni", "いったように", False),
    ("kotaerarenai", "応えられなですよね", "particle_ending", "medium", "missing_ku", "応えられないですよね", False),
    ("eigyou_bucho_sa", "永井さ", "name_honorific", "medium", "incomplete_san", "永井さん", True),
    ("etou_sa", "江藤さ", "name_honorific", "medium", "incomplete_san", "江藤さん", True),
    ("a_san_typo", "Aさですか", "name_honorific", "medium", "participant_a_san_typo", "Aさんですか", True),
    ("minasan_typo", "皆さ、", "name_honorific", "medium", "minasan_missing_n", "皆さん", False),
    ("meishi_koukan", "名詞交換", "business_term", "high", "meishi_vs_meishi", "名刺交換", False),
    ("meishou_morau", "名称をもらう側", "business_term", "high", "meishou_vs_meishi", "名刺をもらう側", False),
    ("meishi_watashi", "名詞を渡して", "business_term", "high", "meishi_vs_meishi", "名刺を渡して", False),
    ("tantou_missing_sa", "担当せていただく", "business_term", "medium", "causative_missing_sa", "担当させていただく", False),
    ("tantou_itadai_typo", "担当させていただいこと", "business_term", "medium", "itadaku_typo", "担当させていただくこと", False),
    ("chin_name_typo", "珍習名", "name_honorific", "high", "name_stt_typo", "チン・シュウメイ", True),
    ("kounin_mono", "公認のもの", "business_term", "medium", "kounin_handover_typo", "後任の者", True),
    ("kounin_mono_alt", "後任のもの", "business_term", "medium", "kounin_mono_typo", "後任の者", False),
    ("midline_punct", "。、", "punctuation_artifact", "medium", "midline_punctuation_artifact", "。", False),
    ("minasan_konnichiwa", "皆さ、こんにちは", "name_honorific", "medium", "greeting_typo", "皆さん、こんにちは", False),
)

_REGEX_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str, str, str, bool], ...] = (
    (
        "tasha_vs_tasha",
        re.compile(r"他者"),
        "business_term",
        "low",
        "possible_tasha_vs_tasha",
        "他社 (context-dependent)",
        True,
    ),
    (
        "missing_punct_between_polite",
        re.compile(r"(です|ます)(いつも|こちらこそ|恐れ入ります)"),
        "sentence_boundary",
        "medium",
        "missing_punctuation_between_sentences",
        "add punctuation between clauses",
        False,
    ),
    (
        "leading_fragment_particle",
        re.compile(r"^(は|が|の|て|から|に)"),
        "sentence_boundary",
        "medium",
        "leading_fragment_particle",
        "merge with previous line",
        False,
    ),
    (
        "company_number_risk",
        re.compile(r"\d{1,4}年|\d{1,2}月|\d{1,2}日|\d+時間|\d+分"),
        "meaning_risk",
        "low",
        "number_or_date_segment",
        "verify against audio",
        True,
    ),
    (
        "department_risk",
        re.compile(r"(営業部|開発部|人事部|総務部)"),
        "meaning_risk",
        "low",
        "department_name",
        "verify against glossary",
        True,
    ),
)

_HANDOVER_CUES = ("御社の担当が変わりました", "担当交代", "後任", "前任者", "ご挨拶に参りました")


def _strip_speaker_prefix(line: str) -> tuple[int, str]:
    m = _SPEAKER_LINE.match(line.strip())
    if not m:
        return 0, line.strip()
    return int(m.group(1)), m.group(2).strip()


def _candidate(
    *,
    line_no: int,
    speaker: int,
    pattern_id: str,
    category: str,
    severity: str,
    suspected_issue: str,
    likely_expected: str,
    requires_glossary: bool,
    snippet: str,
    line: str,
) -> dict[str, Any]:
    return {
        "pattern_id": pattern_id,
        "category": category,
        "severity": severity,
        "line_number": line_no,
        "line_no": line_no,
        "speaker": speaker,
        "line_text": snippet,
        "snippet": snippet,
        "line": line[:240],
        "suspected_issue": suspected_issue,
        "likely_expected_text": likely_expected,
        "auto_corrected": False,
        "correction_allowed": False,
        "requires_reference_or_glossary": requires_glossary,
        "reason": "audit_only_no_auto_correction",
    }


def _log_candidate(pattern_id: str, line_no: int, snippet: str) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "VISIBLE_ERROR_CANDIDATE_FOUND",
            pattern_id=pattern_id,
            line_no=line_no,
            snippet=snippet[:80],
        )
    except Exception:
        pass


def _summarize(candidates: list[dict[str, Any]]) -> dict[str, int]:
    high = sum(1 for c in candidates if c.get("severity") == "high")
    medium = sum(1 for c in candidates if c.get("severity") == "medium")
    low = sum(1 for c in candidates if c.get("severity") == "low")
    name_risk = sum(1 for c in candidates if c.get("category") == "name_honorific")
    business_risk = sum(1 for c in candidates if c.get("category") == "business_term")
    punct = sum(1 for c in candidates if c.get("category") == "punctuation_artifact")
    boundary = sum(1 for c in candidates if c.get("category") == "sentence_boundary")
    return {
        "visible_error_count": len(candidates),
        "visible_error_high_count": high,
        "visible_error_medium_count": medium,
        "visible_error_low_count": low,
        "name_risk_count": name_risk,
        "business_term_risk_count": business_risk,
        "punctuation_artifact_count": punct,
        "sentence_boundary_risk_count": boundary,
    }


def audit_visible_errors(
    alpha_text: str,
    *,
    run_id: str = "",
    handover_context_lines: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Scan Alpha output for visible error candidates — audit only."""
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("VISIBLE_ERROR_AUDIT_STARTED", run_id=run_id, expanded=VISIBLE_ERROR_AUDIT_EXPANDED)
        jp_accuracy_log("VISIBLE_ERROR_AUDIT_ONLY_NO_AUTO_CORRECTION")
    except Exception:
        pass

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    raw_lines = [ln for ln in alpha_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    handover_context = "\n".join(handover_context_lines or raw_lines)
    full_text = "\n".join(raw_lines)
    has_chin = "チン" in full_text or "ちん" in full_text

    def _add(item: dict[str, Any]) -> None:
        key = (int(item.get("line_no", 0)), str(item.get("pattern_id", "")))
        if key in seen:
            return
        seen.add(key)
        candidates.append(item)
        _log_candidate(str(item.get("pattern_id", "")), int(item.get("line_no", 0)), str(item.get("snippet", "")))

    for line_no, line in enumerate(raw_lines, start=1):
        speaker, body = _strip_speaker_prefix(line)
        if body.startswith("、") or body.startswith("。"):
            _add(
                _candidate(
                    line_no=line_no,
                    speaker=speaker,
                    pattern_id="punctuation_start",
                    category="punctuation_artifact",
                    severity="high",
                    suspected_issue="leading_punctuation_after_speaker",
                    likely_expected="remove leading punctuation",
                    requires_glossary=False,
                    snippet=body[:120],
                    line=line,
                )
            )

        if VISIBLE_ERROR_AUDIT_EXPANDED:
            if "ちーさん" in body and has_chin:
                _add(
                    _candidate(
                        line_no=line_no,
                        speaker=speaker,
                        pattern_id="chii_san_typo",
                        category="name_honorific",
                        severity="medium",
                        suspected_issue="chii_vs_chin",
                        likely_expected="チンさん",
                        requires_glossary=True,
                        snippet=body[:120],
                        line=line,
                    )
                )
            if "シンさん" in body and has_chin and "チンさん" in full_text:
                _add(
                    _candidate(
                        line_no=line_no,
                        speaker=speaker,
                        pattern_id="shin_vs_chin",
                        category="name_honorific",
                        severity="medium",
                        suspected_issue="shin_vs_chin",
                        likely_expected="チンさん",
                        requires_glossary=True,
                        snippet=body[:120],
                        line=line,
                    )
                )

        for pattern_id, needle, category, severity, issue, expected, glossary in _STRING_PATTERNS:
            if needle not in body:
                continue
            if pattern_id in ("kounin_mono", "kounin_mono_alt"):
                if not any(cue in handover_context for cue in _HANDOVER_CUES):
                    continue
            _add(
                _candidate(
                    line_no=line_no,
                    speaker=speaker,
                    pattern_id=pattern_id,
                    category=category,
                    severity=severity,
                    suspected_issue=issue,
                    likely_expected=expected,
                    requires_glossary=glossary,
                    snippet=body[:120],
                    line=line,
                )
            )

        if VISIBLE_ERROR_AUDIT_EXPANDED:
            for pattern_id, regex, category, severity, issue, expected, glossary in _REGEX_PATTERNS:
                if not regex.search(body):
                    continue
                _add(
                    _candidate(
                        line_no=line_no,
                        speaker=speaker,
                        pattern_id=pattern_id,
                        category=category,
                        severity=severity,
                        suspected_issue=issue,
                        likely_expected=expected,
                        requires_glossary=glossary,
                        snippet=body[:120],
                        line=line,
                    )
                )

            if "申し訳ございません" in body and "あいにく" in body and "。" not in body[: body.find("あいにく") + 6]:
                _add(
                    _candidate(
                        line_no=line_no,
                        speaker=speaker,
                        pattern_id="moushiwake_ai_niku_punct",
                        category="punctuation_artifact",
                        severity="low",
                        suspected_issue="missing_punctuation_after_apology",
                        likely_expected="申し訳ございません。あいにく",
                        requires_glossary=False,
                        snippet=body[:120],
                        line=line,
                    )
                )

        if line_no > 1:
            prev_speaker, prev_body = _strip_speaker_prefix(raw_lines[line_no - 2])
            if speaker == prev_speaker and prev_body and body.startswith(prev_body[: min(20, len(prev_body))]):
                if prev_body != body:
                    _add(
                        _candidate(
                            line_no=line_no,
                            speaker=speaker,
                            pattern_id="duplicate_line_continuation",
                            category="sentence_boundary",
                            severity="medium",
                            suspected_issue="repeated_prefix_continuation",
                            likely_expected="merge or dedupe continuation",
                            requires_glossary=False,
                            snippet=body[:120],
                            line=line,
                        )
                    )

    summary = _summarize(candidates)
    result: dict[str, Any] = {
        "app_version": APP_VERSION,
        "run_id": run_id,
        "visible_error_audit_expanded": VISIBLE_ERROR_AUDIT_EXPANDED,
        "anti_overfit_mode_enabled": ANTI_OVERFIT_MODE_ENABLED,
        "benchmark_baseline_lock_enabled": BENCHMARK_BASELINE_LOCK_ENABLED,
        "auto_business_correction_level": AUTO_BUSINESS_CORRECTION_LEVEL,
        "audit_only": True,
        "auto_correction_applied": False,
        "line_count": len(raw_lines),
        "candidates": candidates,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **summary,
    }
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("VISIBLE_ERROR_AUDIT_SUMMARY_WRITTEN", **summary)
    except Exception:
        pass
    return result


def write_visible_error_audit(
    alpha_text: str,
    *,
    run_folder: Optional[Path] = None,
    run_id: str = "",
) -> Optional[Path]:
    """Write per-run visible error audit artifacts."""
    if not alpha_text.strip():
        return None
    audit = audit_visible_errors(alpha_text, run_id=run_id)
    if run_folder is None:
        return None
    accuracy_dir = run_folder / "accuracy"
    accuracy_dir.mkdir(parents=True, exist_ok=True)
    json_path = accuracy_dir / "visible_error_audit.json"
    txt_path = accuracy_dir / "visible_error_audit.txt"
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    txt_lines = [
        "# Visible Error Audit (audit-only — no auto-correction)",
        f"# app_version: {APP_VERSION}",
        f"# run_id: {run_id or 'unknown'}",
        f"# visible_error_count: {audit.get('visible_error_count', 0)}",
        f"# high: {audit.get('visible_error_high_count', 0)} medium: {audit.get('visible_error_medium_count', 0)} low: {audit.get('visible_error_low_count', 0)}",
        "",
    ]
    for item in audit.get("candidates", []):
        txt_lines.append(
            f"line={item.get('line_no')} severity={item.get('severity')} "
            f"pattern={item.get('pattern_id')} category={item.get('category')} "
            f"snippet={item.get('snippet', '')}"
        )
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(
            "VISIBLE_ERROR_AUDIT_WRITTEN",
            path=str(json_path),
            count=audit.get("visible_error_count", 0),
        )
    except Exception:
        pass
    return json_path


__all__ = ["audit_visible_errors", "write_visible_error_audit"]
