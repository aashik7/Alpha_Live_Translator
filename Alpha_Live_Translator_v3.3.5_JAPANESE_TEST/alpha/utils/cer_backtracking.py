"""Levenshtein CER scorer with substitution/deletion/insertion backtracking."""

from __future__ import annotations

from typing import Any


def levenshtein_operation_counts(reference: str, hypothesis: str) -> dict[str, Any]:
    """Return edit distance and S/D/I counts via DP backtracking."""
    ref = reference or ""
    hyp = hypothesis or ""
    n = len(ref)
    m = len(hyp)

    if n == 0 and m == 0:
        return _result(ref, hyp, 0, 0, 0, 0)
    if n == 0:
        return _result(ref, hyp, m, 0, 0, m)
    if m == 0:
        return _result(ref, hyp, n, 0, n, 0)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        ca = ref[i - 1]
        for j in range(1, m + 1):
            cb = hyp[j - 1]
            if ca == cb:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(
                    dp[i - 1][j] + 1,
                    dp[i][j - 1] + 1,
                    dp[i - 1][j - 1] + 1,
                )

    substitutions = deletions = insertions = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1
            j -= 1
            continue
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
            continue
        if j > 0 and (i == 0 or dp[i][j] == dp[i][j - 1] + 1):
            insertions += 1
            j -= 1
            continue
        if i > 0 and (j == 0 or dp[i][j] == dp[i - 1][j] + 1):
            deletions += 1
            i -= 1
            continue
        if i > 0:
            deletions += 1
            i -= 1
        elif j > 0:
            insertions += 1
            j -= 1

    distance = dp[n][m]
    return _result(ref, hyp, distance, substitutions, deletions, insertions)


def _result(
    ref: str,
    hyp: str,
    distance: int,
    substitutions: int,
    deletions: int,
    insertions: int,
) -> dict[str, Any]:
    if distance != substitutions + deletions + insertions:
        raise ValueError(
            f"edit_distance mismatch: {distance} != {substitutions}+{deletions}+{insertions}"
        )
    ref_len = max(len(ref), 1)
    cer = round(distance / ref_len, 6)
    accuracy = round(max(0.0, 1.0 - cer) * 100.0, 2)
    if cer > 0 and substitutions == 0 and deletions == 0 and insertions == 0:
        raise ValueError("nonzero CER with zero operation counts")
    expected_cer = round(distance / ref_len, 6)
    if abs(cer - expected_cer) > 1e-9 and ref_len > 0:
        raise ValueError(f"CER arithmetic mismatch: {cer} vs {expected_cer}")
    return {
        "reference_character_count": len(ref),
        "hypothesis_character_count": len(hyp),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "edit_distance": distance,
        "cer": cer,
        "accuracy_percent": accuracy,
    }


def stage_metrics_from_normalized(alpha_norm: str, ref_norm: str) -> dict[str, Any]:
    if not alpha_norm or not ref_norm:
        return {
            "raw_character_count": len(alpha_norm),
            "reference_character_count": len(ref_norm),
            "hypothesis_character_count": len(alpha_norm),
            "substitution_count": 0,
            "deletion_count": 0,
            "insertion_count": 0,
            "edit_distance": 0,
            "cer": None,
            "accuracy_percent": None,
        }
    counts = levenshtein_operation_counts(ref_norm, alpha_norm)
    return {
        "raw_character_count": counts["hypothesis_character_count"],
        "reference_character_count": counts["reference_character_count"],
        "hypothesis_character_count": counts["hypothesis_character_count"],
        "substitution_count": counts["substitutions"],
        "deletion_count": counts["deletions"],
        "insertion_count": counts["insertions"],
        "edit_distance": counts["edit_distance"],
        "cer": counts["cer"],
        "accuracy_percent": counts["accuracy_percent"],
    }
