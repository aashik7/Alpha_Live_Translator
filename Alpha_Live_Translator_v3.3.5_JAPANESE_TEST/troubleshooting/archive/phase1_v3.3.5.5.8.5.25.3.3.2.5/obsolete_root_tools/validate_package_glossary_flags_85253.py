"""Packaging regression: optional glossary flags (8.5.25.3)."""

from __future__ import annotations

import ast
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import package_latest_troubleshooting_run as pkg

OUT_DIR = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3")
OUT_FILE = OUT_DIR / "validate_package_glossary_flags_85253.txt"
STAGE_FILES = (
    "accuracy_stage_compare/raw_deepgram.txt",
    "accuracy_stage_compare/raw_deepgram_events.jsonl",
    "accuracy_stage_compare/stable_assembler_only.txt",
    "accuracy_stage_compare/stable_assembler_events.jsonl",
    "accuracy_stage_compare/final_alpha_output.txt",
    "accuracy_stage_compare/deepgram_request_snapshot.json",
    "accuracy_stage_compare/audio_delivery_summary.json",
    "accuracy_stage_compare/stage_manifest.json",
    "accuracy_stage_compare/three_stage_accuracy_report.json",
    "accuracy_stage_compare/three_stage_accuracy_report.txt",
)


def _glossary_flag_initialized_in_source() -> bool:
    source = Path("package_latest_troubleshooting_run.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            init_line = -1
            glossary_cond = 10**9
            for i, child in enumerate(node.body):
                if (
                    isinstance(child, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "glossary_included"
                        for t in child.targets
                    )
                    and isinstance(child.value, ast.Constant)
                    and child.value.value is False
                ):
                    init_line = i
                if isinstance(child, ast.If):
                    for sub in ast.walk(child):
                        if (
                            isinstance(sub, ast.Name)
                            and sub.id == "glossary_dir"
                            and isinstance(sub.ctx, ast.Load)
                        ):
                            glossary_cond = min(glossary_cond, i)
            return init_line >= 0 and init_line < glossary_cond
    return False


def _make_fixture_tree(base: Path, *, with_glossary: bool, with_stage: bool) -> tuple[Path, Path]:
    troubleshooting = base / "troubleshooting"
    run_folder = troubleshooting / "runs" / "pkg-regression-85253"
    run_folder.mkdir(parents=True, exist_ok=True)
    (run_folder / "RUN_MANIFEST.json").write_text('{"run_type":"validation"}', encoding="utf-8")
    (run_folder / "transcripts").mkdir(exist_ok=True)
    (run_folder / "logs").mkdir(exist_ok=True)
    if with_stage:
        stage = run_folder / "accuracy_stage_compare"
        stage.mkdir(exist_ok=True)
        for rel in STAGE_FILES:
            path = run_folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
    gloss_dir = troubleshooting / "accuracy_benchmark" / "glossaries"
    gloss_dir.mkdir(parents=True, exist_ok=True)
    if with_glossary:
        (gloss_dir / "corporate_ir_glossary_test.json").write_text("{}", encoding="utf-8")
    return run_folder, troubleshooting


def _run_package_scenario(*, with_glossary: bool) -> dict[str, object]:
    base = Path(tempfile.mkdtemp(prefix="pkg-glossary-85253-"))
    try:
        run_folder, troubleshooting = _make_fixture_tree(
            base, with_glossary=with_glossary, with_stage=True
        )
        with patch(
            "alpha.utils.evidence_pointer_finalize.finalize_upload_package_pointer",
            return_value=None,
        ):
            rc = pkg.main(
                run_folder_override=run_folder,
                troubleshooting_root=troubleshooting,
            )
        upload_dir = run_folder / "upload_package"
        zips = sorted(upload_dir.glob("UPLOAD_PACKAGE_*.zip"))
        index_files = sorted(upload_dir.glob("UPLOAD_PACKAGE_INDEX.txt"))
        if not zips or not index_files:
            return {
                "ok": False,
                "rc": rc,
                "error": "package artifacts missing",
            }
        zip_path = zips[-1]
        index_text = index_files[-1].read_text(encoding="utf-8")
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        stage_in_zip = [rel for rel in STAGE_FILES if rel in names]
        glossary_in_zip = any(name.startswith("glossaries/") for name in names)
        expected_flag = "corporate_ir_glossary_included=true" if with_glossary else "corporate_ir_glossary_included=false"
        return {
            "ok": rc == 0 and expected_flag in index_text and not any("sk-" in line for line in index_text.splitlines()),
            "rc": rc,
            "zip_path": str(zip_path),
            "index_has_expected_flag": expected_flag in index_text,
            "glossary_in_zip": glossary_in_zip,
            "stage_files_in_zip": stage_in_zip,
            "stage_file_count": len(stage_in_zip),
            "wav_in_zip": any(name.lower().endswith(".wav") for name in names),
            "env_in_zip": any(name.endswith(".env") for name in names),
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    checks: dict[str, bool] = {
        "glossary_flag_initialized_before_conditionals": _glossary_flag_initialized_in_source(),
        "helper_exists": hasattr(pkg, "_glossary_included_in_package"),
    }
    absent = _run_package_scenario(with_glossary=False)
    present = _run_package_scenario(with_glossary=True)
    checks["scenario_a_no_unbound_local"] = absent.get("rc") == 0 and bool(absent.get("ok"))
    checks["scenario_a_false_summary"] = bool(absent.get("index_has_expected_flag"))
    checks["scenario_a_no_glossary_in_zip"] = not bool(absent.get("glossary_in_zip"))
    checks["scenario_b_true_summary"] = bool(present.get("index_has_expected_flag"))
    checks["scenario_b_glossary_in_zip"] = bool(present.get("glossary_in_zip"))
    checks["scenario_b_package_ok"] = present.get("rc") == 0 and bool(present.get("ok"))
    checks["three_stage_evidence_present"] = int(present.get("stage_file_count", 0)) >= 5
    checks["wav_excluded"] = not bool(absent.get("wav_in_zip")) and not bool(present.get("wav_in_zip"))
    checks["env_excluded"] = not bool(absent.get("env_in_zip")) and not bool(present.get("env_in_zip"))

    failed = [k for k, ok in checks.items() if not ok]
    status = "PASSED" if not failed else "FAILED"
    lines = [
        "VALIDATE_PACKAGE_GLOSSARY_FLAGS_85253",
        f"Result: {status}",
        f"scenario_a_zip={absent.get('zip_path', '')}",
        f"scenario_b_zip={present.get('zip_path', '')}",
        f"scenario_a_stage_files={absent.get('stage_file_count', 0)}",
        f"scenario_b_stage_files={present.get('stage_file_count', 0)}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
