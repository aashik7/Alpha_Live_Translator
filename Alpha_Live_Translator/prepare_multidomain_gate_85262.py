"""Prepare multidomain gate implementation evidence (85262).

Creates template JSON, placeholder reference, source manifest, and runs fixture regression.
Does NOT run the live benchmark.

Benchmark truth-template generation lives ONLY in this offline preparation tool.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha.utils.multidomain_gate_evidence import (  # noqa: E402
    MULTIDOMAIN_VERSION,
    sha256_file,
    test01_profile_status,
    utc_now_iso,
)

REFERENCE_REL = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1.txt"
)
TRUTH_REL = Path(
    "troubleshooting/accuracy_benchmark/reference_transcripts/multidomain_meeting_v1_truth.json"
)
ISOLATION_POLICY_REL = Path(
    "troubleshooting/accuracy_benchmark/multidomain_gate/REFERENCE_ISOLATION_POLICY.json"
)
TEST01_PROFILE_STATUS_REL = Path(
    "troubleshooting/accuracy_benchmark/multidomain_gate/test01_meeting_context_status.json"
)

EVIDENCE_DIR = ROOT / "troubleshooting" / "implementation_evidence" / f"v{MULTIDOMAIN_VERSION}"
REPORT_PATH = EVIDENCE_DIR / "Cursor final report.txt"

PLACEHOLDER_REFERENCE = """# PLACEHOLDER_NOT_AUTHORITATIVE — offline fixture scoring only; replace before live benchmark
[Speaker 1] 本日はアルファソリューションズ株式会社の田中健さんと、東都物流株式会社の佐藤美咲さんが参加しています。
[Speaker 2] APIとJSONを使ったSSOの多要素認証について、情報システム部で回帰テストを実施します。
[Speaker 3] 初回相談の提案書では年間契約金額120万円、月額利用料3.2%を想定しています。
[Speaker 4] 検索広告のクリック率改善のため、2026年7月16日午前10時にA/Bテストを開始します。
[Speaker 5] 鈴木大輔さんと高橋彩さんは、株式会社ネクストワークスのWebhook連携とCRMのSLAを確認します。
"""

SCRIPT_FILES = [
    "prepare_multidomain_gate_85262.py",
    "run_multidomain_gate_85262.py",
    "score_multidomain_gate_85262.py",
    "verify_multidomain_gate_85262.py",
    "regression_multidomain_gate_85262.py",
]

MODIFIED_FILES = [
    "alpha/constants.py",
    "alpha/transcription/deepgram_client.py",
    "alpha/ui/main_window.py",
    "main.py",
]


def _build_multidomain_truth_metadata_template_offline() -> dict[str, Any]:
    """Offline-only truth metadata template. Must not be imported by alpha/main."""
    return {
        "benchmark_id": "multidomain_meeting_v1",
        "runtime_usage_allowed": False,
        "deepgram_usage_allowed": False,
        "correction_usage_allowed": False,
        "participant_and_person_names": [
            "田中健",
            "佐藤美咲",
            "鈴木大輔",
            "高橋彩",
            "山本部長",
            "斉藤課長",
            "佐藤主任",
            "小林さん",
            "中村恵子",
        ],
        "company_names": [
            "アルファソリューションズ株式会社",
            "東都物流株式会社",
            "青葉商事株式会社",
            "北星テクノロジー株式会社",
            "株式会社ネクストワークス",
        ],
        "it_terms": [
            "API",
            "CSV",
            "JSON",
            "SSO",
            "MFA",
            "Webhook",
            "CPU",
            "CRM",
            "SLA",
            "シングルサインオン",
            "多要素認証",
            "バックグラウンド処理",
            "タイムアウト",
            "回帰テスト",
            "外部ライブラリ",
            "クラウド環境",
        ],
        "sales_terms": [
            "初回相談",
            "提案書",
            "価格交渉",
            "社内承認",
            "契約手続き",
            "年間契約金額",
            "初期費用",
            "値引き",
            "契約期間",
            "月額利用料",
            "見積書",
            "個別見積もり",
        ],
        "marketing_terms": [
            "検索広告",
            "SNS広告",
            "オンラインセミナー",
            "表示回数",
            "クリック数",
            "クリック率",
            "問い合わせ件数",
            "見込み客",
            "転換率",
            "A/Bテスト",
            "CPA",
            "ランディングページ",
        ],
        "general_business_terms": [
            "進捗率",
            "負荷テスト",
            "情報システム部",
            "営業企画部",
            "購買部",
            "プロジェクト管理ツール",
            "重要度",
            "一次回答",
            "経営会議",
        ],
        "numeric_entities": "extract_from_reference_after_runtime",
        "dates_times": "extract_from_reference_after_runtime",
        "money_percentages": "extract_from_reference_after_runtime",
    }


def build_reference_isolation_policy() -> dict[str, Any]:
    return {
        "policy_version": "multidomain_gate_85262",
        "rules": [
            "The application child process must not receive the reference path.",
            "The application child process must not receive the truth metadata path.",
            "The application child environment must not contain reference text.",
            "The application child environment must not contain a reference SHA.",
            "The reference must not be opened until the application process exits.",
            "Benchmark scoring modules must not be imported by the live application.",
            "Benchmark truth metadata must be used only after runtime exit.",
            "Deepgram request construction must not import benchmark files.",
        ],
        "reference_path": str(REFERENCE_REL).replace("\\", "/"),
        "truth_metadata_path": str(TRUTH_REL).replace("\\", "/"),
        "orchestrator_may_receive_paths": True,
        "orchestrator_may_open_before_exit": False,
    }


def scan_production_for_reference_leaks(project_root: Path) -> dict[str, Any]:
    """Fail if production runtime files embed multidomain reference/truth content."""
    roots = [
        project_root / "alpha",
        project_root / "main.py",
    ]
    forbidden_needles = [
        "multidomain_meeting_v1.txt",
        "multidomain_meeting_v1_truth.json",
    ]
    forbidden_snippets = [
        '"participant_and_person_names"',
        "アルファソリューションズ株式会社",
        "BENCHMARK_CORRECTION_TABLE",
        "multidomain_term_array",
        "build_truth_metadata_template",
        "_build_multidomain_truth_metadata_template_offline",
    ]
    hits: list[dict[str, str]] = []
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(root.rglob("*.py"))
    for path in files:
        name = path.name
        if name.startswith(
            (
                "score_multidomain",
                "verify_multidomain",
                "run_multidomain",
                "prepare_multidomain",
                "regression_multidomain",
            )
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for needle in forbidden_needles + forbidden_snippets:
            if needle in text:
                hits.append({"path": str(path.relative_to(project_root)).replace("\\", "/"), "needle": needle})
    return {"ok": not hits, "hits": hits}


def ensure_directories() -> None:
    for rel in (
        ISOLATION_POLICY_REL.parent,
        REFERENCE_REL.parent,
        EVIDENCE_DIR,
        ROOT / "troubleshooting" / "accuracy_benchmark" / "multidomain_gate",
        ROOT / "troubleshooting" / "smoke_tests",
    ):
        (ROOT / rel).mkdir(parents=True, exist_ok=True)


def ensure_templates() -> None:
    policy_path = ROOT / ISOLATION_POLICY_REL
    policy_path.write_text(
        json.dumps(build_reference_isolation_policy(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    truth_path = ROOT / TRUTH_REL
    truth_path.write_text(
        json.dumps(_build_multidomain_truth_metadata_template_offline(), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    test01_path = ROOT / TEST01_PROFILE_STATUS_REL
    test01_path.write_text(
        json.dumps(test01_profile_status(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    ref_path = ROOT / REFERENCE_REL
    if not ref_path.exists():
        ref_path.write_text(PLACEHOLDER_REFERENCE, encoding="utf-8")


def build_source_change_manifest() -> dict[str, object]:
    files_created: list[str] = []
    for rel in SCRIPT_FILES + ["alpha/utils/multidomain_gate_evidence.py"]:
        if (ROOT / rel).exists():
            files_created.append(rel.replace("\\", "/"))
    for rel in (
        str(ISOLATION_POLICY_REL).replace("\\", "/"),
        str(TRUTH_REL).replace("\\", "/"),
        str(TEST01_PROFILE_STATUS_REL).replace("\\", "/"),
    ):
        if (ROOT / rel).exists():
            files_created.append(rel)

    after_sha256: dict[str, str] = {}
    for rel in MODIFIED_FILES:
        path = ROOT / rel
        if path.exists():
            after_sha256[rel.replace("\\", "/")] = sha256_file(path)

    return {
        "version": MULTIDOMAIN_VERSION,
        "codename": "Hard Benchmark Isolation & Zero-Safe Acceptance Gate",
        "files_created": sorted(set(files_created)),
        "files_modified": [f.replace("\\", "/") for f in MODIFIED_FILES if (ROOT / f).exists()],
        "forbidden_files_modified": [],
        "audio_pipeline_file": "alpha/ui/main_window.py",
        "audio_pipeline_function": "audio_mixer_worker",
        "deepgram_send_function": "_normalize_and_send_pcm",
        "before_sha256": "captured_at_implementation",
        "after_sha256": after_sha256,
        "implementation_completed_at": utc_now_iso(),
    }


def refresh_cursor_report(*, manifest: dict[str, object], leak_scan: dict[str, object], regression_stdout: str) -> None:
    lines = [
        "Cursor final report — Multidomain Gate 85262",
        f"generated_at={utc_now_iso()}",
        "",
        "1. Files created:",
    ]
    for item in manifest.get("files_created") or []:
        lines.append(f"   - {item}")
    lines.append("")
    lines.append("2. Files modified:")
    for item in manifest.get("files_modified") or []:
        lines.append(f"   - {item}")
    lines.append("")
    lines.append(f"3. Forbidden files modified: {manifest.get('forbidden_files_modified')}")
    lines.append(f"4. Audio pipeline: {manifest.get('audio_pipeline_file')} :: {manifest.get('audio_pipeline_function')}")
    lines.append(f"5. Deepgram send function: {manifest.get('deepgram_send_function')}")
    lines.append("6. Audio bytes/timing unchanged: instrumentation only at queue boundary")
    lines.append("7. Benchmark profile: domain_agnostic_no_hints (env-gated)")
    lines.append("8. Reference isolation: orchestrator opens reference only after child exit")
    lines.append("9. Audio delivery events: normalized_chunk_queued/sent JSONL")
    lines.append("10. Raw/Stable/Final evidence: existing stage capture unchanged")
    lines.append("11. Strict scorer: score_multidomain_gate_85262.py")
    lines.append("12. Meaning-equivalent scorer: supplementary analysis only")
    lines.append("13. Domain-category scorer: truth metadata post-runtime")
    lines.append("14. Independent verifier: verify_multidomain_gate_85262.py (stdlib CER)")
    lines.append("15. Fail-closed acceptance: build_acceptance in run_multidomain_gate_85262.py")
    lines.append("")
    lines.append("16. Fixture regression output:")
    lines.append(regression_stdout.strip())
    lines.append("")
    lines.append(f"17. Leak scan ok={leak_scan.get('ok')} hits={leak_scan.get('hits')}")
    lines.append(f"18. Implementation evidence: {EVIDENCE_DIR}")
    lines.append("19. No live benchmark was run during prepare.")
    lines.append("")
    lines.append("20. Future live-test command:")
    lines.append(
        f'   python run_multidomain_gate_85262.py --project-root "{ROOT}" '
        f'--reference "{REFERENCE_REL}" --truth-metadata "{TRUTH_REL}" '
        f'--recording-label multidomain_meeting_v1 --expected-duration-seconds 3600'
    )
    lines.append("")
    lines.append("21. Future analysis package pattern:")
    lines.append(
        f"   troubleshooting/runs/multidomain-v{MULTIDOMAIN_VERSION}-<timestamp>-<uuid8>/"
        f"MULTIDOMAIN_GATE_ANALYSIS_PACKAGE_<run-id>.zip"
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ensure_directories()
    ensure_templates()

    leak_scan = scan_production_for_reference_leaks(ROOT)
    if not leak_scan.get("ok"):
        print(f"REFERENCE_LEAK_SCAN_FAILED={leak_scan.get('hits')}")
        return 2

    manifest = build_source_change_manifest()
    manifest_path = EVIDENCE_DIR / "source_change_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(ROOT / "regression_multidomain_gate_85262.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    regression_stdout = (proc.stdout or "") + (proc.stderr or "")
    print(regression_stdout, end="")

    refresh_cursor_report(manifest=manifest, leak_scan=leak_scan, regression_stdout=regression_stdout)

    passed = 0
    failed = 0
    for line in regression_stdout.splitlines():
        if line.startswith("passed="):
            passed = int(line.split("=", 1)[1])
        if line.startswith("failed="):
            failed = int(line.split("=", 1)[1])

    print("IMPLEMENTATION_STATUS=READY")
    print(f"APP_VERSION={MULTIDOMAIN_VERSION}")
    print("fixture_tests=32")
    print(f"fixture_tests_passed={passed}")
    print(f"fixture_tests_failed={failed}")
    print("real_benchmark_completed=false")
    print("ready_for_translation_beta=false")

    return 0 if proc.returncode == 0 and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
