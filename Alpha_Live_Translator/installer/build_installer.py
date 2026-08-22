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
import subprocess
import sys
import time
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
        parser = configparser.ConfigParser(interpolation=None)
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


def version_quad(version: str) -> str:
    """The four-part numeric version a Windows version RESOURCE requires.

    `AppVersion` is a human string and one day will be something like
    "1.1.0-rc1"; `VersionInfoVersion` refuses anything but digits and dots. So
    take the leading numeric parts, drop the rest, and pad to four. Getting
    this wrong does not warn -- Inno leaves FileVersion blank, which is exactly
    how the 1.0.1 build shipped with an empty one.
    """
    if not version[:1].isdigit():
        # Otherwise every non-numeric version silently becomes 0.0.0.0 and the
        # read-back check below happily agrees with it.
        raise SystemExit(f"--version must start with a digit, got {version!r}")
    parts: list[str] = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(str(min(int(digits), 65535)))
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])


def read_sign_command() -> str:
    """The signtool command line, if a certificate has been set up.

    Optional on purpose. Signing is the only thing that removes SmartScreen's
    "Windows protected your PC" screen from a downloaded installer, but it
    needs a bought certificate; until there is one the build stays unsigned and
    behaves exactly as before. Configure it as

        [signing]
        command = "C:\\...\\signtool.exe" sign /fd sha256 /tr <url> /td sha256
                  /f "C:\\...\\cert.pfx" /p <password> $f

    in `installer/keys.local.ini` (git ignores that file), or as
    ALPHA_SIGN_COMMAND. `$f` is where Inno substitutes the file to sign and
    must be present.
    """
    command = os.environ.get("ALPHA_SIGN_COMMAND", "").strip()
    if KEYS_FILE.is_file():
        # interpolation=None, because ConfigParser otherwise reads `%` as the
        # start of a substitution and raises on it. A certificate password is
        # exactly the kind of value that contains one.
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(KEYS_FILE, encoding="utf-8")
        if parser.has_section("signing"):
            command = (parser["signing"].get("command", "") or command).strip()
    if not command:
        return ""
    if "$f" not in command:
        # Inno would sign nothing and say nothing. Better to stop here than to
        # hand over an installer that was silently never signed.
        raise SystemExit(
            "the signing command has no $f placeholder, so Inno Setup would "
            "never pass it the file to sign"
        )
    log("signing is configured")
    return command


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


def file_version(path: Path) -> str:
    """The FileVersion out of the exe's own version resource.

    Read back rather than assumed: Inno leaves this EMPTY when
    VersionInfoVersion is missing or not strictly numeric, and says nothing
    about it. The 1.0.1 build shipped with a blank FileVersion for exactly
    that reason.
    """
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.WinDLL("version", use_last_error=True)
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID
        ]
        ver.VerQueryValueW.argtypes = [
            wintypes.LPCVOID, wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT),
        ]

        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        block = wintypes.LPVOID()
        length = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(block), ctypes.byref(length)):
            return ""
        # VS_FIXEDFILEINFO: [0] signature, [1] struct version,
        # [2] dwFileVersionMS, [3] dwFileVersionLS.
        fixed = ctypes.cast(block, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        high, low = fixed[2], fixed[3]
        return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"
    except Exception:
        return ""


DELIVERY_NOTE = """Alpha Live Translator {version} -- how to install

1. Double-click AlphaLiveTranslator-Setup-{version}.exe.
2. Next, Next, Install. No administrator password is needed; it installs for
   your account only, under your AppData folder.
3. Start it from the Start Menu entry "Alpha Live Translator".

IF WINDOWS SAYS "Windows protected your PC"
-------------------------------------------
That screen means Windows does not recognise the file yet, not that anything
is wrong with it. It appears on any newly built app that has not been signed
with a paid certificate.

The reliable way to avoid it is for whoever sends you this file to put it on a
USB stick or a shared network folder instead of emailing or chatting it --
Windows only marks files that arrived from the internet.

If you already downloaded it and see the screen:
  - right-click the .exe, choose Properties,
  - tick "Unblock" at the bottom of the General tab, click OK,
  - then run it again. The screen will not come back.

Or, on the screen itself, click "More info" and then "Run anyway".

IF SOMETHING GOES WRONG
-----------------------
Use the Start Menu entry "Collect diagnostic logs". It writes one zip file to
your Desktop; send that. It contains no passwords or API keys.
"""


def write_delivery_note(output: Path, version: str) -> Path:
    note = output / "README-INSTALL.txt"
    note.write_text(DELIVERY_NOTE.format(version=version), encoding="utf-8", newline="\r\n")
    return note


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
    sign_command = read_sign_command()
    quad = version_quad(args.version)

    ensure_bundle(bundle, args.rebuild)
    verify_bundle(bundle)

    output.mkdir(parents=True, exist_ok=True)
    command = [
        str(iscc),
        f"/DSourceDir={bundle}",
        f"/DDeepgramKey={deepgram}",
        f"/DDeepLKey={deepl}",
        f"/DAppVersion={args.version}",
        f"/DVersionQuad={quad}",
    ]
    if sign_command:
        command += ["/DSignCommand=1", f"/Salphasign={sign_command}"]
    command += [f"/O{output}", str(ISS)]

    log(f"compiling with {iscc.name} (this takes a couple of minutes at lzma2/max)")
    started = time.time()
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        # Keys are on the command line, and so is any certificate password in
        # the signing command. Never echo either back on failure.
        raise SystemExit(
            "Inno Setup failed:\n"
            + "\n".join(
                line for line in (result.stdout + result.stderr).splitlines()
                if not any(s in line for s in ("DeepgramKey", "DeepLKey", "alphasign"))
            )[-3000:]
        )

    # By name, not by picking the last of a glob: with 1.0.0 and 1.0.1 both
    # sitting in the output folder, rebuilding 1.0.0 used to report 1.0.1 --
    # i.e. hand over the previous, stale installer as if it were this build.
    installer = output / f"AlphaLiveTranslator-Setup-{args.version}.exe"
    if not installer.is_file():
        raise SystemExit(
            f"Inno Setup reported success but {installer.name} is not in {output}"
        )
    # Naming it is not enough on its own: a previous build of the same version
    # sitting in the folder would satisfy the check above and be handed over as
    # if it were this one.
    if installer.stat().st_mtime < started - 1:
        raise SystemExit(
            f"{installer.name} is left over from an earlier build -- Inno Setup "
            "exited 0 without writing it"
        )

    stamped = file_version(installer)
    if stamped != quad:
        raise SystemExit(
            f"the version resource reads {stamped or '(empty)'} but should read "
            f"{quad}; VersionInfoVersion did not take effect"
        )

    note = write_delivery_note(output, args.version)
    log("")
    log(f"installer  {installer.stat().st_size // 1024 // 1024} MB  ->  {installer}")
    log(f"version    {stamped}")
    log(f"signed     {'yes' if sign_command else 'NO -- see installer/DELIVERY.md'}")
    log(f"note       {note}")
    log("")
    log("It installs per-user under %LOCALAPPDATA%\\Programs, needs no admin,")
    log("and writes app\\.env itself. Verify it on a machine that has never had")
    log("Python or this repository on it.")
    if not sign_command:
        log("")
        log("Unsigned, so a copy that arrives over the internet will show")
        log("SmartScreen's \"Windows protected your PC\". Hand it over on a USB")
        log("stick or a network share and that screen never appears.")


if __name__ == "__main__":
    main()
