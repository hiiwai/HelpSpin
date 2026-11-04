# Building the HelSpin Windows installer

This produces `HelSpin-<version>-setup.exe`: a single file a colleague
double-clicks to install HelSpin, with a Start Menu entry and an uninstaller,
and **no Python or conda required on their machine**.

Everything here must run **on Windows**. PyInstaller bundles the platform it
runs on, so a Linux or macOS build does not yield a Windows executable.

## What you need, once

1. **A Windows machine** with the HelSpin source and a working conda
   environment — the same `helspin` environment you already use.
2. **PyInstaller**: `pip install pyinstaller`
3. **Inno Setup 6**: https://jrsoftware.org/isdl.php — the standard, free
   Windows installer builder. Accept its defaults.

## Building

From the repository root, in the `helspin` environment:

```
python packaging/build_installer.py
```

That runs both stages and prints the path to the finished installer:

```
dist/installer/HelSpin-<version>-setup.exe
```

The script reads the version from `pyproject.toml`, so a release needs no
hand-editing. It refuses to run if PyInstaller or Inno Setup is missing, and
cleans previous output first so a stale build can't ship by mistake.

### Or run the two stages by hand

```
pyinstaller packaging/helspin.spec --noconfirm
iscc packaging/helspin.iss
```

The first produces `dist/HelSpin/` (the app as a folder); the second wraps
that folder into the setup.exe.

### What the spec generates (do not edit by hand)

The entry script is `packaging/helspin_entry.py`, not `helspin/__main__.py`:
an entry script runs outside its package, and `__main__.py`'s relative
imports made the built app crash on launch (0.9.7 and earlier).

On every build the spec writes, under `build/helspin-version/`:

- `VERSION` -- shipped inside the bundle; the app's only source of its own
  version, since a bundle has no package metadata.
- `version_info.txt` -- the .exe version resource (Properties -> Details).

Both come from `pyproject.toml` via `packaging/spec_support.py`.

## What the installer does

- Installs **per-user**, into `%LOCALAPPDATA%\HelSpin` — **no administrator
  rights**. This is deliberate: on a managed work machine a user often cannot
  write to Program Files, and a per-user install sidesteps that entirely.
- Adds a Start Menu entry, an optional desktop icon, and a licence shortcut.
- Shows the licence and requires acceptance before installing.
- Registers an uninstaller in Windows "Apps & features".
- Leaves the index cache and any licence file in place on uninstall, so a
  reinstall keeps the user's indexed roots.

Expect **300–400 MB installed** and a setup.exe of roughly 120–180 MB. PySide6
and scipy dominate; this is normal for a bundled Qt application.

## The two things that are not optional

### One-directory, never one-file

The spec builds one-dir on purpose. Qt reaches HelSpin under the **LGPL**,
which requires that a recipient be able to replace the Qt libraries with their
own build. A one-file exe unpacks to a temporary directory at run time and
cannot be relinked, which would put the distribution out of compliance. One-dir
keeps every Qt DLL a visible, replaceable file. `NOTICE` records this, and it
travels inside the bundle.

### Code signing

**The installer is unsigned, and that is the last real barrier to
distribution.** An unsigned setup.exe will:

- trip Windows SmartScreen ("Windows protected your PC") on a normal machine;
- be **blocked outright** by the AppLocker / WDAC publisher rules on managed
  machines — the exact machines this installer exists to serve. This is the
  same rule that blocked `helspin-gui.exe` from a user profile.

Signing needs a code-signing certificate the project does not yet have (an OV
certificate is roughly EUR 300–600/year; EV more, and better for SmartScreen
reputation). Once you have one, sign **both** the exe inside the bundle and the
finished installer:

```
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  dist\HelSpin\HelSpin.exe
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  dist\installer\HelSpin-<version>-setup.exe
```

Until then, the pip-install route remains the reliable path onto a managed
machine, because it runs through conda's already-signed `pythonw.exe`. Don't
buy a certificate until enough people want the installer to justify it — the
zip works today.

## Sanity check after building

On the build machine, before handing the installer to anyone:

```
dist\HelSpin\HelSpin.exe
```

It should open the HelSpin window. Then install the setup.exe on a **second**
machine that has no Python at all — that is the only real test that the bundle
is self-contained.

---

## macOS: building HelSpin.app and a .dmg

The result is a disk image a colleague drags to Applications. **No Python,
conda or pip on the machine that installs it** — the interpreter and every
library are inside the bundle.

### This must be built on a Mac

PyInstaller freezes the interpreter and shared libraries of the machine it
runs on, so it does not cross-compile, and `hdiutil` exists only on macOS.
There is no route to a working `.dmg` from Linux or Windows short of a Mac or
a macOS CI runner.

### Build

```
conda activate helspin          # or your venv
pip install -e . pyinstaller
./packaging/build_macos.sh
```

Produces `dist/HelSpin-<version>-<arch>.dmg`. The script checks dependencies
first, builds the bundle, ad-hoc signs it, runs it once with `--version` to
prove it actually starts and reports the version in `pyproject.toml`, then
makes the image with an Applications symlink so
the window is a drag-to-install target.

