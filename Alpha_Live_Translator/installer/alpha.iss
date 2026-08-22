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
; A version RESOURCE accepts only a strict four-part numeric version, which is
; not the same thing as AppVersion ("1.0.1", or one day "1.1.0-rc1"). Derived
; by installer/build_installer.py; the default keeps a hand-run compile going.
#ifndef VersionQuad
  #define VersionQuad "0.0.0.0"
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
; The person this is delivered by. Kept ASCII here because it also becomes the
; exe's version resource, which is written at COMPILE time and cannot vary by
; language. The Japanese rendering of the same name lives in [CustomMessages]
; below and is what the wizard shows when it runs in Japanese.
#define AppPublisher "Md. Tariqul Islam"
#define AppCopyright "Copyright (C) Md. Tariqul Islam"
#define AppExeName "python\pythonw.exe"
#define AppScript "app\main.py"
#define CollectScript "app\collect_logs.py"
#define AppIcon "app\assets\alpha.ico"
#define OutputBase "AlphaLiveTranslator-Setup-" + AppVersion

[Setup]
AppId={{8F3C2A16-4D5E-4B7A-9C21-0A49E1C70001}
AppName={#AppName}
AppVersion={#AppVersion}
; `{cm:...}` so the wizard shows the publisher in the language it is running
; in. VersionInfoCompany below stays the compile-time ASCII form, because a
; version resource has one value and no notion of language.
AppPublisher={cm:PublisherName}
DefaultDirName={localappdata}\Programs\Alpha Live Translator
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename={#OutputBase}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The bundle is ~96 MB on disk and compresses to about a quarter of that.
DiskSpanning=no
UninstallDisplayName={#AppName}
; Alpha's own icon everywhere Windows shows one -- the wizard, the Apps
; list, and the shortcuts below. Without it the operator sees the Python
; logo, because the app runs on pythonw.exe and has no icon of its own.
SetupIconFile={#SourceDir}\{#AppIcon}
UninstallDisplayIcon={app}\{#AppIcon}
AppCopyright={#AppCopyright}
; Inno asks which language to install in as soon as there is more than one.
; Picking by the system language is both fewer clicks and the right answer.
ShowLanguageDialog=no

; WHAT WINDOWS KNOWS ABOUT THIS FILE
; ---------------------------------
; SmartScreen's "Windows protected your PC" screen is driven by two things,
; and neither of them is anything in this block: a Mark of the Web on the
; downloaded copy, and the reputation of the signature on the file. See
; installer/DELIVERY.md -- the fix is a certificate, or a delivery route that
; never applies a Mark of the Web.
;
; These entries are still worth setting. Inno defaults the company from
; AppPublisher and the product name from AppName, but leaves FileVersion and
; the copyright EMPTY -- measured on the 1.0.1 build. A half-empty version
; resource is a well-known antivirus heuristic and shows the operator nothing
; useful in Properties -> Details, which is the one place they can check what
; they have been sent.
VersionInfoVersion={#VersionQuad}
VersionInfoProductVersion={#VersionQuad}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright={#AppCopyright}
VersionInfoOriginalFileName={#OutputBase}.exe

; Code signing, the day a certificate exists. build_installer.py passes both
; /DSignCommand=1 and /Salphasign=<command line>; without them this block is
; skipped and the build is unsigned exactly as it is today. SignedUninstaller
; matters as much as the installer itself: the uninstaller is generated at
; install time, and an unsigned one is what Windows shows when the operator
; later removes the app.
#ifdef SignCommand
SignTool=alphasign
SignedUninstaller=yes
#endif

[Languages]
; Japanese as well as English, because the machine this is handed to runs in
; Japanese. `ShowLanguageDialog=no` above means Inno picks by the system
; language instead of asking -- the operator was promised double-click, next,
; next, and a language prompt is one more thing to get wrong.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
; The one string that differs. The Japanese form is the name as it is written
; in Japanese, not a transliteration made up at compile time.
english.PublisherName=Md. Tariqul Islam
japanese.PublisherName=イスラム　エムディー　タリクル

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
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"
; One button for the operator to gather everything a diagnosis needs.
; There is no terminal on that machine, and uninstalling to start clean
; would destroy the very evidence we would be asking for.
Name: "{group}\Collect diagnostic logs"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#CollectScript}"""; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIcon}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"; Tasks: desktopicon; IconFilename: "{app}\{#AppIcon}"

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Bytecode Python regenerated after install -- Inno did not install it, so it
; would otherwise be left behind.
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"
Type: filesandordirs; Name: "{app}\app\alpha\__pycache__"
Type: files; Name: "{app}\app\.env"
; The UI-language choice. The app writes it, not Inno, so an uninstall
; would otherwise leave it behind.
Type: files; Name: "{app}\app\user_settings.json"

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
