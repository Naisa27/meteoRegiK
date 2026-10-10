; Установщик meteoRegiK (Inno Setup 6). Собирается из папки dist\meteoRegiK,
; которую делает PyInstaller (packaging\meteoregik.spec). Запускать через
; packaging\build.ps1 — он передаёт номер версии из pyproject.toml.
;
; Решения пользователя 2026-10-10:
; - программа ставится в профиль пользователя, права администратора не нужны;
; - база пользователя лежит в "Документы\meteoRegiK" (meteoregik/db/session.py),
;   установщик её не создаёт и не трогает: ни при обновлении, ни при удалении;
; - новая версия ставится поверх старой тем же установщиком.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "meteoRegiK"
#define AppExe "meteoRegiK.exe"

[Setup]
; AppId не менять: по нему установщик узнаёт уже установленную программу
; и обновляет её, а не ставит рядом вторую.
AppId={{6F0B6E0C-5D43-4B3B-9A57-3E1B2C9D7A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Naisa
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=meteoRegiK-setup-{#AppVersion}
SetupIconFile=..\src\meteoregik\static\app_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Обновление поверх запущенной программы: установщик предложит её закрыть
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Файлы прошлой версии: в новой сборке состав библиотек может измениться,
; и оставшиеся старые файлы мешали бы новым
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\meteoRegiK\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
