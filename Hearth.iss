; Inno Setup script for Inglen. Packages dist\Hearth\ into a single Setup.exe.
;
; Built by build_release.ps1 (both editions); build.bat makes dist\Hearth\ alone
; Then open this in Inno Setup Compiler and hit Compile (or: iscc Hearth.iss).
; Output: Output\Hearth-Setup-v<version>.exe
;
; Installs to %LOCALAPPDATA%\Hearth so NO admin rights are needed (easier for a
; tester). Creates Start Menu + optional desktop shortcuts. If the Edge WebView2
; runtime is missing, it installs it — that's what the GUI needs (without it,
; PyWebView falls back to the .NET/winforms backend and crashes with the
; "Python.Runtime.Loader.Initialize" error).

#define MyAppName "Hearth"
; What people see. MyAppName stays: it names the folder existing installs live in.
#define MyAppDisplay "Inglen"
#define MyAppVersion "0.7.6-preview"
#define MyAppPublisher "Inglen"
#define MyAppExeName "Hearth.exe"

; --- Edition parameters (override on the iscc command line) ---
; FULL (default):  iscc /DSrcDir=dist_test\Hearth Hearth.iss
; LITE:            iscc /DSrcDir=dist_lite\Hearth /DEditionSuffix=-Lite /DEditionLabel=" Lite" Hearth.iss
; LITE drops the bundled CUDA llama.cpp server (~1.8 GB smaller); LITE users
; bring their own model server (LM Studio / Ollama) or a cloud key.
#ifndef SrcDir
  #define SrcDir "dist\Hearth"
#endif
#ifndef EditionSuffix
  #define EditionSuffix ""
#endif
#ifndef EditionLabel
  #define EditionLabel ""
#endif

[Setup]
AppId={{B7E2B6A4-1C9F-4F3D-9A6E-7E5C2F0A1D34}
AppName={#MyAppDisplay}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://inglen.app
AppSupportURL=https://github.com/0pen-Sourcer/Hearth/blob/main/docs/SUPPORT.md
AppUpdatesURL=https://github.com/0pen-Sourcer/Hearth/releases
AppContact=hello@inglen.app
AppCopyright=Copyright (c) 2026 Ishant
LicenseFile=LICENSE
SetupIconFile=assets\icon_app.ico
UninstallDisplayIcon={app}\_internal\assets\icon_app.ico
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppDisplay}
; Setup would otherwise reuse the Start menu folder an older install recorded
; ("Hearth"); the folder is a name people read, the install dir is not.
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Hearth-Setup{#EditionSuffix}-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
; Updating over a running Hearth: Windows won't let files be replaced while the
; app holds them open. Hearth exits before handing off, and these let Setup
; close/restart anything still holding a file instead of failing the copy.
; Restart Manager finds whatever is holding a file in the install folder, which
; is what actually blocks an update or uninstall. No AppMutex here on purpose:
; Hearth's single-instance lock is a port, not a named mutex, so an AppMutex
; line would match nothing and only look like protection.
CloseApplications=yes
RestartApplications=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; The whole built bundle (SrcDir is overridable: dist\Hearth, dist_test\Hearth, dist_lite\Hearth).
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; OPTIONAL: drop Microsoft's tiny (~2 MB) "MicrosoftEdgeWebView2Setup.exe"
; (Evergreen Bootstrapper) next to this .iss to bundle the WebView2 installer.
; If it's not here, the [Run] step below is simply skipped.
Source: "MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: WebView2Missing and FileExists(ExpandConstant('{src}\MicrosoftEdgeWebView2Setup.exe'))

[Icons]
Name: "{group}\{#MyAppDisplay}";        Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\_internal\assets\icon_app.ico"
Name: "{group}\{#MyAppDisplay} CLI";    Filename: "{app}\Hearth-cli.bat"; IconFilename: "{app}\_internal\assets\icon_app.ico"
Name: "{group}\Uninstall {#MyAppDisplay}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppDisplay}";  Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\_internal\assets\icon_app.ico"; Tasks: desktopicon
; The CLI is a first-class surface, not an afterthought, but only the GUI got a
; desktop shortcut so it looked like the app was GUI-only.
Name: "{autodesktop}\{#MyAppDisplay} CLI"; Filename: "{app}\Hearth-cli.bat"; \
  IconFilename: "{app}\_internal\assets\icon_app.ico"; Tasks: desktopicon

[InstallDelete]
; Shortcuts from before the rename to Inglen.
Type: files; Name: "{autodesktop}\Hearth.lnk"
Type: files; Name: "{autodesktop}\Hearth CLI.lnk"
Type: filesandordirs; Name: "{autoprograms}\Hearth"

[UninstallDelete]
; Inno only removes files it put there itself. Hearth writes into its own folder
; at runtime (downloaded engines, patch backups, logs), so the uninstaller left
; those behind, then couldn't remove the directory or unins000.exe, and still
; reported success. Sweep the app folder so uninstalling actually leaves nothing.
; User data is untouched: chats, memory and models live in the workspace and in
; %USERPROFILE%\.hearth, not here.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}"

[Run]
; Install the WebView2 runtime if missing (needed for the GUI). Silent.
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Installing the Edge WebView2 runtime, which the Inglen window needs..."; \
  Flags: waituntilterminated; Check: WebView2Missing and FileExists(ExpandConstant('{tmp}\MicrosoftEdgeWebView2Setup.exe'))
; Offer to launch after a normal install (checkbox on the final page).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppDisplay}"; \
  Flags: nowait postinstall skipifsilent
; In-app update path: Hearth exits and runs this with /SILENT, so the postinstall
; entry above is skipped and the user would be left with no app open. Relaunch it
; explicitly in that case so an update ends with Hearth back on screen.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent

[Code]
{ Returns True if the Edge WebView2 Evergreen runtime is NOT installed. Checks
  the standard per-machine + per-user registry locations Microsoft documents. }
function WebView2Missing(): Boolean;
var
  pv: String;
begin
  Result := True;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', pv) and (pv <> '') then
    Result := False
  else if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', pv) and (pv <> '') then
    Result := False
  else if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', pv) and (pv <> '') then
    Result := False;
end;
