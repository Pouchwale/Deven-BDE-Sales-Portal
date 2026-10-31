<#
.SYNOPSIS
  Sets named accounts' passwords on PRODUCTION (Render), safely: backup first,
  then the change, then proof.

.DESCRIPTION
  1. Reads the production credential (set-production-credential.ps1, DPAPI).
     Refuses the expired free database and anything on this machine.
  2. Runs backup-production.ps1 and stops if the backup fails - no backup,
     no change.
  3. Runs `python -m app.seeds.reset_users --target production` for every
     username in -PasswordFile: the sign-in hash AND the encrypted copy the
     Super Admin's eye reads, per account in one transaction, re-read and
     verified. Never creates, deletes or renames anyone. Never prints a
     password.
  4. Runs the same tool with --verify-only and reports PASS/FAIL per account.

  -PasswordFile is a JSON object {"username": "password", ...} kept OUTSIDE
  the repository; delete it afterwards. -VerifyOnly skips steps 2-3.

  The encrypted copies are made with PASSWORD_VIEW_KEY from backend\.env.
  For the eye to show them on Render, Render's PASSWORD_VIEW_KEY must be the
  same value (otherwise each copy repairs itself at that person's next
  sign-in). Sign-in itself works either way.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\set-production-passwords.ps1 -PasswordFile C:\path\outside\repo\pw.json
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$PasswordFile,
  [switch]$VerifyOnly,
  [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin"
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "DbTools.psm1") -Force
Set-PgBin $PgBin

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $RepoRoot "backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
# The free database that expired on 2026-10-17. Never a target again.
$ExpiredHosts = @("dpg-dalsb0ad0e5s738fihug-a")

if (-not (Test-Path -LiteralPath $PasswordFile)) { throw "Password file not found: $PasswordFile" }
if ((Resolve-Path -LiteralPath $PasswordFile).Path.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "Keep the password file OUTSIDE the repository - it must never be committed."
}
$users = (Get-Content -LiteralPath $PasswordFile -Raw | ConvertFrom-Json).PSObject.Properties.Name
if (-not $users) { throw "The password file names no accounts." }

$conn = Get-ProductionConnection
foreach ($expired in $ExpiredHosts) {
  if ($conn.Host -like "$expired*") {
    throw "The stored production credential is the EXPIRED free database ($expired). Run set-production-credential.ps1 with the paid database's External URL first."
  }
}
Write-Host "Target: $(Format-Connection $conn)"
Write-Host "Accounts: $($users.Count) ($($users -join ', '))"

if (-not $VerifyOnly) {
  Write-Host "`n[1/3] Backing up production first..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "backup-production.ps1")
  if ($LASTEXITCODE -ne 0) { throw "Production backup FAILED - nothing was changed." }
}

# This process only: the production URL and the settings ENV=production
# requires at import. Nothing is written to backend\.env.
$userPart = [Uri]::EscapeDataString($conn.User) + ":" + [Uri]::EscapeDataString($conn.Password)
$saved = @{}
$vars = @{
  DATABASE_URL      = "postgresql+psycopg2://$userPart@$($conn.Host):$($conn.Port)/$($conn.Database)?sslmode=$($conn.SslMode)"
  ENV               = "production"
  CORS_ORIGINS      = "https://deven-bde-sales-portal-backend.onrender.com"
  CORS_ORIGIN_REGEX = " "
  COOKIE_SECURE     = "true"
}
foreach ($name in $vars.Keys) {
  $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
  [Environment]::SetEnvironmentVariable($name, $vars[$name], "Process")
}
$exitCode = 0
Push-Location $Backend
try {
  $list = $users -join ","
  if (-not $VerifyOnly) {
    Write-Host "`n[2/3] Setting passwords..."
    & $Python -m app.seeds.reset_users --target production --users $list --from-file $PasswordFile --yes
    if ($LASTEXITCODE -ne 0) { $exitCode = 1; Write-Host "Setting passwords reported a problem (see above)." }
  }
  Write-Host "`n[3/3] Verifying every account..."
  & $Python -m app.seeds.reset_users --target production --users $list --from-file $PasswordFile --verify-only
  if ($LASTEXITCODE -ne 0) { $exitCode = 1 }
}
finally {
  Pop-Location
  foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
}
exit $exitCode
