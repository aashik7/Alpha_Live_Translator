#!/usr/bin/env python3
"""Independent verifier + preflight for current bilingual accuracy audit.

Does not import primary scorer metric values. May reuse only low-level
levenshtein primitives from alpha.utils.cer_backtracking.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import py_compile
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpha.utils.cer_backtracking import levenshtein_operation_counts

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "troubleshooting" / "accuracy_benchmark" / "current_bilingual_accuracy"
TOLERANCE_PP = 0.0001

FROZEN_RUNTIME_FILES = [
    "alpha/transcription/deepgram_client.py",
    "alpha/transcription/japanese_sentence_assembler.py",
    "alpha/transcription/stable_revision_decision.py",
    "alpha/audio/timeline_mixer.py",
    "alpha/constants.py",
    "alpha/ui/main_window.py",
    "alpha/utils/stop_finalize_worker.py",
    "main.py",
]

_SPEAKER_RE = re.compile(r"^\[Speaker\s+\d+\]\s*", re.I)
_TS_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z]+)?|[^\sA-Za-z0-9]+")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- Independent normalizers / metrics (do not import score_current_bilingual_accuracy) ---


def _indep_normalize_ja(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    return re.sub(r"\s+", "", "".join(lines))


def _indep_normalize_en(text: str) -> str:
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


def _indep_en_words(text: str) -> list[str]:
    norm = _indep_normalize_en(text)
    return [t for t in _WORD_RE.findall(norm) if re.search(r"[A-Za-z0-9]", t)]


def _indep_token_wer(ref: list[str], hyp: list[str]) -> dict[str, float | int]:
    n, m = len(ref), len(hyp)
    if n == 0 and m == 0:
        return {"wer_percent": 0.0, "word_accuracy_percent": 100.0, "edit_distance": 0}
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
    distance = dp[n][m]
    wer = distance / max(n, 1)
    wer_pct = wer * 100.0
    return {
        "wer_percent": wer_pct,
        "word_accuracy_percent": max(0.0, 100.0 - wer_pct),
        "edit_distance": distance,
    }


def indep_ja_cer(ref_text: str, hyp_text: str) -> float:
    ref_n = _indep_normalize_ja(ref_text)
    hyp_n = _indep_normalize_ja(hyp_text)
    counts = levenshtein_operation_counts(ref_n, hyp_n)
    ref_len = max(int(counts["reference_character_count"]), 1)
    return (int(counts["edit_distance"]) / ref_len) * 100.0


def indep_en_wer(ref_text: str, hyp_text: str) -> float:
    return float(_indep_token_wer(_indep_en_words(ref_text), _indep_en_words(hyp_text))["wer_percent"])


def indep_en_cer(ref_text: str, hyp_text: str) -> float:
    ref_n = _indep_normalize_en(ref_text)
    hyp_n = _indep_normalize_en(hyp_text)
    counts = levenshtein_operation_counts(ref_n, hyp_n)
    ref_len = max(int(counts["reference_character_count"]), 1)
    return (int(counts["edit_distance"]) / ref_len) * 100.0


def _within_tol(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= TOLERANCE_PP


def _stage_dir(run_folder: Path) -> Path:
    asc = run_folder / "accuracy_stage_compare"
    return asc if asc.is_dir() else run_folder


def _runtime_snapshot() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in FROZEN_RUNTIME_FILES:
        p = ROOT / rel
        if p.is_file():
            out[rel] = _sha256_file(p)
    return out


def _scan_alpha_for_reference(reference_text: str) -> list[str]:
    hits: list[str] = []
    alpha = ROOT / "alpha"
    compact = re.sub(r"\s+", "", reference_text or "")
    if len(compact) < 40:
        return hits
    # Use a mid-body unique-ish phrase to avoid matching generic short strings
    mid = len(compact) // 2
    phrase = compact[max(0, mid - 40) : mid + 40]
    if len(phrase) < 40:
        phrase = compact[:80]
    for p in alpha.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".py", ".txt", ".json", ".md", ".csv"}:
            continue
        try:
            data = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if phrase in re.sub(r"\s+", "", data):
            hits.append(str(p.relative_to(ROOT)))
            if len(hits) >= 10:
                break
    return hits


def run_preflight(*, japanese_reference: Path, english_reference: Path, out_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    details: dict[str, Any] = {"checks": {}}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        details["checks"][name] = {"ok": ok, "detail": detail}
        if not ok:
            failures.append(name)

    # References exist / non-empty
    for label, path in (("japanese_reference", japanese_reference), ("english_reference", english_reference)):
        exists = path.is_file()
        nonempty = exists and path.stat().st_size > 0
        check(f"{label}_exists", exists, str(path))
        check(f"{label}_nonempty", nonempty, path.stat().st_size if exists else 0)
        if exists:
            details["checks"][f"{label}_sha256"] = {"ok": True, "detail": _sha256_file(path)}

    # Not under alpha\
    alpha = (ROOT / "alpha").resolve()
    for label, path in (("japanese_reference", japanese_reference), ("english_reference", english_reference)):
        resolved = path.resolve()
        under_alpha = alpha in resolved.parents or resolved.parent == alpha
        check(f"{label}_not_under_alpha", not under_alpha, str(resolved))

    # No exact reference text / unique phrases under alpha\
    for label, path in (("japanese_reference", japanese_reference), ("english_reference", english_reference)):
        if path.is_file():
            hits = _scan_alpha_for_reference(_read_text(path))
            check(f"{label}_not_copied_under_alpha", len(hits) == 0, hits)

    # Scoring tools compile
    for script in ("score_current_bilingual_accuracy.py", "verify_current_bilingual_accuracy.py"):
        sp = ROOT / script
        try:
            py_compile.compile(str(sp), doraise=True)
            # also parse AST
            ast.parse(sp.read_text(encoding="utf-8"))
            check(f"compile_{script}", True)
        except Exception as exc:
            check(f"compile_{script}", False, str(exc))

    # Stage evidence capability exists
    cap = ROOT / "alpha" / "utils" / "accuracy_stage_capture.py"
    check("stage_evidence_capability_exists", cap.is_file(), str(cap))

    # Output directory creatable
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".preflight_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        check("out_dir_writable", True, str(out_dir))
    except Exception as exc:
        check("out_dir_writable", False, str(exc))

    # Runtime files not modified by this audit: snapshot + compare if prior snapshot
    snapshot = _runtime_snapshot()
    snap_path = out_dir / "FROZEN_RUNTIME_HASH_SNAPSHOT.json"
    if snap_path.is_file():
        prior = _load_json(snap_path)
        changed = [k for k, v in snapshot.items() if prior.get(k) and prior[k] != v]
        check("runtime_files_unchanged_vs_audit_snapshot", len(changed) == 0, changed)
    else:
        _write_json(snap_path, {"created_at_utc": _utc_stamp(), "hashes": snapshot})
        check("runtime_files_snapshot_created", True, list(snapshot.keys()))
        # Also verify audit scripts are not inside frozen list mutations — always pass on first snapshot
        check("runtime_files_unchanged_vs_audit_snapshot", True, "initial_snapshot")

    # Confirm audit tools are not rewriting frozen files now (hash integrity readable)
    check("frozen_runtime_files_readable", len(snapshot) == len(FROZEN_RUNTIME_FILES), list(snapshot.keys()))

    status = "PASSED" if not failures else "FAILED"
    result = {
        "CURRENT_BILINGUAL_ACCURACY_PREFLIGHT": status,
        "generated_at_utc": _utc_stamp(),
        "failures": failures,
        "details": details,
        "japanese_reference": str(japanese_reference),
        "english_reference": str(english_reference),
        "out_dir": str(out_dir),
        "live_testing_allowed": status == "PASSED",
    }
    _write_json(out_dir / "CURRENT_BILINGUAL_ACCURACY_PREFLIGHT.json", result)
    _write_text(
        out_dir / "CURRENT_BILINGUAL_ACCURACY_PREFLIGHT.txt",
        f"CURRENT_BILINGUAL_ACCURACY_PREFLIGHT = {status}\n"
        f"failures={failures}\n"
        f"live_testing_allowed={status == 'PASSED'}\n",
    )
    return result


def verify_against_primary(*, out_dir: Path) -> dict[str, Any]:
    primary_path = out_dir / "CURRENT_BILINGUAL_ACCURACY_REPORT.json"
    if not primary_path.is_file():
        result = {
            "status": "EVIDENCE_INCOMPLETE",
            "reason": "primary_report_missing",
            "failure_codes": ["independent_score_mismatch"],
        }
        _write_json(out_dir / "INDEPENDENT_VERIFICATION.json", result)
        return result

    primary = _load_json(primary_path)
    ja = primary.get("japanese") or {}
    en = primary.get("english") or {}
    comparisons: list[dict[str, Any]] = []
    failure_codes: list[str] = []

    def compare(name: str, expected: float | None, actual: float | None) -> None:
        ok = _within_tol(expected, actual)
        comparisons.append(
            {
                "metric": name,
                "primary": expected,
                "independent": actual,
                "tolerance_pp": TOLERANCE_PP,
                "match": ok,
            }
        )
        if not ok:
            failure_codes.append("independent_score_mismatch")

    # Recalculate from stage files + references
    for lang_key, report, metric_kind in (
        ("japanese", ja, "cer"),
        ("english", en, "wer"),
    ):
        ref_path = Path((report.get("reference") or {}).get("path") or "")
        stage_paths = report.get("stage_paths") or {}
        if not ref_path.is_file():
            failure_codes.append(f"{'ja' if lang_key == 'japanese' else 'en'}_reference_mismatch")
            continue
        ref_text = _read_text(ref_path)
        ref_hash = _sha256_file(ref_path)
        reported_hash = (report.get("reference") or {}).get("sha256")
        if reported_hash and reported_hash != ref_hash:
            failure_codes.append("independent_score_mismatch")
            comparisons.append(
                {
                    "metric": f"{lang_key}_reference_hash",
                    "primary": reported_hash,
                    "independent": ref_hash,
                    "match": False,
                }
            )

        for stage in ("raw", "stable", "final"):
            hyp_path = Path(stage_paths.get(stage) or "")
            if not hyp_path.is_file():
                failure_codes.append(f"{stage}_stage_missing")
                continue
            # Hash check
            file_hash = _sha256_file(hyp_path)
            reported = (report.get("stage_sha256") or {}).get(stage)
            if reported and reported != file_hash:
                failure_codes.append("independent_score_mismatch")
            hyp_text = _read_text(hyp_path)
            st_primary = (report.get("stages") or {}).get(stage) or {}
            if metric_kind == "cer":
                indep = indep_ja_cer(ref_text, hyp_text)
                compare(
                    f"{lang_key}_{stage}_strict_cer_percent",
                    st_primary.get("strict_cer_percent"),
                    indep,
                )
            else:
                indep_w = indep_en_wer(ref_text, hyp_text)
                indep_c = indep_en_cer(ref_text, hyp_text)
                compare(
                    f"{lang_key}_{stage}_strict_wer_percent",
                    st_primary.get("strict_wer_percent"),
                    indep_w,
                )
                compare(
                    f"{lang_key}_{stage}_strict_cer_percent",
                    st_primary.get("strict_cer_percent"),
                    indep_c,
                )

        # Audio delivery / isolation / offline repair checks
        audio = report.get("audio_delivery") or {}
        ratio = audio.get("audio_delivery_ratio")
        if ratio is not None and abs(float(ratio) - 1.0) > 1e-9:
            failure_codes.append("audio_delivery_incomplete")
        if int(audio.get("missing_chunks") or 0) != 0:
            failure_codes.append("audio_delivery_incomplete")
        if int(audio.get("pending_at_close") or 0) != 0:
            failure_codes.append("audio_delivery_incomplete")
        iso = report.get("reference_isolation") or {}
        if iso.get("isolation_passed") is False:
            failure_codes.append("reference_visible_to_runtime")
        repair = report.get("offline_repair") or {}
        if repair.get("used_for_official_score"):
            failure_codes.append("offline_repair_used_for_official_score")

        auth = report.get("authoritative_final_path")
        final_path = stage_paths.get("final")
        if auth and final_path and Path(auth).is_file() and Path(final_path).is_file():
            if _sha256_file(Path(auth)) != _sha256_file(Path(final_path)):
                failure_codes.append("authoritative_final_hash_mismatch")

    failure_codes = sorted(set(failure_codes))
    if not comparisons and "primary_report_missing" not in failure_codes:
        status = "EVIDENCE_INCOMPLETE"
    elif failure_codes:
        # Distinguish incomplete evidence vs pure metric mismatch
        incomplete_markers = {
            "raw_stage_missing",
            "stable_stage_missing",
            "final_stage_missing",
            "ja_reference_mismatch",
            "en_reference_mismatch",
        }
        if failure_codes & incomplete_markers and not any(
            c["metric"].endswith("_strict_cer_percent") or c["metric"].endswith("_strict_wer_percent")
            for c in comparisons
            if not c.get("match")
        ):
            status = "EVIDENCE_INCOMPLETE"
        elif any(not c.get("match") for c in comparisons):
            status = "FAILED"
        elif failure_codes:
            status = "FAILED"
        else:
            status = "PASSED"
    else:
        status = "PASSED" if all(c.get("match") for c in comparisons) else "FAILED"

    if status == "PASSED" and any(not c.get("match") for c in comparisons):
        status = "FAILED"
        failure_codes = sorted(set(failure_codes) | {"independent_score_mismatch"})

    result = {
        "status": status,
        "generated_at_utc": _utc_stamp(),
        "tolerance_pp": TOLERANCE_PP,
        "comparisons": comparisons,
        "failure_codes": failure_codes,
        "japanese_trusted_primary": ja.get("JAPANESE_TRUSTED"),
        "english_trusted_primary": en.get("ENGLISH_TRUSTED"),
        "note": "Independent recalculation; does not import primary scorer metric values.",
    }
    _write_json(out_dir / "INDEPENDENT_VERIFICATION.json", result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify / preflight current bilingual accuracy")
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--japanese-reference", default="")
    ap.add_argument("--english-reference", default="")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    if args.preflight:
        if not args.japanese_reference or not args.english_reference:
            print("ERROR: --preflight requires --japanese-reference and --english-reference")
            return 2
        result = run_preflight(
            japanese_reference=Path(args.japanese_reference),
            english_reference=Path(args.english_reference),
            out_dir=out_dir,
        )
        print(f"CURRENT_BILINGUAL_ACCURACY_PREFLIGHT = {result['CURRENT_BILINGUAL_ACCURACY_PREFLIGHT']}")
        if result["failures"]:
            print(f"failures: {result['failures']}")
        print(f"live_testing_allowed: {result['live_testing_allowed']}")
        return 0 if result["CURRENT_BILINGUAL_ACCURACY_PREFLIGHT"] == "PASSED" else 1

    result = verify_against_primary(out_dir=out_dir)
    print(f"INDEPENDENT_VERIFICATION status = {result.get('status')}")
    if result.get("failure_codes"):
        print(f"failure_codes: {result['failure_codes']}")
    return 0 if result.get("status") == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
