#define MyAppName "申论研习"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "申论研习"
#define MyAppExeName "ShenlunStudio.exe"
#define MySetupBaseName "ShenlunStudio-1.0.1-Setup"

[Setup]
AppId={{B4EC883B-606E-4DB8-94B0-C496D56B0101}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=1.0.1.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 安装程序
DefaultDirName={localappdata}\Programs\ShenlunStudio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename={#MySetupBaseName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
Uninstallable=yes
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\source\dist\ShenlunStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\ShenlunStudio"
Type: filesandordirs; Name: "{app}"

[Code]
const
  CRED_TYPE_GENERIC = 1;

function CredDeleteW(TargetName: string; CredType: Cardinal; Flags: Cardinal): Boolean;
  external 'CredDeleteW@advapi32.dll stdcall';

procedure DeleteCredential(TargetName: string);
begin
  { A missing credential is already the desired state. }
  CredDeleteW(TargetName, CRED_TYPE_GENERIC, 0);
end;

function InitializeUninstall(): Boolean;
begin
  Result := MsgBox(
    '卸载将永久删除申论研习的全部本地内容，包括题库、草稿、训练记录、设置、数据库备份以及保存的模型 API 密钥。' + #13#10 + #13#10 +
    '此操作不可恢复。是否继续？',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    DeleteCredential('ShenlunStudio');
    DeleteCredential('model-api-key@ShenlunStudio');
  end;
end;
