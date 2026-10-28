<#
.SYNOPSIS
  Runs the LOCAL development portal automatically at Windows sign-in - the
  backend (http://127.0.0.1:8000) and the website's development server
  (http://localhost:3000) - so neither is ever "refused to connect" because
  nobody started it.

.DESCRIPTION
  The backend serves the pages AND /api on 127.0.0.1:8000 (backend/.env:
  ENV=development, local PostgreSQL). `next dev` on localhost:3000 shows page
  changes as soon as they are saved and passes /api on to the backend.
  Started by hand they are gone after every restart, every closed terminal -
  and a page Chrome restores from its cache then fails at "Sign in".

  This registers the task "BDE Portal - local backend" (current user, at
  sign-in) and (re)starts it now - re-run it any time to restart. It runs
  hidden (deploy\windows\local-backend.py): the backend restarts whenever
  backend code changes, the website server is started again if it stops.
  Logs: %LOCALAPPDATA%\BDEPortal\backend-local.log and frontend-local.log
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

# Stopping the task does not stop the servers it started (checked: python kept
# port 8000), so the launcher and both servers are stopped by command line.
function Stop-LocalBackend {
  $ErrorActionPreference = "Continue"   # taskkill /T may already have ended a match
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='node.exe'" |
    Where-Object {
      $_.CommandLine -like "*local-backend.py*" -or
      $_.CommandLine -like "*uvicorn app.main:app*--port 8000*" -or
      ($_.Name -eq "node.exe" -and $_.CommandLine -like "*next*dev*--port 3000*")
    } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
}

if ($Remove) {
  Stop-LocalBackend
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed '$TaskName'. Start the backend and website by hand from now on."
  return
}

$backend = (Resolve-Path (Join-Path $PSScriptRoot "..\..\backend")).Path
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Backend virtualenv not found: $python" }
$logDir = Join-Path $env:LOCALAPPDATA "BDEPortal"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "backend-local.log"
$frontendLog = Join-Path $logDir "frontend-local.log"
if (-not (Test-Path -LiteralPath (Join-Path $backend "..\frontend\node_modules\next"))) {
  Write-Warning "frontend\node_modules is missing: run 'npm install' in frontend, or localhost:3000 will not start."
}

# pythonw (no window) runs local-backend.py, which runs uvicorn (restarted
# whenever backend code changes - see that file for why not --reload) and
# next dev (started again if it stops).
$pythonw = Join-Path $backend ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $PSScriptRoot "local-backend.py"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$launcher`" `"$log`" `"$frontendLog`"" -WorkingDirectory $backend
$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
# ExecutionTimeLimit 0 = no limit: the default (72 h) would stop the backend.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Description "Local development portal on http://127.0.0.1:8000 and http://localhost:3000 (logs: $logDir)" -Force | Out-Null
Write-Host "Registered '$TaskName' (starts at every sign-in)."

Stop-LocalBackend
Start-Sleep -Seconds 2
foreach ($port in 8000, 3000) {
  if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port is held by something that is not the local portal. Close it, then run this again."
  }
}
Start-ScheduledTask -TaskName $TaskName
$backendUp = $false
foreach ($i in 1..60) {
  try { Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2 | Out-Null; $backendUp = $true; break }
  catch { Start-Sleep -Seconds 1 }
}
if (-not $backendUp) { throw "Started, but http://127.0.0.1:8000/health did not answer within 60 s. See $log" }
Write-Host "Running: http://127.0.0.1:8000/login"
# next dev compiles a page the first time it is asked for, so allow longer.
foreach ($i in 1..120) {
  try {
    Invoke-WebRequest "http://localhost:3000/login" -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Host "Running: http://localhost:3000/login"
    return
  }
  catch { Start-Sleep -Seconds 1 }
}
Write-Warning "http://localhost:3000 did not answer within 2 minutes. See $frontendLog"
