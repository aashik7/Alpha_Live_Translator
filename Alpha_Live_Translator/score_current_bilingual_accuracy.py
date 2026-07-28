#!/usr/bin/env python3
"""Current bilingual (JA CER / EN WER) live accuracy scorer — measurement only.

Scores Raw / Stable / Final from the same live run against external references.
Does not mutate Alpha runtime behavior. Does not promote offline repair as live accuracy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha.utils.cer_backtracking import levenshtein_operation_counts
from alpha.utils.multidomain_gate_evidence import normalize_transcript_text

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "troubleshooting" / "accuracy_benchmark" / "current_bilingual_accuracy"

STAGE_NAMES = {
    "raw": "raw_deepgram.txt",
    "stable": "stable_transcript.txt",
    "final": "final_alpha_output.txt",
}
PROVIDER_REQUEST_CANDIDATES = (
    "deepgram_request_actual.json",
    "actual_sanitized_deepgram_request.json",
)
CATEGORY_KEYS = [
    "participant_names",
    "company_names",
    "departments_and_job_titles",
    "dates",
    "times",
    "percentages",
    "integers_and_decimals",
    "money_and_currency",
    "ticket_numbers",
    "software_versions",
    "english_acronyms",
    "it_and_security_terminology",
    "sales_terminology",
    "marketing_terminology",
    "general_business_terminology",
    "negation_and_conditional_statements",
    "corrections_and_changed_information",
    "action_items",
    "owners",
    "deadlines",
]

_SPEAKER_RE = re.compile(r"^\[Speaker\s+\d+\]\s*", re.I)
_TS_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z]+)?|[^\sA-Za-z0-9]+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stage_dir(run_folder: Path) -> Path:
    asc = run_folder / "accuracy_stage_compare"
    return asc if asc.is_dir() else run_folder


def _resolve_stage_file(stage_dir: Path, name: str) -> Path | None:
    p = stage_dir / name
    return p if p.is_file() else None


def _resolve_provider_request(stage_dir: Path) -> Path | None:
    for name in PROVIDER_REQUEST_CANDIDATES:
        p = stage_dir / name
        if p.is_file():
            return p
    return None


def normalize_ja_strict(text: str) -> str:
    """Strict JA CER normalizer: Unicode NFKC + existing transcript strip."""
    return normalize_transcript_text(unicodedata.normalize("NFKC", text or ""))


def normalize_ja_punct_insensitive(text: str) -> str:
    base = normalize_ja_strict(text)
    return _PUNCT_RE.sub("", base)


def normalize_en_strict_text(text: str) -> str:
    """Strict EN text: casefold, apostrophe normalize, drop speaker/ts labels, whitespace."""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = _SPEAKER_RE.sub("", line)
        line = _TS_RE.sub("", line)
        lines.append(line)
    body = " ".join(lines)
    body = unicodedata.normalize("NFKC", body)
    body = body.replace("\u2019", "'").replace("\u2018", "'")
    body = re.sub(r"\s+", " ", body).strip().casefold()
    return body


def tokenize_en_words(text: str) -> list[str]:
    norm = normalize_en_strict_text(text)
    # Keep alphanumeric tokens (incl. contractions); drop pure punctuation tokens.
    return [t for t in _WORD_RE.findall(norm) if re.search(r"[A-Za-z0-9]", t)]


def normalize_en_punct_insensitive_words(text: str) -> list[str]:
    norm = normalize_en_strict_text(text)
    norm = _PUNCT_RE.sub(" ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return [t for t in norm.split(" ") if t]


def _levenshtein_tokens(ref: list[str], hyp: list[str]) -> dict[str, Any]:
    n, m = len(ref), len(hyp)
    if n == 0 and m == 0:
        return {
            "reference_word_count": 0,
            "hypothesis_word_count": 0,
            "substitutions": 0,
            "deletions": 0,
            "insertions": 0,
            "edit_distance": 0,
            "wer": 0.0,
            "wer_percent": 0.0,
            "word_accuracy_percent": 100.0,
        }
    if n == 0:
        return {
            "reference_word_count": 0,
            "hypothesis_word_count": m,
            "substitutions": 0,
            "deletions": 0,
            "insertions": m,
            "edit_distance": m,
            "wer": 1.0 if m else 0.0,
            "wer_percent": 100.0 if m else 0.0,
            "word_accuracy_percent": 0.0 if m else 100.0,
        }
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + 1)
    subs = dels = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1
            j -= 1
            continue
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            subs += 1
            i -= 1
            j -= 1
            continue
        if j > 0 and (i == 0 or dp[i][j] == dp[i][j - 1] + 1):
            ins += 1
            j -= 1
            continue
        if i > 0 and (j == 0 or dp[i][j] == dp[i - 1][j] + 1):
            dels += 1
            i -= 1
            continue
        if i > 0:
            dels += 1
            i -= 1
        elif j > 0:
            ins += 1
            j -= 1
    distance = dp[n][m]
    wer = distance / max(n, 1)
    wer_pct = wer * 100.0
    return {
        "reference_word_count": n,
        "hypothesis_word_count": m,
        "substitutions": subs,
        "deletions": dels,
        "insertions": ins,
        "edit_distance": distance,
        "wer": wer,
        "wer_percent": wer_pct,
        "word_accuracy_percent": max(0.0, 100.0 - wer_pct),
    }


def score_cer(ref_text: str, hyp_text: str, *, punct_insensitive: bool = False) -> dict[str, Any]:
    if punct_insensitive:
        ref_n = normalize_ja_punct_insensitive(ref_text)
        hyp_n = normalize_ja_punct_insensitive(hyp_text)
    else:
        ref_n = normalize_ja_strict(ref_text)
        hyp_n = normalize_ja_strict(hyp_text)
    counts = levenshtein_operation_counts(ref_n, hyp_n)
    ref_len = max(int(counts["reference_character_count"]), 1)
    distance = int(counts["edit_distance"])
    cer_pct = (distance / ref_len) * 100.0
    return {
        "reference_character_count": int(counts["reference_character_count"]),
        "hypothesis_character_count": int(counts["hypothesis_character_count"]),
        "substitutions": int(counts["substitutions"]),
        "deletions": int(counts["deletions"]),
        "insertions": int(counts["insertions"]),
        "edit_distance": distance,
        "strict_cer_percent": cer_pct,
        "normalized_cer_percent": cer_pct if punct_insensitive else None,
        "character_accuracy_percent": max(0.0, 100.0 - cer_pct),
        "normalized_text_ref_len": len(ref_n),
        "normalized_text_hyp_len": len(hyp_n),
    }


def score_en_stage(ref_text: str, hyp_text: str) -> dict[str, Any]:
    ref_words = tokenize_en_words(ref_text)
    hyp_words = tokenize_en_words(hyp_text)
    wer = _levenshtein_tokens(ref_words, hyp_words)
    # Secondary CER on strict-normalized character stream (spaces kept)
    ref_c = normalize_en_strict_text(ref_text)
    hyp_c = normalize_en_strict_text(hyp_text)
    cer_counts = levenshtein_operation_counts(ref_c, hyp_c)
    ref_len = max(int(cer_counts["reference_character_count"]), 1)
    cer_pct = (int(cer_counts["edit_distance"]) / ref_len) * 100.0
    punct_wer = _levenshtein_tokens(
        normalize_en_punct_insensitive_words(ref_text),
        normalize_en_punct_insensitive_words(hyp_text),
    )
    return {
        **wer,
        "strict_wer_percent": wer["wer_percent"],
        "word_accuracy_percent": wer["word_accuracy_percent"],
        "strict_cer_percent": cer_pct,
        "character_accuracy_percent": max(0.0, 100.0 - cer_pct),
        "character_substitutions": int(cer_counts["substitutions"]),
        "character_deletions": int(cer_counts["deletions"]),
        "character_insertions": int(cer_counts["insertions"]),
        "punctuation_insensitive_wer_percent": punct_wer["wer_percent"],
        "punctuation_insensitive_word_accuracy_percent": punct_wer["word_accuracy_percent"],
        "official_primary_metric": "WER",
        "official_accuracy_percent": wer["word_accuracy_percent"],
    }


def score_ja_stage(ref_text: str, hyp_text: str) -> dict[str, Any]:
    strict = score_cer(ref_text, hyp_text, punct_insensitive=False)
    norm = score_cer(ref_text, hyp_text, punct_insensitive=True)
    return {
        **strict,
        "normalized_cer_percent": norm["strict_cer_percent"],
        "normalized_character_accuracy_percent": norm["character_accuracy_percent"],
        "official_primary_metric": "CER",
        "official_accuracy_percent": strict["character_accuracy_percent"],
    }


def _duplicate_phrase_count(text: str, *, min_len: int = 12) -> int:
    # Simple consecutive-line / consecutive-chunk duplicate detector
    chunks = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(chunks) < 2:
        body = re.sub(r"\s+", "", text or "")
        count = 0
        i = 0
        while i + min_len * 2 <= len(body):
            piece = body[i : i + min_len]
            if body.find(piece, i + min_len) == i + min_len:
                count += 1
                i += min_len
            else:
                i += 1
        return count
    dups = 0
    for a, b in zip(chunks, chunks[1:]):
        if a == b and len(a) >= min_len:
            dups += 1
    return dups


def _sentence_boundary_risk_count(text: str) -> int:
    # Heuristic: many lines without terminal punctuation / Japanese enders
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return 0
    enders = ("。", "！", "？", ".", "!", "?")
    risky = 0
    for ln in lines:
        if len(ln) >= 40 and not ln.endswith(enders):
            risky += 1
    return risky


def _missing_sentence_count(ref_text: str, hyp_text: str) -> int:
    def _sents(t: str) -> list[str]:
        parts = re.split(r"(?<=[。．.!?？！])\s*", t or "")
        return [p.strip() for p in parts if p and p.strip()]

    ref_s = _sents(ref_text)
    hyp_joined = re.sub(r"\s+", "", hyp_text or "")
    missing = 0
    for s in ref_s:
        key = re.sub(r"\s+", "", s)
        if len(key) >= 8 and key not in hyp_joined:
            missing += 1
    return missing


def assess_reference_quality(ref_text: str, *, language: str) -> dict[str, Any]:
    flags: list[str] = []
    lines = [ln.strip() for ln in (ref_text or "").splitlines() if ln.strip()]
    body = ref_text or ""
    if not body.strip():
        flags.append("empty_reference")
    if re.search(r"^#+\s+", body, re.M):
        flags.append("markdown_headings")
    if sum(1 for ln in lines if re.match(r"^[-*•]\s+", ln)) >= 3:
        flags.append("bullet_summary_style")
    summary_markers = ("summary", "overview", "要点", "まとめ", "要約", "概要")
    if sum(1 for ln in lines if any(m in ln.lower() for m in summary_markers)) >= 2:
        flags.append("summary_style")
    if language == "ja":
        # Expect substantial Japanese script
        ja_chars = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", body))
        if ja_chars < 50:
            flags.append("insufficient_japanese_script")
    else:
        words = tokenize_en_words(body)
        if len(words) < 30:
            flags.append("insufficient_english_words")
        # Paraphrase / instruction markers
        if re.search(r"\b(in other words|paraphrase|tl;dr)\b", body, re.I):
            flags.append("paraphrase_markers")
    valid = len(flags) == 0
    return {
        "reference_quality_valid": valid,
        "flags": flags,
        "line_count": len(lines),
        "char_count": len(body),
        "failure_code": None if valid else f"{language}_reference_mismatch",
    }


def score_categories(
    *,
    hyp_text: str,
    truth: dict[str, Any] | None,
) -> dict[str, Any]:
    if not truth:
        candidates = {k: [] for k in CATEGORY_KEYS}
        return {
            "category_score_trusted": False,
            "reason": "truth_metadata_absent",
            "categories": {
                k: {
                    "expected_count": 0,
                    "exact_match_count": 0,
                    "partial_match_count": 0,
                    "missing_count": 0,
                    "incorrect_substitution_count": 0,
                    "accuracy_percentage": None,
                    "affected_examples": [],
                    "status": "CANDIDATE_NEEDS_HUMAN_CONFIRMATION",
                }
                for k in CATEGORY_KEYS
            },
            "candidate_report": candidates,
        }

    hyp_norm = unicodedata.normalize("NFKC", hyp_text or "").casefold()
    hyp_compact = re.sub(r"\s+", "", hyp_norm)
    categories: dict[str, Any] = {}
    for key in CATEGORY_KEYS:
        terms = list(truth.get(key) or [])
        exact = partial = missing = incorrect = 0
        examples: list[dict[str, Any]] = []
        for term in terms:
            t = str(term).strip()
            if not t:
                continue
            t_norm = unicodedata.normalize("NFKC", t).casefold()
            t_compact = re.sub(r"\s+", "", t_norm)
            if t_norm in hyp_norm or t_compact in hyp_compact:
                exact += 1
                examples.append({"term": t, "status": "exact"})
            else:
                # partial: majority of contiguous chars present
                if len(t_compact) >= 4 and t_compact[: max(3, len(t_compact) // 2)] in hyp_compact:
                    partial += 1
                    examples.append({"term": t, "status": "partial"})
                else:
                    missing += 1
                    examples.append({"term": t, "status": "missing"})
        expected = exact + partial + missing
        accuracy = (exact / expected * 100.0) if expected else None
        categories[key] = {
            "expected_count": expected,
            "exact_match_count": exact,
            "partial_match_count": partial,
            "missing_count": missing,
            "incorrect_substitution_count": incorrect,
            "accuracy_percentage": accuracy,
            "affected_examples": examples[:50],
        }
    return {
        "category_score_trusted": True,
        "reason": "user_supplied_truth_json",
        "categories": categories,
    }


def _audio_delivery_gate(summary: dict[str, Any] | None) -> tuple[bool, list[str], dict[str, Any]]:
    codes: list[str] = []
    if not summary:
        return False, ["audio_delivery_incomplete"], {"present": False}
    ratio = summary.get("audio_delivery_ratio", summary.get("delivery_ratio"))
    missing = summary.get("missing_chunks", summary.get("missing_sent_chunk_ids", []))
    if isinstance(missing, list):
        missing_count = len(missing)
    else:
        missing_count = int(missing or 0)
    pending = int(summary.get("pending_at_close", summary.get("pending_chunk_count", 0)) or 0)
    if ratio is None or abs(float(ratio) - 1.0) > 1e-9:
        codes.append("audio_delivery_incomplete")
    if missing_count != 0:
        codes.append("audio_delivery_incomplete")
    if pending != 0:
        codes.append("audio_delivery_incomplete")
    evidence = {
        "present": True,
        "audio_delivery_ratio": ratio,
        "mixed_audio_chunks_created": summary.get("mixed_audio_chunks_created"),
        "chunks_queued": summary.get("chunks_queued", summary.get("queued_chunk_count")),
        "chunks_sent": summary.get("chunks_sent", summary.get("sent_chunk_count")),
        "chunks_acknowledged": summary.get("chunks_acknowledged"),
        "chunks_dropped": summary.get("chunks_dropped", summary.get("dropped_chunk_count")),
        "chunks_failed": summary.get("chunks_failed", summary.get("failed_chunk_count")),
        "missing_chunks": missing_count,
        "pending_at_close": pending,
        "actual_audio_duration": summary.get("actual_audio_duration", summary.get("audio_duration_sec")),
        "provider_connected_duration": summary.get(
            "provider_connected_duration", summary.get("provider_connected_duration_sec")
        ),
    }
    return len(codes) == 0, codes, evidence


def _find_authoritative_final(run_folder: Path, stage_final: Path) -> tuple[Path | None, bool, str]:
    """Return (path, hash_match, failure_code_or_empty)."""
    candidates = [
        run_folder / "final_alpha_output.txt",
        run_folder / "Final_Alpha.txt",
        run_folder / "exports" / "final_alpha_output.txt",
        stage_final,
    ]
    stage_hash = _sha256_file(stage_final) if stage_final.is_file() else ""
    for c in candidates:
        if c.is_file():
            if _sha256_file(c) == stage_hash:
                return c, True, ""
            # Prefer stage as authoritative if others diverge
            continue
    if stage_final.is_file():
        return stage_final, True, ""
    return None, False, "authoritative_final_hash_mismatch"


def _reference_isolation_check(
    *,
    run_folder: Path,
    stage_dir: Path,
    reference_path: Path,
    reference_text: str,
) -> tuple[bool, list[str], dict[str, Any]]:
    codes: list[str] = []
    alpha_dir = ROOT / "alpha"
    ref_resolved = reference_path.resolve()
    under_alpha = alpha_dir in ref_resolved.parents or ref_resolved.parent == alpha_dir
    under_project = ROOT in ref_resolved.parents and "Alpha_Benchmark_References" not in str(ref_resolved)
    # Isolation requires external folder; under project (except external refs) is a soft fail if under alpha hard fail
    if under_alpha:
        codes.append("reference_visible_to_runtime")
    isolation_file = stage_dir / "reference_isolation_actual.json"
    isolation_payload: dict[str, Any] = {}
    if isolation_file.is_file():
        try:
            isolation_payload = _load_json(isolation_file)
        except Exception:
            isolation_payload = {}
        if isolation_payload.get("reference_available_to_runtime") is True:
            codes.append("reference_visible_to_runtime")
        if isolation_payload.get("isolation_passed") is False:
            codes.append("reference_visible_to_runtime")
    # Contamination: unique long phrase from reference found under alpha/
    phrase = re.sub(r"\s+", "", reference_text)[:80]
    contamination = False
    if len(phrase) >= 40:
        for p in alpha_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".py", ".txt", ".json", ".md", ".csv"}:
                continue
            try:
                if phrase in p.read_text(encoding="utf-8", errors="ignore"):
                    contamination = True
                    break
            except Exception:
                continue
    if contamination:
        codes.append("reference_visible_to_runtime")
    # Benchmark term contamination markers in run
    for name in ("benchmark_keyterms.json", "benchmark_terms.json"):
        if (run_folder / name).is_file() or (stage_dir / name).is_file():
            codes.append("benchmark_term_contamination")
    evidence = {
        "reference_path": str(reference_path),
        "reference_sha256": _sha256_file(reference_path) if reference_path.is_file() else "",
        "reference_under_alpha": under_alpha,
        "reference_under_project_tree": under_project and not under_alpha,
        "isolation_artifact": isolation_payload,
        "isolation_passed": len(codes) == 0,
    }
    return len(codes) == 0, codes, evidence


def _detect_offline_repair(stage_dir: Path, run_folder: Path) -> tuple[bool, dict[str, Any]]:
    """Detect offline repair candidates; never use them as official live score."""
    candidates = []
    for name in (
        "stable_assembler_offline_replay.txt",
        "stable_offline_replay.txt",
        "OFFLINE_REPAIR_STABLE.txt",
    ):
        for base in (stage_dir, run_folder):
            p = base / name
            if p.is_file():
                candidates.append(str(p))
    # stable_assembler_only.txt is live assembler export — NOT offline repair by itself
    return bool(candidates), {
        "offline_repair_candidate_paths": candidates,
        "label": "OFFLINE_REPAIR_CANDIDATE_NOT_CURRENT_LIVE_ACCURACY" if candidates else None,
        "used_for_official_score": False,
    }


def evaluate_language_run(
    *,
    language: str,
    run_folder: Path,
    reference_path: Path,
    truth_path: Path | None,
    expected_language_codes: set[str],
) -> dict[str, Any]:
    failure_codes: list[str] = []
    stage_dir = _stage_dir(run_folder)
    result: dict[str, Any] = {
        "language": language,
        "run_folder": str(run_folder),
        "stage_dir": str(stage_dir),
        "TRUSTED_ACTUAL_ACCURACY": False,
        "failure_codes": [],
        "ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE": False,
    }

    if not run_folder.is_dir():
        failure_codes.append("stage_run_id_mismatch")
        result["failure_codes"] = failure_codes
        result["ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE"] = True
        result[f"{language.upper()}_CURRENT_ACTUAL_ACCURACY"] = "NOT_ESTABLISHED"
        result[f"{language.upper()}_TRUSTED"] = False
        return result

    if not reference_path.is_file():
        failure_codes.append(f"{language}_reference_mismatch")
        result["failure_codes"] = failure_codes
        result["ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE"] = True
        result[f"{language.upper()}_CURRENT_ACTUAL_ACCURACY"] = "NOT_ESTABLISHED"
        result[f"{language.upper()}_TRUSTED"] = False
        return result

    reference_text = _read_text(reference_path)
    ref_quality = assess_reference_quality(reference_text, language=language)
    if not ref_quality["reference_quality_valid"]:
        failure_codes.append(ref_quality["failure_code"] or f"{language}_reference_mismatch")

    stage_paths: dict[str, Path | None] = {
        k: _resolve_stage_file(stage_dir, v) for k, v in STAGE_NAMES.items()
    }
    for stage, path in stage_paths.items():
        if path is None:
            failure_codes.append(f"{stage}_stage_missing")

    provider_req = _resolve_provider_request(stage_dir)
    if provider_req is None:
        # Sanitized request is required evidence for a trusted score.
        failure_codes.append("raw_stage_missing")

    audio_path = _resolve_stage_file(stage_dir, "audio_delivery_summary.json")
    manifest_path = _resolve_stage_file(stage_dir, "stage_manifest.json")
    audio_summary = _load_json(audio_path) if audio_path else None
    manifest = _load_json(manifest_path) if manifest_path else {}

    audio_ok, audio_codes, audio_ev = _audio_delivery_gate(audio_summary)
    failure_codes.extend(audio_codes)

    iso_ok, iso_codes, iso_ev = _reference_isolation_check(
        run_folder=run_folder,
        stage_dir=stage_dir,
        reference_path=reference_path,
        reference_text=reference_text,
    )
    failure_codes.extend(iso_codes)

    repair_present, repair_ev = _detect_offline_repair(stage_dir, run_folder)
    # Presence alone does not fail if not used for official score
    if repair_ev.get("used_for_official_score"):
        failure_codes.append("offline_repair_used_for_official_score")

    # Language selection
    selected_language = (
        (manifest.get("selected_language") if manifest else None)
        or (audio_summary or {}).get("selected_language")
        or ""
    )
    if provider_req and provider_req.is_file():
        try:
            req = _load_json(provider_req)
            selected_language = (
                selected_language
                or req.get("language")
                or req.get("selected_language")
                or (req.get("options") or {}).get("language")
                or ""
            )
        except Exception:
            req = {}
    else:
        req = {}

    sel = str(selected_language).lower().strip()
    if sel and sel not in expected_language_codes and not any(
        sel.startswith(c) for c in expected_language_codes
    ):
        failure_codes.append("wrong_selected_language")

    # Stage hash validation vs manifest
    stage_texts: dict[str, str] = {}
    stage_hashes: dict[str, str] = {}
    for stage, path in stage_paths.items():
        if path and path.is_file():
            stage_texts[stage] = _read_text(path)
            stage_hashes[stage] = _sha256_file(path)
            key = f"{stage}_sha256"
            if manifest and manifest.get(key) and manifest[key] != stage_hashes[stage]:
                failure_codes.append("stage_run_id_mismatch")

    if manifest.get("run_id") and audio_summary and audio_summary.get("run_id"):
        # Allow parent/child divergence in harness wrappers; only hard-fail if both set and stage files missing run
        pass

    auth_path, auth_ok, auth_code = (
        _find_authoritative_final(run_folder, stage_paths["final"])
        if stage_paths.get("final")
        else (None, False, "final_stage_missing")
    )
    if not auth_ok:
        failure_codes.append(auth_code or "authoritative_final_hash_mismatch")
    elif auth_path and stage_paths.get("final"):
        if _sha256_file(auth_path) != stage_hashes.get("final"):
            failure_codes.append("authoritative_final_hash_mismatch")

    # Stop completion markers
    stop_ok = False
    for name in (
        "STOP_EVIDENCE_RECONCILIATION.json",
        "stop_finalize_summary.json",
        "STOP_FINALIZE_SUMMARY.json",
        "live_status.json",
    ):
        p = stage_dir / name
        if not p.is_file():
            p = run_folder / name
        if p.is_file():
            stop_ok = True
            try:
                stop_payload = _load_json(p)
                if stop_payload.get("completed") is False or stop_payload.get("stop_completed") is False:
                    stop_ok = False
            except Exception:
                pass
            break
    if manifest.get("completed") is True:
        stop_ok = True
    if not stop_ok:
        failure_codes.append("stop_not_completed")

    # Score stages (official live texts only)
    stages_metrics: dict[str, Any] = {}
    if language == "ja":
        for stage, text in stage_texts.items():
            stages_metrics[stage] = score_ja_stage(reference_text, text)
            stages_metrics[stage]["duplicate_content_count"] = _duplicate_phrase_count(text)
            stages_metrics[stage]["sentence_boundary_risk_count"] = _sentence_boundary_risk_count(text)
            stages_metrics[stage]["unexplained_deletion_count"] = int(
                stages_metrics[stage].get("deletions") or 0
            )
    else:
        for stage, text in stage_texts.items():
            stages_metrics[stage] = score_en_stage(reference_text, text)
            stages_metrics[stage]["duplicate_phrase_count"] = _duplicate_phrase_count(text)
            stages_metrics[stage]["missing_sentence_count"] = _missing_sentence_count(
                reference_text, text
            )

    def _acc(stage: str) -> float | None:
        m = stages_metrics.get(stage) or {}
        return m.get("official_accuracy_percent")

    raw_acc = _acc("raw")
    stable_acc = _acc("stable")
    final_acc = _acc("final")
    raw_to_stable = (
        None if raw_acc is None or stable_acc is None else (stable_acc - raw_acc)
    )
    stable_to_final = (
        None if stable_acc is None or final_acc is None else (final_acc - stable_acc)
    )

    truth = None
    if truth_path and truth_path.is_file():
        truth = _load_json(truth_path)
    categories = score_categories(hyp_text=stage_texts.get("final", ""), truth=truth)

    # Aggregate names / numbers / business rollups for TXT summary
    def _rollup(keys: list[str]) -> dict[str, Any]:
        cats = categories.get("categories") or {}
        if not categories.get("category_score_trusted"):
            return {"accuracy_percentage": None, "trusted": False}
        exp = exact = 0
        for k in keys:
            c = cats.get(k) or {}
            exp += int(c.get("expected_count") or 0)
            exact += int(c.get("exact_match_count") or 0)
        return {
            "expected_count": exp,
            "exact_match_count": exact,
            "accuracy_percentage": (exact / exp * 100.0) if exp else None,
            "trusted": True,
        }

    names_roll = _rollup(["participant_names", "company_names"])
    numbers_roll = _rollup(
        ["dates", "times", "percentages", "integers_and_decimals", "money_and_currency"]
    )
    business_roll = _rollup(
        [
            "it_and_security_terminology",
            "sales_terminology",
            "marketing_terminology",
            "general_business_terminology",
        ]
    )

    failure_codes = sorted(set(c for c in failure_codes if c))

    trusted = len(failure_codes) == 0 and final_acc is not None and ref_quality["reference_quality_valid"]

    official = final_acc if trusted else "NOT_ESTABLISHED"
    if not trusted:
        result["ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE"] = True

    error_examples: list[str] = []
    # Sample first mismatches via simple windowed compare on normalized forms
    if language == "ja" and "final" in stage_texts:
        ref_n = normalize_ja_strict(reference_text)
        hyp_n = normalize_ja_strict(stage_texts["final"])
        # Find first differing span
        i = 0
        while i < min(len(ref_n), len(hyp_n)) and ref_n[i] == hyp_n[i]:
            i += 1
        if i < max(len(ref_n), len(hyp_n)):
            error_examples.append(
                f"JA Final first-diff@{i}: REF[{ref_n[i:i+40]}] HYP[{hyp_n[i:i+40]}]"
            )
    elif language == "en" and "final" in stage_texts:
        rw = tokenize_en_words(reference_text)
        hw = tokenize_en_words(stage_texts["final"])
        for idx, (a, b) in enumerate(zip(rw, hw)):
            if a != b:
                error_examples.append(
                    f"EN Final first word-diff@{idx}: REF[{a}] HYP[{b}] context_ref={' '.join(rw[max(0,idx-2):idx+3])} context_hyp={' '.join(hw[max(0,idx-2):idx+3])}"
                )
                break

    result.update(
        {
            "TRUSTED_ACTUAL_ACCURACY": trusted,
            "failure_codes": failure_codes,
            f"{language.upper()}_TRUSTED": trusted,
            f"{language.upper()}_CURRENT_ACTUAL_ACCURACY": official,
            "reference": {
                "path": str(reference_path),
                "sha256": _sha256_file(reference_path),
                "quality": ref_quality,
            },
            "reference_isolation": iso_ev,
            "selected_language": selected_language,
            "provider_request_path": str(provider_req) if provider_req else None,
            "provider_request_sha256": _sha256_file(provider_req) if provider_req else None,
            "provider_request_filename_mapping": {
                "preferred": "actual_sanitized_deepgram_request.json",
                "actual": provider_req.name if provider_req else None,
            },
            "audio_delivery": audio_ev,
            "stage_manifest": manifest,
            "stage_paths": {k: str(v) if v else None for k, v in stage_paths.items()},
            "stage_sha256": stage_hashes,
            "authoritative_final_path": str(auth_path) if auth_path else None,
            "offline_repair": repair_ev,
            "stages": stages_metrics,
            "deltas": {
                "raw_to_stable_accuracy_pp": raw_to_stable,
                "stable_to_final_accuracy_pp": stable_to_final,
            },
            "categories": categories,
            "rollups": {
                "names": names_roll,
                "numbers_dates_money": numbers_roll,
                "business_terms": business_roll,
            },
            "error_examples": error_examples,
            "scoring_permitted": trusted,
            "offline_repair_occurred": repair_present,
            "reference_available_to_runtime": not iso_ok,
            "benchmark_specific_terms_supplied": "benchmark_term_contamination" in failure_codes,
        }
    )
    return result


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, str):
        return v
    return f"{float(v):.2f}%"


def build_txt_report(ja: dict[str, Any], en: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("CURRENT BILINGUAL ACCURACY REPORT")
    lines.append("=" * 72)
    lines.append("")
    lines.append("Language | Stage | Primary error metric | Actual accuracy | Trusted")
    lines.append("-" * 72)

    def row(lang: str, stage: str, metric: str, report: dict[str, Any]) -> str:
        st = (report.get("stages") or {}).get(stage) or {}
        if metric == "CER":
            acc = st.get("character_accuracy_percent")
        else:
            acc = st.get("word_accuracy_percent")
        trusted = "yes" if report.get("TRUSTED_ACTUAL_ACCURACY") else "no"
        return f"{lang} | {stage.capitalize()} | {metric} | {_fmt_pct(acc)} | {trusted}"

    for stage in ("raw", "stable", "final"):
        lines.append(row("Japanese", stage, "CER", ja))
    for stage in ("raw", "stable", "final"):
        lines.append(row("English", stage, "WER", en))

    lines.append("")
    lines.append("Japanese detail")
    lines.append("-" * 40)
    for stage in ("raw", "stable", "final"):
        st = (ja.get("stages") or {}).get(stage) or {}
        lines.append(
            f"  {stage}: strict_CER={_fmt_pct(st.get('strict_cer_percent'))} "
            f"norm_CER={_fmt_pct(st.get('normalized_cer_percent'))} "
            f"acc={_fmt_pct(st.get('character_accuracy_percent'))}"
        )
    lines.append(f"  Names: {ja.get('rollups', {}).get('names')}")
    lines.append(f"  Numbers/dates/money: {ja.get('rollups', {}).get('numbers_dates_money')}")
    lines.append(f"  Business terms: {ja.get('rollups', {}).get('business_terms')}")
    lines.append(f"  Audio delivery: {ja.get('audio_delivery')}")
    lines.append(f"  Stable-to-Final loss (pp): {ja.get('deltas', {}).get('stable_to_final_accuracy_pp')}")
    lines.append(f"  Official: {ja.get('JAPANESE_CURRENT_ACTUAL_ACCURACY')} TRUSTED={ja.get('JAPANESE_TRUSTED')}")
    lines.append(f"  Failure codes: {ja.get('failure_codes')}")

    lines.append("")
    lines.append("English detail")
    lines.append("-" * 40)
    for stage in ("raw", "stable", "final"):
        st = (en.get("stages") or {}).get(stage) or {}
        lines.append(
            f"  {stage}: strict_WER={_fmt_pct(st.get('strict_wer_percent'))} "
            f"strict_CER={_fmt_pct(st.get('strict_cer_percent'))} "
            f"word_acc={_fmt_pct(st.get('word_accuracy_percent'))}"
        )
    lines.append(f"  Names: {en.get('rollups', {}).get('names')}")
    lines.append(f"  Numbers/dates/money: {en.get('rollups', {}).get('numbers_dates_money')}")
    lines.append(f"  Business terms: {en.get('rollups', {}).get('business_terms')}")
    lines.append(f"  Audio delivery: {en.get('audio_delivery')}")
    lines.append(f"  Stable-to-Final loss (pp): {en.get('deltas', {}).get('stable_to_final_accuracy_pp')}")
    lines.append(f"  Official: {en.get('ENGLISH_CURRENT_ACTUAL_ACCURACY')} TRUSTED={en.get('ENGLISH_TRUSTED')}")
    lines.append(f"  Failure codes: {en.get('failure_codes')}")
    lines.append("")
    lines.append("NOTE: Japanese and English use different primary metrics; no combined bilingual %.")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_csv(ja: dict[str, Any], en: dict[str, Any]) -> list[list[Any]]:
    rows = [["language", "stage", "primary_metric", "error_percent", "accuracy_percent", "trusted"]]
    for stage in ("raw", "stable", "final"):
        st = (ja.get("stages") or {}).get(stage) or {}
        rows.append(
            [
                "japanese",
                stage,
                "CER",
                st.get("strict_cer_percent"),
                st.get("character_accuracy_percent"),
                ja.get("TRUSTED_ACTUAL_ACCURACY"),
            ]
        )
    for stage in ("raw", "stable", "final"):
        st = (en.get("stages") or {}).get(stage) or {}
        rows.append(
            [
                "english",
                stage,
                "WER",
                st.get("strict_wer_percent"),
                st.get("word_accuracy_percent"),
                en.get("TRUSTED_ACTUAL_ACCURACY"),
            ]
        )
    return rows


def package_results(out_dir: Path, ja: dict[str, Any], en: dict[str, Any]) -> Path | None:
    stamp = _utc_stamp()
    zip_path = out_dir / f"CURRENT_BILINGUAL_ACCURACY_PACKAGE_{stamp}.zip"
    include_names = [
        "CURRENT_BILINGUAL_ACCURACY_REPORT.json",
        "CURRENT_BILINGUAL_ACCURACY_REPORT.txt",
        "CURRENT_BILINGUAL_ACCURACY_REPORT.csv",
        "JAPANESE_CURRENT_ACCURACY_REPORT.json",
        "ENGLISH_CURRENT_ACCURACY_REPORT.json",
        "CURRENT_BILINGUAL_CATEGORY_REPORT.json",
        "CURRENT_BILINGUAL_ERROR_EXAMPLES.txt",
        "CURRENT_BILINGUAL_TRUST_GATE.json",
        "INDEPENDENT_VERIFICATION.json",
        "CURRENT_BILINGUAL_ACCURACY_PACKAGE_INDEX.txt",
        "EXISTING_CAPABILITY_AUDIT.txt",
        "REFERENCE_HASHES.json",
        "JAPANESE_REFERENCE_QUALITY.json",
        "ENGLISH_REFERENCE_QUALITY.json",
        "Cursor_final_report.txt",
    ]
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in include_names:
                p = out_dir / name
                if p.is_file():
                    zf.write(p, arcname=name)
            # Stage copies
            for label, report in (("japanese", ja), ("english", en)):
                stage_dir = Path(report.get("stage_dir") or "")
                run_folder = Path(report.get("run_folder") or "")
                for fname in (
                    "raw_deepgram.txt",
                    "stable_transcript.txt",
                    "final_alpha_output.txt",
                    "stage_manifest.json",
                    "audio_delivery_summary.json",
                    "deepgram_request_actual.json",
                    "actual_sanitized_deepgram_request.json",
                    "reference_isolation_actual.json",
                    "STOP_EVIDENCE_RECONCILIATION.json",
                    "live_status.json",
                    "run_manifest.json",
                ):
                    for base in (stage_dir, run_folder):
                        src = base / fname
                        if src.is_file():
                            zf.write(src, arcname=f"{label}_run/{fname}")
                            break
                # truth if present beside out
                truth = out_dir / f"{label}_category_truth.json"
                if truth.is_file():
                    zf.write(truth, arcname=truth.name)
        return zip_path
    except Exception as exc:
        _write_text(out_dir / "PACKAGE_ERROR.txt", f"package_failed: {exc}\n")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Score current bilingual live accuracy (JA CER / EN WER)")
    ap.add_argument("--japanese-run", required=True)
    ap.add_argument("--japanese-reference", required=True)
    ap.add_argument("--english-run", required=True)
    ap.add_argument("--english-reference", required=True)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--japanese-truth", default="")
    ap.add_argument("--english-truth", default="")
    ap.add_argument("--fixture-mode", action="store_true", help="Mark results as TEST_FIXTURE_NOT_PRODUCT_ACCURACY")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    ja = evaluate_language_run(
        language="ja",
        run_folder=Path(args.japanese_run),
        reference_path=Path(args.japanese_reference),
        truth_path=Path(args.japanese_truth) if args.japanese_truth else None,
        expected_language_codes={"ja", "ja-jp", "japanese"},
    )
    en = evaluate_language_run(
        language="en",
        run_folder=Path(args.english_run),
        reference_path=Path(args.english_reference),
        truth_path=Path(args.english_truth) if args.english_truth else None,
        expected_language_codes={"en", "en-us", "en-gb", "english"},
    )

    if args.fixture_mode:
        ja["TEST_FIXTURE_NOT_PRODUCT_ACCURACY"] = True
        en["TEST_FIXTURE_NOT_PRODUCT_ACCURACY"] = True
        ja["TRUSTED_ACTUAL_ACCURACY"] = False
        en["TRUSTED_ACTUAL_ACCURACY"] = False
        ja["JAPANESE_TRUSTED"] = False
        en["ENGLISH_TRUSTED"] = False
        ja["JAPANESE_CURRENT_ACTUAL_ACCURACY"] = "NOT_ESTABLISHED"
        en["ENGLISH_CURRENT_ACTUAL_ACCURACY"] = "NOT_ESTABLISHED"
        ja["failure_codes"] = sorted(set(ja.get("failure_codes") or []) | {"test_fixture"})
        en["failure_codes"] = sorted(set(en.get("failure_codes") or []) | {"test_fixture"})

    bilingual = {
        "generated_at_utc": _utc_stamp(),
        "fixture_mode": bool(args.fixture_mode),
        "japanese": ja,
        "english": en,
        "combined_bilingual_percentage": None,
        "note": "Japanese CER and English WER remain separate; no combined bilingual accuracy.",
        "JAPANESE_CURRENT_ACTUAL_ACCURACY": ja.get("JAPANESE_CURRENT_ACTUAL_ACCURACY"),
        "JAPANESE_TRUSTED": ja.get("JAPANESE_TRUSTED"),
        "ENGLISH_CURRENT_ACTUAL_ACCURACY": en.get("ENGLISH_CURRENT_ACTUAL_ACCURACY"),
        "ENGLISH_TRUSTED": en.get("ENGLISH_TRUSTED"),
    }

    trust_gate = {
        "japanese": {
            "TRUSTED_ACTUAL_ACCURACY": ja.get("TRUSTED_ACTUAL_ACCURACY"),
            "failure_codes": ja.get("failure_codes"),
            "ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE": ja.get(
                "ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE"
            ),
        },
        "english": {
            "TRUSTED_ACTUAL_ACCURACY": en.get("TRUSTED_ACTUAL_ACCURACY"),
            "failure_codes": en.get("failure_codes"),
            "ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE": en.get(
                "ACCURACY_RESULT_INVALID_OR_EVIDENCE_INCOMPLETE"
            ),
        },
        "both_trusted": bool(ja.get("TRUSTED_ACTUAL_ACCURACY") and en.get("TRUSTED_ACTUAL_ACCURACY")),
    }

    category_report = {
        "japanese": ja.get("categories"),
        "english": en.get("categories"),
    }

    ref_hashes = {
        "japanese_reference_path": str(Path(args.japanese_reference)),
        "japanese_reference_sha256": _sha256_file(Path(args.japanese_reference))
        if Path(args.japanese_reference).is_file()
        else "",
        "english_reference_path": str(Path(args.english_reference)),
        "english_reference_sha256": _sha256_file(Path(args.english_reference))
        if Path(args.english_reference).is_file()
        else "",
    }

    _write_json(out_dir / "CURRENT_BILINGUAL_ACCURACY_REPORT.json", bilingual)
    _write_text(out_dir / "CURRENT_BILINGUAL_ACCURACY_REPORT.txt", build_txt_report(ja, en))
    with (out_dir / "CURRENT_BILINGUAL_ACCURACY_REPORT.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(build_csv(ja, en))
    _write_json(out_dir / "JAPANESE_CURRENT_ACCURACY_REPORT.json", ja)
    _write_json(out_dir / "ENGLISH_CURRENT_ACCURACY_REPORT.json", en)
    _write_json(out_dir / "CURRENT_BILINGUAL_CATEGORY_REPORT.json", category_report)
    _write_json(out_dir / "CURRENT_BILINGUAL_TRUST_GATE.json", trust_gate)
    _write_json(out_dir / "REFERENCE_HASHES.json", ref_hashes)
    _write_json(out_dir / "JAPANESE_REFERENCE_QUALITY.json", ja.get("reference", {}).get("quality") or {})
    _write_json(out_dir / "ENGLISH_REFERENCE_QUALITY.json", en.get("reference", {}).get("quality") or {})

    err_lines = ["CURRENT BILINGUAL ERROR EXAMPLES", "=" * 40, "", "Japanese:", ""]
    err_lines.extend(ja.get("error_examples") or ["(none)"])
    err_lines.extend(["", "English:", ""])
    err_lines.extend(en.get("error_examples") or ["(none)"])
    err_lines.append("")
    _write_text(out_dir / "CURRENT_BILINGUAL_ERROR_EXAMPLES.txt", "\n".join(err_lines))

    # Placeholder independent verification until verifier runs
    if not (out_dir / "INDEPENDENT_VERIFICATION.json").is_file():
        _write_json(
            out_dir / "INDEPENDENT_VERIFICATION.json",
            {
                "status": "EVIDENCE_INCOMPLETE",
                "note": "Run verify_current_bilingual_accuracy.py after primary scoring.",
            },
        )

    zip_path = package_results(out_dir, ja, en)
    index_lines = [
        "CURRENT_BILINGUAL_ACCURACY_PACKAGE_INDEX",
        f"generated_at_utc={_utc_stamp()}",
        f"out_dir={out_dir}",
        f"zip_path={zip_path}",
        f"JAPANESE_TRUSTED={ja.get('JAPANESE_TRUSTED')}",
        f"ENGLISH_TRUSTED={en.get('ENGLISH_TRUSTED')}",
        f"JAPANESE_CURRENT_ACTUAL_ACCURACY={ja.get('JAPANESE_CURRENT_ACTUAL_ACCURACY')}",
        f"ENGLISH_CURRENT_ACTUAL_ACCURACY={en.get('ENGLISH_CURRENT_ACTUAL_ACCURACY')}",
        f"japanese_run={args.japanese_run}",
        f"english_run={args.english_run}",
        "",
        "Upload: CURRENT_BILINGUAL_ACCURACY_PACKAGE_*.zip plus Cursor_final_report.txt if present.",
        "",
    ]
    _write_text(out_dir / "CURRENT_BILINGUAL_ACCURACY_PACKAGE_INDEX.txt", "\n".join(index_lines))

    print(build_txt_report(ja, en))
    print(f"Wrote reports under: {out_dir}")
    if zip_path:
        print(f"Package: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
