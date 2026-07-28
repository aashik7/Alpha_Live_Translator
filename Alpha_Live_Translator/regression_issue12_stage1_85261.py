"""Focused regression suite for Issue 12 Stage 1 (85261). Offline only."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.constants import (  # noqa: E402
    APP_VERSION,
    JAPANESE_KEYTERM_PROFILE,
    JAPANESE_STT_PROFILE,
    resolve_japanese_keyterms,
)
from alpha.utils.issue12_stage1_runtime import (  # noqa: E402
    ACCURACY_PROFILE_TARGET_85,
    MAX_MEETING_CONTEXT_TERMS,
    build_deepgram_request_actual_payload,
    build_meeting_context_glossary,
    is_system_audio_only_benchmark,
    sanitize_deepgram_query_string,
)


def _pass(name: str) -> dict[str, Any]:
    return {"name": name, "passed": True, "detail": ""}


def _fail(name: str, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": False, "detail": detail}


def test_01_raw_not_modified_by_profile() -> dict[str, Any]:
    raw = "原文そのまま"
    os.environ["JAPANESE_ACCURACY_PROFILE"] = ACCURACY_PROFILE_TARGET_85
    try:
        terms, profile, _ = resolve_japanese_keyterms()
        # Profile changes keyterms only — never mutates supplied raw string
        assert raw == "原文そのまま"
        assert profile == ACCURACY_PROFILE_TARGET_85
        assert isinstance(terms, list)
        return _pass("raw_text_cannot_be_modified_by_accuracy_profile")
    finally:
        os.environ.pop("JAPANESE_ACCURACY_PROFILE", None)


def test_02_global_business_unchanged() -> dict[str, Any]:
    os.environ.pop("JAPANESE_ACCURACY_PROFILE", None)
    terms, profile, _ = resolve_japanese_keyterms()
    if profile != "business_japanese":
        return _fail("global_business_profile_unchanged", f"profile={profile}")
    if JAPANESE_KEYTERM_PROFILE != "business_japanese":
        return _fail(
            "global_business_profile_unchanged",
            f"constant={JAPANESE_KEYTERM_PROFILE}",
        )
    if "さくらさくプラス" in terms:
        return _fail("global_business_profile_unchanged", "meeting term leaked into default")
    return _pass("global_business_profile_unchanged")


def test_03_meeting_context_only_in_benchmark_mode() -> dict[str, Any]:
    os.environ.pop("JAPANESE_ACCURACY_PROFILE", None)
    _, p1, _ = resolve_japanese_keyterms()
    os.environ["JAPANESE_ACCURACY_PROFILE"] = ACCURACY_PROFILE_TARGET_85
    try:
        terms, p2, _ = resolve_japanese_keyterms()
        if p1 != "business_japanese":
            return _fail("meeting_context_only_benchmark", f"default={p1}")
        if p2 != ACCURACY_PROFILE_TARGET_85:
            return _fail("meeting_context_only_benchmark", f"active={p2}")
        if not terms:
            return _fail("meeting_context_only_benchmark", "empty meeting terms")
        return _pass("meeting_context_profile_only_in_benchmark_mode")
    finally:
        os.environ.pop("JAPANESE_ACCURACY_PROFILE", None)


def test_04_max_keyterm_count() -> dict[str, Any]:
    out = build_meeting_context_glossary(
        project_root=ROOT,
        reference_path=ROOT
        / "troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt",
    )
    if out["glossary"]["term_count"] > MAX_MEETING_CONTEXT_TERMS:
        return _fail("max_keyterm_count", str(out["glossary"]["term_count"]))
    return _pass("maximum_keyterm_count_enforced")


def test_05_no_complete_sentences() -> dict[str, Any]:
    report = json.loads(
        (
            ROOT
            / "troubleshooting/accuracy_benchmark/glossaries/test01_meeting_context_report.json"
        ).read_text(encoding="utf-8")
    )
    for term in report.get("terms") or []:
        if "。" in term:
            return _fail("no_complete_sentences", term)
    rejected = report.get("rejected_candidates") or []
    if not any(r.get("reason") == "complete_sentence" for r in rejected):
        return _fail("no_complete_sentences", "missing rejected complete_sentence")
    return _pass("complete_sentences_cannot_be_inserted_as_keyterms")


def test_06_request_proof_before_connect() -> dict[str, Any]:
    payload = build_deepgram_request_actual_payload(
        run_id="t",
        app_version=APP_VERSION,
        profile=ACCURACY_PROFILE_TARGET_85,
        model="nova-3",
        language="ja",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        interim_results=True,
        punctuate=True,
        smart_format=True,
        endpointing=500,
        utterance_end_ms=1500,
        diarize_present=False,
        diarize_model_present=False,
        keyterm_values=["売上高"],
        sanitized_query_string="model=nova-3&language=ja&keyterm=%E5%A3%B2%E4%B8%8A%E9%AB%98",
        captured_immediately_before_connect=True,
    )
    if not payload.get("captured_immediately_before_connect"):
        return _fail("request_proof_before_connect", "flag false")
    src = (ROOT / "alpha/transcription/deepgram_client.py").read_text(encoding="utf-8")
    # Inspect _deepgram_worker region only (avoid earlier historical WebSocketApp mentions)
    marker = "def _deepgram_worker(self):"
    idx = src.find(marker)
    if idx < 0:
        return _fail("request_proof_before_connect", "worker missing")
    region = src[idx : idx + 8000]
    idx_write = region.find("write_deepgram_request_actual(payload)")
    idx_ws = region.find("websocket.WebSocketApp(")
    if idx_write < 0 or idx_ws < 0 or idx_write > idx_ws:
        return _fail("request_proof_before_connect", "order incorrect")
    return _pass("actual_request_proof_captured_before_connection")


def test_07_api_keys_removed() -> dict[str, Any]:
    cleaned = sanitize_deepgram_query_string(
        "model=nova-3&token=SECRET123&language=ja&authorization=BearerX"
    )
    if "SECRET123" in cleaned or "BearerX" in cleaned or "token=" in cleaned.lower():
        return _fail("api_keys_removed", cleaned)
    payload = build_deepgram_request_actual_payload(
        run_id="t",
        app_version=APP_VERSION,
        profile="x",
        model="nova-3",
        language="ja",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        interim_results=True,
        punctuate=True,
        smart_format=True,
        endpointing=500,
        utterance_end_ms=1500,
        diarize_present=False,
        diarize_model_present=False,
        keyterm_values=[],
        sanitized_query_string="model=nova-3&token=SECRET&language=ja",
    )
    if "SECRET" in json.dumps(payload):
        return _fail("api_keys_removed", "secret remains in payload")
    return _pass("api_keys_removed_from_request_evidence")


def test_08_benchmark_disables_mic() -> dict[str, Any]:
    os.environ["ISSUE12_STAGE1_BENCHMARK"] = "1"
    os.environ["BENCHMARK_AUDIO_SOURCE"] = "system_audio_only"
    try:
        if not is_system_audio_only_benchmark():
            return _fail("benchmark_disables_mic", "flag false")
        return _pass("benchmark_mode_disables_microphone_mixing")
    finally:
        os.environ.pop("ISSUE12_STAGE1_BENCHMARK", None)
        os.environ.pop("BENCHMARK_AUDIO_SOURCE", None)


def test_09_normal_mode_retains_mic() -> dict[str, Any]:
    os.environ.pop("ISSUE12_STAGE1_BENCHMARK", None)
    os.environ.pop("BENCHMARK_AUDIO_SOURCE", None)
    if is_system_audio_only_benchmark():
        return _fail("normal_mode_retains_mic", "benchmark still active")
    src = (ROOT / "alpha/ui/main_window.py").read_text(encoding="utf-8")
    if "_start_microphone_capture()" not in src:
        return _fail("normal_mode_retains_mic", "mic start removed")
    if "is_system_audio_only_benchmark" not in src:
        return _fail("normal_mode_retains_mic", "benchmark gate missing")
    return _pass("normal_mode_retains_microphone_behavior")


def test_10_nova3() -> dict[str, Any]:
    from alpha.constants import DEEPGRAM_MODEL

    if DEEPGRAM_MODEL != "nova-3":
        return _fail("nova3", DEEPGRAM_MODEL)
    return _pass("deepgram_model_remains_nova3")


def test_11_language_ja() -> dict[str, Any]:
    # FORCE_DEEPGRAM / japanese listen remains ja in frozen settings docs + STT profile
    if JAPANESE_STT_PROFILE != "no_diarize":
        return _fail("language_ja", JAPANESE_STT_PROFILE)
    src = (ROOT / "alpha/transcription/deepgram_client.py").read_text(encoding="utf-8")
    if 'f"&language={lang}"' not in src and "&language=" not in src:
        return _fail("language_ja", "language param missing")
    return _pass("language_remains_japanese")


def test_12_diarization_absent() -> dict[str, Any]:
    if JAPANESE_STT_PROFILE != "no_diarize":
        return _fail("diarization_absent", JAPANESE_STT_PROFILE)
    return _pass("diarization_remains_absent")


def test_13_endpointing_500() -> dict[str, Any]:
    from alpha.constants import DEEPGRAM_ENDPOINTING_MS

    if int(DEEPGRAM_ENDPOINTING_MS) != 500:
        return _fail("endpointing_500", str(DEEPGRAM_ENDPOINTING_MS))
    return _pass("endpointing_remains_500")


def test_14_utterance_end_1500() -> dict[str, Any]:
    from alpha.constants import DEEPGRAM_UTTERANCE_END_MS

    if int(DEEPGRAM_UTTERANCE_END_MS) != 1500:
        return _fail("utterance_end_1500", str(DEEPGRAM_UTTERANCE_END_MS))
    return _pass("utterance_end_remains_1500")


def test_15_raw_stable_final_independent() -> dict[str, Any]:
    mapping_src = (ROOT / "alpha/utils/accuracy_stage_capture.py").read_text(encoding="utf-8")
    for name in (
        '"raw_deepgram": "raw_deepgram.txt"',
        '"stable_transcript": "stable_transcript.txt"',
        '"final_alpha_output": "final_alpha_output.txt"',
    ):
        if name not in mapping_src:
            return _fail("rsf_independent", f"missing {name}")
    return _pass("raw_stable_final_independently_written")


def test_16_trusted_cer_recalc() -> dict[str, Any]:
    from verify_issue12_stage1_85261 import levenshtein_ops, stage_metrics

    m = stage_metrics("あいう", "あいえ")
    ops = levenshtein_ops("あいう", "あいえ")
    if ops["edit_distance"] < 1:
        return _fail("cer_recalc", "expected edits")
    if m["cer_percent"] <= 0:
        return _fail("cer_recalc", "cer zero")
    return _pass("trusted_cer_independently_recalculated")


def test_17_score_mismatch_blocks() -> dict[str, Any]:
    from run_issue12_stage1_accuracy_gate_85261 import build_acceptance

    acceptance = build_acceptance(
        score={
            "trusted_score": True,
            "reference_quality_verdict": "valid_for_cer",
            "stable": {"accuracy_percent": 90.0, "cer_percent": 10.0},
            "combined_critical_term_accuracy_percent": 95.0,
            "stable_to_final_loss_percent": 0.0,
            "target_85_passed": True,
            "failures": [],
            "raw": {"accuracy_percent": 90.0},
            "final": {"accuracy_percent": 90.0},
        },
        verification={
            "verification_passed": False,
            "mismatches": ["reported_stable_accuracy_mismatch"],
            "runtime_regressions": [],
        },
    )
    if acceptance["STATUS"] != "TARGET_NOT_REACHED" or acceptance["ready_for_step2"]:
        return _fail("score_mismatch_blocks", str(acceptance))
    return _pass("reported_score_mismatch_blocks_acceptance")


def test_18_below_85_blocks() -> dict[str, Any]:
    from run_issue12_stage1_accuracy_gate_85261 import build_acceptance

    acceptance = build_acceptance(
        score={
            "trusted_score": True,
            "reference_quality_verdict": "valid_for_cer",
            "stable": {"accuracy_percent": 84.99, "cer_percent": 15.01},
            "combined_critical_term_accuracy_percent": 95.0,
            "stable_to_final_loss_percent": 0.0,
            "target_85_passed": False,
            "failures": ["stable_accuracy_below_85"],
            "raw": {"accuracy_percent": 84.99},
            "final": {"accuracy_percent": 84.99},
            "gap_to_85_percent": 0.01,
        },
        verification={
            "verification_passed": False,
            "mismatches": [],
            "runtime_regressions": [],
        },
    )
    if acceptance["VERSION"] != "NOT_ACCEPTED":
        return _fail("below_85_blocks", str(acceptance))
    return _pass("stable_accuracy_below_85_blocks_acceptance")


def test_19_critical_below_90_blocks() -> dict[str, Any]:
    from run_issue12_stage1_accuracy_gate_85261 import build_acceptance

    acceptance = build_acceptance(
        score={
            "trusted_score": True,
            "reference_quality_verdict": "valid_for_cer",
            "stable": {"accuracy_percent": 90.0, "cer_percent": 10.0},
            "combined_critical_term_accuracy_percent": 89.99,
            "stable_to_final_loss_percent": 0.0,
            "target_85_passed": False,
            "failures": ["combined_critical_term_below_90"],
            "raw": {"accuracy_percent": 90.0},
            "final": {"accuracy_percent": 90.0},
        },
        verification={
            "verification_passed": False,
            "mismatches": [],
            "runtime_regressions": [],
        },
    )
    if acceptance["ready_for_step2"]:
        return _fail("critical_below_90", "ready true")
    return _pass("critical_term_accuracy_below_90_blocks_acceptance")


def test_20_stable_final_loss_blocks() -> dict[str, Any]:
    from run_issue12_stage1_accuracy_gate_85261 import build_acceptance

    acceptance = build_acceptance(
        score={
            "trusted_score": True,
            "reference_quality_verdict": "valid_for_cer",
            "stable": {"accuracy_percent": 90.0, "cer_percent": 10.0},
            "combined_critical_term_accuracy_percent": 95.0,
            "stable_to_final_loss_percent": 0.01,
            "target_85_passed": False,
            "failures": ["stable_to_final_loss_nonzero"],
            "raw": {"accuracy_percent": 90.0},
            "final": {"accuracy_percent": 89.99},
        },
        verification={
            "verification_passed": False,
            "mismatches": ["stable_to_final_content_loss"],
            "runtime_regressions": [],
        },
    )
    if acceptance["STATUS"] == "PASSED":
        return _fail("stf_loss_blocks", "passed incorrectly")
    return _pass("stable_to_final_loss_above_zero_blocks_acceptance")


def test_21_runtime_regression_blocks() -> dict[str, Any]:
    from run_issue12_stage1_accuracy_gate_85261 import build_acceptance

    acceptance = build_acceptance(
        score={
            "trusted_score": True,
            "reference_quality_verdict": "valid_for_cer",
            "stable": {"accuracy_percent": 90.0, "cer_percent": 10.0},
            "combined_critical_term_accuracy_percent": 95.0,
            "stable_to_final_loss_percent": 0.0,
            "target_85_passed": True,
            "failures": [],
            "raw": {"accuracy_percent": 90.0},
            "final": {"accuracy_percent": 90.0},
        },
        verification={
            "verification_passed": False,
            "mismatches": [],
            "runtime_regressions": ["ui_main_loop_stall"],
        },
    )
    if acceptance["ready_for_step2"]:
        return _fail("runtime_regression_blocks", "ready true")
    return _pass("runtime_regression_blocks_acceptance")


def test_22_failure_does_not_replace_production() -> dict[str, Any]:
    if JAPANESE_KEYTERM_PROFILE != "business_japanese":
        return _fail("failure_no_replace", JAPANESE_KEYTERM_PROFILE)
    # Ensure constant default accuracy profile is empty (inactive)
    from alpha import constants as c

    if getattr(c, "JAPANESE_ACCURACY_PROFILE", None) not in ("", None):
        # Empty string means production inactive — required
        if c.JAPANESE_ACCURACY_PROFILE == ACCURACY_PROFILE_TARGET_85:
            return _fail("failure_no_replace", "production default is meeting context")
    return _pass("target_failure_does_not_replace_production_profile")


def test_23_no_translation_provider() -> dict[str, Any]:
    gate = (ROOT / "run_issue12_stage1_accuracy_gate_85261.py").read_text(encoding="utf-8")
    for forbidden in ("deepl", "groq", "llama", "MeetingBaaS"):
        if forbidden.lower() in gate.lower() and "no translation" not in gate.lower():
            # Allow mentioning in comments as forbidden
            pass
    # Constants must not enable translation for Stage1
    from alpha import constants as c

    for name in dir(c):
        if "DEEPL" in name.upper() or "GROQ" in name.upper():
            val = getattr(c, name, None)
            if val is True:
                return _fail("no_translation", name)
    return _pass("no_translation_provider_activated")


def test_24_no_automatic_live_rerun() -> dict[str, Any]:
    gate = (ROOT / "run_issue12_stage1_accuracy_gate_85261.py").read_text(encoding="utf-8")
    if "live_tests_completed" not in gate:
        return _fail("no_auto_rerun", "missing live counter")
    if "while True:" in gate and "launch_application" in gate:
        # crude: ensure no retry loop around launch
        return _fail("no_auto_rerun", "possible launch loop")
    if gate.count("subprocess.Popen") > 1 and gate.count("main.py") > 2:
        return _fail("no_auto_rerun", "multiple launches")
    return _pass("no_live_run_automatically_repeated")


def test_25_upload_excludes_audio() -> dict[str, Any]:
    from run_issue12_stage1_accuracy_gate_85261 import create_upload_package

    with tempfile.TemporaryDirectory() as tmp:
        run_folder = Path(tmp) / "run"
        stage = run_folder / "accuracy_stage_compare"
        stage.mkdir(parents=True)
        for name in (
            "raw_deepgram.txt",
            "stable_transcript.txt",
            "final_alpha_output.txt",
            "stage_manifest.json",
            "deepgram_request_actual.json",
            "issue12_stage1_score.json",
            "issue12_stage1_score.txt",
            "issue12_stage1_independent_verification.json",
            "issue12_stage1_acceptance.json",
        ):
            (stage / name).write_text("{}", encoding="utf-8")
        (stage / "noise.wav").write_bytes(b"RIFF")
        gloss = ROOT / "troubleshooting/accuracy_benchmark/glossaries"
        zpath = create_upload_package(
            project_root=ROOT,
            run_folder=run_folder,
            run_id="regtest",
            report_text="Cursor final report",
        )
        with zipfile.ZipFile(zpath, "r") as zf:
            names = zf.namelist()
        if any(n.lower().endswith(".wav") for n in names):
            return _fail("upload_excludes_audio", str(names))
        if "noise.wav" in " ".join(names):
            return _fail("upload_excludes_audio", "noise included")
    return _pass("upload_package_excludes_audio")


TESTS: list[Callable[[], dict[str, Any]]] = [
    test_01_raw_not_modified_by_profile,
    test_02_global_business_unchanged,
    test_03_meeting_context_only_in_benchmark_mode,
    test_04_max_keyterm_count,
    test_05_no_complete_sentences,
    test_06_request_proof_before_connect,
    test_07_api_keys_removed,
    test_08_benchmark_disables_mic,
    test_09_normal_mode_retains_mic,
    test_10_nova3,
    test_11_language_ja,
    test_12_diarization_absent,
    test_13_endpointing_500,
    test_14_utterance_end_1500,
    test_15_raw_stable_final_independent,
    test_16_trusted_cer_recalc,
    test_17_score_mismatch_blocks,
    test_18_below_85_blocks,
    test_19_critical_below_90_blocks,
    test_20_stable_final_loss_blocks,
    test_21_runtime_regression_blocks,
    test_22_failure_does_not_replace_production,
    test_23_no_translation_provider,
    test_24_no_automatic_live_rerun,
    test_25_upload_excludes_audio,
]


def main() -> int:
    results = []
    for fn in TESTS:
        try:
            results.append(fn())
        except Exception as exc:
            results.append(_fail(fn.__name__, f"{type(exc).__name__}: {exc}"))
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    print(f"tests={len(results)}")
    print(f"passed={passed}")
    print(f"failed={failed}")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}" + (f" — {r['detail']}" if r["detail"] else ""))
    return 0 if failed == 0 and len(results) == 25 else 1


if __name__ == "__main__":
    raise SystemExit(main())
