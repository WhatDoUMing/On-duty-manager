; Source only: build this after an explicit 打包 request and PyInstaller output.
#define AppVersion "0.1.0"
#define AppExe "DutyManager.exe"
#ifdef PackagingTest
  #define AppName "值日管家（安装验证）"
  #define AppGuid "{{779F246E-99EE-4671-B93E-9A71A7083BD3}"
  #define RunValueName "DutyManagerPackagingTest"
  #define ProtocolName "dutymanager-packaging-test"
#else
  #define AppName "值日管家"
  #define AppGuid "{{5C63B66F-4033-4FB6-A178-4191F264D4DB}"
  #define RunValueName "DutyManager"
  #define ProtocolName "dutymanager"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\DutyManager
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\output
OutputBaseFilename=DutyManager-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=安装 - %1
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=本向导将在您的电脑上安装 [name/ver]。%n%n安装后可通过托盘查看与管理值日安排。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=完成(&F)
SelectTasksLabel2=请选择附加选项：
FinishedHeadingLabel=值日管家安装完成
FinishedLabelNoIcons=值日管家已安装到您的电脑。

[Tasks]
Name: "autostart"; Description: "登录 Windows 后自动启动并在托盘提醒"; Flags: unchecked
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "..\dist\DutyManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#RunValueName}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#RunValueName}"; Tasks: not autostart; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#RunValueName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: autostart
Root: HKCU; Subkey: "Software\Classes\{#ProtocolName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#RunValueName}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "打开值日管家"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; User schedules deliberately remain in LocalAppData\DutyManager.
Type: files; Name: "{app}\runtime.json"
