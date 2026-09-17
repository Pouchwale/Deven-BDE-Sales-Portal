<#
.SYNOPSIS
  Starts the BDE & Sales Portal backend in production mode (foreground).

.DESCRIPTION
  Used directly, or as the command a Windows service (NSSM) runs.

  1. Requires backend\.env (or process environment) with ENV=production. The
     backend itself refuses to start on an unsafe production configuration.
  2. Runs `python -m app.db.migrate check` and aborts if any migration is
     pending or modified. Apply migrations deliberately, after a backup:
       deploy\backup\backup-postgres.ps1
       backend\.venv\Scripts\python.exe -m app.db.migrate upgrade
  3. Starts uvicorn on 127.0.0.1:8000 - reachable only through Caddy.

  ONE worker, on purpose: the SAP workbook watcher is a background thread and
  the login/chat rate limiters and lockout counters live in process memory.
  Several workers would run several watchers importing the same file and give
  every worker its own limiter, multiplying the allowed attempts. One uvicorn
  worker comfortably serves a portal of this size.

  No --reload in production. No access log from uvicorn: the backend's own
  request logging (JSON, with request ids) and Caddy's access log cover it.
#>
[CmdletBinding()]
param(
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $RepoRoot "backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Backend ".env"

if (-not (Test-Path -LiteralPath $Python)) {
  throw "Python virtualenv not found at $Python. Create it: python -m venv backend\.venv; backend\.venv\Scripts\pip install -r backend\requirements.txt"
}

# ENV=production must be configured - in the process environment or in backend\.env.
$envValue = $env:ENV
if (-not $envValue -and (Test-Path -LiteralPath $EnvFile)) {
  foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*ENV\s*=\s*"?([A-Za-z]+)"?\s*$') { $envValue = $Matches[1] }
  }
}
if (-not $envValue -or @("production", "prod") -notcontains $envValue.ToLowerInvariant()) {
  throw "ENV is not 'production' (process environment or $EnvFile). Copy deploy\env\backend.production.env.example to backend\.env and fill it in."
}

Set-Location -LiteralPath $Backend

Write-Host "Checking database migrations..."
& $Python -m app.db.migrate check
if ($LASTEXITCODE -ne 0) {
  Write-Host "ABORT: migrations are pending or modified. Back up, then run: backend\.venv\Scripts\python.exe -m app.db.migrate upgrade"
  exit 1
}

Write-Host "Starting backend on http://${BindHost}:${Port} (1 worker)..."
& $Python -m uvicorn app.main:app `
  --host $BindHost --port $Port `
  --workers 1 `
  --proxy-headers --forwarded-allow-ips 127.0.0.1 `
  --no-access-log
exit $LASTEXITCODE
