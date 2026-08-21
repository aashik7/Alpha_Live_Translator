; Alpha Live Translator -- hand-over installer (sprint item 49, phase 2).
;
; THIS FILE CONTAINS NO API KEYS AND MUST NEVER CONTAIN ANY.
; They arrive as command-line defines from installer/build_installer.py, which
; reads them from installer/keys.local.ini -- a file git ignores. A key baked
; into a committed script is a key in the repository history forever.
;
; WHY PER-USER AND NOT Program Files
; ----------------------------------
; The bundle ships without __pycache__ (tools/build_bundle.py removes ~10 MB of
; it), so Python rewrites the bytecode on first import and the install
; directory has to be writable. The app also writes its evidence trail to
; app\troubleshooting\runs\. Program Files is not writable by a normal user, so
; PrivilegesRequired=lowest and an install under {localappdata} are load-bearing
; here, not a preference -- and they also mean the operator is never asked for
; an admin password.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #error SourceDir must be defined (the folder holding python\ and app\)
#endif
#ifndef DeepgramKey
  #error DeepgramKey must be defined
#endif
#ifndef DeepLKey
  #error DeepLKey must be defined
#endif

#define AppName "Alpha Live Translator"
#define AppPublisher "Wicresoft"
#define AppExeName "python\pythonw.exe"
#define AppScript "app\main.py"

[Setup]
AppId={{8F3C2A16-4D5E-4B7A-9C21-0A49E1C70001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Alpha Live Translator
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=AlphaLiveTranslator-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The bundle is ~96 MB on disk and compresses to about a quarter of that.
DiskSpanning=no
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The whole bundle, exactly as tools/build_bundle.py produced it. recursesubdirs
; plus createallsubdirs so empty directories survive.
Source: "{#SourceDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\Alpha.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Straight at pythonw.exe rather than at Alpha.bat: a .bat shortcut flashes a
; console window before the UI appears.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Bytecode Python regenerated after install -- Inno did not install it, so it
; would otherwise be left behind.
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"
Type: filesandordirs; Name: "{app}\app\alpha\__pycache__"
Type: files; Name: "{app}\app\.env"

[Code]
// Write the .env beside the app folder so alpha/config.py's PROJECT_ROOT
// finds it. PROJECT_ROOT is Path(__file__).resolve().parent.parent, which
// lands there -- verified against the built bundle, where the key read back
// as "configured". Written here rather than shipped as a [Files] entry so
// the keys never exist as a file in the source tree.
//
// Line comments, not a brace block: Pascal treats braces as a comment, so
// the closing brace of an Inno constant like the app constant ends the
// comment early and the rest is parsed as code. That broke the first
// compile at line 89.
procedure WriteEnvFile();
var
  Lines: TArrayOfString;
  EnvPath: String;
begin
  EnvPath := ExpandConstant('{app}\app\.env');
  SetArrayLength(Lines, 6);
  Lines[0] := '# Written by the Alpha Live Translator installer.';
  Lines[1] := '# These are delivery-specific keys. If they stop working, ask';
  Lines[2] := '# whoever gave you this build for a new installer.';
  Lines[3] := 'DEEPGRAM_API_KEY={#DeepgramKey}';
  Lines[4] := 'DEEPL_AUTH_KEY={#DeepLKey}';
  Lines[5] := 'DEEPL_API_PLAN=auto';
  if not SaveStringsToFile(EnvPath, Lines, False) then
    MsgBox('Could not write the configuration file:' + #13#10 + EnvPath + #13#10 +
           'The app will start but will not be able to transcribe.',
           mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteEnvFile();
end;
