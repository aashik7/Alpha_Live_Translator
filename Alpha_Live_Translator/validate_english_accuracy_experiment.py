#!/usr/bin/env python3
"""Validate English accuracy experiment outputs and Japanese non-regression."""

from __future__ import annotations

import json
import py_compile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EXPERIMENTS = ROOT / "troubleshooting" / "experiments"
POINTER = EXPERIMENTS / "english_accuracy_90" / "LATEST_EXPERIMENT.json"
OUT = EXPERIMENTS / "english_accuracy_90" / "VALIDATION"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(name: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def resolve_exp_dir() -> Path | None:
    if POINTER.is_file():
        data = _load(POINTER)
        p = Path(str(data.get("experiment_dir") or ""))
        if p.is_dir():
            return p
    # fallback: newest english_accuracy_90* folder
    cands = sorted(EXPERIMENTS.glob("english_accuracy_90*"), key=lambda p: p.stat().st_mtime)
    cands = [p for p in cands if p.is_dir() and p.name != "english_accuracy_90"]
    return cands[-1] if cands else None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    exp = resolve_exp_dir()
    checks.append(_check("experiment_dir_exists", exp is not None, str(exp)))
    if exp is None:
        report = {
            "ENGLISH_ACCURACY_EXPERIMENT_VALIDATION": "FAILED",
            "generated_at_utc": _utc(),
            "checks": checks,
        }
        (OUT / "ENGLISH_ACCURACY_EXPERIMENT_VALIDATION.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print("ENGLISH_ACCURACY_EXPERIMENT_VALIDATION = FAILED")
        return 1

    required = [
        "BASELINE_ENGLISH_ACCURACY.json",
        "REFERENCE_QUALITY_AUDIT.json",
        "REFERENCE_QUALITY_AUDIT.txt",
        "AUDIO_PATH_DIAGNOSIS.json",
        "CANDIDATE_COMPARISON.json",
        "CANDIDATE_COMPARISON.txt",
        "ENGLISH_ERROR_ALIGNMENT.jsonl",
        "ENGLISH_90_PERCENT_GATE.json",
        "ENGLISH_ACCURACY_DECISION_REPORT.txt",
        "experiment_manifest.json",
    ]
    for name in required:
        p = exp / name
        checks.append(_check(f"output_{name}", p.is_file() and p.stat().st_size > 0, str(p)))

    baseline = _load(exp / "BASELINE_ENGLISH_ACCURACY.json")
    gate = _load(exp / "ENGLISH_90_PERCENT_GATE.json")
    audio = _load(exp / "AUDIO_PATH_DIAGNOSIS.json")
    cmp_ = _load(exp / "CANDIDATE_COMPARISON.json")

    wer = baseline.get("strict_wer_percent")
    checks.append(_check("baseline_wer_present", wer is not None, wer))
    checks.append(
        _check(
            "baseline_near_expected_15pct",
            wer is not None and 10.0 <= float(wer) <= 25.0,
            wer,
        )
    )
    checks.append(
        _check(
            "exact_audio_flag_present",
            "MICROPHONE_PLAYBACK_ECHO_DETECTED" in audio,
            audio.get("MICROPHONE_PLAYBACK_ECHO_DETECTED"),
        )
    )
    checks.append(
        _check(
            "gate_file_valid",
            gate.get("ENGLISH_90_PERCENT_GATE") in {"REACHED", "NOT_REACHED"},
            gate.get("ENGLISH_90_PERCENT_GATE"),
        )
    )
    checks.append(
        _check(
            "no_false_promotion",
            gate.get("promotion_allowed") is False
            or gate.get("ENGLISH_90_PERCENT_GATE") == "REACHED",
            gate.get("promotion_allowed"),
        )
    )
    checks.append(_check("candidates_non_empty", bool(cmp_.get("candidates")), len(cmp_.get("candidates") or [])))

    # Japanese non-regression: routing still ja/en; JA constants unchanged
    from alpha.constants import FORCE_DEEPGRAM_LANGUAGE, JAPANESE_STT_PROFILE
    from alpha.config import DEEPGRAM_JA_ENDPOINTING_MS
    from alpha.utils.language_routing import resolve_ui_language_to_deepgram_code

    checks.append(_check("japanese_routes_to_ja", resolve_ui_language_to_deepgram_code("Japanese") == "ja"))
    checks.append(_check("english_routes_to_en", resolve_ui_language_to_deepgram_code("English") == "en"))
    checks.append(_check("force_language_disabled", FORCE_DEEPGRAM_LANGUAGE in (None, "", False)))
    checks.append(_check("japanese_no_diarize_preserved", str(JAPANESE_STT_PROFILE) == "no_diarize"))
    checks.append(_check("japanese_endpointing_500", int(DEEPGRAM_JA_ENDPOINTING_MS) == 500))

    for script in (
        "run_english_accuracy_experiment.py",
        "validate_english_accuracy_experiment.py",
        "package_english_accuracy_experiment.py",
        "verify_language_routing.py",
    ):
        try:
            py_compile.compile(str(ROOT / script), doraise=True)
            checks.append(_check(f"compile_{script}", True))
        except Exception as exc:
            checks.append(_check(f"compile_{script}", False, str(exc)))

    failures = [c["name"] for c in checks if not c["ok"]]
    status = "PASSED" if not failures else "FAILED"
    report = {
        "ENGLISH_ACCURACY_EXPERIMENT_VALIDATION": status,
        "generated_at_utc": _utc(),
        "experiment_dir": str(exp),
        "checks": checks,
        "failures": failures,
        "gate": gate.get("ENGLISH_90_PERCENT_GATE"),
        "root_cause": gate.get("strongest_root_cause"),
    }
    out_path = OUT / "ENGLISH_ACCURACY_EXPERIMENT_VALIDATION.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ENGLISH_ACCURACY_EXPERIMENT_VALIDATION = {status}")
    if failures:
        print(f"failures={failures}")
    print(f"Wrote {out_path}")
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
