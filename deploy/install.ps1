<#
.SYNOPSIS
  Cyber-Scout server installer for Windows 10/11 (Docker Desktop with the WSL2 backend). Re-run it to update.

.DESCRIPTION
  Checks Docker Desktop (Linux containers, WSL2 backend), an NVIDIA GPU usable from containers (else CPU mode),
  disk space and the port, downloads the code (git, or a tarball when git isn't installed), writes .env, puts the
  robot detector in place, runs `docker compose up -d --build`, waits until the site answers and prints the URL
  and the one-time setup code. Re-running it updates the code and rebuilds; data\ and .env are kept.

  Run it (no execution-policy changes needed):
    powershell -ExecutionPolicy Bypass -File install.ps1
  Straight from GitHub:
    irm https://raw.githubusercontent.com/Grindlewalt/Cyber-Scout/main/deploy/install.ps1 -OutFile install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 [-Cpu] [-Port 8080] ...
  Remove:
    powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall [-Purge]

.PARAMETER Dir          Install folder (default %USERPROFILE%\CyberScout).
.PARAMETER Port         Web port (default 7560; the next free one is offered if it's taken).
.PARAMETER Bind         Publish the port on this address only (e.g. 127.0.0.1 or a Tailscale IP). Default 0.0.0.0.
.PARAMETER Repo         Git repo (default https://github.com/Grindlewalt/Cyber-Scout.git; private repos over SSH).
.PARAMETER Branch       Git branch (default main).
.PARAMETER Tarball      Install from a source .tar.gz (path or URL) instead of git.
.PARAMETER Source       Copy the code from a local checkout instead of git.
.PARAMETER Model        robot.onnx to put at data\models\robot.onnx when it's missing (path or URL).
.PARAMETER ModelSha256  Refuse the detector unless its SHA-256 matches.
.PARAMETER ReplaceApp   Replace an app folder that isn't a git checkout (kept as app.bak-<time>); asked otherwise.
.PARAMETER Cpu          Force CPU-only mode.
.PARAMETER Gpu          Force GPU mode (fails if containers can't use the GPU).
.PARAMETER GpuIds       GPU(s) for the worker (nvidia-smi numbering), e.g. 0 or 0,1.
.PARAMETER Prefix       Compose project / container / image prefix (default cyber-scout).
.PARAMETER Services     Start only these services, e.g. api,web (skips the ~3.5 GB worker build).
.PARAMETER NoWorker     Same as -Services api,web.
.PARAMETER WorkerImage  Reuse an existing worker image instead of building it.
.PARAMETER UseImages    Build nothing; start the existing <prefix>-* images.
.PARAMETER Yes          No questions; use the defaults.
.PARAMETER DryRun       Print what would be done; change nothing.
.PARAMETER Uninstall    Stop and remove the containers, network and images; keep app\, data\ and .env.
.PARAMETER Purge        With -Uninstall: also delete the install folder (all data!) after confirming.
.PARAMETER KeepImages   With -Uninstall: leave the images.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'interactive installer output')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification = 'parameters are read in the script body')]
[CmdletBinding()]
param(
  [string]$Dir = '',
  [int]$Port = 0,
  [string]$Bind = '',
  [string]$Repo = 'https://github.com/Grindlewalt/Cyber-Scout.git',
  [string]$Branch = 'main',
  [string]$Tarball = '',
  [string]$Source = '',
  [string]$Model = '',
  [string]$ModelSha256 = '',
  [switch]$ReplaceApp,
  [switch]$Cpu,
  [switch]$Gpu,
  [string]$GpuIds = '',
  [string]$Prefix = '',
  [string]$Services = '',
  [switch]$NoWorker,
  [string]$WorkerImage = '',
  [switch]$UseImages,
  [switch]$Yes,
  [switch]$DryRun,
  [switch]$Uninstall,
  [switch]$Purge,
  [switch]$KeepImages
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar in PS 5.1
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { Write-Verbose 'TLS 1.2 already on' }

# ---------------------------------------------------------------- helpers
function Write-Bold([string]$m) { Write-Host $m -ForegroundColor White }
function Write-Info([string]$m) { Write-Host "  $m" }
function Write-Warn([string]$m) { Write-Host "! $m" -ForegroundColor Yellow }
function Exit-Fail([string]$m) { Write-Host "x $m" -ForegroundColor Red; exit 1 }
# a missing prerequisite: stop (or, with -DryRun, note it and carry on so the rest can be shown)
function Test-Need([string]$m) { if ($DryRun) { Write-Warn "[dry-run] would stop here: $m" } else { Exit-Fail $m } }
function Test-Cmd([string]$n) { return [bool](Get-Command $n -ErrorAction SilentlyContinue) }
function Test-Interactive {
  if ($Yes) { return $false }
  try { if ([Console]::IsInputRedirected) { return $false } } catch { return $false }
  return [Environment]::UserInteractive
}
function Read-Answer([string]$q, [string]$def) {
  if (-not (Test-Interactive)) { return $def }
  $a = Read-Host "$q [$def]"
  if ([string]::IsNullOrWhiteSpace($a)) { return $def }
  return $a.Trim()
}
function Test-YesAnswer([string]$q, [string]$def) { return ((Read-Answer "$q (y/n)" $def) -match '^(y|yes)$') }
# run a native command; output streams to the console. Returns the exit code.
function Invoke-Exe {
  param([string]$Exe, [string[]]$ArgList)
  if ($DryRun) { Write-Host "  [dry-run] $Exe $($ArgList -join ' ')" -ForegroundColor DarkGray; return 0 }
  $ErrorActionPreference = 'Continue'
  & $Exe @ArgList | Out-Host
  return $LASTEXITCODE
}
# run a native command quietly and capture its output (for checks). Returns @{Code; Out}.
function Invoke-Quiet {
  param([string]$Exe, [string[]]$ArgList)
  $ErrorActionPreference = 'Continue'
  try { $out = & $Exe @ArgList 2>&1 | ForEach-Object { "$_" }; $code = $LASTEXITCODE }
  catch { $out = @("$_"); $code = 1 }
  return @{ Code = $code; Out = (@($out) -join "`n") }
}
function Invoke-Step([string]$desc, [scriptblock]$block) {
  if ($DryRun) { Write-Host "  [dry-run] $desc" -ForegroundColor DarkGray } else { & $block }
}
function Test-VersionAtLeast([string]$a, [string]$b) {
  $clean = { param($v) ($v -replace '^v', '' -replace '[^0-9.].*$', '') }
  try { return ([version](& $clean $a)) -ge ([version](& $clean $b)) } catch { return $true }
}
function Get-FreeGB([string]$path) {
  try {
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
    return [math]::Floor((New-Object IO.DriveInfo $root).AvailableFreeSpace / 1GB)
  } catch { return -1 }
}

if ($Purge -and -not $Uninstall) { Exit-Fail '-Purge only goes with -Uninstall' }
$OnWindows = ($env:OS -eq 'Windows_NT')
if (-not $OnWindows) {
  if ($DryRun) { Write-Warn 'This is the Windows installer (on Linux/macOS use install.sh); -DryRun continues anyway.' }
  else { Exit-Fail 'This is the Windows installer. On Linux or macOS run: bash install.sh' }
}
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
if (-not $Dir) { $Dir = Join-Path $HomeDir 'CyberScout' }
$Dir = [IO.Path]::GetFullPath($Dir)
$EnvFile = Join-Path $Dir '.env'

function Read-EnvValue([string]$key) {
  if (-not (Test-Path -LiteralPath $EnvFile)) { return '' }
  $v = ''
  foreach ($l in [IO.File]::ReadAllLines($EnvFile)) { if ($l -match "^$([regex]::Escape($key))=(.*)$") { $v = $Matches[1].Trim() } }
  return $v
}
# .env must be UTF-8 without a BOM and LF line endings (docker compose reads it inside Linux tooling too)
function Write-EnvFile([string[]]$lines) {
  $enc = New-Object Text.UTF8Encoding $false
  [IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), $enc)
}
function Write-EnvKey([string]$key, [string]$value) {
  if ($DryRun) { Write-Host "  [dry-run] .env: $key=$value" -ForegroundColor DarkGray; return }
  $lines = @([IO.File]::ReadAllLines($EnvFile))
  $found = $false
  for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^$([regex]::Escape($key))=") { $lines[$i] = "$key=$value"; $found = $true } }
  if (-not $found) { $lines += "$key=$value" }
  Write-EnvFile $lines
}
function Clear-EnvKey([string]$key) {
  if ($DryRun) { Write-Host "  [dry-run] .env: remove $key" -ForegroundColor DarkGray; return }
  Write-EnvFile @([IO.File]::ReadAllLines($EnvFile) | Where-Object { $_ -notmatch "^$([regex]::Escape($key))=" })
}

$Arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if (-not $Arch) { $Arch = 'AMD64' }
$title = 'Cyber-Scout installer'; if ($DryRun) { $title += ' (dry run: nothing is changed)' }
Write-Bold $title
Write-Info "Windows $([Environment]::OSVersion.Version), $Arch, PowerShell $($PSVersionTable.PSVersion)"

# ---------------------------------------------------------------- Docker Desktop
$DockerOk = $false; $Wsl2 = $false; $ComposeVer = ''
function Get-DockerInfo { return (Invoke-Quiet 'docker' @('info', '--format', '{{.OSType}}|{{.KernelVersion}}|{{.OperatingSystem}}|{{.MemTotal}}')) }
if (-not (Test-Cmd 'docker')) {
  Write-Warn 'Docker Desktop is not installed. Cyber-Scout runs in Docker Desktop with the WSL2 backend:'
  Write-Info '1. In an admin PowerShell: wsl --install   (then restart Windows)'
  Write-Info '2. Install Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/'
  Write-Info '   (or: winget install -e --id Docker.DockerDesktop), keep "Use WSL 2 instead of Hyper-V" ticked.'
  Write-Info '3. Start Docker Desktop once, accept the terms; Settings -> General: "Start Docker Desktop when you sign in".'
  Write-Info 'Then run this installer again.'
  if (-not $Uninstall) { Test-Need 'Docker Desktop is required' }
} else {
  $info = Get-DockerInfo
  if ($info.Code -ne 0 -and -not $DryRun) {
    Write-Warn 'Docker Desktop is not running.'
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if ((Test-Path -LiteralPath $exe) -and (Test-YesAnswer 'Start Docker Desktop now?' 'y')) {
      Start-Process -FilePath $exe | Out-Null
      Write-Info 'waiting for Docker Desktop to start (up to 3 minutes)'
      for ($i = 0; $i -lt 90; $i++) { Start-Sleep -Seconds 2; $info = Get-DockerInfo; if ($info.Code -eq 0) { break } }
    }
  }
  if ($info.Code -eq 0) {
    $line = @($info.Out -split "`n" | Where-Object { $_ -match '\|' }) + @('|||') | Select-Object -First 1
    $p = $line.Trim().Split('|')
    if ($p[0] -ne 'linux') {
      Write-Info 'Docker is in Windows-containers mode. Right-click the Docker tray icon -> "Switch to Linux containers..."'
      Test-Need 'Docker must run Linux containers'
    } else {
      $DockerOk = $true
      $Wsl2 = ($p[1] -match 'WSL2|microsoft-standard')
      $cv = Invoke-Quiet 'docker' @('compose', 'version', '--short')
      if ($cv.Code -ne 0) { Test-Need "Docker Compose v2 is missing: update Docker Desktop" } else { $ComposeVer = $cv.Out.Trim() -replace '^v', '' }
      $sv = (Invoke-Quiet 'docker' @('version', '--format', '{{.Server.Version}}')).Out.Trim()
      Write-Info "Docker $sv ($($p[2])), Compose $ComposeVer, $(if ($Wsl2) { 'WSL2 backend' } else { 'not the WSL2 backend' })"
      if (-not $Wsl2) { Write-Warn 'Docker Desktop is not using the WSL2 backend: it works, but GPUs only work with WSL2 (Settings -> General -> Use the WSL 2 based engine).' }
      $mem = 0; [void][long]::TryParse($p[3], [ref]$mem)
      if ($mem -gt 0 -and $mem -lt 7500000000) {
        Write-Warn "Docker has only $([math]::Round($mem / 1GB)) GB of memory; the worker wants ~8 GB. Raise it in %USERPROFILE%\.wslconfig ([wsl2] memory=12GB), then: wsl --shutdown"
      }
    }
  } elseif (-not $Uninstall) {
    Test-Need 'start Docker Desktop, wait until it says "Engine running", and run this again'
  }
}

function Invoke-Compose([string[]]$a) {
  Push-Location -LiteralPath $(if (Test-Path -LiteralPath $Dir) { $Dir } else { $HomeDir })
  try { return (Invoke-Exe 'docker' (@('compose') + $a)) } finally { Pop-Location }
}

# ================================================================ uninstall
if (-not $Prefix) { $Prefix = Read-EnvValue 'CS_PREFIX' }
if (-not $Prefix) { $Prefix = 'cyber-scout' }
if ($Prefix -notmatch '^[a-z0-9][a-z0-9-]*$') { Exit-Fail "-Prefix: lowercase letters, digits and - only ($Prefix)" }
if ($Uninstall) {
  if (-not (Test-Path -LiteralPath (Join-Path $Dir 'docker-compose.yml')) -and -not $DryRun) {
    Exit-Fail "$Dir doesn't look like a Cyber-Scout install (no docker-compose.yml). Use -Dir."
  }
  Write-Bold "Removing Cyber-Scout ($Prefix) from $Dir"
  if ($DockerOk -and (Test-Path -LiteralPath (Join-Path $Dir 'docker-compose.yml'))) {
    if ($KeepImages) { [void](Invoke-Compose @('down', '--remove-orphans')) }
    else {
      if ((Invoke-Compose @('down', '--remove-orphans', '--rmi', 'all')) -ne 0) { [void](Invoke-Compose @('down', '--remove-orphans')) }
      $imgs = (Invoke-Quiet 'docker' @('image', 'ls', '--format', '{{.Repository}}:{{.Tag}}')).Out -split "`n" |
        Where-Object { $_ -match "^$([regex]::Escape($Prefix))-(worker|api|web):" }
      foreach ($i in $imgs) { [void](Invoke-Quiet 'docker' @('rmi', $i)) }
    }
  }
  if ($Purge) {
    $md = Read-EnvValue 'MEDIA_DIR'
    Write-Warn "-Purge deletes $Dir completely: accounts, settings, scouting notes, processed matches and kept videos."
    if (-not $Yes) {
      if (-not (Test-Interactive)) { Exit-Fail '-Purge needs a console to confirm (or add -Yes)' }
      if ((Read-Host 'Type DELETE to confirm') -cne 'DELETE') { Exit-Fail 'not confirmed; nothing deleted (containers are already removed)' }
    }
    Invoke-Step "Remove-Item -Recurse -Force $Dir" { Remove-Item -LiteralPath $Dir -Recurse -Force }
    if ($md -and $md -notmatch '^\./' -and -not $md.StartsWith($Dir)) { Write-Info "MEDIA_DIR was $md (outside the folder): left alone." }
    Write-Bold 'Removed.'
  } else {
    Write-Bold "Removed the containers$(if (-not $KeepImages) { ' and images' }). Kept $Dir (app\, data\, .env)."
    Write-Info 'Reinstall: run this installer again.   Delete everything: -Uninstall -Purge'
  }
  exit 0
}

# ================================================================ install / update
$Existing = Test-Path -LiteralPath $EnvFile
if (-not $Existing -and -not $PSBoundParameters.ContainsKey('Dir')) {
  $Dir = [IO.Path]::GetFullPath((Read-Answer 'Install folder' $Dir)); $EnvFile = Join-Path $Dir '.env'
  $Existing = Test-Path -LiteralPath $EnvFile
}
if ($Existing) { Write-Info "existing install in ${Dir}: updating it (data\ and .env are kept)" }
$SetPort = ($Port -ne 0); $SetBind = [bool]$Bind; $SetPrefix = $PSBoundParameters.ContainsKey('Prefix'); $SetGpuIds = [bool]$GpuIds
if (-not $Bind) { $Bind = Read-EnvValue 'WEB_BIND' }; if (-not $Bind) { $Bind = '0.0.0.0' }
if ($Port -eq 0) { $ep = Read-EnvValue 'WEB_PORT'; if ($ep) { $Port = [int]$ep } else { $Port = 7560 } }

# Never take over another stack: compose project names are global, so the same prefix from another folder would
# recreate *those* containers.
$SameStack = $false
if ($DockerOk) {
  $other = ((Invoke-Quiet 'docker' @('ps', '-a', '--filter', "label=com.docker.compose.project=$Prefix", '--format',
            '{{.Label "com.docker.compose.project.working_dir"}}')).Out -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
  if ($other) {
    $other = $other.Trim()
    if ([IO.Path]::GetFullPath($other).TrimEnd('\', '/') -eq $Dir.TrimEnd('\', '/')) { $SameStack = $true }
    else { Test-Need "A Cyber-Scout stack named '$Prefix' already runs from $other. Update that one (-Dir `"$other`"), or install a second copy with -Prefix NAME (and another -Port)." }
  }
}

$All = @('worker', 'recorder', 'api', 'web')
if ($NoWorker) { $Services = 'api,web' }
$Svc = @()
if ($Services) {
  foreach ($s in ($Services -split '[, ]+' | Where-Object { $_ })) {
    if ($All -notcontains $s) { Exit-Fail "-Services: unknown service '$s' (worker, recorder, api, web)" }
    $Svc += $s
  }
  if ($Svc -contains 'web' -and $Svc -notcontains 'api') { $Svc += 'api' }
}
$WithWorker = ($Svc.Count -eq 0) -or ($Svc -contains 'worker') -or ($Svc -contains 'recorder')

# ---------------------------------------------------------------- GPU
$UseGpu = $false
$envCpu = (Read-EnvValue 'COMPOSE_FILE') -match 'docker-compose\.cpu\.yml'
if ($Cpu) { Write-Info 'CPU mode (asked for)' }
elseif ($Existing -and $envCpu -and -not $Gpu) { Write-Info 'CPU mode (from .env; run with -Gpu to switch)' }
elseif (Test-Cmd 'nvidia-smi') {
  $l = Invoke-Quiet 'nvidia-smi' @('-L')
  if ($l.Code -eq 0 -and $l.Out -match 'GPU') {
    foreach ($g in ($l.Out -split "`n")) { if ($g -match '^GPU') { Write-Info ("found " + ($g -replace ' \(UUID.*', '')) } }
    if (-not $Wsl2) { Write-Warn 'NVIDIA GPUs only work in Docker Desktop with the WSL2 backend.' }
    elseif ($DryRun -or -not $DockerOk) { $UseGpu = $true }
    else {
      # the card the worker will get, asked for by id the way compose does (some hosts fail "--gpus all" while each
      # card works); a list like 0,1 needs embedded quotes that Windows PowerShell mangles, so that one tests "all"
      $tIds = if ($GpuIds) { $GpuIds } elseif ($Existing) { Read-EnvValue 'GPU_IDS' } else { '' }
      $tGpus = if ($tIds -match '^[0-9]+$') { "device=$tIds" } elseif (-not $tIds) { 'device=0' } else { 'all' }
      Write-Info "checking that containers can use the GPU (docker run --gpus $tGpus ... nvidia-smi)"
      $t = Invoke-Quiet 'docker' @('run', '--rm', '--gpus', $tGpus, 'python:3.12-slim', 'sh', '-c', 'nvidia-smi -L || /usr/lib/wsl/lib/nvidia-smi -L')
      if ($t.Code -eq 0 -and $t.Out -match 'GPU') { $UseGpu = $true; Write-Info 'GPU works in containers' }
      else {
        Write-Warn 'A test container could not use the GPU. Update the NVIDIA driver (Game Ready / Studio drivers include'
        Write-Warn 'WSL support) and Docker Desktop, run "wsl --update", restart, and run this again for GPU mode.'
      }
    }
    if (-not $UseGpu) {
      if ($Gpu) { Test-Need '-Gpu was given but containers cannot use the GPU' }
      if (-not (Test-YesAnswer 'Continue in CPU-only mode for now?' 'y')) { Exit-Fail 'Fix GPU support, then run this again.' }
    }
  }
}
if (-not $UseGpu -and -not $Cpu -and -not ($Existing -and $envCpu)) {
  if ($Gpu) { Test-Need '-Gpu was given but no NVIDIA GPU / driver was found' }
  Write-Info 'CPU mode: each match takes ~4x longer than on an NVIDIA GPU (~20 min instead of ~3-6). Everything else works.'
}
if ($UseGpu) {
  if (-not $GpuIds -and $Existing) { $GpuIds = Read-EnvValue 'GPU_IDS' }
  if (-not $GpuIds) { $GpuIds = '0' }
  if ($GpuIds -notmatch '^[0-9]+(,[0-9]+)*$') { Exit-Fail "-GpuIds: like 0 or 0,1 ($GpuIds)" }
  Write-Info "GPU mode, GPU_IDS=$GpuIds"
} elseif ($ComposeVer -and -not (Test-VersionAtLeast $ComposeVer '2.24.4')) {
  Test-Need "CPU-only mode needs Docker Compose >= 2.24.4 (you have $ComposeVer). Update Docker Desktop."
}
$Platform = ''
if ($Arch -eq 'ARM64' -and $WithWorker) {
  $Platform = 'linux/amd64'
  Write-Info 'ARM CPU: the worker needs x86-64 libraries, so images are built for linux/amd64 and run emulated (slow).'
}

# ---------------------------------------------------------------- disk
$NeedGB = 20; if (-not $WithWorker -or $WorkerImage -or $UseImages) { $NeedGB = 2 }
$dockerData = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Docker' } else { $HomeDir }
$free = Get-FreeGB $dockerData
if ($free -ge 0) {
  Write-Info "free space for Docker images: $free GB (drive of $dockerData)"
  if ($free -lt $NeedGB) {
    Write-Warn "Less than $NeedGB GB free where Docker Desktop keeps its disk. The worker image alone is ~3.5 GB."
    if (-not (Test-YesAnswer 'Continue anyway?' 'y')) { Exit-Fail 'Free some space, then run this again.' }
  }
}
$freeDir = Get-FreeGB $Dir
if ($freeDir -ge 0 -and $freeDir -lt 20) { Write-Warn "Only $freeDir GB free on the drive of $Dir (kept match videos take ~100 MB each)." }

# ---------------------------------------------------------------- port
$DockerPorts = ''
if ($DockerOk) { $DockerPorts = (Invoke-Quiet 'docker' @('ps', '--format', '{{.Ports}}')).Out }
function Test-PortBusy([int]$p) {
  if ($DockerPorts -match ":$p->") { return $true }   # published by a container
  if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    if (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) { return $true }
  }
  $busy = $false
  try {   # something answering on localhost
    $c = New-Object Net.Sockets.TcpClient
    $iar = $c.BeginConnect('127.0.0.1', $p, $null, $null)
    if ($iar.AsyncWaitHandle.WaitOne(500) -and $c.Connected) { $busy = $true }
    $c.Close()
  } catch { Write-Verbose "port $p not answering" }
  if ($busy) { return $true }
  try { $l = New-Object Net.Sockets.TcpListener ([Net.IPAddress]::Any), $p; $l.ExclusiveAddressUse = $true; $l.Start(); $l.Stop(); return $false } catch { return $true }
}
function Test-OurPort([int]$p) {
  if (-not $SameStack) { return $false }
  return ((Invoke-Quiet 'docker' @('port', "$Prefix-web", '80')).Out -match ":$p(\s|$)")
}
if (-not $SetPort -and -not $Existing) { $Port = [int](Read-Answer 'Web port' "$Port"); $SetPort = $true }
if ((Test-PortBusy $Port) -and -not (Test-OurPort $Port)) {
  $alt = $Port; do { $alt++ } while ((Test-PortBusy $alt) -and $alt -lt $Port + 100)
  Write-Warn "Port $Port is already in use on this machine."
  $Port = [int](Read-Answer 'Use port' "$alt"); $SetPort = $true
  if ((Test-PortBusy $Port) -and -not (Test-OurPort $Port)) { Test-Need "port $Port is in use too (-Port N)" }
}
Write-Info "web port $Port on $Bind"

# ---------------------------------------------------------------- folder + code
if (-not (Test-Path -LiteralPath $Dir)) { Invoke-Step "New-Item -ItemType Directory $Dir" { New-Item -ItemType Directory -Path $Dir -Force | Out-Null } }
Write-Bold "Installing into $Dir"
$App = Join-Path $Dir 'app'
$GitSha = ''
$env:GIT_TERMINAL_PROMPT = '0'

function Get-GithubSlug([string]$url) {
  if ($url -match '^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(\.git)?$') { return $Matches[2] }
  return ''
}
function Move-AppTree([string]$newDir) {   # swap in a fresh code tree (old one kept as app.prev until the stack is up)
  $prev = Join-Path $Dir 'app.prev'
  if (Test-Path -LiteralPath $prev) { Remove-Item -LiteralPath $prev -Recurse -Force }
  if (Test-Path -LiteralPath $App) { Move-Item -LiteralPath $App -Destination $prev }
  Move-Item -LiteralPath $newDir -Destination $App
}
function Get-Tarball([string]$src) {
  if ($DryRun) { Write-Host "  [dry-run] download/unpack $src -> $App" -ForegroundColor DarkGray; return }
  if (-not (Test-Cmd 'tar')) { Exit-Fail 'tar.exe is missing (it ships with Windows 10 1803+). Install git instead and run again.' }
  $tmp = Join-Path $Dir ('.src.' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Path (Join-Path $tmp 'x') -Force | Out-Null
  try {
    $f = $src
    if ($src -match '^https?://') {
      Write-Info "downloading $src"
      $f = Join-Path $tmp 'src.tar.gz'
      Invoke-WebRequest -Uri $src -OutFile $f -UseBasicParsing
    } elseif (-not (Test-Path -LiteralPath $f)) { Exit-Fail "no such tarball: $f" }
    Write-Info 'unpacking'
    if ((Invoke-Exe 'tar' @('-xzf', $f, '-C', (Join-Path $tmp 'x'))) -ne 0) { Exit-Fail "couldn't unpack $f" }
    $top = Join-Path $tmp 'x'
    $kids = @(Get-ChildItem -LiteralPath $top -Force)
    if ($kids.Count -eq 1 -and $kids[0].PSIsContainer) { $top = $kids[0].FullName }
    if (-not (Test-Path -LiteralPath (Join-Path $top 'deploy\docker-compose.yml'))) { Exit-Fail "$src doesn't contain Cyber-Scout" }
    Move-AppTree $top
  } finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force } }
}
function Invoke-GitClone([string]$url, [string]$dest) {
  $r = Invoke-Quiet 'git' @('-c', 'core.autocrlf=false', 'clone', '--quiet', '--depth', '1', '--branch', $Branch, $url, $dest)
  return ($r.Code -eq 0)
}

if ($Source) {
  if (-not (Test-Path -LiteralPath $Source)) { Test-Need "-Source: no such folder: $Source" }
  Write-Info "copying the code from $Source"
  $rc = Invoke-Exe 'robocopy' @($Source, $App, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
    '/XD', '.git', '.venv', '.work', 'node_modules', '__pycache__', (Join-Path $Source 'data'),
    '/XF', '.env', '*.key', '.roboflow_key')
  if ($rc -ge 8) { Exit-Fail "copying from $Source failed (robocopy $rc)" }
  if ((Test-Cmd 'git') -and (Test-Path -LiteralPath (Join-Path $Source '.git'))) { $GitSha = (Invoke-Quiet 'git' @('-C', $Source, 'rev-parse', '--short', 'HEAD')).Out.Trim() }
} elseif ($Tarball) {
  Get-Tarball $Tarball
} elseif ((Test-Path -LiteralPath (Join-Path $App '.git')) -and (Test-Cmd 'git')) {
  Write-Info "updating the code (git fetch, branch $Branch)"
  if ((Invoke-Quiet 'git' @('-C', $App, 'status', '--porcelain', '--untracked-files=no')).Out.Trim()) {
    Test-Need "$App has local changes an update would overwrite (git -C `"$App`" status)"
  }
  if ((Invoke-Exe 'git' @('-C', $App, 'fetch', '--quiet', 'origin', $Branch)) -ne 0) { Test-Need 'git fetch failed (network? repo access?). The installed version keeps running.' }
  [void](Invoke-Exe 'git' @('-C', $App, '-c', 'core.autocrlf=false', 'checkout', '--quiet', '-B', $Branch, 'FETCH_HEAD'))
} else {
  $slug = Get-GithubSlug $Repo
  if (Test-Path -LiteralPath $App) {
    Write-Warn "$App exists but isn't a git checkout (copied by hand?), so it can't be updated in place."
    if ($ReplaceApp -or (-not $Yes -and (Test-YesAnswer "Replace it with a fresh copy from $Repo (the old one is kept as app.bak-<time>)?" 'n'))) {
      $bak = Join-Path $Dir ('app.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
      Invoke-Step "move $App -> $bak" { Move-Item -LiteralPath $App -Destination $bak }
    } else { Test-Need 'Left the app folder alone. Run again with -ReplaceApp, or use -Source / -Tarball to install that code.' }
  }
  if (Test-Cmd 'git') {
    Write-Info "downloading the code: git clone $Repo ($Branch)"
    if ($DryRun) { Write-Host "  [dry-run] git -c core.autocrlf=false clone --depth 1 --branch $Branch $Repo $App" -ForegroundColor DarkGray }
    else {
      $tmp = Join-Path $Dir '.app.new'
      if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
      if (-not (Invoke-GitClone $Repo $tmp)) {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
        $hasKey = @(Get-ChildItem -Path (Join-Path $HomeDir '.ssh') -Filter 'id_*' -ErrorAction SilentlyContinue).Count -gt 0
        if ($slug -and $Repo -match '^https://' -and $hasKey) {
          Write-Info "https clone failed (private repo?); trying SSH: git@github.com:$slug.git"
          $env:GIT_SSH_COMMAND = 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new'
          if (-not (Invoke-GitClone "git@github.com:$slug.git" $tmp)) { Exit-Fail "couldn't clone $Repo over https or SSH (check access: ssh -T git@github.com)" }
        } else { Exit-Fail "couldn't clone $Repo ($Branch). Private repo? Add an SSH key to GitHub, use -Repo git@github.com:OWNER/REPO.git, or -Tarball." }
      }
      [void](Invoke-Quiet 'git' @('-C', $tmp, 'config', 'core.autocrlf', 'false'))
      Move-AppTree $tmp
    }
  } elseif ($slug) {
    Write-Info "git isn't installed: downloading a tarball of $slug ($Branch) instead"
    Get-Tarball "https://codeload.github.com/$slug/tar.gz/refs/heads/$Branch"
  } else { Test-Need "git is required for $Repo (winget install -e --id Git.Git)" }
}
if (-not $GitSha -and (Test-Path -LiteralPath (Join-Path $App '.git')) -and (Test-Cmd 'git')) {
  $GitSha = (Invoke-Quiet 'git' @('-C', $App, 'rev-parse', '--short', 'HEAD')).Out.Trim()
}
if ($GitSha) { Write-Info "code version $GitSha" }
if (-not $DryRun) {
  # scripts copied into Linux containers must have LF endings (a CRLF checkout breaks the entrypoints)
  $ep = Join-Path $App 'live\entrypoint.sh'
  if ((Test-Path -LiteralPath $ep) -and ([IO.File]::ReadAllText($ep).Contains("`r`n"))) {
    Exit-Fail "$App has Windows (CRLF) line endings. Delete $App and run again (this installer clones with core.autocrlf=false)."
  }
  if (-not (Test-Path -LiteralPath (Join-Path $App 'deploy\docker-compose.yml'))) { Exit-Fail "$App has no deploy\docker-compose.yml" }
}
foreach ($f in @('docker-compose.yml', 'docker-compose.cpu.yml')) {
  $src = Join-Path $App "deploy\$f"; $dst = Join-Path $Dir $f
  Invoke-Step "copy $src -> $dst" {
    if ((Test-Path -LiteralPath $dst) -and ((Get-FileHash -LiteralPath $src).Hash -ne (Get-FileHash -LiteralPath $dst).Hash)) {
      Copy-Item -LiteralPath $dst -Destination "$dst.bak" -Force; Write-Info "previous $f saved as $f.bak"
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
  }
}

# ---------------------------------------------------------------- .env (infrastructure only; no secrets needed)
$CpuFiles = 'docker-compose.yml;docker-compose.cpu.yml'   # ';' = the COMPOSE_FILE separator on Windows
function Get-IanaTimeZone {
  $id = [TimeZoneInfo]::Local.Id
  if ($id -match '^[A-Za-z_]+/[A-Za-z_/+-]+$') { return $id }   # already IANA (PowerShell 7 on Linux/macOS)
  try { $iana = $null; if ([TimeZoneInfo]::TryConvertWindowsIdToIanaId($id, [ref]$iana)) { return $iana } } catch { Write-Verbose 'old .NET: no tz conversion' }
  $map = @{
    'Pacific Standard Time' = 'America/Los_Angeles'; 'Mountain Standard Time' = 'America/Denver'
    'US Mountain Standard Time' = 'America/Phoenix'; 'Central Standard Time' = 'America/Chicago'
    'Eastern Standard Time' = 'America/New_York'; 'Alaskan Standard Time' = 'America/Anchorage'
    'Hawaiian Standard Time' = 'Pacific/Honolulu'; 'Atlantic Standard Time' = 'America/Halifax'
    'Canada Central Standard Time' = 'America/Regina'; 'Central Standard Time (Mexico)' = 'America/Mexico_City'
    'E. South America Standard Time' = 'America/Sao_Paulo'; 'GMT Standard Time' = 'Europe/London'
    'W. Europe Standard Time' = 'Europe/Berlin'; 'Romance Standard Time' = 'Europe/Paris'
    'Central Europe Standard Time' = 'Europe/Budapest'; 'Israel Standard Time' = 'Asia/Jerusalem'
    'Turkey Standard Time' = 'Europe/Istanbul'; 'India Standard Time' = 'Asia/Kolkata'
    'China Standard Time' = 'Asia/Shanghai'; 'Taipei Standard Time' = 'Asia/Taipei'; 'Tokyo Standard Time' = 'Asia/Tokyo'
    'AUS Eastern Standard Time' = 'Australia/Sydney'; 'New Zealand Standard Time' = 'Pacific/Auckland'
  }
  if ($map.ContainsKey($id)) { return $map[$id] }
  return 'UTC'
}
if (-not $Existing) {
  Write-Info 'writing .env'
  $lines = @(
    '# Cyber-Scout infrastructure settings (written by install.ps1; re-running it keeps your edits).',
    '# App settings (TBA key, team, events, GPU options, accounts) live in the web app. See app/deploy/env.example.',
    "CS_PREFIX=$Prefix",
    "WEB_PORT=$Port",
    '# publish the web port on this address only (e.g. a LAN or Tailscale IP); 0.0.0.0 = all',
    "WEB_BIND=$Bind",
    '# Docker Desktop maps file ownership itself; these only matter inside the containers',
    'PUID=1000',
    'PGID=1000',
    "TZ=$(Get-IanaTimeZone)",
    'MEDIA_DIR=./data/media',
    'YT_FORCE_IPV4=1'
  )
  if ($UseGpu) { $lines += "GPU_IDS=$GpuIds" } else { $lines += '# no usable NVIDIA GPU: CPU-only override'; $lines += "COMPOSE_FILE=$CpuFiles" }
  if ($Platform) { $lines += "DOCKER_DEFAULT_PLATFORM=$Platform" }
  if ($DryRun) { foreach ($l in $lines) { Write-Host "  [dry-run] .env: $l" -ForegroundColor DarkGray } } else { Write-EnvFile $lines }
} else {
  Write-Info 'keeping .env (updating only what this run changes)'
  if ($SetPort -and (Read-EnvValue 'WEB_PORT') -ne "$Port") { Write-EnvKey 'WEB_PORT' "$Port" }
  if ($SetBind -and (Read-EnvValue 'WEB_BIND') -ne $Bind) { Write-EnvKey 'WEB_BIND' $Bind }
  if ($SetPrefix -and (Read-EnvValue 'CS_PREFIX') -ne $Prefix) { Write-EnvKey 'CS_PREFIX' $Prefix }
  if ($UseGpu) {
    if (($SetGpuIds -or -not (Read-EnvValue 'GPU_IDS')) -and (Read-EnvValue 'GPU_IDS') -ne $GpuIds) { Write-EnvKey 'GPU_IDS' $GpuIds }
    if ($envCpu) { if ($Gpu) { Clear-EnvKey 'COMPOSE_FILE'; Write-Info 'switched .env to GPU mode' } else { Write-Info 'GPU available, but .env is CPU-only; run with -Gpu to switch' } }
  } elseif (-not $envCpu) { Write-EnvKey 'COMPOSE_FILE' $CpuFiles; Write-Info 'added the CPU-only override to .env' }
  if ($Platform -and -not (Read-EnvValue 'DOCKER_DEFAULT_PLATFORM')) { Write-EnvKey 'DOCKER_DEFAULT_PLATFORM' $Platform }
}

# ---------------------------------------------------------------- data folder + robot detector
foreach ($d in @('data\models', 'data\media', 'data\site\api', 'data\config')) {
  $p = Join-Path $Dir $d
  if (-not (Test-Path -LiteralPath $p)) { Invoke-Step "mkdir $p" { New-Item -ItemType Directory -Path $p -Force | Out-Null } }
}
$ModelPath = Join-Path $Dir 'data\models\robot.onnx'
$haveModel = (Test-Path -LiteralPath $ModelPath) -and ((Get-Item -LiteralPath $ModelPath).Length -gt 0)
if (-not $haveModel -and $Model) {
  $part = "$ModelPath.part"
  if ($Model -match '^https?://') {
    Write-Info 'downloading the robot detector'
    Invoke-Step "download $Model -> $ModelPath" {
      try { Invoke-WebRequest -Uri $Model -OutFile $part -UseBasicParsing }
      catch { Remove-Item -LiteralPath $part -ErrorAction SilentlyContinue; Write-Warn "couldn't download the detector: $_" }
    }
  } elseif ((Test-Path -LiteralPath $Model) -or $DryRun) {
    Write-Info "copying the robot detector from $Model"
    Invoke-Step "copy $Model -> $ModelPath" { Copy-Item -LiteralPath $Model -Destination $part -Force }
  } else { Write-Warn "-Model: no such file: $Model" }
  if (-not $DryRun -and (Test-Path -LiteralPath $part)) {
    $okSum = $true
    if ($ModelSha256) {
      $sum = (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash
      if ($sum -ne $ModelSha256.ToUpperInvariant()) { $okSum = $false; Write-Warn "the detector's SHA-256 ($sum) doesn't match -ModelSha256: not using it" }
      else { Write-Info 'SHA-256 checked' }
    }
    if ($okSum) { Move-Item -LiteralPath $part -Destination $ModelPath -Force } else { Remove-Item -LiteralPath $part -Force }
  }
  $haveModel = $DryRun -or ((Test-Path -LiteralPath $ModelPath) -and ((Get-Item -LiteralPath $ModelPath).Length -gt 0))
}
if ($haveModel) { Write-Info "robot detector: $ModelPath" }
else {
  Write-Warn "No robot detector yet ($ModelPath)."
  Write-Info "The site, setup and accounts work without it, but matches won't be processed until it's there."
  Write-Info 'Copy robot.onnx from another Cyber-Scout server (data/models/robot.onnx) or your team, to that path'
  Write-Info '(or run again with -Model PATH|URL). No restart needed.'
}

# ---------------------------------------------------------------- build + start
$Run = if ($Svc.Count -gt 0) { $Svc } else { $All }
$Build = @()
foreach ($s in $Run) {
  if ($UseImages) { continue }
  if ($WorkerImage -and ($s -eq 'worker' -or $s -eq 'recorder')) { continue }
  $Build += $s
}
if ($WorkerImage -and $WithWorker) {
  if ($DockerOk -and -not $DryRun -and (Invoke-Quiet 'docker' @('image', 'inspect', $WorkerImage)).Code -ne 0) { Exit-Fail "-WorkerImage: no image $WorkerImage" }
  if ($WorkerImage -ne "$Prefix-worker:latest") {
    Write-Info "using the existing worker image $WorkerImage (no worker build)"
    [void](Invoke-Exe 'docker' @('tag', $WorkerImage, "$Prefix-worker:latest"))
  }
}
if ($UseImages -and $DockerOk -and -not $DryRun) {
  foreach ($s in $Run) {
    $img = if ($s -eq 'recorder') { "$Prefix-worker:latest" } else { "$Prefix-${s}:latest" }
    if ((Invoke-Quiet 'docker' @('image', 'inspect', $img)).Code -ne 0) { Exit-Fail "-UseImages: image $img doesn't exist" }
  }
}
if (-not $WithWorker) { Write-Info "starting only: $($Svc -join ' ') (no worker: matches aren't processed; 'docker compose up -d' starts everything)" }
if ($GitSha) { $env:GIT_SHA = $GitSha }
try {
  if ($Build.Count -gt 0) {
    $note = 'the first build takes a few minutes'; if ($Build -contains 'worker') { $note += '; the worker image is ~3.5 GB' }
    Write-Bold "Building ($note)"
    $a = @('build'); if ($Build.Count -lt 4) { $a += $Build }
    if ((Invoke-Compose $a) -ne 0) { Test-Need "the build failed; see the output above (cd `"$Dir`"; docker compose build)" }
  }
  Write-Bold 'Starting'
  $a = @('up', '-d', '--no-build'); if ($Svc.Count -gt 0) { $a += $Svc }
  if ((Invoke-Compose $a) -ne 0) { Test-Need "docker compose up failed (cd `"$Dir`"; docker compose logs)" }
} finally { Remove-Item Env:\GIT_SHA -ErrorAction SilentlyContinue }

$HostAddr = $Bind; if ($HostAddr -in @('', '0.0.0.0', '::', '[::]')) { $HostAddr = '127.0.0.1' }
$Base = "http://${HostAddr}:$Port"
$Ok = $false
if ($DryRun) { $Ok = $true; Write-Host "  [dry-run] wait for $Base/app/v1/health" -ForegroundColor DarkGray }
else {
  Write-Info "waiting for the app to answer on $Base"
  for ($i = 0; $i -lt 90 -and -not $Ok; $i++) {
    try {
      $h = Invoke-WebRequest -Uri "$Base/app/v1/health" -UseBasicParsing -TimeoutSec 5
      $r = Invoke-WebRequest -Uri "$Base/" -UseBasicParsing -TimeoutSec 5
      if ($h.Content -match '"ok":\s*true' -and $r.StatusCode -eq 200) { $Ok = $true; break }
    } catch { Write-Verbose 'not up yet' }
    Start-Sleep -Seconds 2
  }
}
if ($Ok) { $prev = Join-Path $Dir 'app.prev'; if (Test-Path -LiteralPath $prev) { Invoke-Step "remove $prev" { Remove-Item -LiteralPath $prev -Recurse -Force } } }
else { Write-Warn "The app didn't answer on $Base within 3 minutes. Look at: cd `"$Dir`"; docker compose ps; docker compose logs api web" }

# ---------------------------------------------------------------- where to go
$Ip = ''
if ($HostAddr -ne '127.0.0.1' -or $Bind -eq '127.0.0.1') { $Ip = $HostAddr }
elseif (Get-Command Get-NetRoute -ErrorAction SilentlyContinue) {
  try {
    $rt = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop | Sort-Object RouteMetric | Select-Object -First 1
    $Ip = (Get-NetIPAddress -InterfaceIndex $rt.ifIndex -AddressFamily IPv4 -ErrorAction Stop | Select-Object -First 1).IPAddress
  } catch { $Ip = '' }
}
if (-not $Ip) { $Ip = 'localhost' }
Write-Host ''
Write-Bold "Cyber-Scout is $(if ($DryRun) { '(would be) ' })$(if ($Ok) { 'running' } else { 'starting' }):"
Write-Info "http://${Ip}:$Port"
Write-Info "http://localhost:$Port   (on this PC)"
$CodeFile = Join-Path $Dir 'data\setup-code.txt'
$Code = ''
if (-not $DryRun) {
  for ($i = 0; $i -lt 10; $i++) {
    if (Test-Path -LiteralPath $CodeFile) { $Code = ([IO.File]::ReadAllText($CodeFile)).Trim(); if ($Code) { break } }
    elseif ($Ok) { break }
    Start-Sleep -Seconds 1
  }
}
if ($Code) {
  Write-Host ''
  Write-Bold "First-time setup code: $Code"
  Write-Info "Open the site, enter the code and create the admin account. (Later: type `"$CodeFile`")"
} elseif ($Ok -and $Existing) { Write-Info '(setup already done: sign in with your admin account)' }
else { Write-Info "The one-time setup code is in $CodeFile (until the admin account is created)." }
# installed with get.ps1 from a downloads site: each laptop gets the Client with one line, already pointed at this server
if ($env:CS_SITE -and $Bind -ne '127.0.0.1' -and $env:CS_SITE -match '^https?://[A-Za-z0-9.:\[\]_-]+$') {
  $Srv = "http://${Ip}:$Port"
  Write-Host ''
  Write-Bold 'Laptops: the Cyber-Scout Client in one line (installs it, starts it at sign-in, connects to this server):'
  Write-Info "Windows:        `$env:CS_SERVER='$Srv'; irm $($env:CS_SITE)/downloads/get-client.ps1 | iex"
  Write-Info "macOS / Linux:  curl -fsSL $($env:CS_SITE)/downloads/get-client.sh | bash -s -- --server $Srv"
  Write-Info "Phones: open $Srv in the browser and add it to the home screen."
}

Write-Host ''
Write-Info 'Other devices on your network: allow the port in Windows Firewall if Windows asks (Private networks).'
Write-Info 'Keep it running: Docker Desktop -> Settings -> General -> "Start Docker Desktop when you sign in".'
Write-Info "Logs:      cd `"$Dir`"; docker compose logs -f worker"
Write-Info 'Update:    run this installer again (the one-click updater in Admin -> Updates needs a Linux server).'
Write-Info "Backup:    the whole $Dir folder (data\ holds everything)."
Write-Info "Uninstall: powershell -ExecutionPolicy Bypass -File `"$App\deploy\install.ps1`" -Uninstall -Dir `"$Dir`"   (add -Purge to delete the data)"
if (-not $Ok) { exit 1 }
