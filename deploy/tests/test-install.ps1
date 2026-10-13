<#
  Checks for deploy/install.ps1 that change nothing on the machine: PSScriptAnalyzer (when the module is
  available; -InstallAnalyzer installs it for the current user, for CI) and -DryRun scenarios.
  Windows PowerShell 5.1 and PowerShell 7 (also runs under pwsh on Linux/macOS: the installer then warns that it
  is the Windows installer and carries on in -DryRun).
    powershell -ExecutionPolicy Bypass -File deploy\tests\test-install.ps1
    pwsh -File deploy/tests/test-install.ps1 -InstallAnalyzer
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'test output')]
param([switch]$InstallAnalyzer)

$ErrorActionPreference = 'Stop'
$Install = Join-Path (Split-Path -Parent $PSScriptRoot) 'install.ps1'
$Exe = (Get-Process -Id $PID).Path   # the same PowerShell (5.1 or 7) that runs this test
$script:Fails = 0
function Write-Pass([string]$m) { Write-Host "ok    $m" }
function Write-Fail([string]$m) { Write-Host "FAIL  $m" -ForegroundColor Red; $script:Fails++ }
function Invoke-Installer([string[]]$a) {
  $ErrorActionPreference = 'Continue'
  $out = & $Exe -NoProfile -ExecutionPolicy Bypass -File $Install @a 2>&1 | ForEach-Object { "$_" }
  return @{ Code = $LASTEXITCODE; Out = (@($out) -join "`n") }
}
function Test-Output([string]$name, $r, [string[]]$patterns) {
  foreach ($p in $patterns) {
    if ($r.Out -notmatch $p) { Write-Fail "${name}: output lacks /$p/"; Write-Host $r.Out; return }
  }
  Write-Pass $name
}

# syntax: the parser must accept the file
$tokens = $null; $errs = $null
[void][Management.Automation.Language.Parser]::ParseFile($Install, [ref]$tokens, [ref]$errs)
if ($errs.Count -eq 0) { Write-Pass "parses (PowerShell $($PSVersionTable.PSVersion))" } else { Write-Fail "parse errors: $($errs -join '; ')" }
if (([IO.File]::ReadAllBytes($Install) | Where-Object { $_ -gt 127 }).Count -eq 0) { Write-Pass 'ASCII only (PS 5.1 reads BOM-less files as ANSI)' }
else { Write-Fail 'non-ASCII characters in install.ps1' }

if ($InstallAnalyzer -and -not (Get-Module -ListAvailable PSScriptAnalyzer)) {
  Install-Module PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck
}
if (Get-Module -ListAvailable PSScriptAnalyzer) {
  Import-Module PSScriptAnalyzer
  $settings = @{ IncludeDefaultRules = $true; Rules = @{ PSUseCompatibleSyntax = @{ Enable = $true; TargetVersions = @('5.1', '7.0') } } }
  $found = @(Invoke-ScriptAnalyzer -Path $Install -Settings $settings | Where-Object { $_.Severity -in 'Warning', 'Error' -or $_.RuleName -eq 'PSUseCompatibleSyntax' })
  if ($found.Count -eq 0) { Write-Pass 'PSScriptAnalyzer (warnings, errors, 5.1/7 syntax)' }
  else { Write-Fail "PSScriptAnalyzer: $($found.Count) finding(s)"; $found | Format-Table -AutoSize RuleName, Line, Message | Out-String -Width 200 | Write-Host }
} else { Write-Host 'skip  PSScriptAnalyzer (not installed; -InstallAnalyzer installs it for this user)' }

$tmp = Join-Path ([IO.Path]::GetTempPath()) ('cs-ps-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$dir = Join-Path $tmp 'stack'
try {
  $r = Invoke-Installer @('-DryRun', '-Yes', '-Dir', $dir, '-Port', '7599')
  if ($r.Code -eq 0) { Write-Pass 'dry-run exit code 0' } else { Write-Fail "dry-run exit code $($r.Code)"; Write-Host $r.Out }
  Test-Output 'dry-run fresh install' $r @('dry run', '\.env: WEB_PORT=7599', '\.env: CS_PREFIX=cyber-scout', '\.env: TZ=\S+',
    '(GPU_IDS=|COMPOSE_FILE=docker-compose\.yml;docker-compose\.cpu\.yml)', 'docker compose build', 'docker compose up -d --no-build')
  if (Test-Path -LiteralPath $dir) { Write-Fail "dry-run created $dir" } else { Write-Pass 'dry-run changed nothing' }

  $r = Invoke-Installer @('-DryRun', '-Yes', '-Dir', $dir, '-Cpu', '-NoWorker', '-Prefix', 'cs-test', '-Bind', '127.0.0.1', '-Port', '7598',
    '-Tarball', 'https://example.invalid/src.tar.gz', '-Model', 'https://example.invalid/robot.onnx')
  Test-Output 'dry-run -Cpu -NoWorker -Tarball -Model' $r @('COMPOSE_FILE=docker-compose\.yml;docker-compose\.cpu\.yml', 'CS_PREFIX=cs-test',
    'WEB_BIND=127\.0\.0\.1', 'compose build api web', 'up -d --no-build api web', 'example\.invalid/src\.tar\.gz', 'example\.invalid/robot\.onnx', 'starting only')
  if (Test-Path -LiteralPath $dir) { Write-Fail "dry-run 2 created $dir" } else { Write-Pass 'dry-run 2 changed nothing' }

  New-Item -ItemType Directory -Path $dir -Force | Out-Null
  [IO.File]::WriteAllText((Join-Path $dir 'docker-compose.yml'), "services: {}`n")
  [IO.File]::WriteAllText((Join-Path $dir '.env'), "CS_PREFIX=cs-test`n")
  $r = Invoke-Installer @('-DryRun', '-Uninstall', '-Purge', '-Yes', '-Dir', $dir)
  Test-Output 'dry-run -Uninstall -Purge' $r @('Removing Cyber-Scout \(cs-test\)', 'Remove-Item -Recurse -Force')
  if (Test-Path -LiteralPath (Join-Path $dir '.env')) { Write-Pass 'dry-run uninstall kept the files' } else { Write-Fail 'dry-run uninstall deleted files' }

  $r = Invoke-Installer @('-Purge')
  if ($r.Code -ne 0 -and $r.Out -match 'only goes with') { Write-Pass '-Purge needs -Uninstall' } else { Write-Fail '-Purge alone accepted'; Write-Host $r.Out }
} finally {
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}

Write-Host ''
if ($script:Fails -eq 0) { Write-Host 'all installer checks passed' } else { Write-Host "$($script:Fails) check(s) failed"; exit 1 }
