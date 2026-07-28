"""Paragraph/sliding-window alignment V2 for short Alpha lines vs long reference (8.5.23.4.1)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_JP_CHAR_RE = re.compile(r"[一-龯ぁ-んァ-ヶー]")
_WINDOW_SIZES = (3, 5, 8, 12)
_CHUNK_SIZE = 320
_CHUNK_MIN = 250
_CHUNK_MAX = 400


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def chars_only_japanese(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if _JP_CHAR_RE.match(ch))


def window_overlap_score(a: str, b: str) -> float:
    a = chars_only_japanese(a)
    b = chars_only_japanese(b)
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    set_score = len(sa & sb) / max(len(sa | sb), 1)
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if not shorter:
        return 0.0
    best_seq = 0.0
    step = max(1, len(shorter) // 8)
    for i in range(0, max(1, len(longer) - len(shorter) + 1), step):
        chunk = longer[i : i + len(shorter)]
        matches = sum(1 for x, y in zip(shorter, chunk) if x == y)
        best_seq = max(best_seq, matches / len(shorter))
    return round((set_score + best_seq) / 2, 4)


def build_alpha_windows(alpha_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _jp_log("ALPHA_SLIDING_WINDOW_STARTED")
    windows: list[dict[str, Any]] = []
    n = len(alpha_rows)
    for size in _WINDOW_SIZES:
        for start in range(n):
            end = min(start + size, n)
            text = "".join(r["normalized"] for r in alpha_rows[start:end])
            jp = chars_only_japanese(text)
            if len(jp) < 12:
                continue
            windows.append(
                {
                    "window_id": f"w{size}_{start}",
                    "size": size,
                    "alpha_start_idx": start,
                    "alpha_end_idx": end - 1,
                    "line_start": alpha_rows[start]["line_number"],
                    "line_end": alpha_rows[end - 1]["line_number"],
                    "text": jp,
                }
            )
    _jp_log("ALPHA_SLIDING_WINDOW_COMPLETED", count=len(windows))
    return windows


def chunk_reference_paragraphs(ref_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    _jp_log("REFERENCE_PARAGRAPH_CHUNKING_STARTED")
    full = "".join(r["normalized"] for r in ref_rows)
    jp = chars_only_japanese(full)
    paragraph_detected = any(len(chars_only_japanese(r["normalized"])) > _CHUNK_MIN for r in ref_rows)
    chunks: list[dict[str, Any]] = []
    if not jp:
        _jp_log("REFERENCE_PARAGRAPH_CHUNKING_COMPLETED", chunks=0)
        return chunks, paragraph_detected
  # Prefer splitting long reference rows first
    pos = 0
    chunk_idx = 0
    for row in ref_rows:
        row_jp = chars_only_japanese(row["normalized"])
        if len(row_jp) <= _CHUNK_MAX:
            if len(row_jp) >= 20:
                chunks.append(
                    {
                        "chunk_id": f"chunk_{chunk_idx}",
                        "char_start": pos,
                        "char_end": pos + len(row_jp),
                        "text": row_jp,
                        "source_line": row["line_number"],
                    }
                )
                pos += len(row_jp)
                chunk_idx += 1
            continue
        for i in range(0, len(row_jp), _CHUNK_SIZE):
            piece = row_jp[i : i + _CHUNK_SIZE]
            if len(piece) < 20:
                continue
            chunks.append(
                {
                    "chunk_id": f"chunk_{chunk_idx}",
                    "char_start": pos,
                    "char_end": pos + len(piece),
                    "text": piece,
                    "source_line": row["line_number"],
                }
            )
            pos += len(piece)
            chunk_idx += 1
    if not chunks and jp:
        for i in range(0, len(jp), _CHUNK_SIZE):
            piece = jp[i : i + _CHUNK_SIZE]
            if len(piece) >= 20:
                chunks.append(
                    {
                        "chunk_id": f"chunk_{chunk_idx}",
                        "char_start": i,
                        "char_end": i + len(piece),
                        "text": piece,
                    }
                )
                chunk_idx += 1
    _jp_log("REFERENCE_PARAGRAPH_CHUNKING_COMPLETED", chunks=len(chunks))
    return chunks, paragraph_detected


def run_alignment_v2(
    alpha_rows: list[dict[str, Any]],
    ref_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    _jp_log("ALIGNMENT_V2_STARTED")
    _jp_log("MONOTONIC_WINDOW_ALIGNMENT_STARTED")
    windows = build_alpha_windows(alpha_rows)
    ref_chunks, paragraph_detected = chunk_reference_paragraphs(ref_rows)

    total_alpha_chars = sum(len(chars_only_japanese(r["normalized"])) for r in alpha_rows)
    total_ref_chars = sum(len(c["text"]) for c in ref_chunks)
    avg_line_len = total_alpha_chars / max(len(alpha_rows), 1)
    short_fragmentation = avg_line_len < 45 and len(alpha_rows) > max(len(ref_rows) * 3, 10)

    matches: list[dict[str, Any]] = []
    last_alpha_end_idx = -1
    order_violations = 0
    overlap_scores: list[float] = []

    for chunk in ref_chunks:
        best: dict[str, Any] | None = None
        best_score = 0.0
        for w in windows:
            if w["alpha_start_idx"] <= last_alpha_end_idx:
                continue
            score = window_overlap_score(w["text"], chunk["text"])
            if score > best_score:
                best_score = score
                best = w
        if best is not None and best_score >= 0.12:
            if best["alpha_start_idx"] < last_alpha_end_idx:
                order_violations += 1
            matches.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "window_id": best["window_id"],
                    "overlap_score": best_score,
                    "alpha_line_start": best["line_start"],
                    "alpha_line_end": best["line_end"],
                    "alpha_window_size": best["size"],
                    "reference_excerpt": chunk["text"][:120],
                    "alpha_excerpt": best["text"][:120],
                }
            )
            overlap_scores.append(best_score)
            last_alpha_end_idx = best["alpha_end_idx"]

    aligned_line_numbers: set[int] = set()
    for m in matches:
        for row in alpha_rows:
            if m["alpha_line_start"] <= row["line_number"] <= m["alpha_line_end"]:
                aligned_line_numbers.add(row["line_number"])

    aligned_alpha_chars = sum(
        len(chars_only_japanese(r["normalized"]))
        for r in alpha_rows
        if r["line_number"] in aligned_line_numbers
    )
    unaligned_alpha_chars = max(0, total_alpha_chars - aligned_alpha_chars)
    matched_chunk_ids = {m["chunk_id"] for m in matches}
    aligned_ref_chars = sum(len(c["text"]) for c in ref_chunks if c["chunk_id"] in matched_chunk_ids)
    unaligned_ref_chars = max(0, total_ref_chars - aligned_ref_chars)

    aligned_alpha_lines = len(aligned_line_numbers)
    total_alpha_lines = len(alpha_rows)
    unaligned_alpha_lines = max(0, total_alpha_lines - aligned_alpha_lines)

    unaligned_alpha_char_ratio = round(unaligned_alpha_chars / max(total_alpha_chars, 1), 4)
    unaligned_ref_char_ratio = round(unaligned_ref_chars / max(total_ref_chars, 1), 4)
    unaligned_alpha_line_ratio = round(unaligned_alpha_lines / max(total_alpha_lines, 1), 4)

    avg_overlap = round(sum(overlap_scores) / max(len(overlap_scores), 1), 4) if overlap_scores else 0.0
    min_overlap = round(min(overlap_scores), 4) if overlap_scores else 0.0
    max_overlap = round(max(overlap_scores), 4) if overlap_scores else 0.0

    line_mismatch = len(alpha_rows) > max(len(ref_rows) * 3, 10)
    line_mismatch_tolerated = (
        line_mismatch
        and unaligned_alpha_char_ratio <= 0.25
        and unaligned_ref_char_ratio <= 0.25
        and avg_overlap >= 0.50
    )
    if line_mismatch_tolerated:
        _jp_log("LINE_COUNT_MISMATCH_TOLERATED_BY_CHAR_COVERAGE")

    integrity, coverage, recommendation = _verdict_v2(
        unaligned_alpha_char_ratio=unaligned_alpha_char_ratio,
        unaligned_ref_char_ratio=unaligned_ref_char_ratio,
        avg_overlap=avg_overlap,
        order_violations=order_violations,
        matched_window_count=len(matches),
    )

    if integrity == "strong":
        _jp_log("ALIGNMENT_V2_VERDICT_STRONG")
    elif integrity == "acceptable":
        _jp_log("ALIGNMENT_V2_VERDICT_ACCEPTABLE")
    elif integrity == "weak":
        _jp_log("ALIGNMENT_V2_VERDICT_WEAK")
    else:
        _jp_log("ALIGNMENT_V2_VERDICT_INVALID")

    _jp_log("ALIGNMENT_V2_COVERAGE_CALCULATED")
    _jp_log("MONOTONIC_WINDOW_ALIGNMENT_COMPLETED", matches=len(matches))

    return {
        "alignment_algorithm_version": "v2_paragraph_sliding_window",
        "matched_windows": matches,
        "matched_window_count": len(matches),
        "total_alpha_chars": total_alpha_chars,
        "aligned_alpha_chars": aligned_alpha_chars,
        "unaligned_alpha_chars": unaligned_alpha_chars,
        "unaligned_alpha_char_ratio": unaligned_alpha_char_ratio,
        "total_reference_chars": total_ref_chars,
        "aligned_reference_chars": aligned_ref_chars,
        "unaligned_reference_chars": unaligned_ref_chars,
        "unaligned_reference_char_ratio": unaligned_ref_char_ratio,
        "total_alpha_lines": total_alpha_lines,
        "aligned_alpha_lines": aligned_alpha_lines,
        "unaligned_alpha_lines": unaligned_alpha_lines,
        "unaligned_alpha_line_ratio": unaligned_alpha_line_ratio,
        "average_window_overlap_score": avg_overlap,
        "min_window_overlap_score": min_overlap,
        "max_window_overlap_score": max_overlap,
        "alignment_order_violations": order_violations,
        "line_count_mismatch_detected": line_mismatch,
        "line_count_mismatch_tolerated": line_mismatch_tolerated,
        "paragraph_reference_detected": paragraph_detected,
        "short_alpha_line_fragmentation_detected": short_fragmentation,
        "alignment_integrity_verdict_v2": integrity,
        "alignment_coverage_verdict_v2": coverage,
        "recommendation": recommendation,
    }


def _verdict_v2(
    *,
    unaligned_alpha_char_ratio: float,
    unaligned_ref_char_ratio: float,
    avg_overlap: float,
    order_violations: int,
    matched_window_count: int,
) -> tuple[str, str, str]:
    if matched_window_count < 2 or avg_overlap < 0.20:
        return "invalid", "failed", "Do not use CER for product decisions."
    if order_violations > 5:
        return "weak", "failed", "Alignment order violations too high."
    if unaligned_alpha_char_ratio > 0.25 or unaligned_ref_char_ratio > 0.25:
        return "weak", "failed", "Character coverage insufficient for trusted CER."
    if avg_overlap < 0.50:
        return "weak", "failed", "Window overlap too low for trusted CER."
    if unaligned_alpha_char_ratio <= 0.10 and unaligned_ref_char_ratio <= 0.10 and avg_overlap >= 0.70:
        return "strong", "passed", "Alignment V2 coverage acceptable for benchmark scoring."
    return "acceptable", "passed", "Alignment V2 usable with caution; verify coverage."
