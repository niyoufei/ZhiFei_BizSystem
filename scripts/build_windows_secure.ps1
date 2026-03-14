Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "Installing runtime dependencies..."
python -m pip install -r requirements.txt

Write-Host "Installing Windows build tooling..."
python -m pip install -e ".[windows-build]"

Write-Host "Building PyInstaller bundle..."
pyinstaller .\packaging\windows\QingtianBidSecure.spec --noconfirm

$isccCandidates = @(
  $env:INNO_SETUP_ISCC,
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }

if ($isccCandidates.Count -gt 0) {
  $iscc = $isccCandidates[0]
  Write-Host "Building installer with Inno Setup..."
  & $iscc ".\packaging\windows\QingtianBidSecure.iss"
  Write-Host "Installer ready under dist\installer"
} else {
  Write-Warning "Inno Setup not found. PyInstaller output is available under dist\QingtianBidSecure"
}
