# F5Voice для Windows: установка одной командой (PowerShell).
#   irm https://raw.githubusercontent.com/axepq/f5voice/main/install-windows.ps1 | iex
# Можно и из клона репозитория: powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
# Ставит Git и Python через winget (если их нет), своё Python-окружение с faster-whisper,
# скачивает модель, добавляет ярлык в автозагрузку и запускает (без консоли, значок в трее).
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($m) { Write-Host "> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "x $m" -ForegroundColor Red; exit 1 }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget-Install($id, $what) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "Нет winget. Обнови Windows (нужен App Installer из Microsoft Store) или поставь $what сам и запусти снова." }
    Step "Ставлю $what через winget"
    winget install -e --id $id --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
}

$RepoUrl = "https://github.com/axepq/f5voice.git"
$HomeDir = if ($env:F5VOICE_HOME) { $env:F5VOICE_HOME } else { Join-Path $HOME ".f5voice" }
$Src = Join-Path $HomeDir "src"
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

# Откуда исходники: запущены из файла внутри клона — используем его,
# иначе (irm | iex) скачиваем репозиторий и перезапускаемся из него.
$ScriptPath = $MyInvocation.MyCommand.Path
$RepoDir = if ($ScriptPath) { Split-Path -Parent $ScriptPath } else { $null }
if ($RepoDir -and (Test-Path (Join-Path $RepoDir "python\dictate.py"))) {
    $resolved = if (Test-Path $Src) { (Get-Item $Src).Target } else { $null }
    if (($RepoDir -ne $Src) -and ($resolved -ne $RepoDir)) {
        if ((Test-Path $Src) -and -not ((Get-Item $Src).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Fail "$Src уже существует и это не ссылка. Убери его или запусти установщик оттуда."
        }
        if (Test-Path $Src) { (Get-Item $Src).Delete() }
        New-Item -ItemType Junction -Path $Src -Target $RepoDir | Out-Null
        Step "Исходники: $RepoDir (ссылка $Src)"
    }
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Winget-Install "Git.Git" "Git"
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git поставлен, но не виден: открой новое окно PowerShell и запусти команду снова." }
    }
    if (Test-Path (Join-Path $Src ".git")) { Step "Обновляю исходники F5Voice"; git -C $Src pull --ff-only --quiet }
    else {
        if (Test-Path $Src) { Fail "$Src уже существует, но это не репозиторий. Убери его и запусти команду снова." }
        Step "Скачиваю F5Voice"; git clone --depth 1 --quiet $RepoUrl $Src
    }
    if ($LASTEXITCODE -ne 0) { Fail "git не смог получить исходники" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "install-windows.ps1")
    exit $LASTEXITCODE
}

function Test-Python($exe) {
    try {
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]"3.9")) { return $true }
    } catch {}
    return $false
}
$Py = $null
foreach ($c in @("python", "python3")) { if (-not $Py -and (Test-Python $c)) { $Py = $c } }
if (-not $Py) {
    Winget-Install "Python.Python.3.12" "Python 3.12"
    if (Test-Python "python") { $Py = "python" } else { Fail "Python поставлен, но не виден в PATH: открой новое окно PowerShell и запусти установщик снова" }
}

Step "Python-окружение"
$VenvPy = Join-Path $HomeDir "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $HomeDir "venv"); if ($LASTEXITCODE -ne 0) { Fail "не создался venv" } }
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $Src "python\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip не смог поставить зависимости" }

$Config = Join-Path $HomeDir "config.json"
if (-not (Test-Path $Config)) { Copy-Item (Join-Path $Src "python\config.example.json") $Config }
$Cfg = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$Model = if ($Cfg.model) { $Cfg.model } else { "large-v3-turbo" }
$Hotkey = if ($Cfg.hotkey) { $Cfg.hotkey } else { "<ctrl>+<alt>+space" }

Step "Модель $Model (первый раз около 1,6 ГБ)"
& $VenvPy -c "from faster_whisper.utils import download_model; download_model('$Model')"
if ($LASTEXITCODE -ne 0) { Fail "не удалось скачать модель $Model - проверь интернет и имя модели в $Config" }

Step "Проверка ядра"
Push-Location $Src
& $VenvPy -m common.selftest | Out-Null
$selftest = $LASTEXITCODE
Pop-Location
if ($selftest -ne 0) { Fail "самопроверка ядра не прошла: cd $Src; $VenvPy -m common.selftest" }

Step "Автозагрузка"
$Startup = [Environment]::GetFolderPath("Startup")
$PythonW = Join-Path $HomeDir "venv\Scripts\pythonw.exe"
$Script = Join-Path $Src "python\dictate.py"
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut((Join-Path $Startup "F5Voice.lnk"))
$Lnk.TargetPath = $PythonW
$Lnk.Arguments = "`"$Script`" --log"
$Lnk.WorkingDirectory = $HomeDir
$Lnk.Description = "F5Voice: локальная диктовка"
$Lnk.Save()

Step "Запуск"
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*python\dictate.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Process -FilePath $PythonW -ArgumentList "`"$Script`" --log" -WorkingDirectory $HomeDir

Write-Host ""
Write-Host "Готово. F5Voice запущен и будет стартовать при входе в Windows." -ForegroundColor Green
Write-Host "  $Hotkey - запись, ещё раз - текст в активном поле, Esc - отмена. Значок микрофона в области уведомлений."
Write-Host "  Настройки: $Config (клавиша, модель, языки), лог: $(Join-Path $HomeDir 'f5voice.log')"
Write-Host "  Видеокарта NVIDIA: в config.json device=cuda, compute_type=float16 (нужны CUDA 12 и cuDNN 9)."
