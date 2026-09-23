// Patch Setup support, included by azeo.iss inside [Code] when PatchBase is
// defined. The new version folder is assembled from the installed base by the
// base runtime's private Python (_azeo_patch.py) before anything changes, and
// verified against the complete new manifest before the switch-over entries
// ([INI], [Registry], [Icons]) run through PatchVerified.
var
  PatchVerification: Integer;

function PatchVersionDir(Version: String): String;
begin
  Result := ExpandConstant('{app}\versions\') + Version;
end;

function PatchInstallable(Previous: String): Boolean;
begin
  Result := Previous = '{#PatchBase}';
  if not Result then
    SuppressibleMsgBox('This patch applies to Azeo {#PatchBase} only; the installed version is "' +
      Previous + '". Use the full Azeo Setup for version {#ProductVersion}.', mbError, MB_OK, IDOK);
end;

function PatchArguments(): String;
begin
  Result := '"' + PatchVersionDir('{#PatchBase}') + '" "' + PatchVersionDir('{#ProductVersion}') + '" "' +
    ExpandConstant('{tmp}\patch-files.txt') + '" "' + WizardSelectedComponents(False) + '" ' +
    '{#PatchBaseManifestHash} {#PatchNewManifestHash}';
end;

function RunPatchTool(Mode: String; var Detail: String): Boolean;
var
  Python, Tool, Report, Command: String;
  Raw: AnsiString;
  ResultCode: Integer;
begin
  Result := False;
  Detail := '';
  Python := PatchVersionDir('{#PatchBase}') + '\runtime\python.exe';
  if not FileExists(Python) then
  begin
    Detail := 'the installed base runtime is missing (' + Python + ')';
    Exit;
  end;
  ExtractTemporaryFile('_azeo_patch.py');
  ExtractTemporaryFile('patch-files.txt');
  Tool := ExpandConstant('{tmp}\_azeo_patch.py');
  Report := ExpandConstant('{tmp}\patch-' + Mode + '.json');
  DeleteFile(Report);
  Command := '-I "' + Tool + '" ' + Mode + ' ' + PatchArguments() + ' "' + Report + '"';
  Log('Patch tool: ' + Python + ' ' + Command);
  if not Exec(Python, Command, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Detail := 'the installed base runtime could not start';
    Exit;
  end;
  if LoadStringFromFile(Report, Raw) then
    Detail := String(Raw)
  else
    Detail := 'no report (exit code ' + IntToStr(ResultCode) + ')';
  Log('Patch tool report: ' + Detail);
  Result := ResultCode = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Detail: String;
begin
  Result := '';
  PatchVerification := 0;
  if not RunPatchTool('assemble', Detail) then
    Result := 'Azeo {#ProductVersion} could not be assembled from the installed {#PatchBase} files: ' +
      Detail + #13#10 + 'Nothing was changed. Use the full Azeo Setup for version {#ProductVersion}.';
end;

function PatchVerified(): Boolean;
var
  Detail: String;
begin
  if PatchVerification = 0 then
  begin
    if RunPatchTool('verify', Detail) then
      PatchVerification := 1
    else
    begin
      PatchVerification := 2;
      DelTree(PatchVersionDir('{#ProductVersion}'), True, True, True);
      RaiseException('The assembled Azeo {#ProductVersion} files failed verification and were removed; ' +
        'version {#PatchBase} remains installed. ' + Detail);
    end;
  end;
  Result := PatchVerification = 1;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // A patch keeps the installed location and application selection; use the
  // full Setup's Modify to change them.
  Result := (PageID = wpSelectDir) or (PageID = wpSelectComponents) or (PageID = wpSelectTasks);
end;
