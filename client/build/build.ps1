# Build the standalone Cyber-Scout Client for Windows into client\dist\cyberscout-client-windows-<arch>.exe
#   powershell -ExecutionPolicy Bypass -File client\build\build.ps1
# Needs Python 3.9+ from python.org (or `py` launcher). Uses a throwaway venv under .work\client-build\.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Client = Split-Path -Parent $Here
$Repo = Split-Path -Parent $Client
$Work = Join-Path $Repo ".work\client-build"
$Py = if ($env:PYTHON) { $env:PYTHON } else { "py" }
$Arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }

New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Venv = Join-Path $Work "venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\pyinstaller.exe"))) {
  & $Py -3 -m venv $Venv
  & (Join-Path $Venv "Scripts\python.exe") -m pip install --quiet --upgrade pip
  & (Join-Path $Venv "Scripts\python.exe") -m pip install --quiet "pyinstaller>=6.6,<7"
}
& (Join-Path $Venv "Scripts\python.exe") (Join-Path $Here "make_bundle.py")
& (Join-Path $Venv "Scripts\pyinstaller.exe") --noconfirm --clean --log-level WARN `
  --distpath (Join-Path $Work "dist") --workpath (Join-Path $Work "work") (Join-Path $Here "cyberscout.spec")
New-Item -ItemType Directory -Force -Path (Join-Path $Client "dist") | Out-Null
$Out = Join-Path $Client "dist\cyberscout-client-windows-$Arch.exe"
Copy-Item (Join-Path $Work "dist\cyberscout-client.exe") $Out -Force
(Get-FileHash $Out -Algorithm SHA256).Hash.ToLower() + "  " + (Split-Path -Leaf $Out) | Out-File -Encoding ascii "$Out.sha256"
Write-Host "built $Out"
