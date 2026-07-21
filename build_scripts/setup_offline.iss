; ============================================================================
; setup_offline.iss — KiCad Constraint Configurator Offline Installer
; Compiles a self-contained .exe bundling the full PyInstaller dist output.
;
; Build via build.py or directly:
;   ISCC.exe /DAppVersion=1.0.0 /DRootDir="D:\path\to\repo" ^
;            /DOutputDir="releases\v1.0.0\standalone_installer" setup_offline.iss
; ============================================================================

; ---------------------------------------------------------------------------
; Version defines (overridable from CLI with /D flag)
; ---------------------------------------------------------------------------
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#ifndef AppName
  #define AppName "KiCadConstraintConfigurator"
#endif

#ifndef RootDir
  #define RootDir ".."
#endif

#ifndef OutputDir
  #define OutputDir "..\releases\v1.0.0\standalone_installer"
#endif

; ---------------------------------------------------------------------------
; [Setup] section
; ---------------------------------------------------------------------------
[Setup]
AppId={{8F2C9A11-3E47-4B6D-A1CC-9D5F2B8E0C3A}
AppName=KiCad Constraint Configurator
AppVersion={#AppVersion}
AppVerName=KiCad Constraint Configurator v{#AppVersion}
AppPublisher=KiCad Constraint Configurator Team
AppPublisherURL=https://github.com/omkardas22/Kicad_Configurator
AppSupportURL=https://github.com/omkardas22/Kicad_Configurator/issues
AppUpdatesURL=https://github.com/omkardas22/Kicad_Configurator/releases

; Installation directories
DefaultDirName={autopf}\KiCad Constraint Configurator
DefaultGroupName=KiCad Constraint Configurator
DisableProgramGroupPage=yes
DisableDirPage=no

; Output
OutputDir={#OutputDir}
OutputBaseFilename=KiCadConfigurator_FullSetup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
CompressionThreads=auto

; Installer appearance
WizardStyle=modern
SetupIconFile={#RootDir}\app_icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe

; Privileges & architecture
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Misc
AllowNoIcons=yes
ChangesAssociations=no
CreateUninstallRegKey=yes

; ---------------------------------------------------------------------------
; [Languages] section
; ---------------------------------------------------------------------------
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ---------------------------------------------------------------------------
; [Tasks] section
; ---------------------------------------------------------------------------
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenuicon"; Description: "Create Start Menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

; ---------------------------------------------------------------------------
; [Files] section — bundles the entire PyInstaller --onedir output
; ---------------------------------------------------------------------------
[Files]
; Main application directory (all PyInstaller output)
Source: "{#RootDir}\dist\{#AppName}\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; \
  Permissions: users-readexec

; ---------------------------------------------------------------------------
; [Icons] section
; ---------------------------------------------------------------------------
[Icons]
; Start Menu icon
Name: "{group}\KiCad Constraint Configurator"; \
  Filename: "{app}\{#AppName}.exe"; \
  WorkingDir: "{app}"; \
  Comment: "Launch KiCad Constraint Configurator"

; Desktop icon (task controlled)
Name: "{autodesktop}\KiCad Constraint Configurator"; \
  Filename: "{app}\{#AppName}.exe"; \
  WorkingDir: "{app}"; \
  Tasks: desktopicon; \
  Comment: "Launch KiCad Constraint Configurator"

; Uninstaller in Start Menu
Name: "{group}\Uninstall KiCad Constraint Configurator"; \
  Filename: "{uninstallexe}"

; ---------------------------------------------------------------------------
; [Run] section — launch after install
; ---------------------------------------------------------------------------
[Run]
Filename: "{app}\{#AppName}.exe"; \
  Description: "{cm:LaunchProgram,KiCad Constraint Configurator}"; \
  Flags: nowait postinstall skipifsilent

; ---------------------------------------------------------------------------
; [UninstallRun] section
; ---------------------------------------------------------------------------
[UninstallRun]
; No extra cleanup needed — standard unins000.exe handles all registered files

; ---------------------------------------------------------------------------
; [Code] section — custom wizard pages and validation
; ---------------------------------------------------------------------------
[Code]

function InitializeSetup(): Boolean;
begin
  Result := True;
  // Check Windows version — require Windows 10+
  if not IsWin64 then
  begin
    MsgBox('KiCad Constraint Configurator requires a 64-bit Windows system.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Nothing special needed post-install for offline setup
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  // Note: User config in %APPDATA%\KiCadConfigurator is intentionally
  // preserved on uninstall to protect saved API keys and output paths.
  MsgBox(
    'This will remove KiCad Constraint Configurator from your computer.' + #13#10 + #13#10 +
    'Your saved configuration (API key, settings) in %APPDATA%\KiCadConfigurator' + #13#10 +
    'will NOT be deleted. Remove that folder manually if desired.',
    mbInformation, MB_OK
  );
end;
