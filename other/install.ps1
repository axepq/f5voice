# Установка F5Voice на Windows одной командой (PowerShell):
#   gh repo clone axepq/f5voice "$HOME\.f5voice\src"; powershell -ExecutionPolicy Bypass -File "$HOME\.f5voice\src\other\install.ps1"
# Ставит Python и Git через winget, если их нет, окружение с faster-whisper, скачивает
# модель, добавляет ярлык в автозагрузку и запускает сейчас (без консоли, значок в трее).
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($m) { Write-Host "> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "x $m" -ForegroundColor Red; exit 1 }

$RepoUrl = "https://github.com/axepq/f5voice.git"
$HomeDir = if ($env:F5VOICE_HOME) { $env:F5VOICE_HOME } else { Join-Path $HOME ".f5voice" }
$Src = Join-Path $HomeDir "src"
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
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
    Step "Python не найден - ставлю Python 3.12 через winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "нет winget: поставь Python 3.12 с python.org (галочка Add to PATH) и запусти снова" }
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    if (Test-Python "python") { $Py = "python" } else { Fail "Python поставлен, но не виден в PATH: открой новое окно PowerShell и запусти установщик снова" }
}

# Исходники: запуск из клона репозитория или скачать.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
if (Test-Path (Join-Path $RepoDir "other\dictate.py")) {
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
        Step "Git не найден - ставлю через winget"
        winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements | Out-Null
        Refresh-Path
    }
    if (Test-Path (Join-Path $Src ".git")) { Step "Обновляю исходники"; git -C $Src pull --ff-only }
    else { Step "Скачиваю исходники"; git clone --depth 1 $RepoUrl $Src }
    if ($LASTEXITCODE -ne 0) { Fail "git не смог получить исходники" }
}

Step "Python-окружение"
$VenvPy = Join-Path $HomeDir "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $HomeDir "venv"); if ($LASTEXITCODE -ne 0) { Fail "не создался venv" } }
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $Src "other\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip не смог поставить зависимости" }

$Config = Join-Path $HomeDir "config.json"
if (-not (Test-Path $Config)) { Copy-Item (Join-Path $Src "other\config.example.json") $Config }
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
$Script = Join-Path $Src "other\dictate.py"
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut((Join-Path $Startup "F5Voice.lnk"))
$Lnk.TargetPath = $PythonW
$Lnk.Arguments = "`"$Script`" --log"
$Lnk.WorkingDirectory = $HomeDir
$Lnk.Description = "F5Voice: локальная диктовка"
$Lnk.Save()

Step "Запуск"
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*other\dictate.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Process -FilePath $PythonW -ArgumentList "`"$Script`" --log" -WorkingDirectory $HomeDir

Write-Host ""
Write-Host "Готово. F5Voice запущен и будет стартовать при входе в Windows." -ForegroundColor Green
Write-Host "  $Hotkey - запись, ещё раз - текст в активном поле, Esc - отмена. Значок микрофона в области уведомлений."
Write-Host "  Настройки: $Config (клавиша, модель, языки), лог: $(Join-Path $HomeDir 'f5voice.log')"
Write-Host "  Видеокарта NVIDIA: в config.json device=cuda, compute_type=float16 (нужны CUDA 12 и cuDNN 9)."
