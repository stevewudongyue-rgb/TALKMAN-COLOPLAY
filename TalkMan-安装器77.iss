#define ProductName "TALKMAN COLOPLAY"
#include "build\TalkMan-version.iss"
#define Payload "dist-77-clear-py312\TalkMan"

[Setup]
AppId={{1BD73F53-9B70-4A42-AF17-E62FA5B318CF}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppPublisher=Cololight
AppCopyright=Cololight
VersionInfoProductName={#ProductName}
VersionInfoDescription={#ProductName} 安装程序
DefaultDirName={localappdata}\Programs\TALKMAN COLOPLAY
DefaultGroupName=TALKMAN COLOPLAY
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\TalkMan-COLOPLAY文件\17-桌面77与通用卡带71
OutputBaseFilename=TalkMan-COLOPLAY-0.1.28.77-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=TalkMan-assets\TalkMan-COLOPLAY.ico
UninstallDisplayIcon={app}\TALKMAN COLOPLAY.exe
Uninstallable=yes
CreateUninstallRegKey=no
CloseApplications=no
RestartApplications=no
AllowNoIcons=yes
InfoBeforeFile={#Payload}\TalkMan-安装说明.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建 TALKMAN COLOPLAY 桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#Payload}\TalkMan.exe"; DestDir: "{app}"; DestName: "TALKMAN COLOPLAY.exe"; Flags: ignoreversion
Source: "{#Payload}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\TalkMan-安装说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "TalkMan-assets\TalkMan-COLOPLAY.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\TALKMAN COLOPLAY"; Filename: "{app}\TALKMAN COLOPLAY.exe"; WorkingDir: "{app}"; IconFilename: "{app}\TalkMan-COLOPLAY.ico"; Tasks: desktopicon
Name: "{group}\TALKMAN COLOPLAY"; Filename: "{app}\TALKMAN COLOPLAY.exe"; WorkingDir: "{app}"; IconFilename: "{app}\TalkMan-COLOPLAY.ico"
Name: "{group}\设备模拟器"; Filename: "{app}\TALKMAN COLOPLAY.exe"; Parameters: "--coloplay-simulator"; WorkingDir: "{app}"; IconFilename: "{app}\TalkMan-COLOPLAY.ico"
Name: "{group}\卸载 TALKMAN COLOPLAY"; Filename: "{uninstallexe}"

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and
     (CompareText(RemoveBackslashUnlessRoot(WizardDirValue), RemoveBackslashUnlessRoot(ExpandConstant('{localappdata}\TalkMan-COLOPLAY'))) = 0) then
  begin
    MsgBox('请选择独立程序目录，不要安装到草稿与配对数据目录。', mbError, MB_OK);
    Result := False;
  end;
end;
