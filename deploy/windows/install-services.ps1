<#
.SYNOPSIS
  Registers the portal's three Windows services with NSSM:
    BDEPortalBackend   uvicorn (deploy\windows\start-backend.ps1)
    BDEPortalFrontend  next start (deploy\windows\start-frontend.ps1)
    BDEPortalCaddy     caddy run --config <Caddyfile>

.DESCRIPTION
  Run ONCE, from an ELEVATED PowerShell, on the production host, AFTER:
    1. backend\.env filled in from deploy\env\backend.production.env.example
    2. migrations applied (backend\.venv\Scripts\python.exe -m app.db.migrate upgrade)
    3. first admin created (python -m app.seeds.bootstrap_admin ...)
    4. frontend built: deploy\windows\start-frontend.ps1 -Build   (then Ctrl+C)
    5. Caddyfile written from deploy\Caddyfile.example and validated

  Prerequisites
    NSSM 2.24+  https://nssm.cc/download  - extract win64\nssm.exe, e.g. to C:\tools\nssm\nssm.exe
    Caddy v2    https://caddyserver.com/download (Windows amd64) - e.g. C:\caddy\caddy.exe

  Service account: the services run as LocalSystem by default. If SAP_DATA_FILE
  points into a user's OneDrive folder, run BDEPortalBackend as THAT user
  instead (nssm set BDEPortalBackend ObjectName .\<user> <password>), because
  LocalSystem cannot see another user's OneDrive sync, and OneDrive only syncs
  while that user is signed in.

  Logs (stdout/stderr, rotated by NSSM) go to $LogDir - outside the repo.

  Alternative without NSSM - Task Scheduler (less robust: no automatic restart
  on crash unless configured, no log rotation):
    $a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File "<repo>\deploy\windows\start-backend.ps1"'
    $t = New-ScheduledTaskTrigger -AtStartup
    $s = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
    Register-ScheduledTask -TaskName "BDE Portal Backend" -Action $a -Trigger $t -Settings $s -User SYSTEM -RunLevel Highest
  (repeat for start-frontend.ps1 and for "caddy.exe run --config ...").

  Managing afterwards
    nssm restart BDEPortalBackend      Get-Service BDEPortal*
    Update: stop frontend+backend, back up, git pull, migrate upgrade,
            start-frontend.ps1 -Build (Ctrl+C when it starts), start services.

.NOTES
  This script is NOT run as part of development. It changes system services.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$Nssm = "C:\tools\nssm\nssm.exe",
  [string]$Caddy = "C:\caddy\caddy.exe",
  [string]$Caddyfile = "C:\caddy\Caddyfile",
  [string]$LogDir = "D:\GP3\logs"
)

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this from an elevated (Administrator) PowerShell."
}
foreach ($path in @($Nssm, $Caddy, $Caddyfile)) {
  if (-not (Test-Path -LiteralPath $path)) { throw "Not found: $path (see the prerequisites in this script's help)" }
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "backend\.env"))) { throw "backend\.env is missing." }
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "frontend\.next\BUILD_ID"))) { throw "Frontend is not built. Run start-frontend.ps1 -Build first." }

& $Caddy validate --config $Caddyfile --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw "Caddyfile is invalid." }

New-Item -ItemType Directory -Force -Path $LogDir, (Join-Path $LogDir "caddy") | Out-Null
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Install-PortalService {
  param([string]$Name, [string]$DisplayName, [string]$Exe, [string]$Arguments, [string]$Directory, [string[]]$DependsOn = @())

  if (-not $PSCmdlet.ShouldProcess($Name, "Install service")) { return }

  if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
    & $Nssm stop $Name confirm | Out-Null
    & $Nssm remove $Name confirm | Out-Null
  }
  & $Nssm install $Name $Exe $Arguments
  if ($LASTEXITCODE -ne 0) { throw "nssm install $Name failed" }
  & $Nssm set $Name DisplayName $DisplayName | Out-Null
  & $Nssm set $Name AppDirectory $Directory | Out-Null
  & $Nssm set $Name Start SERVICE_AUTO_START | Out-Null
  & $Nssm set $Name AppStdout (Join-Path $LogDir "$Name.out.log") | Out-Null
  & $Nssm set $Name AppStderr (Join-Path $LogDir "$Name.err.log") | Out-Null
  & $Nssm set $Name AppRotateFiles 1 | Out-Null
  & $Nssm set $Name AppRotateOnline 1 | Out-Null
  & $Nssm set $Name AppRotateBytes 20971520 | Out-Null
  # Restart on crash after 5 s; give the app 15 s to shut down cleanly.
  & $Nssm set $Name AppExit Default Restart | Out-Null
  & $Nssm set $Name AppRestartDelay 5000 | Out-Null
  & $Nssm set $Name AppStopMethodConsole 15000 | Out-Null
  if ($DependsOn.Count -gt 0) { & $Nssm set $Name DependOnService @DependsOn | Out-Null }
  Write-Host "Installed $Name"
}

$psArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"'

Install-PortalService -Name "BDEPortalBackend" -DisplayName "BDE Portal - Backend (FastAPI)" `
  -Exe $powershell -Arguments ($psArgs -f (Join-Path $PSScriptRoot "start-backend.ps1")) `
  -Directory (Join-Path $RepoRoot "backend") -DependsOn @("postgresql-x64-18")

Install-PortalService -Name "BDEPortalFrontend" -DisplayName "BDE Portal - Frontend (Next.js)" `
  -Exe $powershell -Arguments ($psArgs -f (Join-Path $PSScriptRoot "start-frontend.ps1")) `
  -Directory (Join-Path $RepoRoot "frontend")

Install-PortalService -Name "BDEPortalCaddy" -DisplayName "BDE Portal - Caddy (HTTPS)" `
  -Exe $Caddy -Arguments ('run --config "{0}" --adapter caddyfile' -f $Caddyfile) `
  -Directory (Split-Path -Parent $Caddy) -DependsOn @("BDEPortalBackend", "BDEPortalFrontend")

Write-Host ""
Write-Host "Start them:   Start-Service BDEPortalBackend, BDEPortalFrontend, BDEPortalCaddy"
Write-Host "Firewall:     New-NetFirewallRule -DisplayName 'BDE Portal HTTPS' -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow"
Write-Host "Ports 8000 and 3000 stay bound to 127.0.0.1 and need no firewall rule."
