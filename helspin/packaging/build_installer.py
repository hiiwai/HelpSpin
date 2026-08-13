"""Build the Windows installer, start to finish.

Run on Windows, from the repository root, in the project's environment:

    python packaging/build_installer.py

It reads the version from pyproject.toml so nothing has to be edited by hand
for a release, runs PyInstaller, then runs the Inno Setup compiler. The result
is dist/installer/HelSpin-<version>-setup.exe.

Prerequisites, checked below before anything slow happens:
  * pip install pyinstaller
  * Inno Setup 6 (https://jrsoftware.org/isdl.php); iscc.exe on PATH, or at its
    default install location, which this script also looks in.

Nothing here signs the result. An unsigned installer works but trips
SmartScreen and is blocked by the publisher rules on managed machines -- the
very machines this is for. Signing is a separate step with a certificate the
project does not yet have; see the build guide.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"


def read_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("Could not find version in pyproject.toml")
    return match.group(1)


def find_iscc() -> str | None:
    """Locate the Inno Setup compiler on PATH or where it usually installs."""
    found = shutil.which("iscc")
    if found:
        return found
    for base in (r"C:\Program Files (x86)\Inno Setup 6",
                 r"C:\Program Files\Inno Setup 6"):
        candidate = Path(base) / "ISCC.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def check_prerequisites() -> str:
    if sys.platform != "win32":
        print("WARNING: not on Windows. PyInstaller produces a bundle for the "
              "platform it runs on, so this will not yield a Windows .exe.\n")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is not installed. Run: pip install pyinstaller")
    iscc = find_iscc()
    if iscc is None:
        sys.exit(
            "Inno Setup's compiler (iscc) was not found. Install Inno Setup 6 "
            "from https://jrsoftware.org/isdl.php, or add iscc to PATH."
        )
    return iscc


def run(cmd: list[str], step: str) -> None:
    print(f"\n=== {step} ===\n" + " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f"{step} failed with exit code {result.returncode}")


def main() -> None:
    iscc = check_prerequisites()
    version = read_version()
    print(f"Building HelSpin {version}")

    # Clean prior output so a stale bundle cannot be shipped by accident.
    for path in (ROOT / "dist" / "HelSpin", ROOT / "build"):
        if path.exists():
            shutil.rmtree(path)

    run([sys.executable, "-m", "PyInstaller",
         str(PACKAGING / "helspin.spec"), "--noconfirm"],
        "PyInstaller (one-dir bundle)")

    bundle = ROOT / "dist" / "HelSpin" / "HelSpin.exe"
    if sys.platform == "win32" and not bundle.is_file():
        sys.exit(f"Expected {bundle}, but it is missing. Aborting.")

    run([iscc, f"/DMyAppVersion={version}", str(PACKAGING / "helspin.iss")],
        "Inno Setup (installer)")

    out = ROOT / "dist" / "installer" / f"HelSpin-{version}-setup.exe"
    print(f"\nDone. Installer at:\n  {out}" if out.is_file()
          else "\nInno Setup reported success; check dist/installer/.")


if __name__ == "__main__":
    main()
