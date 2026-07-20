; Inno Setup script for Hearth — packages dist\Hearth\ into a single Setup.exe.
;
; Build first:  build.bat   (produces dist\Hearth\)
; Then open this in Inno Setup Compiler and hit Compile (or: iscc Hearth.iss).
; Output: Output\Hearth-Setup-v0.7.0-preview.exe
;
; Installs to %LOCALAPPDATA%\Hearth so NO admin rights are needed (easier for a
; tester). Creates Start Menu + optional desktop shortcuts. If the Edge WebView2
; runtime is missing, it installs it — that's what the GUI needs (without it,
; PyWebView falls back to the .NET/winforms backend and crashes with the
; "Python.Runtime.Loader.Initialize" error).

#define MyAppName "Hearth"
#define MyAppVersion "0.7.2-preview"
#define MyAppPublisher "0pen-sourcer"
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
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=Hearth-Setup{#EditionSuffix}-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
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
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} CLI";    Filename: "{app}\Hearth-cli.bat"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Install the WebView2 runtime if missing (needed for the GUI). Silent.
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Installing Edge WebView2 runtime (needed for the GUI)..."; \
  Flags: waituntilterminated; Check: WebView2Missing and FileExists(ExpandConstant('{tmp}\MicrosoftEdgeWebView2Setup.exe'))
; Offer to launch after install.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; \
  Flags: nowait postinstall skipifsilent

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
