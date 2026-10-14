<#
.SYNOPSIS
  Backs up the PRODUCTION database (Render PostgreSQL). Read only.

.DESCRIPTION
  The live portal's data - users, passwords, leads, references, feedback,
  audit trail - lives in Render's PostgreSQL. This dumps THAT database, not
  this PC's development copy:

  - pg_dump custom format of production, as production-YYYYMMDD-HHmmss.dump
    in backups\production in the folder that holds the repository
    (D:\BDE and SALES PORTAL\backups\production), with a .sha256 beside it.
  - Proves the archive is readable and holds table data (pg_restore --list).
  - Retention: newest of each of the last 14 days, last 8 Sundays, last 6
    first-of-month dumps. Hand-named dumps are never deleted.
  - Nothing is ever written to production. Nothing local is changed.
  - Log: backup.log in that folder. Exit code 1 on failure, which
    Task Scheduler shows as Last Run Result <> 0x0.

  Credential: set-production-credential.ps1 (once). Nightly schedule:
  register-backup-task.ps1.

  Render's free PostgreSQL is deleted 30 days after creation (+14 days grace).
  These dumps are what survives that - keep a copy off this PC too.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\backup-production.ps1
#>
[CmdletBinding()]
param(
  # Blank = backups\production beside the repository, worked out below rather
  # than a fixed drive path: renaming or moving the project folder must not
  # send backups somewhere else. (Not here: Windows PowerShell leaves
  # $PSScriptRoot empty in the parameter defaults of a [CmdletBinding()] script.)
  [string]$Destination = "",
  [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin"
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "DbTools.psm1") -Force
Set-PgBin $PgBin

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Destination) { $Destination = Join-Path (Split-Path $RepoRoot -Parent) "backups\production" }
$exitCode = 0
try {
  if (Test-InsidePath $Destination $RepoRoot) {
    throw "Refusing to write backups inside the repository ($RepoRoot)."
  }
  New-Item -ItemType Directory -Force $Destination | Out-Null
  Set-DbLogFile (Join-Path $Destination "backup.log")

  $prod = Get-ProductionConnection
  Write-DbLog "INFO" "Production backup started: $(Format-Connection $prod)"
  $dump = New-VerifiedDump $prod $Destination "production"
  $users = Invoke-PsqlScalar $prod "SELECT count(*) FROM users;"
  Write-DbLog "OK" ("Production backup OK: {0} ({1:N0} bytes, {2} tables with data, {3} users, sha256 {4})" -f $dump.Name, $dump.Bytes, $dump.Tables, $users, $dump.Sha256)
  Invoke-Retention $Destination "production"
}
catch {
  $exitCode = 1
  Write-DbLog "ERROR" ("Production backup FAILED: " + $_.Exception.Message)
}
exit $exitCode
