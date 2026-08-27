"""Build the in-place update package that a client runs to catch up to this code.

The delivered installer is the only way a machine gets Alpha, and reinstalling
is not an acceptable way to ship a fix: the Inno uninstaller deletes
`app\\user_settings.json`, a fresh install cannot bring back `app\\troubleshooting\\`
(the run evidence -- the only thing that makes a failure on an unreachable
machine diagnosable), and the delivery keys would have to be re-entered.

Only `app\\` differs between builds. `python\\` is the ~96 MB embedded CPython and
is byte-identical from build to build, so the update ships `app\\` alone: 5 MB.

What goes in the payload is `build_bundle.APP_ITEMS`, imported rather than
restated, so the update can never ship a different set of files than the
installer does. That drift is the whole failure mode a second hand-maintained
list would introduce.

Run:  python tools/build_update_package.py [--out DIR] [--zip]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_bundle import APP_ITEMS, REPO_ROOT  # noqa: E402

PAYLOAD_SOURCE = Path(__file__).resolve().parent / "update_payload"
UPDATER_FILES = ("Update_Alpha.bat", "apply_update.py", "README_FIRST.txt")


def app_version() -> str:
    text = (REPO_ROOT / "alpha" / "constants.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise SystemExit("APP_VERSION not found in alpha/constants.py")
    return match.group(1)


def copy_app(app_dir: Path) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    for name in APP_ITEMS:
        src = REPO_ROOT / name
        if not src.exists():
            raise SystemExit(f"missing from the repo: {src}")
        if src.is_dir():
            shutil.copytree(
                src, app_dir / name, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            shutil.copy2(src, app_dir / name)


def manifest(app_dir: Path, version: str) -> dict:
    files = {}
    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[str(path.relative_to(app_dir)).replace("\\", "/")] = digest
    return {
        "app_version": version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "file_count": len(files),
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="output folder (default: build/update)")
    parser.add_argument("--zip", action="store_true", help="also write a .zip beside it")
    args = parser.parse_args()

    version = app_version()
    out = Path(args.out).resolve() if args.out else REPO_ROOT / "build" / "update"
    package = out / f"AlphaLiveTranslator_Update_{version}"

    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    print(f"building update package for {version}")
    copy_app(package / "app")

    for name in UPDATER_FILES:
        source = PAYLOAD_SOURCE / name
        if not source.is_file():
            raise SystemExit(f"missing updater file: {source}")
        shutil.copy2(source, package / name)

    data = manifest(package / "app", version)
    (package / "UPDATE_MANIFEST.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )

    size_kb = sum(p.stat().st_size for p in package.rglob("*") if p.is_file()) // 1024
    print(f"  {data['file_count']} files, {size_kb} KB")
    print(f"  {package}")

    if args.zip:
        # NOT with_suffix: the version is dotted, so `..._3.3.5.5.8.5.26.5.3`
        # has ".3" treated as the suffix and the archive comes out named for a
        # version that does not exist.
        archive = package.parent / (package.name + ".zip")
        if archive.exists():
            archive.unlink()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    zf.write(path, Path(package.name) / path.relative_to(package))
        print(f"  {archive}  ({archive.stat().st_size // 1024} KB)")

    print()
    print("Send the folder (or the zip) to the client. They extract it and")
    print("double-click Update_Alpha.bat. Nothing else has to be installed.")


if __name__ == "__main__":
    main()
