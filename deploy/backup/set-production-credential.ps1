<#
.SYNOPSIS
  Stores the Render (production) database URL for backup-production.ps1 and
  copy-from-render.ps1. Run once, and again whenever Render's URL changes.

.DESCRIPTION
  Render: your PostgreSQL -> Connect -> "External Database URL".

  The URL is read hidden, checked (a PostgreSQL URL, NOT on this machine),
  tested with a read-only "SELECT 1", and saved encrypted with Windows DPAPI
  for the current Windows user only, at:

      %LOCALAPPDATA%\BDEPortal\production-database-url.dpapi

  That is outside the repository - it can never be committed - and only this
  Windows account on this PC can decrypt it. It is never printed or logged.
  backend\.env never holds it: the local backend has no business knowing
  where production lives.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\set-production-credential.ps1
#>
[CmdletBinding()]
param([string]$PgBin = "C:\Program Files\PostgreSQL\18\bin")

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "DbTools.psm1") -Force
Set-PgBin $PgBin

$secure = Read-Host "Paste Render's EXTERNAL database URL (hidden)" -AsSecureString
if ($secure.Length -eq 0) { Write-Host "Nothing entered - unchanged."; exit 1 }

# Check it before saving: parse, refuse local, prove it connects read-only.
$env:RENDER_DATABASE_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
try {
  $conn = Get-ProductionConnection
  Write-Host "Testing $(Format-Connection $conn) ..."
  $r = Invoke-Pg "psql" $conn @("-At", "-c", "SELECT 'ok', current_setting('server_version'), (SELECT count(*) FROM users);") -WithDatabase
  if ($r.ExitCode -ne 0) { throw "Could not connect with that URL (is it the EXTERNAL URL, and is the database awake?)." }
  $parts = ($r.Output | Select-Object -First 1) -split '\|'
  Write-Host ("Connected: PostgreSQL {0}, {1} user account(s)." -f $parts[1], $parts[2]) -ForegroundColor Green
}
finally {
  Remove-Item Env:\RENDER_DATABASE_URL -ErrorAction SilentlyContinue
}

Save-ProductionDatabaseUrl $secure
Write-Host "Saved (encrypted for $env:USERNAME): $(Get-CredentialPath)" -ForegroundColor Green
Write-Host "Next: deploy\backup\backup-production.ps1 to take a production backup now."
