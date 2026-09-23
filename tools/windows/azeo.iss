; Compiled by build_windows_installer.py with generated, hash-checked file entries.
; With PatchBase defined (build_windows_patch.py) the same script compiles a patch
; Setup: patch.iss assembles the new version from the installed base and the
; switch-over entries carry {#SwitchCheck}.
#ifndef SwitchCheck
#define SwitchCheck ""
#endif
[Setup]
AppId={#InstallId}
AppName=Azeo
AppVersion={#ProductVersion}
AppPublisher=Azeo
VersionInfoVersion={#ProductVersion}.0
DefaultDirName={localappdata}\Programs\Azeo
DefaultGroupName={#ProgramGroup}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
WizardStyle=modern light
WizardSizePercent=115
#ifdef AppIcon
SetupIconFile={#AppIcon}
#endif
DisableProgramGroupPage=yes
DisableWelcomePage=no
UsePreviousAppDir=yes
UsePreviousSetupType=yes
UsePreviousTasks=yes
ShowComponentSizes=yes
UninstallDisplayIcon={app}\versions\{#ProductVersion}\AzeoHelp.exe
UninstallFilesDir={app}\maintenance
AppMutex={#AppMutexName}
SetupMutex={#SetupMutexName}
CloseApplications=no
RestartApplications=no
SetupLogging=yes
Compression=lzma2/fast
SolidCompression=yes
OutputDir={#OutputDirectory}
#ifdef PatchBase
OutputBaseFilename=Azeo-Patch-{#ProductVersion}-from-{#PatchBase}-x64
#else
OutputBaseFilename=Azeo-Setup-{#ProductVersion}-x64
#endif
#ifdef SignCommand
SignTool=AzeoRelease
SignedUninstaller=yes
#endif

[Types]
Name: "full"; Description: "Full — all applications and integration SDK"
Name: "engineering"; Description: "Engineering — Explorer, Control Designer, Graphics and PA Designer"
Name: "operator"; Description: "Operator Station — runtime and offline Help"
Name: "custom"; Description: "Custom — choose applications"; Flags: iscustom

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcuts"; Flags: unchecked

#include "components.iss"
#include "files.iss"
#include "shortcuts.iss"

[INI]
Filename: "{app}\versions\{#ProductVersion}\installation.ini"; Section: "Install"; Key: "Version"; String: "{#ProductVersion}"; Flags: uninsdeleteentry{#SwitchCheck}
Filename: "{app}\versions\{#ProductVersion}\installation.ini"; Section: "Install"; Key: "Components"; String: "{code:SelectedComponents}"; Flags: uninsdeleteentry{#SwitchCheck}

[Registry]
Root: HKCU; Subkey: "{#RegistryKey}"; ValueType: string; ValueName: "Version"; ValueData: "{#ProductVersion}"; Flags: uninsdeletekey{#SwitchCheck}

[Run]
Filename: "{app}\versions\{#ProductVersion}\AzeoHelp.exe"; Description: "Open Azeo Help Center"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
Type: files; Name: "{app}\versions\{#ProductVersion}\installation.ini"
#ifdef PatchBase
; Files assembled from the base were not installed by Setup, so remove the folder.
Type: filesandordirs; Name: "{app}\versions\{#ProductVersion}"
#endif

[Code]
#ifdef PatchBase
#include "patch.iss"
#endif
function SelectedComponents(Param: String): String;
begin
  Result := WizardSelectedComponents(False);
end;

function InitializeSetup(): Boolean;
var
  Previous: String;
  PreviousNumber, TargetNumber: Int64;
begin
  Result := True;
  Previous := '';
  if RegQueryStringValue(HKCU, '{#RegistryKey}', 'Version', Previous) then
  begin
    if not StrToVersion(Previous, PreviousNumber) then
    begin
      SuppressibleMsgBox('The existing Azeo version record is invalid. Repair the existing installation before updating.', mbError, MB_OK, IDOK);
      Result := False;
      Exit;
    end;
    StrToVersion('{#ProductVersion}', TargetNumber);
    if (ComparePackedVersion(PreviousNumber, TargetNumber) > 0) and
       (ExpandConstant('{param:ALLOWDOWNGRADE|0}') <> '1') then
    begin
      SuppressibleMsgBox('A newer Azeo version is installed. Use the documented backup and rollback procedure before an intentional downgrade.', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
#ifdef PatchBase
  if Result then
    Result := PatchInstallable(Previous);
#endif
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectComponents then
    if not (WizardIsComponentSelected('explorer') or
            WizardIsComponentSelected('control_designer') or
            WizardIsComponentSelected('graphics_designer') or
            WizardIsComponentSelected('operator_station') or
            WizardIsComponentSelected('pa_designer') or
            WizardIsComponentSelected('simulation_workbench') or
            WizardIsComponentSelected('sdk')) then
    begin
      SuppressibleMsgBox('Select at least one application or the integration SDK.', mbError, MB_OK, IDOK);
      Result := False;
    end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine + MemoComponentsInfo + NewLine +
    NewLine + MemoTasksInfo + NewLine + NewLine +
    'Your projects and databases are stored separately in your Azeo workspace.' + NewLine +
    'Updates and uninstall preserve that workspace. Back it up before updating.';
end;
