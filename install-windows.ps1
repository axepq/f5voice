# F5Voice for Windows: one-command install (PowerShell).
#   irm -TimeoutSec 60 https://raw.githubusercontent.com/axepq/f5voice/main/install-windows.ps1 | iex
# From a repository clone: powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
# Downloads the sources as a zip (no git needed), installs Python via winget if missing,
# creates its own Python environment with faster-whisper, downloads the model, adds
# Desktop / Start menu / Startup shortcuts and launches F5Voice (no console, tray icon).
# ASCII only on purpose: Windows PowerShell 5.1 misreads UTF-8 without BOM, and a BOM breaks irm | iex.
# Never calls `exit` when piped through iex: that would close the whole PowerShell window.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"          # Python prints Cyrillic/arrows even on cp1252 consoles
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}   # and the console shows them
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

$IsFileMode = [bool]$MyInvocation.MyCommand.Path
function Step($m) { Write-Host ""; Write-Host "> $m" -ForegroundColor Cyan }
function Fail($m) { throw "F5VOICE: $m" }
function Finish($code) {
    if ($IsFileMode) { Write-Host ""; Read-Host "Press Enter to close this window" | Out-Null; exit $code }
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function New-Shortcut($path, $target, $arguments, $workdir, $desc) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $target
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $workdir
    $lnk.Description = $desc
    $lnk.Save()
}

$HomeDir = if ($env:F5VOICE_HOME) { $env:F5VOICE_HOME } else { Join-Path $HOME ".f5voice" }
$Log = Join-Path $HomeDir "f5voice.log"

