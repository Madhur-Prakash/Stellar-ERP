; =============================================================================
; Personal ERP - Windows installer
;
; Packages the Flutter desktop release build into a single setup executable.
;
;   1. cd app_frontend && flutter build windows --release
;   2. compile this script (Inno Setup Compiler, F9 - or `iscc installer\personal-erp.iss`)
;   3. the installer lands in installer\dist\
;
; See README.md beside this file for prerequisites and the options that matter.
; =============================================================================

#define AppName        "Personal ERP"
#define AppVersion     "1.0.0"
#define AppPublisher   "Personal ERP"
; The repository, not a running deployment. This is what Windows shows as the
; publisher link in Add/Remove Programs, and every install points at it - so it has
; to be somewhere that answers for *this software*, not for one person's server.
#define AppUrl         "https://github.com/Madhur-Prakash/Personal-ERP"
#define AppExeName     "personalerp_desktop.exe"

; Relative to this script. `flutter build windows` writes here; nothing else in the
; tree is packaged, because everything the app needs is already inside this folder.
#define BuildDir       "..\app_frontend\build\windows\x64\runner\Release"
#define IconFile       "..\app_frontend\windows\runner\resources\app_icon.ico"

; Fail at compile time with a sentence a human can act on. Without this, a missing
; build produces an installer that is technically valid and completely empty - which
; is only discovered on the machine you were trying to install it on.
#if !FileExists(SourcePath + BuildDir + "\" + AppExeName)
  #error Release build not found. Run: cd app_frontend && flutter build windows --release
#endif

; The Visual C++ runtime is bundled rather than assumed - see the note in [Files] for
; why. It is ~25 MB of Microsoft's binary, so it is deliberately not committed; this
; check is what stops a fresh clone producing an installer that is silently missing it.
; Download it once and it stays put.
#if !FileExists(SourcePath + "VC_redist.x64.exe")
  #error VC_redist.x64.exe not found beside this script. Download it from https://aka.ms/vs/17/release/VC_redist.x64.exe into the installer folder.
#endif

[Setup]
; Never reuse this GUID for another product: it is the identity Windows tracks the
; install under, and it is what makes the next version replace this one in place
; rather than sitting beside it in Add/Remove Programs.
AppId={{8F3C1A72-5E4D-4B6A-9C21-7D0E5A8B4F19}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}
VersionInfoVersion={#AppVersion}

; `{autopf}` resolves to Program Files for an administrative install and to
; %LocalAppData%\Programs for a per-user one, so this single line covers both modes
; selected below.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

; **Per-user by default, with the choice offered.** The app writes nothing to its own
; directory - preferences go to %AppData% - so it does not need Program Files, and a
; default that needs no UAC prompt is one fewer reason for someone to abandon the
; install. `dialog` still lets an administrator install it for everyone.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Flutter builds this app for x64 only. Without these two lines a 32-bit install on an
; ARM or x86 machine would succeed and then fail to launch.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Shuts a running copy down instead of failing the file copy, which is what makes
; installing an update over a running app work.
CloseApplications=yes
RestartApplications=no

OutputDir=dist
; A fixed name, so a download link or a deploy script never has to be edited for a new
; release. The version is still recoverable from the file itself - `VersionInfoVersion`
; above puts it in the Properties dialog - and from Add/Remove Programs once installed.
; The trade is that each build overwrites the last: archive one before rebuilding if you
; need to keep it.
OutputBaseFilename=PersonalERP-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

; lzma2/max plus solid compression: `flutter_windows.dll` alone is 21 MB and is mostly
; compressible, which takes the whole payload to roughly a third of its size.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Unchecked by default. An installer that litters the desktop without asking is a
; small rudeness that everyone notices.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire Release folder, recursively - the .exe, flutter_windows.dll, the plugin
; DLLs, and data\ (icudtl.dat, app.so, flutter_assets). All of it is required: the app
; will not start if `data\` is missing, and the failure is a silent exit rather than an
; error message.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; --- Visual C++ runtime -------------------------------------------------------------
; A Flutter release build links against the MSVC 2015-2022 runtime (msvcp140.dll,
; vcruntime140.dll, vcruntime140_1.dll). Those are not in the build folder - the
; executable declares them and expects Windows to have them.
;
; Practically every Windows 10/11 machine does, because hundreds of applications
; install it. But "practically every" is not "every" - a freshly imaged laptop, a
; locked-down corporate build, a clean VM or a Server install often does not - and the
; failure mode gives nobody anything to work with: Windows cannot resolve the imports,
; kills the process before a single line of our code runs, and the user sees a
; double-click that does *nothing at all*. No window, no error, no log they will find.
; It reads as a broken build, and the report we get is "it doesn't open".
;
; So it ships in the box. `{tmp}` with `deleteafterinstall` means the payload is
; unpacked, used, and removed rather than left behind in Program Files, and the
; `Check:` on the [Run] entry skips the whole step on the machines that already have
; it, which is most of them.
Source: "VC_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; The runtime first, and only when it is missing - see the note in [Files].
;
; `/quiet` keeps Microsoft's own wizard from appearing in the middle of ours, and
; `/norestart` stops it rebooting the machine unasked. This step is machine-wide, so
; on a per-user install (the default) Windows raises a UAC prompt for it; declining
; leaves the app installed but unable to start on a machine that lacks the runtime.
; README.md beside this file documents that for whoever hits it.
Filename: "{tmp}\VC_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Visual C++ runtime..."; Check: not VCRedistInstalled

Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Flutter writes a small settings file next to the executable on some plugin
; configurations; remove the directory if it is empty afterwards rather than leaving a
; stub in Program Files.
Type: dirifempty; Name: "{app}"

[Code]
// True when the MSVC 2015-2022 x64 runtime is already registered. The [Run] entry
// above consults it so the bundled redistributable is executed only on the machines
// that actually need it - which is the minority, and they are the ones that would
// otherwise install an app that never opens.
function VCRedistInstalled: Boolean;
var
  Installed: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and (Installed = 1);
end;
