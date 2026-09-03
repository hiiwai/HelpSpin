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
dist/installer/HelSpin-0.5.4-setup.exe
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
prove it actually starts, then makes the image with an Applications symlink so
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

It does **not** satisfy Gatekeeper on someone else's Mac. On first launch they
will see *"HelSpin cannot be opened because the developer cannot be verified"*.
The recipient must either:

- **right-click the app and choose Open**, then confirm — once only; or
- run `xattr -dr com.apple.quarantine /Applications/HelSpin.app`

This is the macOS counterpart of the "Access is denied" problem on managed
Windows machines, and it will be the first thing anyone reports.

### Signing and notarising properly

To remove the warning entirely you need an **Apple Developer Program**
membership (99 USD/year). With a Developer ID certificate installed:

```
# 1. sign with hardened runtime (notarisation requires it)
codesign --force --deep --options runtime --timestamp \
         --sign "Developer ID Application: YOUR NAME (TEAMID)" \
         dist/HelSpin.app

# 2. notarise the disk image and wait for Apple's verdict
xcrun notarytool submit dist/HelSpin-<version>-<arch>.dmg \
      --apple-id you@example.com --team-id TEAMID \
      --password APP_SPECIFIC_PASSWORD --wait

# 3. staple the ticket so it works offline
xcrun stapler staple dist/HelSpin-<version>-<arch>.dmg
```

Worth weighing against how the tool is distributed: for a handful of named
collaborators the right-click-Open instruction is enough. For anything wider,
notarisation is the difference between a tool people can install and one they
assume is broken.

### LGPL

A `.app` is a directory, so Qt's libraries stay visible and replaceable inside
`Contents/Frameworks` — the one-file mode that would break LGPL compliance is
not used, and must not be. See `NOTICE`.