### Architecture

The bundle is built for the architecture of the build machine. **An Apple
Silicon build will not run on an Intel Mac, or the reverse.** A universal2
build needs universal2 wheels for every binary dependency and PySide6 does not
publish them, so build on each architecture you need to support, or state the
requirement on the download.

The filename carries the architecture (`-arm64`, `-x86_64`) so the two cannot
be confused.

### Gatekeeper — read this before sending it to anyone

The app is **ad-hoc signed**, not signed with an Apple Developer ID. The
ad-hoc signature is not cosmetic: on Apple Silicon an unsigned binary is
killed on launch rather than warned about, so it is what makes the app run at
all on your own machine.

It does **not** satisfy Gatekeeper on someone else's Mac. On first launch
macOS 15+ says *"Apple could not verify "HelSpin" is free of malware"* (older:
*"cannot be opened because the developer cannot be verified"*). The recipient
must either:

- click **Done**, then **System Settings → Privacy & Security → Open Anyway**
  — once only. On macOS 15 and later the old right-click → Open shortcut no
  longer bypasses Gatekeeper; on 14 and earlier it still works; or
- run `xattr -dr com.apple.quarantine /Applications/HelSpin.app`

INSTALL.md walks a recipient through both.

This is the macOS counterpart of the "Access is denied" problem on managed
Windows machines, and it will be the first thing anyone reports.

### Signing and notarising

The only way to remove the warning for everyone is a **Developer ID**
signature plus Apple **notarisation**. Both need an **Apple Developer
Program** membership (99 USD/year; an individual membership is enough, or an
organisation's, if it will let you sign under its name). No code changes are
needed; the build script does the rest once the credentials exist.

One-time setup on the Mac that builds:

1. developer.apple.com → Certificates → **+** → *Developer ID Application*.
   Install it; `security find-identity -v -p codesigning` then lists
   `Developer ID Application: YOUR NAME (TEAMID)`.
2. appleid.apple.com → *App-Specific Passwords* → create one, then save the
   notarisation credentials in the keychain under a name:

   ```
   xcrun notarytool store-credentials helspin-notary \
         --apple-id you@example.com --team-id TEAMID \
         --password APP-SPECIFIC-PASSWORD
   ```

Every build after that:

```
export HELSPIN_CODESIGN_IDENTITY="Developer ID Application: YOUR NAME (TEAMID)"
export HELSPIN_NOTARY_PROFILE=helspin-notary
./packaging/build_macos.sh
```

With `HELSPIN_CODESIGN_IDENTITY` set, the spec makes PyInstaller sign every
binary inside-out with hardened runtime, a secure timestamp and
`packaging/entitlements.plist` (no ad-hoc `--deep` re-sign afterwards — that
would undo it). The script then checks the signature, signs the `.dmg`,
submits it to Apple, refuses to finish unless Apple returns *Accepted*
(printing Apple's log if not), staples the ticket so it also works offline,
and asks Gatekeeper (`spctl`) for its verdict.

The entitlements are the two a frozen Python application needs under
hardened runtime: executable memory for ctypes/libffi, and loading the
bundled Python extension libraries. Do not add more without a reason.

#### On GitHub instead of your Mac

`.github/workflows/macos-dmg.yml` builds the same image on GitHub's Macs and
attaches it to a draft release:

```
gh release create v<version> --draft --title "HelSpin <version>" --notes "..."
gh workflow run macos-dmg.yml -f tag=v<version>            # Apple Silicon
gh workflow run macos-dmg.yml -f tag=v<version> -f intel=true   # + Intel
```

Without secrets it is ad-hoc signed (the Gatekeeper warning remains). To sign
and notarise there, add these repository secrets (Settings → Secrets and
variables → Actions):

| Secret | Value |
|---|---|
| `MACOS_CERTIFICATE` | Developer ID Application certificate **with its private key**, exported from Keychain Access as `.p12`, then `base64 -i cert.p12 \| pbcopy` |
| `MACOS_CERTIFICATE_PASSWORD` | the password chosen for that `.p12` |
| `MACOS_SIGNING_IDENTITY` | `Developer ID Application: YOUR NAME (TEAMID)` |
| `NOTARY_KEY` | App Store Connect → Users and Access → Integrations → API key (`.p8`), base64-encoded |
| `NOTARY_KEY_ID` | that key's ID |
| `NOTARY_ISSUER` | the issuer ID shown on the same page |

The Intel runner label (`macos-15-intel`) is GitHub's; if GitHub retires it,
build the Intel image on an Intel Mac with the script instead.

Worth weighing against how the tool is distributed: for a handful of named
collaborators the right-click-Open instruction is enough. For anything wider,
notarisation is the difference between a tool people can install and one they
assume is broken.

### LGPL

A `.app` is a directory, so Qt's libraries stay visible and replaceable inside
`Contents/Frameworks` — the one-file mode that would break LGPL compliance is
not used, and must not be. See `NOTICE`.
