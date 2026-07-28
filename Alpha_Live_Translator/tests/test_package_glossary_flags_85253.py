"""Unit tests for optional glossary packaging flags (8.5.25.3)."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import package_latest_troubleshooting_run as pkg  # noqa: E402


class TestPackageGlossaryFlags85253(unittest.TestCase):
    def test_glossary_helper_absent(self) -> None:
        include_files = [(Path("x.txt"), "transcripts/x.txt")]
        self.assertFalse(pkg._glossary_included_in_package(include_files))

    def test_glossary_helper_present(self) -> None:
        include_files = [(Path("g.json"), "glossaries/corporate_ir_glossary_test.json")]
        self.assertTrue(pkg._glossary_included_in_package(include_files))

    def test_main_glossary_absent_no_unbound_local(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="pkg-unit-absent-"))
        run_folder = base / "troubleshooting" / "runs" / "pkg-unit-absent"
        troubleshooting = base / "troubleshooting"
        run_folder.mkdir(parents=True)
        troubleshooting.mkdir(parents=True, exist_ok=True)
        (run_folder / "RUN_MANIFEST.json").write_text("{}", encoding="utf-8")
        (run_folder / "transcripts").mkdir(exist_ok=True)
        (run_folder / "logs").mkdir(exist_ok=True)
        (troubleshooting / "accuracy_benchmark" / "glossaries").mkdir(parents=True, exist_ok=True)
        with patch(
            "alpha.utils.evidence_pointer_finalize.finalize_upload_package_pointer",
            return_value=None,
        ):
            rc = pkg.main(run_folder_override=run_folder, troubleshooting_root=troubleshooting)
        self.assertEqual(rc, 0)
        index = next((run_folder / "upload_package").glob("UPLOAD_PACKAGE_INDEX.txt"))
        body = index.read_text(encoding="utf-8")
        self.assertIn("corporate_ir_glossary_included=false", body)

    def test_main_glossary_present_after_successful_inclusion(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="pkg-unit-present-"))
        run_folder = base / "troubleshooting" / "runs" / "pkg-unit-present"
        troubleshooting = base / "troubleshooting"
        run_folder.mkdir(parents=True)
        (run_folder / "RUN_MANIFEST.json").write_text("{}", encoding="utf-8")
        (run_folder / "transcripts").mkdir(exist_ok=True)
        (run_folder / "logs").mkdir(exist_ok=True)
        gloss_dir = troubleshooting / "accuracy_benchmark" / "glossaries"
        gloss_dir.mkdir(parents=True, exist_ok=True)
        (gloss_dir / "corporate_ir_glossary_test.json").write_text("{}", encoding="utf-8")
        with patch(
            "alpha.utils.evidence_pointer_finalize.finalize_upload_package_pointer",
            return_value=None,
        ):
            rc = pkg.main(run_folder_override=run_folder, troubleshooting_root=troubleshooting)
        self.assertEqual(rc, 0)
        upload_dir = run_folder / "upload_package"
        index = next(upload_dir.glob("UPLOAD_PACKAGE_INDEX.txt"))
        body = index.read_text(encoding="utf-8")
        self.assertIn("corporate_ir_glossary_included=true", body)
        zip_path = next(upload_dir.glob("UPLOAD_PACKAGE_*.zip"))
        with zipfile.ZipFile(zip_path, "r") as zf:
            self.assertTrue(any(name.startswith("glossaries/") for name in zf.namelist()))


if __name__ == "__main__":
    unittest.main()
