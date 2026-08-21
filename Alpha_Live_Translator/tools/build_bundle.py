"""Build the self-contained Windows bundle for hand-over (sprint item 49).

WHAT THIS PRODUCES
------------------
    <out>/
        python/     embeddable CPython + every locked dependency + tkinter
        app/        alpha/, assets/, main.py, requirements files
        Alpha.bat   launcher

The boss's machine needs no Python, no pip, no admin rights. `.env` goes beside
`app/` and is written by the installer (sprint §11, option C).

WHY OPTION C AND NOT A PyInstaller EXE
---------------------------------------
Every path anchor in the app is `Path(__file__)`, so under a PyInstaller
onefile build they resolve inside `sys._MEIPASS` -- a temp directory deleted on
exit. Two concrete consequences, not hypotheticals: `.env` is looked for in the
temp extract so the operator's API keys are never found, and
`troubleshooting/runs/` evidence is written there and lost when the app closes.
This layout keeps `PROJECT_ROOT` at `<out>/app`, so **no shipped module changes**
-- verified: `PROJECT_ROOT = ...\\app`, `deepgram key: configured`, and the run
evidence lands in `app\\troubleshooting\\runs\\`.

THE TWO TRAPS, BOTH MEASURED RATHER THAN GUESSED
-------------------------------------------------
1. **The embeddable distribution has no tkinter**, and the missing piece is not
   only the obvious one. `_tkinter.pyd`, `tcl86t.dll` and `tk86t.dll` are not
   enough: `tcl86t.dll` also imports **`zlib1.dll`**, and without it the failure
   is `ImportError: DLL load failed while importing _tkinter`, which blames
   `_tkinter` rather than its dependency. Found by reading the DLL's import
   table.

2. **`import site` in the `._pth` breaks isolation.** It puts the USER
   site-packages on `sys.path`, so a package installed for the developer but
   absent from `requirements-lock.txt` works here and fails on the target -- the
   exact "works on my machine" trap. Measured: `networkx` imported from
   `%APPDATA%\\Python\\Python314\\site-packages` until it was removed. pip needs
   `site`, so it is enabled only while installing and taken out again.

Run:  python tools/build_bundle.py [--out DIR] [--keep-cache] [--no-trim]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = "3.14.6"
PY_TAG = "python314"
EMBED_URL = (
    f"https://www.python.org/ftp/python/{PY_VERSION}/"
    f"python-{PY_VERSION}-embed-amd64.zip"
)

# Copied from a full CPython install of the same version. `zlib1.dll` is the
# one that is easy to miss; see the module docstring.
TK_FILES = (
    Path("DLLs") / "_tkinter.pyd",
    Path("DLLs") / "tcl86t.dll",
    Path("DLLs") / "tk86t.dll",
    Path("DLLs") / "zlib1.dll",
)
TK_DIRS = (Path("Lib") / "tkinter", Path("tcl"))

# What the app itself needs. Everything else in the repo -- runs, logs, docs,
# analysis scripts -- stays behind.
APP_ITEMS = (
    "alpha",
    "assets",
    "main.py",
    # The operator's one-button diagnostic collector. Without it a problem on
    # a machine we cannot reach has no way of reaching us.
    "collect_logs.py",
    "requirements.txt",
    "requirements-lock.txt",
)

LAUNCHER = """@echo off
rem Alpha Live Translator launcher. `start ""` so the console window closes
rem immediately and the operator is left with just the app.
cd /d "%~dp0"
start "" "%~dp0python\\pythonw.exe" "%~dp0app\\main.py"
"""


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def kb(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size // 1024
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // 1024


def find_source_python() -> Path:
    """A full CPython install to take tkinter from.

    The running interpreter is usually a venv, whose `base_prefix` points at the
    real install. Checked for the tkinter pieces rather than trusted, so a wrong
    guess fails here with a clear message instead of at the operator's first
    launch.
    """
    override = os.environ.get("ALPHA_SOURCE_PYTHON")
    candidates = [Path(override)] if override else []
    candidates += [Path(sys.base_prefix), Path(sysconfig.get_config_var("installed_base") or sys.base_prefix)]
    for base in candidates:
        if all((base / rel).exists() for rel in TK_FILES + TK_DIRS):
            return base
    searched = "\n  ".join(str(c) for c in candidates)
    raise SystemExit(
        "Could not find a full CPython install carrying tkinter.\n"
        f"Looked in:\n  {searched}\n"
        "Set ALPHA_SOURCE_PYTHON to the install directory (the one with "
        "DLLs\\_tkinter.pyd and tcl\\)."
    )


def fetch_embeddable(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = cache / f"python-{PY_VERSION}-embed-amd64.zip"
    if zip_path.is_file():
        log(f"embeddable already cached ({kb(zip_path)} KB)")
        return zip_path
    log(f"downloading {EMBED_URL}")
    urllib.request.urlretrieve(EMBED_URL, zip_path)
    log(f"downloaded ({kb(zip_path)} KB)")
    return zip_path


def write_pth(py_dir: Path, *, with_site: bool) -> None:
    """`._pth` REPLACES sys.path, so `..\\app` has to be listed explicitly --
    the script's own directory is not added the way it normally would be.

    Written as bytes with explicit CRLF: a shell `printf` turns the `\\a` of
    `..\\app` into a BEL byte and the entry silently becomes `..pp`.
    """
    lines = [f"{PY_TAG}.zip", ".", r"Lib\site-packages", r"..\app"]
    if with_site:
        lines.append("import site")
    (py_dir / f"{PY_TAG}._pth").write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


def build_python(py_dir: Path, cache: Path) -> None:
    log("extracting embeddable CPython")
    py_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(fetch_embeddable(cache)) as zf:
        zf.extractall(py_dir)

    source = find_source_python()
    log(f"adding tkinter from {source}")
    for rel in TK_FILES:
        shutil.copy2(source / rel, py_dir / rel.name)
    for rel in TK_DIRS:
        shutil.copytree(source / rel, py_dir / rel.name, dirs_exist_ok=True)

    # pip needs `site`; the shipped bundle must not have it.
    write_pth(py_dir, with_site=True)
    log("bootstrapping pip")
    get_pip = cache / "get-pip.py"
    if not get_pip.is_file():
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip)
    run(py_dir / "python.exe", str(get_pip), "-q", "--no-warn-script-location")

    lock = REPO_ROOT / "requirements-lock.txt"
    log(f"installing {sum(1 for l in lock.read_text('utf-8').splitlines() if l.strip() and not l.startswith('#'))} locked packages")
    run(py_dir / "python.exe", "-m", "pip", "install", "-q",
        "--no-warn-script-location", "-r", str(lock))

    write_pth(py_dir, with_site=False)
    log("isolation restored (no `import site`)")


def run(*cmd: str | Path) -> None:
    result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"command failed: {' '.join(str(c) for c in cmd)}\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


def copy_app(app_dir: Path) -> None:
    log("copying application")
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


def trim(py_dir: Path) -> None:
    """Remove what the RUNTIME never reads. Nothing here is a judgement call:
    each entry is either a build-time tool, a test suite, or a cache Python
    regenerates on demand.
    """
    before = kb(py_dir)
    removed: list[tuple[str, int]] = []

    def drop(path: Path, label: str) -> None:
        if not path.exists():
            return
        size = kb(path)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        removed.append((label, size))

    # pip and its console scripts: installing is finished, and the bundle is
    # deliberately not something the operator extends.
    drop(py_dir / "Lib" / "site-packages" / "pip", "pip")
    drop(py_dir / "Scripts", "Scripts")
    for name in ("get-pip.py", "pip.exe", "pip3.exe"):
        drop(py_dir / name, name)

    # numpy ships its test suite; ~16 MB that no runtime import touches.
    total = 0
    for tests in (py_dir / "Lib" / "site-packages" / "numpy").rglob("tests"):
        if tests.is_dir():
            total += kb(tests)
            shutil.rmtree(tests)
    if total:
        removed.append(("numpy tests", total))

    # Tcl build artefacts: import libraries and nmake fragments for compiling
    # AGAINST Tcl, never read when merely running it.
    for name in ("nmake", "tcl86t.lib", "tk86t.lib"):
        drop(py_dir / "tcl" / name, f"tcl/{name}")

    # Bytecode caches. Python rewrites these on first import, so the install
    # location must stay writable -- which is why the installer is per-user.
    total = 0
    for cache_dir in py_dir.rglob("__pycache__"):
        if cache_dir.is_dir():
            total += kb(cache_dir)
            shutil.rmtree(cache_dir, ignore_errors=True)
    if total:
        removed.append(("__pycache__", total))

    for label, size in sorted(removed, key=lambda r: -r[1]):
        log(f"  removed {label:<16} {size:>7} KB")
    log(f"trimmed {before - kb(py_dir)} KB total")


def verify(out: Path) -> None:
    """Prove the bundle before handing it on. A build that reports success and
    produces something that will not start is worse than a build that fails.
    """
    log("verifying")
    python = out / "python" / "python.exe"
    checks = (
        ("isolation", "import sys; assert not [p for p in sys.path if 'Roaming' in p], sys.path"),
        ("tkinter", "import tkinter; r=tkinter.Tk(); r.withdraw(); r.destroy()"),
        ("dependencies",
         "import customtkinter, deepl, numpy, sounddevice, websocket, "
         "pyaudiowpatch, PIL, dotenv, requests, psutil"),
        ("app package", "import alpha; from alpha.ui.main_window import AlphaApp"),
        ("project root",
         "from alpha.config import PROJECT_ROOT; "
         "assert PROJECT_ROOT.name == 'app', PROJECT_ROOT"),
        ("pip is gone", "import importlib.util as u; assert u.find_spec('pip') is None"),
    )
    for label, code in checks:
        result = subprocess.run([str(python), "-c", code], capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"verification FAILED at {label}:\n{result.stderr[-1500:]}")
        log(f"  ok  {label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "build" / "AlphaLiveTranslator"))
    parser.add_argument("--cache", default=str(REPO_ROOT / "build" / "_cache"))
    parser.add_argument("--no-trim", action="store_true", help="keep pip, tests and caches")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        log(f"clearing {out}")
        shutil.rmtree(out)

    build_python(out / "python", Path(args.cache).resolve())
    copy_app(out / "app")
    (out / "Alpha.bat").write_text(LAUNCHER, encoding="ascii", newline="\r\n")

    if not args.no_trim:
        trim(out / "python")
    verify(out)

    log("")
    log(f"python  {kb(out / 'python'):>8} KB")
    log(f"app     {kb(out / 'app'):>8} KB")
    log(f"TOTAL   {kb(out):>8} KB   ->  {out}")
    log("")
    log("`.env` is NOT included. The installer writes it beside app\\ (item 49 K3).")


if __name__ == "__main__":
    main()
