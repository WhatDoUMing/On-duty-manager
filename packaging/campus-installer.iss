; Configuration only. Compile after an explicit 打包 request.
#define AppVersion "0.1.0"
#define AppExe "CampusDutyManager.exe"
#ifdef PackagingTest
  #define AppName "校园值日管家（安装验证）"
  #define AppGuid "{{6A03C588-4C11-426C-A12C-F6ECC4F86E3B}"
  #define RunValueName "CampusDutyManagerPackagingTest"
  #define AppFolder "CampusDutyManagerPackagingTest"
#else
  #define AppName "校园值日管家"
  #define AppGuid "{{76BDE656-13D6-4B38-AE18-C6607C203E03}"
  #define RunValueName "CampusDutyManager"
  #define AppFolder "CampusDutyManager"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppFolder}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\output\campus
OutputBaseFilename=CampusDutyManager-Setup-{#AppVersion}
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
WelcomeLabel2=本向导将在您的电脑上安装 [name/ver]。%n%n校园版使用独立数据目录，可与宿舍版同时运行。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=完成(&F)
SelectTasksLabel2=请选择附加选项：
FinishedHeadingLabel=校园值日管家安装完成

[Tasks]
Name: "autostart"; Description: "登录 Windows 后静默启动到托盘"; Flags: unchecked
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "..\dist\CampusDutyManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#RunValueName}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#RunValueName}"; Tasks: not autostart; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#RunValueName}"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "打开校园值日管家"; Flags: nowait postinstall skipifsilent

; Class schedules and assessment records remain in LocalAppData\CampusDutyManager.
