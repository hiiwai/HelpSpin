#!/bin/bash
# Build HelSpin.app and package it as a .dmg. RUN ON A MAC.
#
#     ./packaging/build_macos.sh
#
# Produces dist/HelSpin-<version>-<arch>.dmg, a drag-to-Applications disk
# image with no Python, conda or pip needed on the machine that installs it.
#
# Optional, for a disk image that opens on other Macs WITHOUT a security
# warning (needs an Apple Developer ID; see packaging/BUILD.md):
#   HELSPIN_CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"
#       sign everything with that identity and hardened runtime;
#   and then ONE of
#   HELSPIN_NOTARY_PROFILE=<name>   a profile saved with
#       `xcrun notarytool store-credentials <name> ...`, or
#   HELSPIN_NOTARY_KEY=<AuthKey.p8 path> HELSPIN_NOTARY_KEY_ID=<id>
#   HELSPIN_NOTARY_ISSUER=<issuer uuid>   an App Store Connect API key (CI)
#       to notarise the image with Apple and staple the ticket to it.
# With none of them set, the result is ad-hoc signed exactly as before.
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

VERSION=$(python3 -c 'import sys; sys.path.insert(0, "packaging"); from spec_support import read_version; print(read_version("."))')
ARCH=$(uname -m)
APP="dist/HelSpin.app"
DMG="dist/HelSpin-${VERSION}-${ARCH}.dmg"
IDENTITY="${HELSPIN_CODESIGN_IDENTITY:-}"
NOTARY_PROFILE="${HELSPIN_NOTARY_PROFILE:-}"
NOTARY_KEY="${HELSPIN_NOTARY_KEY:-}"
NOTARISE=no
if [[ -n "$NOTARY_PROFILE" || -n "$NOTARY_KEY" ]]; then
    if [[ -z "$IDENTITY" ]]; then
        echo "error: notarisation needs HELSPIN_CODESIGN_IDENTITY as well" >&2
        exit 1
    fi
    if [[ -n "$NOTARY_KEY" && ( -z "${HELSPIN_NOTARY_KEY_ID:-}" \
            || -z "${HELSPIN_NOTARY_ISSUER:-}" || ! -f "$NOTARY_KEY" ) ]]; then
        echo "error: HELSPIN_NOTARY_KEY needs an existing .p8 file plus" >&2
        echo "       HELSPIN_NOTARY_KEY_ID and HELSPIN_NOTARY_ISSUER" >&2
        exit 1
    fi
    NOTARISE=yes
fi

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
if [[ -n "$IDENTITY" ]] && ! security find-identity -v -p codesigning \
        | grep -qF "$IDENTITY"; then
    echo "error: signing identity not in the keychain: $IDENTITY" >&2
    echo "       'security find-identity -v -p codesigning' lists those present." >&2
    exit 1
fi

echo "==> Cleaning previous build"
rm -rf build dist "$DMG"

echo "==> Building the application bundle"
python3 -m PyInstaller packaging/helspin_macos.spec --noconfirm --clean

[[ -d "$APP" ]] || { echo "error: $APP was not produced" >&2; exit 1; }

if [[ -n "$IDENTITY" ]]; then
    # PyInstaller already signed every binary and the bundle with the
    # identity (see the spec). Re-signing with --deep here would replace that
    # careful inside-out signature with a worse one, so only CHECK it.
    echo "==> Checking the Developer ID signature"
    codesign --verify --deep --strict --verbose=2 "$APP"
    SIGNATURE=$(codesign -dvv "$APP" 2>&1)
    if [[ "$SIGNATURE" != *"(runtime)"* ]]; then
        echo "error: the bundle is not signed with hardened runtime;" >&2
        echo "       Apple will refuse to notarise it." >&2
        exit 1
    fi
else
    # Ad-hoc signature. Not a Developer ID -- it does not stop Gatekeeper
    # warning on another Mac -- but it IS required on Apple Silicon, where an
    # unsigned binary is killed on launch rather than merely warned about.
    echo "==> Signing (ad-hoc)"
    codesign --force --deep --sign - "$APP" 2>/dev/null \
        || echo "    warning: ad-hoc signing failed; the app may not launch on Apple Silicon"
fi

echo "==> Verifying the bundle launches"
# --version exits immediately, so this catches a bundle that is missing a
# library WITHOUT needing a window server. A broken bundle usually fails here
# rather than at the user's first double-click.
if ! REPORTED=$("$APP/Contents/MacOS/HelSpin" --version); then
    echo "error: the built application does not run" >&2
    exit 1
fi
echo "    $REPORTED"
# A bundle without its VERSION file still runs but says "0.0.0+unknown".
if [[ "$REPORTED" != "HelSpin ${VERSION}" ]]; then
    echo "error: the app reports '$REPORTED', expected 'HelSpin ${VERSION}'" >&2
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

if [[ -n "$IDENTITY" ]]; then
    echo "==> Signing the disk image"
    codesign --force --timestamp --sign "$IDENTITY" "$DMG"
fi

if [[ "$NOTARISE" == yes ]]; then
    echo "==> Notarising with Apple (usually a few minutes)"
    if [[ -n "$NOTARY_PROFILE" ]]; then
        AUTH=(--keychain-profile "$NOTARY_PROFILE")
    else
        AUTH=(--key "$NOTARY_KEY" --key-id "$HELSPIN_NOTARY_KEY_ID"
              --issuer "$HELSPIN_NOTARY_ISSUER")
    fi
    # JSON rather than the exit status: submit --wait can return 0 for a
    # submission Apple REJECTED, and a rejected image must not ship.
    RESULT=$(xcrun notarytool submit "$DMG" "${AUTH[@]}" --wait --output-format json)
    STATUS=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("status",""))' "$RESULT")
    SUBMISSION=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("id",""))' "$RESULT")
    echo "    $STATUS ($SUBMISSION)"
    if [[ "$STATUS" != "Accepted" ]]; then
        echo "error: notarisation was not accepted. Apple's log:" >&2
        xcrun notarytool log "$SUBMISSION" "${AUTH[@]}" >&2 || true
        exit 1
    fi
    # The ticket inside the image lets Gatekeeper approve it offline too.
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    echo "==> Gatekeeper's verdict"
    spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
fi

echo
echo "==> Done: $DMG"
echo "    $(du -h "$DMG" | cut -f1)"
echo "    sha256: $(shasum -a 256 "$DMG" | cut -d' ' -f1)"
echo
echo "NOTE: this build is ${ARCH} only. An Apple Silicon build will not run"
echo "on an Intel Mac, or the reverse. Build on each architecture you need."
echo
if [[ "$NOTARISE" == yes ]]; then
    echo "Signed and notarised: it opens on any Mac without a security warning."
elif [[ -n "$IDENTITY" ]]; then
    echo "NOTE: signed with a Developer ID but NOT notarised, so Gatekeeper still"
    echo "blocks it on another Mac. Set HELSPIN_NOTARY_PROFILE (BUILD.md)."
else
    echo "NOTE: not signed with an Apple Developer ID, so on another Mac it is"
    echo "blocked on first launch (\"Apple could not verify ...\"). Click Done,"
    echo "then System Settings > Privacy & Security > Open Anyway. Or run:"
    echo "    xattr -dr com.apple.quarantine /Applications/HelSpin.app"
    echo "See packaging/BUILD.md to sign and notarise, which removes the warning."
fi
