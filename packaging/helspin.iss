; Inno Setup script for HelSpin.
;
; Turns the PyInstaller one-dir output (dist\HelSpin\) into a single
; setup.exe with a Start Menu entry, an optional desktop icon, an
; uninstaller, and a per-user install that needs no administrator rights --
; which matters on managed machines where users cannot install to Program
; Files.
;
; Build:
;   1. pyinstaller packaging\helspin.spec --noconfirm      (produces dist\HelSpin)
;   2. iscc packaging\helspin.iss                          (produces the setup.exe)
;
; iscc is the Inno Setup command-line compiler; install Inno Setup 6 from
; https://jrsoftware.org/isdl.php first.
;
; The version is passed in by build_installer.py so it never drifts from
; pyproject.toml. Compiling this file directly uses the fallback below.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "HelSpin"
#define MyAppPublisher "H. Iw-ai"
#define MyAppExeName "HelSpin.exe"

[Setup]
AppId={{A2F4E1C8-9B3D-4E7A-8F21-HELSPIN000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (C) 2026 H. Iw-ai. All rights reserved.

; Per-user install: no admin prompt, lands in %LOCALAPPDATA%. This is what
; lets a scientist install it themselves on a locked-down work machine.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; The licence is shown and must be accepted before install -- HelSpin is free
; for academic use and not for commercial use, so the user should see the
; terms. The file is read from the repo root at compile time.
LicenseFile=..\LICENSE

OutputDir=..\dist\installer
OutputBaseFilename=HelSpin-{#MyAppVersion}-setup
SetupIconFile=..\helspin\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; Refuse to run on 32-bit Windows: the PyInstaller bundle is 64-bit.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The entire PyInstaller output. recursesubdirs takes the _internal tree with
; all the Qt DLLs -- which stay individual, replaceable files, as the LGPL
; requires. Never repackage this as a single compressed blob.
Source: "..\dist\HelSpin\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} Licence"; Filename: "{app}\_internal\helspin\resources\LICENSE.txt"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch on finish. nowait so the installer closes cleanly.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller writes nothing outside {app}, but the index cache and licence
; live in %LOCALAPPDATA%\HelSpin\cache. Leave them: a reinstall should keep
; the user's indexed roots and any licence file. They can be cleared by hand.
Type: dirifempty; Name: "{app}"
