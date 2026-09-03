# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for HelSpin on macOS -- produces HelSpin.app.

MUST BE RUN ON A MAC. PyInstaller does not cross-compile: it bundles the
interpreter and the shared libraries of the machine it runs on, so a build
started on Linux or Windows produces something macOS cannot execute. There is
no way around this short of a macOS machine or CI runner.

    pip install pyinstaller
    pyinstaller packaging/helspin_macos.spec --noconfirm

Result: dist/HelSpin.app. packaging/build_macos.sh does this and wraps the
result in a .dmg.

ONE-DIRECTORY, not one-file -- and on macOS this is automatic, because a .app
IS a directory. That matters for more than tidiness: Qt reaches HelSpin under
the LGPL, which requires a recipient be able to substitute their own Qt build.
A one-file binary unpacks to a temp directory at run time and cannot be
relinked, which would put the distribution out of compliance. Inside the
bundle every Qt library stays a visible, replaceable file. See NOTICE.

ARCHITECTURE. The bundle is built for the architecture of the build machine:
building on Apple Silicon gives arm64, which will NOT run on an Intel Mac.
A universal2 build needs universal2 wheels for every binary dependency, which
PySide6 does not currently publish, so a single fat build is not available.
Build on each architecture you need to support, or state the requirement.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
RES = ROOT / "helspin" / "resources"


def _version() -> str:
    """Read the version from pyproject rather than repeating it here.

    A version baked into the spec is a version that silently goes stale: the
    About dialog would say one thing and the bundle's Info.plist another, and
    macOS uses the plist value for update checks and Finder's Get Info.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("cannot find version in pyproject.toml")


VERSION = _version()

# Ship the licence, notices and artwork INSIDE the bundle. The licence dialog
# reads these from helspin/resources at run time, so they must land at the same
# relative path or Help -> Licence falls back to its built-in summary, and the
# LGPL notices would not travel with the binary.
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
    # HelSpin uses the Qt Agg backend only. Excluding these saves a lot of
    # bundle size and start-up time. PyQt must go: two Qt bindings in one
    # process is a crash on import, not a warning.
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HelSpin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,             # GUI app: no terminal window
    disable_windowed_traceback=False,
    # None means "the build machine's architecture". Do not set "universal2"
    # unless every dependency ships universal2 wheels -- PySide6 does not, and
    # the build fails late and confusingly when one of them does not.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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

app = BUNDLE(
    coll,
    name="HelSpin.app",
    icon=str(RES / "icon.icns"),
    # Reverse-DNS on a domain the project actually controls. macOS uses this
    # to key preferences, the dock icon and Gatekeeper decisions, so a
    # made-up or duplicated identifier causes collisions with other apps.
    bundle_identifier="com.ligsciss.helspin",
    version=VERSION,
    info_plist={
        "CFBundleName": "HelSpin",
        "CFBundleDisplayName": "HelSpin",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundlePackageType": "APPL",
        "NSHumanReadableCopyright":
            "Copyright \u00a9 2026 H. Iw-ai. All rights reserved. "
            "Contact iwai@ligsciss.com",
        # Retina: without this the whole UI is drawn at 1x and upscaled, so
        # text and spectra look soft on every modern Mac.
        "NSHighResolutionCapable": True,
        # 10.15 is the floor for current PySide6 wheels. Claiming lower would
        # let the app launch on a system where Qt then fails to load.
        "LSMinimumSystemVersion": "10.15",
        "NSRequiresAquaSystemAppearance": False,   # allow dark mode
        # Bruker data usually lives on a mounted share; on recent macOS,
        # reading one prompts for consent and the prompt shows this text.
        # Without a usage string the request is denied outright.
        "NSDesktopFolderUsageDescription":
            "HelSpin reads Bruker NMR datasets from folders you choose.",
        "NSDocumentsFolderUsageDescription":
            "HelSpin reads Bruker NMR datasets from folders you choose.",
        "NSDownloadsFolderUsageDescription":
            "HelSpin reads Bruker NMR datasets from folders you choose.",
        "NSNetworkVolumesUsageDescription":
            "HelSpin reads Bruker NMR datasets from mounted spectrometer "
            "shares.",
        "NSRemovableVolumesUsageDescription":
            "HelSpin reads Bruker NMR datasets from removable drives.",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "HelSpin Session",
                "CFBundleTypeExtensions": ["helspin"],
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
            }
        ],
    },
)
