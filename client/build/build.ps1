# Build the standalone Cyber-Scout Client for Windows into client\dist\cyberscout-client-windows-<x64|arm64>.exe
#   powershell -ExecutionPolicy Bypass -File client\build\build.ps1
#   $env:PYTHON = "C:\Python312\python.exe"; powershell -ExecutionPolicy Bypass -File client\build\build.ps1
#   $env:CS_SMOKE = "1"   # also run build\smoke_test.py on the result ($env:CS_SMOKE_SERVER: also Test connection)
# Needs Python 3.9+ from python.org (the `py` launcher is used when PYTHON is not set). Uses a throwaway venv under
# .work\client-build\ (nothing is installed globally). Works in Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Client = Split-Path -Parent $Here
$Repo = Split-Path -Parent $Client
$Work = if ($env:CS_BUILD_WORK) { $env:CS_BUILD_WORK } else { Join-Path $Repo ".work\client-build" }
$Arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }

# native commands don't throw on a non-zero exit code: check every one
function Invoke-Checked {
  param([string]$Exe, [string[]]$Arguments)
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

if ($env:PYTHON) { $PyExe = $env:PYTHON; $PyArgs = @() } else { $PyExe = "py"; $PyArgs = @("-3") }
$Tag = (& $PyExe @PyArgs -c "import sys, platform; print('py%d%d-%s' % (sys.version_info[0], sys.version_info[1], platform.machine().lower()))").Trim()
if ($LASTEXITCODE -ne 0) { throw "no Python found (set PYTHON or install the py launcher)" }

New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Venv = Join-Path $Work "venv-$Tag"
$VPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path (Join-Path $Venv "Scripts\pyinstaller.exe"))) {
  if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
  Invoke-Checked $PyExe ($PyArgs + @("-m", "venv", $Venv))
  Invoke-Checked $VPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
  Invoke-Checked $VPy @("-m", "pip", "install", "--quiet", "pyinstaller>=6.10,<7")
}
Invoke-Checked $VPy @((Join-Path $Here "make_bundle.py"))
$B = Join-Path $Work "windows-$Arch"
Invoke-Checked (Join-Path $Venv "Scripts\pyinstaller.exe") @("--noconfirm", "--clean", "--log-level", "WARN",
  "--distpath", (Join-Path $B "dist"), "--workpath", (Join-Path $B "work"), (Join-Path $Here "cyberscout.spec"))
New-Item -ItemType Directory -Force -Path (Join-Path $Client "dist") | Out-Null
$Name = "cyberscout-client-windows-$Arch.exe"
$Out = Join-Path $Client "dist\$Name"
Copy-Item (Join-Path $B "dist\cyberscout-client.exe") $Out -Force
# "<sha256>  <name>" like sha256sum, ASCII, no BOM (sha256sum -c / shasum -c can check it)
$Hash = (Get-FileHash $Out -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText("$Out.sha256", "$Hash  $Name`n", (New-Object System.Text.ASCIIEncoding))
Write-Host ("built $Out ({0:N1} MB)" -f ((Get-Item $Out).Length / 1MB))
if ($env:CS_SMOKE) {
  $SmokeArgs = @((Join-Path $Here "smoke_test.py"), $Out)
  if ($env:CS_SMOKE_SERVER) { $SmokeArgs += @("--server", $env:CS_SMOKE_SERVER) }
  Invoke-Checked $VPy $SmokeArgs
}
