<#
.SYNOPSIS
  MANUAL development refresh: replaces this PC's development database with a
  snapshot of production (Render). Production is only read.

.DESCRIPTION
  Local development has its own database and its own accounts (the "-dev"
  accounts, see backend\app\seeds\dev_accounts.py). Use this only when you
  want current production data locally - it REPLACES local data, including
  any test data you made. It never runs on a schedule.

  1. Checks the target: backend\.env must say ENV=development and its
     DATABASE_URL must be on this machine. The source must NOT be. Anything
     ambiguous stops the run before anything is changed.
  2. Backs up the local database  -> D:\GP3\backups\local-dev\local-before-refresh-<time>.dump
  3. Dumps production (read only) -> D:\GP3\backups\production\production-<time>.dump
     (or uses -FromBackup <file> and does not connect to production at all)
  4. Restores into the local database in ONE transaction - a failure leaves
     it untouched.
  5. Verifies: every table's row count and every account's password hash
     match production; an active Super Admin exists. On failure, the local
     backup from step 2 is put back automatically.
  6. Applies this checkout's migrations, keeps the copied passwords (the
     local SUPER_ADMIN_* values never reset them), and re-creates the "-dev"
     accounts, which production does not have.

  Production credential: set-production-credential.ps1 (once).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\copy-from-render.ps1

.EXAMPLE
  # From a production backup already on disk, without connecting to Render:
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\copy-from-render.ps1 -FromBackup D:\GP3\backups\production\production-20261002-020000.dump
#>
[CmdletBinding()]
param(
  [string]$FromBackup = "",
  [string]$EnvFile = "",
  [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
  [string]$BackupRoot = "D:\GP3\backups",
  # Skip the typed confirmation (for scripted use). The target checks still apply.
  [switch]$Yes
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "DbTools.psm1") -Force
Set-PgBin $PgBin

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $RepoRoot "backend"
if (-not $EnvFile) { $EnvFile = Join-Path $Backend ".env" }
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$ProductionDir = Join-Path $BackupRoot "production"
$LocalDir = Join-Path $BackupRoot "local-dev"

function Invoke-Backend([string[]]$Arguments, [string]$What) {
  Push-Location $Backend
  try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)." }
  }
  finally { Pop-Location }
}

