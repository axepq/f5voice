# Полное удаление F5Voice с Windows. Кэш модели в %USERPROFILE%\.cache\huggingface не трогает.
$HomeDir = if ($env:F5VOICE_HOME) { $env:F5VOICE_HOME } else { Join-Path $HOME ".f5voice" }
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*python\dictate.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "F5Voice.lnk") -ErrorAction SilentlyContinue
$Src = Join-Path $HomeDir "src"
if ((Test-Path $Src) -and ((Get-Item $Src).Attributes -band [IO.FileAttributes]::ReparsePoint)) { (Get-Item $Src).Delete() }
Remove-Item -Recurse -Force $HomeDir -ErrorAction SilentlyContinue
Write-Host "F5Voice удалён."
