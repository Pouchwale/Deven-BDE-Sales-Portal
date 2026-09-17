<#
.SYNOPSIS
  Copies the portal's local PostgreSQL database into a hosted one (e.g. Render).

.DESCRIPTION
  One-time move of real data - users, teams, leads, customers, SAP invoice
  lines, references, notifications, audit trail - from this PC to the hosted
  database the deployed backend uses.

  1. Dumps the LOCAL database (DATABASE_URL in backend\.env) with pg_dump.
  2. Asks for the TARGET database URL (Render: your Postgres -> Connect ->
     "External Database URL"). It is read hidden and never written anywhere.
  3. Restores into the target with --clean: tables that already exist there
     (an empty schema, or a Super Admin created at first start) are REPLACED
     by the local copy. Nothing on this PC is changed.
  4. Compares row counts per table, local vs target, and prints PASS/FAIL.

  Stop the hosted backend first (Render: Suspend) or expect a few harmless
  errors while it holds connections; resume it afterwards.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\copy-to-render.ps1
#>
[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
  [string]$WorkDir = "D:\GP3\backups\postgres"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $RepoRoot "backend\.env" }

function Get-LocalDatabaseUrl([string]$Path) {
  foreach ($raw in Get-Content -LiteralPath $Path) {
    if ($raw.Trim() -match '^\s*DATABASE_URL\s*=\s*(.+)$') {
      $value = $Matches[1].Trim().Trim('"').Trim("'")
      # pg_dump speaks libpq URLs, not SQLAlchemy ones.
      return ($value -replace '^postgres(ql)?\+[a-z0-9_]+://', 'postgresql://')
    }
  }
  throw "DATABASE_URL not found in $Path"
}

function Get-RowCounts([string]$Url) {
  $sql = "SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text::bigint FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"
  $out = & (Join-Path $PgBin "psql.exe") --dbname=$Url -At -F "|" -c $sql
  if ($LASTEXITCODE -ne 0) { throw "Could not count rows" }
  $counts = @{}
  foreach ($line in $out) {
    if ($line -match '^(.+)\|(\d+)$') { $counts[$Matches[1]] = [int64]$Matches[2] }
  }
  return $counts
}

foreach ($tool in "pg_dump.exe", "pg_restore.exe", "psql.exe") {
  if (-not (Test-Path (Join-Path $PgBin $tool))) { throw "$tool not found in $PgBin" }
}

$localUrl = Get-LocalDatabaseUrl $EnvFile
Write-Host "Local database:  $($localUrl -replace '://([^:]+):[^@]*@', '://$1:***@')"

$secure = Read-Host "Paste the TARGET (Render External) database URL" -AsSecureString
$targetUrl = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
$targetUrl = $targetUrl.Trim() -replace '^postgres(ql)?(\+[a-z0-9_]+)?://', 'postgresql://'
if ($targetUrl -notmatch '^postgresql://') { throw "That is not a PostgreSQL URL." }
if ($targetUrl -match '@(localhost|127\.0\.0\.1)[:/]') { throw "The target is this PC - refusing to overwrite a local database." }
if ($targetUrl -notmatch 'sslmode=') {
  $targetUrl += $(if ($targetUrl.Contains("?")) { "&" } else { "?" }) + "sslmode=require"
}
Write-Host "Target database: $($targetUrl -replace '://([^:]+):[^@]*@', '://$1:***@')"

$answer = Read-Host "This REPLACES the portal tables in the target with your local data. Type COPY to continue"
if ($answer -ne "COPY") { Write-Host "Cancelled."; exit 1 }

New-Item -ItemType Directory -Force $WorkDir | Out-Null
$dump = Join-Path $WorkDir ("copy-to-render-{0}.dump" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

Write-Host "`n[1/3] Dumping local database..."
& (Join-Path $PgBin "pg_dump.exe") --dbname=$localUrl -Fc --no-owner --no-acl -f $dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }

Write-Host "[2/3] Restoring into target (this can take a minute)..."
& (Join-Path $PgBin "pg_restore.exe") --dbname=$targetUrl --clean --if-exists --no-owner --no-acl --single-transaction $dump
$restoreExit = $LASTEXITCODE

Write-Host "[3/3] Comparing row counts..."
$local = Get-RowCounts $localUrl
$remote = Get-RowCounts $targetUrl
$failed = $false
"{0,-32} {1,10} {2,10}" -f "table", "local", "target" | Write-Host
foreach ($table in ($local.Keys | Sort-Object)) {
  $r = if ($remote.ContainsKey($table)) { $remote[$table] } else { "missing" }
  $mark = if ("$r" -eq "$($local[$table])") { "" } else { $failed = $true; "  <-- differs" }
  "{0,-32} {1,10} {2,10}{3}" -f $table, $local[$table], $r, $mark | Write-Host
}

Remove-Item -LiteralPath $dump -Force
if ($restoreExit -ne 0 -or $failed) {
  Write-Host "`nRESULT: FAIL (pg_restore exit $restoreExit). Nothing was committed if the restore itself failed." -ForegroundColor Red
  exit 1
}
Write-Host "`nRESULT: PASS - every table copied with matching row counts." -ForegroundColor Green
