<#
.SYNOPSIS
  Runs the LOCAL development portal (http://127.0.0.1:8000) automatically at
  Windows sign-in, so it is never "Cannot reach the server" because nobody
  started it.

.DESCRIPTION
  The local portal is one process: the backend serves the pages AND /api on
  127.0.0.1:8000 (backend/.env: ENV=development, local PostgreSQL). Started
  by hand it is gone after every restart, every closed terminal - and a page
  Chrome restores from its cache then fails at "Sign in".

  This registers the task "BDE Portal - local backend" (current user, at
  sign-in) and (re)starts it now - re-run it any time to restart. It runs
  hidden (deploy\windows\local-backend.py), restarting itself whenever
  backend code changes; page changes need only `npm run export:backend`. Output: %LOCALAPPDATA%\BDEPortal\backend-local.log
  (replaced on each start). Local development only - production is Render.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\windows\register-local-backend.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\windows\register-local-backend.ps1 -Remove
#>
[CmdletBinding()]
param(
  [string]$TaskName = "BDE Portal - local backend",
  [switch]$Remove
)

$ErrorActionPreference = "Stop"

# Stopping the task does not stop the server it started (checked: python kept
# port 8000), so the launcher and the server are stopped by command line.
function Stop-LocalBackend {
  $ErrorActionPreference = "Continue"   # taskkill /T may already have ended a match
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*local-backend.py*" -or $_.CommandLine -like "*uvicorn app.main:app*--port 8000*" } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
}

if ($Remove) {
  Stop-LocalBackend
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed '$TaskName'. Start the backend by hand from now on."
  return
}

$backend = (Resolve-Path (Join-Path $PSScriptRoot "..\..\backend")).Path
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Backend virtualenv not found: $python" }
$logDir = Join-Path $env:LOCALAPPDATA "BDEPortal"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "backend-local.log"

# pythonw (no window) runs local-backend.py, which runs uvicorn and restarts
# it whenever backend code changes - see that file for why not --reload.
$pythonw = Join-Path $backend ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $PSScriptRoot "local-backend.py"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$launcher`" `"$log`"" -WorkingDirectory $backend
$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
# ExecutionTimeLimit 0 = no limit: the default (72 h) would stop the backend.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Description "Local development portal on http://127.0.0.1:8000 (log: $log)" -Force | Out-Null
Write-Host "Registered '$TaskName' (starts at every sign-in)."

Stop-LocalBackend
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
  throw "Port 8000 is held by something that is not the local backend. Close it, then run this again."
}
Start-ScheduledTask -TaskName $TaskName
foreach ($i in 1..60) {
  try { Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2 | Out-Null; Write-Host "Running: http://127.0.0.1:8000/login"; return }
  catch { Start-Sleep -Seconds 1 }
}
throw "Started, but http://127.0.0.1:8000/health did not answer within 60 s. See $log"
