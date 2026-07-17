"""Runtime smoke: three-stage accuracy isolation (8.5.25.3)."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.transcription.deepgram_client import DeepgramClientMixin
from alpha.utils.alpha_output_protection import (
    RUN_TYPE_SMOKE_TEST,
    hash_latest_live_output,
    reset_alpha_export_run_type,
    set_alpha_export_run_type,
    verify_latest_live_unchanged,
    write_smoke_test_alpha_outputs,
)
from alpha.utils.accuracy_stage_capture import (
    finalize_accuracy_stage_artifacts,
    record_assembler_only_event,
    record_raw_deepgram_final,
    reset_accuracy_stage_capture,
    write_deepgram_request_snapshot,
)

OUT_DIR = Path("troubleshooting/smoke_tests/v3.3.5.5.8.5.25.3")
OUT_FILE = OUT_DIR / "runtime_smoke_accuracy_stage_isolation_85253.txt"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    checks: dict[str, bool] = {}
    warnings: list[str] = []

    before_hash = hash_latest_live_output()
    checks["latest_hash_before"] = True

    class _BuildUrlHost(DeepgramClientMixin):
        def __init__(self) -> None:
            self._listen_language = "ja"
            self._jp_keyterms_fallback_used = False

    try:
        smoke_url = _BuildUrlHost()._build_deepgram_url()
        checks["build_deepgram_url_no_network"] = smoke_url.startswith("wss://api.deepgram.com/v1/listen?")
        checks["build_deepgram_url_no_api_key"] = "sk-" not in smoke_url
    except Exception:
        checks["build_deepgram_url_no_network"] = False
        checks["build_deepgram_url_no_api_key"] = False
        warnings.append("build_deepgram_url_startup_failed")

    with tempfile.TemporaryDirectory() as tmp:
        smoke_run = Path(tmp) / "smoke_run"
        (smoke_run / "accuracy_stage_compare").mkdir(parents=True)
        (smoke_run / "transcripts").mkdir(parents=True)

        set_alpha_export_run_type(RUN_TYPE_SMOKE_TEST)
        reset_accuracy_stage_capture("smoke-run-85253", run_folder=smoke_run)

        rid1 = record_raw_deepgram_final(run_id="smoke-run-85253", speaker=2, raw_text="テスト一文です。")
        rid2 = record_raw_deepgram_final(run_id="smoke-run-85253", speaker=2, raw_text="二文目です。")
        checks["raw_events"] = bool(rid1 and rid2)

        e1 = record_assembler_only_event(
            run_id="smoke-run-85253",
            speaker=2,
            assembler_text="テスト一文です。",
            action="append",
        )
        e2 = record_assembler_only_event(
            run_id="smoke-run-85253",
            speaker=2,
            assembler_text="テスト一文です。二文目です。",
            action="revise_previous",
            update_previous=True,
        )
        checks["assembler_events"] = bool(e1 and e2)

        write_deepgram_request_snapshot(
            {
                "run_id": "smoke-run-85253",
                "model": "nova-3",
                "language": "ja",
                "endpointing": 500,
                "utterance_end_ms": 1500,
                "sample_rate": 16000,
                "channels": 1,
            },
            run_folder=smoke_run,
        )

        final_src = smoke_run / "transcripts" / "Alpha_output_FINAL.txt"
        final_src.write_text("[Speaker 2] テスト一文です。二文目です。\n", encoding="utf-8")

        result = finalize_accuracy_stage_artifacts(
            run_folder=smoke_run,
            final_alpha_source_path=final_src,
            run_type="smoke_test",
            run_status="completed",
        )
        checks["finalize_ok"] = bool(result.get("manifest")) and (
            result.get("manifest", {}).get("stage_capture_complete")
            or (
                (smoke_run / "accuracy_stage_compare" / "raw_deepgram.txt").exists()
                and (smoke_run / "accuracy_stage_compare" / "final_alpha_output.txt").exists()
            )
        )
        checks["manifest_complete"] = bool(
            (smoke_run / "accuracy_stage_compare" / "stage_manifest.json").exists()
        )

        smoke_out = write_smoke_test_alpha_outputs(
            "[Speaker 2] smoke fixture only\n",
            run_type=RUN_TYPE_SMOKE_TEST,
            status={"smoke": True},
        )
        checks["smoke_redirect"] = "smoke_tests" in smoke_out.get("smoke_alpha_path", "")
        checks["live_paths_not_updated"] = not smoke_out.get("live_paths_updated", True)

        reset_alpha_export_run_type()

    after_hash = hash_latest_live_output()
    checks["latest_unchanged"] = verify_latest_live_unchanged(before_hash, after_hash)

    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("SMOKE_TEST_LATEST_LIVE_HASH_BEFORE", sha256=before_hash)
        jp_accuracy_log("SMOKE_TEST_LATEST_LIVE_HASH_AFTER", sha256=after_hash)
    except Exception:
        pass

    failed = [k for k, ok in checks.items() if not ok]
    status = "PASSED" if not failed else "FAILED"
    lines = [
        "RUNTIME_SMOKE_ACCURACY_STAGE_ISOLATION_85253",
        f"Result: {status}",
        f"APP_VERSION: {APP_VERSION}",
        f"timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
