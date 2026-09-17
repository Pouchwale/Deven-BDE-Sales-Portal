<#
.SYNOPSIS
  Proves the newest backup actually restores.

.DESCRIPTION
  1. Picks the newest *.dump in the backup folder and verifies its .sha256.
  2. Creates a scratch database (default bde_portal_restore_check) as the
     PostgreSQL superuser and restores the dump into it (--no-owner).
  3. Counts rows per table in the scratch database and, read-only, in the live
     database, and prints a comparison.
  4. Drops the scratch database - always, even on failure.

  The live database is only ever read (SELECT count(*)), as the application
  role from DATABASE_URL. The superuser password is taken from
  $env:PGPASSWORD_ADMIN or asked for interactively; it is never stored.

  Result:
    PASS  every table restored and every row count matches the live database
    WARN  every table restored, but some counts differ - normal when the live
          database has changed since the dump was taken (check the list)
    FAIL  restore error, checksum mismatch, or tables missing
  Exit code: 0 for PASS/WARN (1 for WARN with -Strict), 1 for FAIL.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\restore-check.ps1
#>
[CmdletBinding()]
param(
  [string]$BackupDir = "D:\GP3\backups\postgres",
  [string]$DumpFile = "",
  [string]$ScratchDb = "bde_portal_restore_check",
  [string]$AdminUser = "postgres",
  [string]$EnvFile = "",
  [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
  [switch]$Strict
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $RepoRoot "backend\.env" }

function Get-DatabaseUrl([string]$Path) {
  if ($env:DATABASE_URL) { return $env:DATABASE_URL }
  if (-not (Test-Path -LiteralPath $Path)) { throw "No DATABASE_URL in the environment and no env file at $Path" }
  foreach ($raw in Get-Content -LiteralPath $Path) {
    if ($raw.Trim() -match '^\s*DATABASE_URL\s*=\s*(.+)$') {
      $value = $Matches[1].Trim()
      if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
      }
      return $value
    }
  }
  throw "DATABASE_URL not found in $Path"
}

function ConvertFrom-DatabaseUrl([string]$Url) {
  $pattern = '^postgres(?:ql)?(?:\+[a-z0-9_]+)?://(?<user>[^:@/]+)(?::(?<pw>.*))?@(?<host>[^:/?@]+)(?::(?<port>\d+))?/(?<db>[^?]+)'
  if ($Url -notmatch $pattern) { throw "DATABASE_URL is not a PostgreSQL URL." }
  $port = 5432
  if ($Matches['port']) { $port = [int]$Matches['port'] }
  $pw = ""
  if ($Matches['pw']) { $pw = [System.Uri]::UnescapeDataString($Matches['pw']) }
  return [pscustomobject]@{
    User     = [System.Uri]::UnescapeDataString($Matches['user'])
    Password = $pw
    Host     = $Matches['host']
    Port     = $port
    Database = [System.Uri]::UnescapeDataString($Matches['db'])
  }
}

function Resolve-Tool([string]$Name) {
  $candidate = Join-Path $PgBin "$Name.exe"
  if (Test-Path -LiteralPath $candidate) { return $candidate }
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "$Name not found (looked in $PgBin and PATH). Pass -PgBin."
}

$psql = Resolve-Tool "psql"
$pgRestore = Resolve-Tool "pg_restore"

