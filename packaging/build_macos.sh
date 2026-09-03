#!/bin/bash
# Build HelSpin.app and package it as a .dmg. RUN ON A MAC.
#
#     ./packaging/build_macos.sh
#
# Produces dist/HelSpin-<version>-<arch>.dmg, a drag-to-Applications disk
# image with no Python, conda or pip needed on the machine that installs it.
#
# Why this cannot be built for you elsewhere: PyInstaller bundles the
# interpreter and shared libraries of the machine it runs on, so it does not
# cross-compile, and hdiutil -- the tool that makes a .dmg -- exists only on
# macOS. A Mac is required, and this script is what to run on it.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "error: this must run on macOS (found $(uname -s))." >&2
    echo "PyInstaller does not cross-compile and hdiutil is macOS-only." >&2
    exit 1
fi

VERSION=$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
ARCH=$(uname -m)
APP="dist/HelSpin.app"
DMG="dist/HelSpin-${VERSION}-${ARCH}.dmg"

echo "==> HelSpin ${VERSION} for ${ARCH}"

# Build in the environment that will be bundled: PyInstaller freezes THIS
# interpreter's packages, so a stale or wrong environment ships silently.
python3 -c "import PySide6, matplotlib, nmrglue, numpy" || {
    echo "error: dependencies missing. Run: pip install -e ." >&2
    exit 1
}
python3 -m PyInstaller --version >/dev/null 2>&1 || {
    echo "error: PyInstaller not installed. Run: pip install pyinstaller" >&2
    exit 1
}

echo "==> Cleaning previous build"
rm -rf build dist "$DMG"

echo "==> Building the application bundle"
python3 -m PyInstaller packaging/helspin_macos.spec --noconfirm --clean

[[ -d "$APP" ]] || { echo "error: $APP was not produced" >&2; exit 1; }

# Ad-hoc signature. Not a Developer ID -- it does not stop Gatekeeper warning
# on another Mac -- but it IS required on Apple Silicon, where an unsigned
# binary is killed on launch rather than merely warned about. Without this the
# app would simply not start on an M-series machine.
echo "==> Signing (ad-hoc)"
codesign --force --deep --sign - "$APP" 2>/dev/null \
    || echo "    warning: ad-hoc signing failed; the app may not launch on Apple Silicon"

echo "==> Verifying the bundle launches"
# --version exits immediately, so this catches a bundle that is missing a
# library WITHOUT needing a window server. A broken bundle usually fails here
# rather than at the user's first double-click.
if ! "$APP/Contents/MacOS/HelSpin" --version; then
    echo "error: the built application does not run" >&2
    exit 1
fi

echo "==> Building the disk image"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
# The Applications symlink is what makes the window a drag-to-install target.
# Without it the user is left holding an .app in a mounted volume, which many
# people then run from the disk image without ever copying it.
ln -s /Applications "$STAGE/Applications"
cp README.md MANUAL.pdf LICENSE "$STAGE/" 2>/dev/null || true

hdiutil create \
    -volname "HelSpin ${VERSION}" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG"
rm -rf "$STAGE"

echo
echo "==> Done: $DMG"
echo "    $(du -h "$DMG" | cut -f1)"
echo "    sha256: $(shasum -a 256 "$DMG" | cut -d' ' -f1)"
echo
echo "NOTE: this build is ${ARCH} only. An Apple Silicon build will not run"
echo "on an Intel Mac, or the reverse. Build on each architecture you need."
echo
echo "NOTE: the app is not signed with an Apple Developer ID, so on another"
echo "Mac Gatekeeper will refuse it on first launch. The recipient should"
echo "right-click the app and choose Open (once), or run:"
echo "    xattr -dr com.apple.quarantine /Applications/HelSpin.app"
echo "See packaging/BUILD.md for how to sign and notarise properly."
