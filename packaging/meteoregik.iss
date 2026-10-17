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

[Code]
// Обновление (решение пользователя 2026-10-18): если программа уже
// установлена, мастер говорит не "Установка", а "Обновление с версии X до
// версии Y". Установленную версию Inno Setup сам записывает в реестр
// (DisplayVersion в ключе удаления программы, по AppId). Запуск установщика
// той же или более старой версии — с вопросом, по умолчанию "Нет".

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F0B6E0C-5D43-4B3B-9A57-3E1B2C9D7A41}_is1';
  ModeInstall = 0;
  ModeUpgrade = 1;
  ModeReinstall = 2;
  ModeDowngrade = 3;

var
  InstalledVersion: String;
  Mode: Integer;

function ReadInstalledVersion: String;
begin
  Result := '';
  // Программа ставится без прав администратора — запись в HKCU; HKLM — на
  // случай установки, сделанной когда-то от имени администратора
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Result) then
    RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', Result);
end;

// Очередное число версии "0.10.2" начиная с позиции Pos; Pos сдвигается за точку
function NextNumber(const Version: String; var Pos: Integer): Integer;
var
  Digits: String;
begin
  Digits := '';
  while (Pos <= Length(Version)) and (Version[Pos] <> '.') do
  begin
    Digits := Digits + Version[Pos];
    Pos := Pos + 1;
  end;
  Pos := Pos + 1;
  Result := StrToIntDef(Digits, 0);
end;

// По числам, а не строкой: "0.10.0" новее "0.9.0"
function CompareVersions(const A, B: String): Integer;
var
  PosA, PosB, NumA, NumB: Integer;
begin
  Result := 0;
  PosA := 1;
  PosB := 1;
  while (Result = 0) and ((PosA <= Length(A)) or (PosB <= Length(B))) do
  begin
    NumA := NextNumber(A, PosA);
    NumB := NextNumber(B, PosB);
    if NumA < NumB then Result := -1
    else if NumA > NumB then Result := 1;
  end;
end;

function InitializeSetup: Boolean;
var
  Question: String;
begin
  Result := True;
  InstalledVersion := ReadInstalledVersion;
  if InstalledVersion = '' then
    Mode := ModeInstall
  else if CompareVersions('{#AppVersion}', InstalledVersion) > 0 then
    Mode := ModeUpgrade
  else if CompareVersions('{#AppVersion}', InstalledVersion) = 0 then
    Mode := ModeReinstall
  else
    Mode := ModeDowngrade;

  if Mode = ModeReinstall then
    Question := 'Версия {#AppVersion} программы {#AppName} уже установлена.' + #13#10#13#10 +
      'Установить её заново? Это нужно, только если программа перестала запускаться ' +
      'или работает с ошибками. Ваши данные сохранятся.'
  else if Mode = ModeDowngrade then
    Question := 'Установлена более новая версия {#AppName}: ' + InstalledVersion + '.' + #13#10 +
      'Этот установщик — более старой версии {#AppVersion}.' + #13#10#13#10 +
      'Заменить новую версию старой? Старая версия может не открыть базу данных, ' +
      'обновлённую новой версией.'
  else
    Exit;
  // В тихом режиме (/SILENT) вопросов нет — установка продолжается
  Result := SuppressibleMsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDYES) = IDYES;
end;

function ActionTitle: String;
begin
  case Mode of
    ModeUpgrade: Result := 'Обновление';
    ModeReinstall: Result := 'Переустановка';
    ModeDowngrade: Result := 'Замена версии';
  else
    Result := '';
  end;
end;

procedure InitializeWizard;
begin
  if Mode <> ModeInstall then
    WizardForm.Caption := ActionTitle + ' — {#AppName}';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if Mode = ModeInstall then
    Exit;
  case CurPageID of
    wpSelectTasks:
      if Mode = ModeUpgrade then
        WizardForm.SelectTasksLabel.Caption := 'Выберите дополнительные задачи, которые должны ' +
          'выполниться при обновлении {#AppName}, после этого нажмите «Далее»:';
    wpReady:
      begin
        if Mode = ModeUpgrade then
          WizardForm.PageNameLabel.Caption := 'Всё готово к обновлению'
        else
          WizardForm.PageNameLabel.Caption := 'Всё готово к установке';
        WizardForm.PageDescriptionLabel.Caption := 'Установлена версия ' + InstalledVersion + '.';
        case Mode of
          ModeUpgrade:
            WizardForm.ReadyLabel.Caption := 'Программа {#AppName} будет обновлена с версии ' +
              InstalledVersion + ' до версии {#AppVersion}.';
          ModeReinstall:
            WizardForm.ReadyLabel.Caption := 'Версия {#AppVersion} программы {#AppName} будет ' +
              'установлена заново.';
          ModeDowngrade:
            WizardForm.ReadyLabel.Caption := 'Версия ' + InstalledVersion + ' будет заменена ' +
              'более старой версией {#AppVersion}.';
        end;
        if Mode = ModeUpgrade then
          WizardForm.NextButton.Caption := 'Обновить'
        else
          WizardForm.NextButton.Caption := 'Установить';
      end;
    wpInstalling:
      begin
        WizardForm.PageNameLabel.Caption := ActionTitle;
        if Mode = ModeUpgrade then
          WizardForm.PageDescriptionLabel.Caption := 'Подождите, идёт обновление {#AppName} до версии {#AppVersion}.'
        else
          WizardForm.PageDescriptionLabel.Caption := 'Подождите, идёт установка {#AppName} {#AppVersion}.';
      end;
    wpFinished:
      begin
        if Mode = ModeUpgrade then
        begin
          WizardForm.FinishedHeadingLabel.Caption := '{#AppName} обновлена до версии {#AppVersion}';
          WizardForm.FinishedLabel.Caption := 'Программа обновлена с версии ' + InstalledVersion +
            ' до версии {#AppVersion}.' + #13#10#13#10 + 'Нажмите «Завершить», чтобы выйти из программы установки.';
        end
        else
          WizardForm.FinishedLabel.Caption := 'Программа {#AppName} {#AppVersion} установлена.' + #13#10#13#10 +
            'Нажмите «Завершить», чтобы выйти из программы установки.';
      end;
  end;
end;

// Сведения под текстом страницы "Всё готово": при обновлении — какая версия
// какой сменится и что будет с базой (в подписи над списком помещается одна
// строка — поэтому здесь); папку и ярлыки Inno Setup дописывает сам
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := '';
  if Mode <> ModeInstall then
  begin
    Result := 'Версия:' + NewLine + Space + InstalledVersion + ' → {#AppVersion}' + NewLine + NewLine +
      'База данных:' + NewLine + Space + 'сохранится, удалять и переносить её не нужно';
    if Mode = ModeUpgrade then
      Result := Result + ';' + NewLine + Space + 'если новой версии нужно досчитать в ней новые показатели,' + NewLine +
        Space + 'она сделает это при первом запуске, сохранив перед этим копию базы';
    Result := Result + NewLine + NewLine;
  end;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;