try {
    Write-Host "F5Voice: Windows install starting. Takes a few minutes: the speech model is ~1.6 GB."
    $Repo = "axepq/f5voice"
    $ZipUrl = "https://github.com/$Repo/archive/refs/heads/main.zip"
    $Src = Join-Path $HomeDir "src"
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

    # Sources: running from a file inside a clone -> use it; otherwise (irm | iex) download the zip.
    $RepoDir = if ($IsFileMode) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { $null }
    if ($RepoDir -and (Test-Path (Join-Path $RepoDir "python\dictate.py"))) {
        $isLink = (Test-Path $Src) -and ((Get-Item $Src).Attributes -band [IO.FileAttributes]::ReparsePoint)
        $target = if ($isLink) { (Get-Item $Src).Target } else { $null }
        if (($RepoDir -ne $Src) -and ($target -ne $RepoDir)) {
            if (Test-Path $Src) { if ($isLink) { (Get-Item $Src).Delete() } else { Remove-Item -Recurse -Force $Src } }
            New-Item -ItemType Junction -Path $Src -Target $RepoDir | Out-Null
            Write-Host "Sources: $RepoDir (linked as $Src)"
        }
    } else {
        Step "Downloading F5Voice sources ($ZipUrl)"
        $Tmp = Join-Path $HomeDir "download"
        if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
        New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
        $Zip = Join-Path $Tmp "f5voice.zip"
        try { Invoke-WebRequest -Uri $ZipUrl -OutFile $Zip -TimeoutSec 120 -UseBasicParsing }
        catch { Fail "Could not download the sources: $($_.Exception.Message). Check access to github.com (a VPN may be needed)." }
        Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
        $Extracted = Join-Path $Tmp "f5voice-main"
        if (-not (Test-Path (Join-Path $Extracted "python\dictate.py"))) { Fail "Archive unpacked but sources are missing" }
        if (Test-Path $Src) {
            if ((Get-Item $Src).Attributes -band [IO.FileAttributes]::ReparsePoint) { (Get-Item $Src).Delete() } else { Remove-Item -Recurse -Force $Src }
        }
        Move-Item $Extracted $Src
        Remove-Item -Recurse -Force $Tmp
        Write-Host "Sources: $Src"
    }

    function Test-Python($exe) {
        try {
            $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v -ge [version]"3.9")) { return $true }
        } catch {}
        return $false
    }
    $Py = $null
    foreach ($c in @("python", "python3", "py")) { if (-not $Py -and (Test-Python $c)) { $Py = $c } }
    if (-not $Py) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail "Python not found and winget is unavailable. Install Python 3.12 from python.org (check 'Add to PATH') and run again."
        }
        Step "Python not found - installing Python 3.12 with winget (per-user, no admin prompt)"
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        Refresh-Path
        if (Test-Python "python") { $Py = "python" }
        else { Fail "Python installed but not visible in PATH yet. Open a new PowerShell window and run the install command again." }
    }
    Write-Host "Python: $Py"

    Step "Python environment in $HomeDir\venv"
    $VenvPy = Join-Path $HomeDir "venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) { & $Py -m venv (Join-Path $HomeDir "venv"); if ($LASTEXITCODE -ne 0) { Fail "could not create venv" } }
    & $VenvPy -m pip install --upgrade pip
    Step "Dependencies (faster-whisper and friends, ~200 MB)"
    & $VenvPy -m pip install -r (Join-Path $Src "python\requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "pip could not install dependencies" }

    $Config = Join-Path $HomeDir "config.json"
    if (-not (Test-Path $Config)) { Copy-Item (Join-Path $Src "python\config.example.json") $Config }
    $Cfg = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    $Model = if ($Cfg.model) { $Cfg.model } else { "large-v3-turbo" }
    $Hotkey = if ($Cfg.hotkey) { $Cfg.hotkey } else { "<ctrl>+<alt>+space" }

    Step "Model $Model (first time ~1.6 GB, progress below)"
    $env:PYTHONWARNINGS = "ignore"
    & $VenvPy (Join-Path $Src "python\download_model.py") $Model
    $dl = $LASTEXITCODE
    Remove-Item Env:\PYTHONWARNINGS -ErrorAction SilentlyContinue
    if ($dl -ne 0) { Fail "could not download model $Model - check internet and the model name in $Config" }

    Step "Core self-test"
    Push-Location $Src
    & $VenvPy -m common.selftest | Select-Object -Last 1
    $selftest = $LASTEXITCODE
    Pop-Location
    if ($selftest -ne 0) { Fail "core self-test failed: cd $Src; $VenvPy -m common.selftest" }

    $Script = Join-Path $Src "python\dictate.py"
    Step "Checking microphone and model load in the foreground (may take a minute on CPU)"
    & $VenvPy $Script --check
    if ($LASTEXITCODE -ne 0) { Fail ("model or microphone check failed (exit code $LASTEXITCODE). The messages above say why." + $(if ($LASTEXITCODE -eq -1073741795) { " Exit code 0xC000001D = this CPU lacks instructions required by CTranslate2." } else { "" })) }

    if (-not $env:F5VOICE_NO_SERVICE) {
        Step "Shortcuts: Startup, Desktop, Start menu"
        $PythonW = Join-Path $HomeDir "venv\Scripts\pythonw.exe"
        $LaunchArgs = "`"$Script`" --log"
        New-Shortcut (Join-Path ([Environment]::GetFolderPath("Startup")) "F5Voice.lnk") $PythonW $LaunchArgs $HomeDir "F5Voice: local dictation (autostart)"
        New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "F5Voice.lnk") $PythonW $LaunchArgs $HomeDir "F5Voice: start dictation service"
        New-Shortcut (Join-Path ([Environment]::GetFolderPath("Programs")) "F5Voice.lnk") $PythonW $LaunchArgs $HomeDir "F5Voice: start dictation service"

        Step "Launching"
        Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
            Where-Object { $_.CommandLine -like "*python\dictate.py*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        if (Test-Path $Log) { Add-Content -Path $Log -Value "" -Encoding UTF8 }
        $mark = if (Test-Path $Log) { (Get-Content $Log -Encoding UTF8).Count } else { 0 }
        Start-Process -FilePath $PythonW -ArgumentList $LaunchArgs -WorkingDirectory $HomeDir
        Write-Host "Waiting for F5Voice to load the model (up to 90 s)..."
        $deadline = (Get-Date).AddSeconds(90)
        $ready = $false
        do {
            Start-Sleep -Seconds 3
            $alive = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*python\dictate.py*" }
            $fresh = if (Test-Path $Log) { (Get-Content $Log -Encoding UTF8) | Select-Object -Skip $mark } else { @() }
            if ($fresh -match "\[READY\]") { $ready = $true }
            if ($fresh -match "\[FATAL\]") { break }
        } until ($ready -or -not $alive -or (Get-Date) -gt $deadline)
        if (Test-Path $Log) { Write-Host "Log tail ($Log):"; Get-Content $Log -Tail 8 -Encoding UTF8 | ForEach-Object { "    $_" } }
        if (-not $ready) { Fail "F5Voice did not report readiness. The log above says why; send it to the author." }
    }

    Write-Host ""
    Write-Host "Done. F5Voice is running and will start with Windows." -ForegroundColor Green
    Write-Host "  $Hotkey - record, press again - the text is typed where the cursor is, Esc - cancel."
    Write-Host "  It lives as a microphone icon in the tray (bottom right, maybe behind the ^ arrow)."
    Write-Host "  Shortcuts 'F5Voice' on the Desktop and in the Start menu start it again if you closed it."
    Write-Host "  Settings: $Config (hotkey, model, languages). Log: $Log"
    Write-Host "  NVIDIA GPU: works on CPU by default; to use the GPU see python/README.md (needs cuBLAS + cuDNN for CUDA 12)."
    Finish 0
} catch {
    $raw = $_.Exception.Message
    Write-Host ""
    if ($raw -like "F5VOICE: *") {
        Write-Host ("x " + $raw.Substring(9)) -ForegroundColor Red
    } else {
        Write-Host "x Unexpected error: $raw" -ForegroundColor Red
        if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    }
    Write-Host "Installation did not finish. Send this whole output to the author. Log (if any): $Log" -ForegroundColor Yellow
    Finish 1
}
