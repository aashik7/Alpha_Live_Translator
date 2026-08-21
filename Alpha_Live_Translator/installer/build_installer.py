"""Compile the hand-over installer (sprint item 49, phase 2).

    python installer/build_installer.py [--bundle DIR] [--version 1.0.0]

Runs `tools/build_bundle.py` first unless `--bundle` points at one that already
exists, then hands the result to Inno Setup's compiler.

WHERE THE KEYS COME FROM, AND WHY NOT FROM THE .iss
----------------------------------------------------
`installer/keys.local.ini`, which git ignores. They reach Inno Setup as
command-line `/D` defines and are written into `.env` by the installer at
install time, so they exist in exactly two places: that ignored file, and the
compiled installer the operator receives.

A key committed to a repository is in its history permanently — rewriting the
history does not recall the copies people already cloned. That is why the `.iss`
declares the defines and refuses to compile without them, rather than carrying
a default.

These are deliberately SEPARATE, restricted keys ("K3"), not the development
ones. Anything inside a distributed binary can be extracted; the point is not to
pretend otherwise but to make a leak cost a key that can be revoked on its own.
"""

from __future__ import annotations

import argparse
import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_DIR = REPO_ROOT / "installer"
ISS = INSTALLER_DIR / "alpha.iss"
KEYS_FILE = INSTALLER_DIR / "keys.local.ini"
DEFAULT_BUNDLE = REPO_ROOT / "build" / "AlphaLiveTranslator"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "installer"

ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
)


def log(msg: str) -> None:
    print(f"[installer] {msg}", flush=True)


def find_iscc() -> Path:
    override = os.environ.get("ALPHA_ISCC")
    for candidate in ([Path(override)] if override else []) + list(ISCC_CANDIDATES):
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Inno Setup's compiler (ISCC.exe) was not found.\n"
        "Install Inno Setup 6 from https://jrsoftware.org/isdl.php, or set "
        "ALPHA_ISCC to the full path of ISCC.exe."
    )


def read_keys() -> tuple[str, str]:
    """Keys from `keys.local.ini`, or from the environment as a fallback.

    Fails loudly and specifically. A build that silently produced an installer
    with an empty key would look successful and hand the operator an app that
    cannot transcribe — the failure would surface on the target machine, which
    is the worst possible place for it.
    """
    deepgram = os.environ.get("ALPHA_DEEPGRAM_KEY", "").strip()
    deepl = os.environ.get("ALPHA_DEEPL_KEY", "").strip()

    if KEYS_FILE.is_file():
        parser = configparser.ConfigParser()
        parser.read(KEYS_FILE, encoding="utf-8")
        section = parser["keys"] if parser.has_section("keys") else {}
        deepgram = (section.get("deepgram", "") or deepgram).strip()
        deepl = (section.get("deepl", "") or deepl).strip()

    missing = [n for n, v in (("deepgram", deepgram), ("deepl", deepl)) if not v]
    if missing:
        raise SystemExit(
            f"missing key(s): {', '.join(missing)}\n"
            f"Create {KEYS_FILE} from keys.local.ini.example, or set "
            "ALPHA_DEEPGRAM_KEY / ALPHA_DEEPL_KEY."
        )
    # Never printed. The only signal is the length, which is enough to tell a
    # real key from an empty string or a placeholder.
    log(f"keys loaded (deepgram {len(deepgram)} chars, deepl {len(deepl)} chars)")
    return deepgram, deepl


def ensure_bundle(bundle: Path, rebuild: bool) -> None:
    if bundle.is_dir() and not rebuild:
        log(f"using the existing bundle at {bundle}")
        return
    log("building the bundle first")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "build_bundle.py"), "--out", str(bundle)]
    )
    if result.returncode != 0:
        raise SystemExit("tools/build_bundle.py failed; the installer was not built")


def verify_bundle(bundle: Path) -> None:
    """Check the bundle before wrapping 96 MB of it into an installer."""
    required = (
        bundle / "python" / "pythonw.exe",
        bundle / "python" / "python314._pth",
        bundle / "app" / "main.py",
        bundle / "app" / "alpha" / "config.py",
        bundle / "Alpha.bat",
    )
    missing = [str(p.relative_to(bundle)) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"bundle is incomplete, missing: {', '.join(missing)}")
    stray = bundle / "app" / ".env"
    if stray.is_file():
        # A developer .env left in the bundle would be shipped and would beat
        # the one the installer writes.
        log("removing a stray .env from the bundle (the installer writes it)")
        stray.unlink()
    log("bundle verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the bundle even if present")
    args = parser.parse_args()

    bundle = Path(args.bundle).resolve()
    output = Path(args.output).resolve()
    iscc = find_iscc()
    deepgram, deepl = read_keys()

    ensure_bundle(bundle, args.rebuild)
    verify_bundle(bundle)

    output.mkdir(parents=True, exist_ok=True)
    log(f"compiling with {iscc.name} (this takes a couple of minutes at lzma2/max)")
    result = subprocess.run(
        [
            str(iscc),
            f"/DSourceDir={bundle}",
            f"/DDeepgramKey={deepgram}",
            f"/DDeepLKey={deepl}",
            f"/DAppVersion={args.version}",
            f"/O{output}",
            str(ISS),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Keys are on the command line, so never echo it back on failure.
        raise SystemExit(
            "Inno Setup failed:\n"
            + "\n".join(
                line for line in (result.stdout + result.stderr).splitlines()
                if "DeepgramKey" not in line and "DeepLKey" not in line
            )[-3000:]
        )

    produced = sorted(output.glob("AlphaLiveTranslator-Setup-*.exe"))
    if not produced:
        raise SystemExit(f"Inno Setup reported success but produced nothing in {output}")
    installer = produced[-1]
    log("")
    log(f"installer  {installer.stat().st_size // 1024 // 1024} MB  ->  {installer}")
    log("")
    log("It installs per-user under %LOCALAPPDATA%\\Programs, needs no admin,")
    log("and writes app\\.env itself. Verify it on a machine that has never had")
    log("Python or this repository on it.")


if __name__ == "__main__":
    main()
