# Сборка meteoRegiK: приложение (PyInstaller) -> самопроверка -> установщик (Inno Setup).
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Результат: dist\meteoRegiK-setup-<версия>.exe. Версия берётся из pyproject.toml.
# Нужен Inno Setup 6 (один раз: winget install JRSoftware.InnoSetup).
#   -SkipInstaller  — только собрать и проверить приложение, без установщика.

param([switch]$SkipInstaller)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$versionLine = Get-Content 'pyproject.toml' | Where-Object { $_ -match '^version\s*=\s*"(.+)"' } | Select-Object -First 1
if (-not $versionLine) { throw 'В pyproject.toml не найдена строка version = "..."' }
$version = [regex]::Match($versionLine, '"(.+)"').Groups[1].Value
Write-Host "meteoRegiK $version"

# --- 1. приложение ---
Write-Host '1/3 Сборка приложения (PyInstaller)...'
uv run pyinstaller packaging/meteoregik.spec --noconfirm --log-level WARN
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller завершился с ошибкой' }

# --- 2. самопроверка: собранный exe запускается, создаёт базу и открывает карту ---
# База — во временной папке (METEOREGIK_DATA_DIR), настоящие данные не затрагиваются.
Write-Host '2/3 Самопроверка собранного приложения...'
$check = Join-Path $env:TEMP "meteoregik-smoke-$([guid]::NewGuid().ToString('N'))"
$env:METEOREGIK_DATA_DIR = Join-Path $check 'data'
$env:METEOREGIK_SMOKE_TEST = Join-Path $check 'out'
try {
    $process = Start-Process -FilePath 'dist\meteoRegiK\meteoRegiK.exe' -PassThru
    if (-not $process.WaitForExit(120000)) {
        $process.Kill()
        throw 'Самопроверка: приложение не закрылось за 2 минуты'
    }
    $reportPath = Join-Path $check 'out\smoke.json'
    if (-not (Test-Path $reportPath)) { throw 'Самопроверка: приложение не записало отчёт (не запустилось?)' }
    $report = Get-Content $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.error) { throw "Самопроверка: $($report.error)" }
    if (-not $report.map_page_loaded) { throw 'Самопроверка: страница карты не загрузилась' }
    if ($report.window_title -ne "meteoRegiK $version") { throw "Самопроверка: в заголовке окна '$($report.window_title)', ожидался номер версии $version" }
    if ($report.directory_stations -lt 1) { throw 'Самопроверка: справочник станций пуст (шаблон базы не попал в сборку?)' }
    Write-Host "    карта открылась, подложка: $($report.map_base), станций в справочнике: $($report.directory_stations)"
}
finally {
    Remove-Item Env:METEOREGIK_DATA_DIR, Env:METEOREGIK_SMOKE_TEST -ErrorAction SilentlyContinue
    if (Test-Path $check) { Remove-Item $check -Recurse -Force -ErrorAction SilentlyContinue }
}

if ($SkipInstaller) { Write-Host 'Готово: dist\meteoRegiK (установщик пропущен)'; exit 0 }

# --- 3. установщик ---
Write-Host '3/3 Сборка установщика (Inno Setup)...'
$candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
)
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Не найден Inno Setup 6 (ISCC.exe). Установите: winget install JRSoftware.InnoSetup' }
& $iscc "/DAppVersion=$version" '/Q' 'packaging\meteoregik.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup завершился с ошибкой' }

$installer = Get-Item "dist\meteoRegiK-setup-$version.exe"
Write-Host ("Готово: {0} ({1:N0} МБ)" -f $installer.FullName, ($installer.Length / 1MB))
