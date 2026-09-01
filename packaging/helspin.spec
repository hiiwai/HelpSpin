# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for HelSpin.

ONE-DIRECTORY, not one-file, and this is not a stylistic choice. Qt reaches
HelSpin under the LGPL, which requires that a recipient be able to replace the
Qt libraries with their own build. A one-file exe unpacks to a temp directory
at run time and cannot be relinked, so it would put the distribution out of
compliance. One-dir keeps every Qt DLL a visible, replaceable file. See NOTICE.

Build from the repository root, on Windows, in the project's environment:

    pip install pyinstaller
    pyinstaller packaging/helspin.spec --noconfirm

The result is dist/HelSpin/, containing HelSpin.exe and its dependencies.
That folder is the input to the Inno Setup script (packaging/helspin.iss),
which turns it into a single setup.exe.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# The spec runs from the repo root (that is where pyinstaller is invoked), so
# paths are relative to it. SPECPATH points at packaging/, one level down.
ROOT = Path(SPECPATH).parent
RES = ROOT / "helspin" / "resources"

# Ship the licence, notices and artwork INSIDE the bundle. The licence dialog
# reads LICENSE.txt and NOTICE.txt from helspin/resources at run time, so they
# must land at that same relative path or Help -> Licence falls back to its
# built-in summary -- and the LGPL notices would not travel with the binary.
datas = [
    (str(RES / "LICENSE.txt"), "helspin/resources"),
    (str(RES / "NOTICE.txt"), "helspin/resources"),
    (str(RES / "icon.png"), "helspin/resources"),
    (str(RES / "logo.png"), "helspin/resources"),
]

# nmrglue is imported lazily and in places PyInstaller's static analysis
# misses, so collect it whole. scipy and numpy hooks ship with PyInstaller.
hiddenimports = collect_submodules("nmrglue")

a = Analysis(
    [str(ROOT / "helspin" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim large unused stacks. matplotlib pulls these in, HelSpin uses the Qt
    # Agg backend only. Excluding them saves ~40 MB and speeds first launch.
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# EXE holds only the bootstrap in one-dir mode; the heavy files go in COLLECT.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HelSpin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX trips antivirus heuristics; not worth it
    console=False,             # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(RES / "icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HelSpin",
)
