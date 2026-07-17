"""Final-output residual duplicate sweep and punctuation cleanup (8.5.24.2)."""

from __future__ import annotations

import re
from typing import Any

from alpha.constants import (
    PUNCTUATION_ARTIFACT_CLEANUP_ENABLED,
    RESIDUAL_DUPLICATE_CLEANUP_ENABLED,
)
from alpha.transcription.stable_line_revision import prefix_overlap_ratio, _strip_speaker
from alpha.utils.cjk_text import compact_cjk_for_compare

_PUNCT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"。、+"), "。"),
    (re.compile(r"、。+"), "。"),
    (re.compile(r"。{2,}"), "。"),
    (re.compile(r"、{2,}"), "、"),
    (re.compile(r"。\s+。"), "。"),
    (re.compile(r"、\s+、"), "、"),
]


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def cleanup_punctuation_artifacts(text: str) -> tuple[str, bool]:
    if not PUNCTUATION_ARTIFACT_CLEANUP_ENABLED:
        return text, False
    out = text or ""
    changed = False
    for pattern, repl in _PUNCT_PATTERNS:
        new = pattern.sub(repl, out)
        if new != out:
            changed = True
            out = new
    out = re.sub(r"([。！？])([、])", r"\1", out)
    out = re.sub(r"([、])([。！？])", r"\2", out)
    if out != text:
        changed = True
    if changed:
        _jp_log("PUNCTUATION_ARTIFACT_CLEANED", before=(text or "")[:80], after=out[:80])
    return out, changed


def count_punctuation_artifacts(lines: list[str]) -> int:
    count = 0
    for ln in lines:
        body = _strip_speaker(ln)
        if re.search(r"。、|、。|\.\.|、、", body):
            count += 1
    return count


def _jp_char_len(text: str) -> int:
    try:
        from alpha.transcription.japanese_stable_accuracy import count_japanese_chars

        return count_japanese_chars(text)
    except Exception:
        return len(compact_cjk_for_compare(text, "ja"))


def _is_independent_sentence(prev: str, cur: str) -> bool:
    """Avoid merging when current is clearly a new complete sentence."""
    cur_body = _strip_speaker(cur)
    if _jp_char_len(cur_body) >= 40 and is_clear_sentence(cur_body):
        prev_c = compact_cjk_for_compare(_strip_speaker(prev), "ja")
        cur_c = compact_cjk_for_compare(cur_body, "ja")
        if not cur_c.startswith(prev_c) and prefix_overlap_ratio(prev, cur) < 0.5:
            return True
    return False


def is_clear_sentence(text: str) -> bool:
    try:
        from alpha.transcription.japanese_stable_accuracy import is_clear_sentence as _ics

        return _ics(text)
    except Exception:
        return text.rstrip().endswith(("。", "？", "！"))


