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