$exitCode = 0
try {
  if (Test-InsidePath $BackupRoot $RepoRoot) { throw "Refusing to write backups inside the repository." }
  New-Item -ItemType Directory -Force $ProductionDir, $LocalDir | Out-Null
  Set-DbLogFile (Join-Path $BackupRoot "refresh-local-from-production.log")
  if (-not (Test-Path -LiteralPath $Python)) { throw "Backend virtualenv not found: $Python" }

  # ------------------------------------------------- 1. who is who, or stop
  $local = Get-LocalDevConnection $EnvFile
  $prod = $null
  if ($FromBackup) {
    if (-not (Test-Path -LiteralPath $FromBackup)) { throw "Backup file not found: $FromBackup" }
    $source = "the backup file $(Split-Path $FromBackup -Leaf)"
  }
  else {
    $prod = Get-ProductionConnection
    $source = Format-Connection $prod
  }
  Write-DbLog "INFO" "Refresh requested: $source -> $(Format-Connection $local)"

  if (-not $Yes) {
    Write-Host ""
    Write-Host "This REPLACES everything in your LOCAL development database" -ForegroundColor Yellow
    Write-Host "  $(Format-Connection $local)" -ForegroundColor Yellow
    Write-Host "with $source. Local test data will be gone (a backup is taken first)." -ForegroundColor Yellow
    Write-Host "Production is only read." -ForegroundColor Yellow
    $answer = Read-Host "Type REFRESH to continue"
    if ($answer -cne "REFRESH") { Write-DbLog "INFO" "Cancelled - nothing changed."; exit 1 }
  }

  # ----------------------------------------------------- 2. local undo point
  Write-DbLog "INFO" "[1/5] Backing up the local database..."
  $undo = New-VerifiedDump $local $LocalDir "local-before-refresh"
  Write-DbLog "OK" "Local backup: $($undo.Path)"

  # ---------------------------------------------- 3. production, read only
  $expectedCounts = $null
  $expectedAccounts = $null
  if ($FromBackup) {
    $sourceDump = (Resolve-Path -LiteralPath $FromBackup).Path
    Write-DbLog "INFO" "[2/5] Using $sourceDump (production is not contacted)."
  }
  else {
    Write-DbLog "INFO" "[2/5] Dumping production (read only)..."
    $before = Get-RowCounts $prod
    $dump = New-VerifiedDump $prod $ProductionDir "production"
    $expectedCounts = Get-RowCounts $prod
    $expectedAccounts = Get-AccountFingerprint $prod
    foreach ($table in $before.Keys) {
      if ($before[$table] -ne $expectedCounts[$table]) {
        throw "Production changed while it was being copied ($table). Nothing local was changed - run again in a quiet moment."
      }
    }
    $sourceDump = $dump.Path
    Write-DbLog "OK" ("Production backup: {0} ({1:N0} bytes)" -f $dump.Name, $dump.Bytes)
  }

  # ------------------------------------------- 4 + 5. restore, verify, or undo
  Write-DbLog "INFO" "[3/5] Restoring into the local database (one transaction)..."
  $verify = {
    $admins = [int](Invoke-PsqlScalar $local "SELECT count(*) FROM users WHERE role = 'SUPER_ADMIN' AND is_active;")
    if ($admins -lt 1) { throw "no active Super Admin after the restore" }
    if ($expectedCounts) {
      $got = Get-RowCounts $local
      $diff = @()
      foreach ($table in ($expectedCounts.Keys | Sort-Object)) {
        $g = if ($got.ContainsKey($table)) { $got[$table] } else { "missing" }
        if ("$g" -ne "$($expectedCounts[$table])") { $diff += "$table production=$($expectedCounts[$table]) local=$g" }
      }
      if ($diff.Count) { throw "row counts differ: $($diff -join '; ')" }
      if ((Get-AccountFingerprint $local) -ne $expectedAccounts) { throw "account passwords differ from production" }
      Write-DbLog "OK" "Verified: $($expectedCounts.Count) tables, every row count and every account match production."
    }
    else {
      Write-DbLog "OK" "Verified: restored from file, $admins active Super Admin(s)."
    }
  }
  Invoke-LocalRestore $local $sourceDump $undo.Path $verify

  # --------------------- 6. schema, keep copied passwords, dev accounts back
  try {
    Write-DbLog "INFO" "[4/5] Applying this checkout's migrations..."
    Invoke-Backend @("-m", "app.db.migrate", "upgrade") "Migrations"
    Write-DbLog "INFO" "[5/5] Keeping production passwords; re-creating the -dev accounts..."
    Invoke-Backend @("-m", "app.services.super_admin_env", "adopt") "Adopting SUPER_ADMIN_* values"
    if (Get-EnvFileValue $EnvFile "DEV_ACCOUNT_PASSWORD") {
      Invoke-Backend @("-m", "app.seeds.dev_accounts") "Creating dev accounts"
    }
    else {
      Write-DbLog "WARN" "DEV_ACCOUNT_PASSWORD is not set in backend\.env - no -dev accounts created."
    }
  }
  catch {
    $reason = $_.Exception.Message
    Write-DbLog "ERROR" "$reason - putting the local database back."
    $u = Invoke-Pg "pg_restore" $local @("--clean", "--if-exists", "--no-owner", "--no-acl", "--single-transaction", $undo.Path) -WithDatabase
    if ($u.ExitCode -ne 0) { throw "$reason AND the automatic undo failed. Restore by hand from $($undo.Path)" }
    throw "$reason The local database was put back as it was."
  }

  Invoke-Retention $ProductionDir "production"
  Get-ChildItem -LiteralPath $LocalDir -Filter "local-before-refresh-*.dump" -File |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 |
    ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Force
      Remove-Item -LiteralPath "$($_.FullName).sha256" -Force -ErrorAction SilentlyContinue
    }

  Write-DbLog "OK" "RESULT: PASS - local development now holds the production snapshot, plus the -dev accounts."
  Write-DbLog "INFO" "Undo point if you need it: $($undo.Path)"
}
catch {
  $exitCode = 1
  Write-DbLog "ERROR" ("RESULT: FAIL - " + $_.Exception.Message)
}
exit $exitCode