# Runs one SQL statement and returns unaligned, tuples-only output lines.
function Invoke-Sql([string]$User, [string]$Password, [string]$Database, [string]$Sql) {
  $env:PGPASSWORD = $Password
  try {
    $out = & $psql --no-password --no-psqlrc -X -v ON_ERROR_STOP=1 -At -F "|" `
      --host $script:conn.Host --port $script:conn.Port --username $User --dbname $Database -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql failed on $Database (exit $LASTEXITCODE)" }
    return @($out)
  }
  finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
  }
}

function Get-RowCounts([string]$User, [string]$Password, [string]$Database) {
  $tables = @(Invoke-Sql $User $Password $Database "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1" | Where-Object { $_ })
  $counts = [ordered]@{}
  if ($tables.Count -eq 0) { return $counts }
  $parts = foreach ($t in $tables) {
    $lit = $t.Replace("'", "''")
    $ident = $t.Replace('"', '""')
    "SELECT '$lit', count(*) FROM public.""$ident"""
  }
  foreach ($line in @(Invoke-Sql $User $Password $Database ($parts -join " UNION ALL ") | Where-Object { $_ })) {
    $pair = $line.Split("|")
    $counts[$pair[0]] = [long]$pair[1]
  }
  return $counts
}

$adminPassword = $null
$result = "FAIL"
$script:conn = $null
$scratchCreated = $false

try {
  $script:conn = ConvertFrom-DatabaseUrl (Get-DatabaseUrl $EnvFile)
  if ($ScratchDb -ieq $conn.Database -or $ScratchDb -ieq "postgres" -or $ScratchDb -notmatch '^[a-z0-9_]+$') {
    throw "Refusing to use '$ScratchDb' as the scratch database."
  }

  if (-not $DumpFile) {
    $newest = Get-ChildItem -LiteralPath $BackupDir -Filter "*.dump" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $newest) { throw "No .dump files in $BackupDir" }
    $DumpFile = $newest.FullName
  }
  Write-Host "Dump:        $DumpFile"
  Write-Host ("Taken:       {0}" -f (Get-Item -LiteralPath $DumpFile).LastWriteTime)

  $shaFile = "$DumpFile.sha256"
  if (Test-Path -LiteralPath $shaFile) {
    $expected = ((Get-Content -LiteralPath $shaFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $DumpFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -ne $actual) { throw "Checksum mismatch for $DumpFile" }
    Write-Host "Checksum:    OK"
  } else {
    Write-Host "Checksum:    (no .sha256 file - skipped)"
  }

  if ($env:PGPASSWORD_ADMIN) {
    $adminPassword = $env:PGPASSWORD_ADMIN
  } else {
    $secure = Read-Host -AsSecureString "Password for PostgreSQL superuser '$AdminUser'"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  }

  Invoke-Sql $AdminUser $adminPassword "postgres" "DROP DATABASE IF EXISTS ""$ScratchDb"" WITH (FORCE)" | Out-Null
  Invoke-Sql $AdminUser $adminPassword "postgres" "CREATE DATABASE ""$ScratchDb""" | Out-Null
  $scratchCreated = $true
  Write-Host "Scratch DB:  $ScratchDb created"

  $env:PGPASSWORD = $adminPassword
  try {
    & $pgRestore --no-password --no-owner --no-privileges --exit-on-error `
      --host $conn.Host --port $conn.Port --username $AdminUser --dbname $ScratchDb $DumpFile
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed (exit $LASTEXITCODE)" }
  }
  finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
  }
  Write-Host "Restore:     OK"

  $restored = Get-RowCounts $AdminUser $adminPassword $ScratchDb
  $live = Get-RowCounts $conn.User $conn.Password $conn.Database

  $missing = @()
  $differ = @()
  Write-Host ""
  Write-Host ("{0,-36} {1,12} {2,12}" -f "table", "restored", "live")
  foreach ($table in $live.Keys) {
    $r = if ($restored.Contains($table)) { $restored[$table] } else { $null }
    $l = $live[$table]
    $flag = ""
    if ($null -eq $r) { $missing += $table; $flag = "  MISSING" }
    elseif ($r -ne $l) { $differ += $table; $flag = "  differs" }
    $shown = if ($null -eq $r) { "-" } else { $r }
    Write-Host ("{0,-36} {1,12} {2,12}{3}" -f $table, $shown, $l, $flag)
  }
  Write-Host ""

  if ($restored.Count -eq 0 -or $missing.Count -gt 0) {
    $result = "FAIL"
    if ($missing.Count -gt 0) { Write-Host ("Missing tables: " + ($missing -join ", ")) }
  } elseif ($differ.Count -gt 0) {
    $result = "WARN"
    Write-Host ("Row counts differ (expected if the live database changed after the dump): " + ($differ -join ", "))
  } else {
    $result = "PASS"
  }
}
catch {
  $result = "FAIL"
  Write-Host ("ERROR: " + $_.Exception.Message)
}
finally {
  if ($scratchCreated -and $adminPassword) {
    try {
      Invoke-Sql $AdminUser $adminPassword "postgres" "DROP DATABASE IF EXISTS ""$ScratchDb"" WITH (FORCE)" | Out-Null
      Write-Host "Scratch DB:  $ScratchDb dropped"
    } catch {
      Write-Host "WARNING: could not drop $ScratchDb - drop it manually."
    }
  }
  $adminPassword = $null
  Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

Write-Host "RESULT: $result"
if ($result -eq "FAIL") { exit 1 }
if ($result -eq "WARN" -and $Strict) { exit 1 }
exit 0