def sweep_residual_duplicates(lines: list[str]) -> tuple[list[str], dict[str, Any]]:
    metrics: dict[str, Any] = {
        "residual_duplicate_before_count": 0,
        "residual_duplicate_after_count": 0,
        "residual_duplicate_suppressed_count": 0,
        "residual_duplicate_revised_count": 0,
        "residual_duplicate_chain_count": 0,
        "punctuation_artifact_before_count": 0,
        "punctuation_artifact_after_count": 0,
        "punctuation_artifact_cleaned_count": 0,
    }
    if not lines:
        return [], metrics

    _jp_log("RESIDUAL_DUPLICATE_SWEEP_STARTED", input_lines=len(lines))
    _jp_log("PUNCTUATION_ARTIFACT_SWEEP_STARTED", input_lines=len(lines))
    metrics["punctuation_artifact_before_count"] = count_punctuation_artifacts(lines)

    cleaned_input: list[str] = []
    for ln in lines:
        body = ln.strip()
        if not body:
            continue
        sp = re.match(r"^(\[Speaker\s+\d+\]\s*)", body)
        prefix = sp.group(1) if sp else ""
        text_body = body[len(prefix) :] if prefix else body
        cleaned, punct_changed = cleanup_punctuation_artifacts(text_body)
        if punct_changed:
            metrics["punctuation_artifact_cleaned_count"] += 1
            _jp_log("PUNCTUATION_ARTIFACT_DETECTED", text_preview=cleaned[:80])
        cleaned_input.append(f"{prefix}{cleaned}" if prefix else cleaned)

    if not RESIDUAL_DUPLICATE_CLEANUP_ENABLED:
        metrics["punctuation_artifact_after_count"] = count_punctuation_artifacts(cleaned_input)
        _jp_log("RESIDUAL_DUPLICATE_SWEEP_COMPLETED", output_lines=len(cleaned_input))
        return cleaned_input, metrics

    out: list[str] = []
    for cur in cleaned_input:
        if not out:
            out.append(cur)
            continue
        prev = out[-1]
        prev_body = _strip_speaker(prev)
        cur_body = _strip_speaker(cur)

        if _is_independent_sentence(prev, cur):
            out.append(cur)
            continue

        is_prefix = cur_body.startswith(prev_body) and _jp_char_len(prev_body) >= 12 and len(cur_body) > len(prev_body)
        overlap = prefix_overlap_ratio(prev, cur) >= 0.7
        prev_in_cur = prev_body in cur_body and len(cur_body) - len(prev_body) >= 4

        if is_prefix or prev_in_cur or overlap:
            metrics["residual_duplicate_before_count"] += 1
            metrics["residual_duplicate_chain_count"] += 1
            _jp_log("RESIDUAL_DUPLICATE_CHAIN_DETECTED")
            if is_prefix or prev_in_cur:
                _jp_log("RESIDUAL_DUPLICATE_PREFIX_CONTAINMENT_DETECTED")
            if overlap:
                _jp_log("RESIDUAL_DUPLICATE_OVERLAP_DETECTED")
            if len(cur_body) >= len(prev_body):
                out[-1] = cur
                metrics["residual_duplicate_revised_count"] += 1
                _jp_log("RESIDUAL_DUPLICATE_LONGER_LINE_KEPT", kept_preview=cur_body[:80])
            else:
                metrics["residual_duplicate_suppressed_count"] += 1
                _jp_log("RESIDUAL_DUPLICATE_OLD_LINE_SUPPRESSED", suppressed_preview=cur_body[:80])
            continue

        out.append(cur)

    metrics["punctuation_artifact_after_count"] = count_punctuation_artifacts(out)
    for i in range(1, len(out)):
        prev = _strip_speaker(out[i - 1])
        cur = _strip_speaker(out[i])
        if cur.startswith(prev) and len(cur) > len(prev) and _jp_char_len(prev) >= 12:
            metrics["residual_duplicate_after_count"] += 1

    _jp_log(
        "RESIDUAL_DUPLICATE_SWEEP_COMPLETED",
        output_lines=len(out),
        suppressed=metrics["residual_duplicate_suppressed_count"],
    )
    _jp_log("PUNCTUATION_ARTIFACT_SWEEP_COMPLETED", after_count=metrics["punctuation_artifact_after_count"])
    return out, metrics


def detect_cumulative_alpha_lines_v2(lines: list[str]) -> dict[str, Any]:
    from alpha.constants import CUMULATIVE_ALPHA_REJECTION_ENABLED

    cumulative_count = 0
    prefix_chain = 0
    for i in range(1, len(lines)):
        prev = _strip_speaker(lines[i - 1])
        cur = _strip_speaker(lines[i])
        if not prev or not cur:
            continue
        if cur.startswith(prev) and len(cur) > len(prev) and _jp_char_len(prev) >= 12:
            cumulative_count += 1
            prefix_chain += 1
        elif prefix_overlap_ratio(lines[i - 1], lines[i]) >= 0.7:
            cumulative_count += 1
    suspected = cumulative_count > 0 if CUMULATIVE_ALPHA_REJECTION_ENABLED else cumulative_count >= 3
    punct_count = count_punctuation_artifacts(lines)
    return {
        "cumulative_duplicate_count": cumulative_count,
        "prefix_chain_count": prefix_chain,
        "alpha_output_cumulative_duplicate_suspected": suspected,
        "punctuation_artifact_count": punct_count,
        "alpha_output_punctuation_artifact_suspected": punct_count > 0,
    }
