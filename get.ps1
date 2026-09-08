# F5Voice: установка одной командой на Windows (PowerShell):
#   irm https://raw.githubusercontent.com/axepq/f5voice/main/get.ps1 | iex
# Ставит git через winget, если его нет, скачивает исходники в ~\.f5voice\src
# и запускает установщик (он поставит Python, окружение и модель).
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoUrl = "https://github.com/axepq/f5voice.git"
$HomeDir = if ($env:F5VOICE_HOME) { $env:F5VOICE_HOME } else { Join-Path $HOME ".f5voice" }
$Src = Join-Path $HomeDir "src"

function Step($m) { Write-Host "> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "x $m" -ForegroundColor Red; exit 1 }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Нет winget. Обнови Windows или поставь Git с git-scm.com и запусти команду снова."
    }
    Step "Ставлю Git через winget"
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git поставлен, но не виден: открой новое окно PowerShell и запусти команду снова." }
}

New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
if (Test-Path (Join-Path $Src ".git")) {
    Step "Обновляю исходники F5Voice"
    git -C $Src pull --ff-only --quiet
} else {
    if (Test-Path $Src) { Fail "$Src уже существует, но это не репозиторий. Убери его и запусти команду снова." }
    Step "Скачиваю F5Voice"
    git clone --depth 1 --quiet $RepoUrl $Src
}
if ($LASTEXITCODE -ne 0) { Fail "git не смог получить исходники" }

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "other\install.ps1")
exit $LASTEXITCODE
