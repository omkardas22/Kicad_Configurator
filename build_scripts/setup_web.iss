; ============================================================================
; setup_web.iss — KiCad Constraint Configurator Web/Stub Installer
; Lightweight setup stub that downloads app_payload.zip from GitHub Releases
; during installation using PowerShell, then extracts it to {app}.
;
; Build via build.py or directly:
;   ISCC.exe /DAppVersion=1.0.0 /DRootDir="D:\path\to\repo" ^
;            /DOutputDir="releases\v1.0.0\web_installer" setup_web.iss
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
  #define OutputDir "..\releases\v1.0.0\web_installer"
#endif

; Payload download URL — points to GitHub release asset
#define PayloadURL "https://raw.githubusercontent.com/omkardas22/Kicad_Configurator/main/releases/v" + AppVersion + "/app_payload.zip"

; ---------------------------------------------------------------------------
; [Setup] section
; ---------------------------------------------------------------------------
[Setup]
AppId={{9A1D8C22-4F58-5C7E-B2DD-AE6G3C9F1D4B}
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

; Output — lightweight stub, no embedded app files
OutputDir={#OutputDir}
OutputBaseFilename=KiCadConfigurator_WebSetup_v{#AppVersion}
Compression=lzma2/fast
SolidCompression=yes

; Installer appearance
WizardStyle=modern
SetupIconFile=
UninstallDisplayIcon={app}\{#AppName}.exe

; Privileges & architecture
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

AllowNoIcons=yes

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
; [Files] section — NO app files (web stub only)
; ---------------------------------------------------------------------------
[Files]
; Only a placeholder README is shipped in the stub
; The actual application is downloaded during installation via [Code]

; ---------------------------------------------------------------------------
; [Icons] section
; ---------------------------------------------------------------------------
[Icons]
Name: "{group}\KiCad Constraint Configurator"; \
  Filename: "{app}\{#AppName}.exe"; \
  WorkingDir: "{app}"; \
  Comment: "Launch KiCad Constraint Configurator"

Name: "{autodesktop}\KiCad Constraint Configurator"; \
  Filename: "{app}\{#AppName}.exe"; \
  WorkingDir: "{app}"; \
  Tasks: desktopicon; \
  Comment: "Launch KiCad Constraint Configurator"

Name: "{group}\Uninstall KiCad Constraint Configurator"; \
  Filename: "{uninstallexe}"

; ---------------------------------------------------------------------------
; [Run] section
; ---------------------------------------------------------------------------
[Run]
Filename: "{app}\{#AppName}.exe"; \
  Description: "{cm:LaunchProgram,KiCad Constraint Configurator}"; \
  Flags: nowait postinstall skipifsilent

; ---------------------------------------------------------------------------
; [Code] section — Download & extract payload via PowerShell
; ---------------------------------------------------------------------------
[Code]

const
  PayloadURL = '{#PayloadURL}';

var
  DownloadPage: TDownloadWizardPage;

// ── Helper: Run a PowerShell command and return exit code ──────────────────
function RunPS(Script: string): Integer;
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Result := ResultCode;
end;

// ── InitializeSetup ─────────────────────────────────────────────────────────
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('KiCad Constraint Configurator requires a 64-bit Windows system.', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  // Check internet connectivity via a quick PowerShell test
  if RunPS('Test-Connection -ComputerName github.com -Count 1 -Quiet') <> 0 then
  begin
    MsgBox(
      'Cannot reach github.com.' + #13#10 +
      'A working internet connection is required to download the application.' + #13#10 +
      #13#10 +
      'Please check your network and try again, or use the Offline installer.',
      mbError, MB_OK
    );
    Result := False;
  end;
end;

// ── CreateDownloadPage ───────────────────────────────────────────────────────
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    DownloadPage.Clear;
    DownloadPage.Add(PayloadURL, 'app_payload.zip', '');
    DownloadPage.Show;
    try
      DownloadPage.Download;
    except
      if DownloadPage.AbortedByUser then
      begin
        MsgBox('Download was cancelled by the user.', mbError, MB_OK);
        Result := False;
      end
      else
      begin
        MsgBox(
          'Failed to download the application payload.' + #13#10 +
          'URL: ' + PayloadURL + #13#10 +
          #13#10 +
          GetExceptionMessage,
          mbError, MB_OK
        );
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

// ── InitializeWizard ─────────────────────────────────────────────────────────
procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading Application',
    'Please wait while the application is being downloaded from GitHub ...',
    nil
  );
end;

// ── CurStepChanged — Extract payload after files are installed ───────────────
procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir, ZipPath, PSCmd: string;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    AppDir   := ExpandConstant('{app}');
    ZipPath  := ExpandConstant('{tmp}\app_payload.zip');

    // The downloader saves to {tmp}\app_payload.zip by default
    // Copy from DownloadPage temp dir if needed
    if not FileExists(ZipPath) then
    begin
      MsgBox('Payload zip not found at: ' + ZipPath, mbError, MB_OK);
      Exit;
    end;

    // Extract via PowerShell Expand-Archive
    PSCmd :=
      'Expand-Archive -LiteralPath ''' + ZipPath + ''' ' +
      '-DestinationPath ''' + AppDir + ''' -Force; ' +
      'Get-ChildItem -Path ''' + AppDir + '\{#AppName}\*''' +
      ' | Move-Item -Destination ''' + AppDir + ''' -Force; ' +
      'Remove-Item -Recurse -Force ''' + AppDir + '\{#AppName}''' +
      ' -ErrorAction SilentlyContinue';

    if RunPS(PSCmd) <> 0 then
      MsgBox(
        'Failed to extract the application.' + #13#10 +
        'You may need to extract ' + ZipPath + ' manually to:' + #13#10 +
        AppDir,
        mbError, MB_OK
      );

    // Clean up zip
    DeleteFile(ZipPath);
  end;
end;

// ── InitializeUninstall ──────────────────────────────────────────────────────
function InitializeUninstall(): Boolean;
begin
  Result := True;
  MsgBox(
    'This will remove KiCad Constraint Configurator from your computer.' + #13#10 +
    #13#10 +
    'Your saved configuration (API key, settings) in %APPDATA%\KiCadConfigurator' + #13#10 +
    'will NOT be deleted. Remove that folder manually if desired.',
    mbInformation, MB_OK
  );
end;
