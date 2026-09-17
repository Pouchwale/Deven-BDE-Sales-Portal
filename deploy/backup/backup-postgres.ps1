<#
.SYNOPSIS
  Nightly PostgreSQL backup of the BDE & Sales Portal database.

.DESCRIPTION
  - pg_dump custom format (-Fc) of the live database as the application role.
  - Credentials come from $env:DATABASE_URL, or else DATABASE_URL in
    backend/.env. The password is URL-decoded, handed to pg_dump through
    $env:PGPASSWORD for this process only, and cleared afterwards. It is never
    printed or written anywhere.
  - Writes <db>-YYYYMMDD-HHmmss.dump plus a .sha256 next to it, and proves the
    archive is readable with `pg_restore --list`.
  - Retention: newest backup of each of the last 14 days, the last 8 Sunday
    backups and the last 6 first-of-month backups are kept; older timestamped
    dumps are deleted. Files not matching the timestamped name (for example a
    hand-made "...-pre-hardening.dump") are never touched.
  - Refuses to write inside the repository.
  - Appends to backup.log in the destination folder; exits non-zero on failure.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\backup-postgres.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\backup-postgres.ps1 -Destination E:\pg-backups
#>
[CmdletBinding()]
param(
  [string]$Destination = "D:\GP3\backups\postgres",
  [string]$EnvFile = "",
  [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin",
  [int]$KeepDaily = 14,
  [int]$KeepWeekly = 8,
  [int]$KeepMonthly = 6
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $RepoRoot "backend\.env" }

$LogFile = $null

function Write-Log([string]$Level, [string]$Message) {
  $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
  Write-Host $line
  if ($script:LogFile) {
    try { Add-Content -LiteralPath $script:LogFile -Value $line -Encoding ASCII } catch { }
  }
}

function Get-DatabaseUrl([string]$Path) {
  if ($env:DATABASE_URL) { return $env:DATABASE_URL }
  if (-not (Test-Path -LiteralPath $Path)) { throw "No DATABASE_URL in the environment and no env file at $Path" }
  foreach ($raw in Get-Content -LiteralPath $Path) {
    $line = $raw.Trim()
    if ($line -match '^\s*DATABASE_URL\s*=\s*(.+)$') {
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
  # postgresql[+driver]://user:password@host[:port]/database[?params]
  $pattern = '^postgres(?:ql)?(?:\+[a-z0-9_]+)?://(?<user>[^:@/]+)(?::(?<pw>.*))?@(?<host>[^:/?@]+)(?::(?<port>\d+))?/(?<db>[^?]+)'
  if ($Url -notmatch $pattern) { throw "DATABASE_URL is not a PostgreSQL URL (SQLite cannot be backed up with this script)." }
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

function Test-InsidePath([string]$Child, [string]$Parent) {
  $c = [System.IO.Path]::GetFullPath($Child).TrimEnd('\') + '\'
  $p = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
  return $c.StartsWith($p, [System.StringComparison]::OrdinalIgnoreCase)
}

function Resolve-Tool([string]$Name) {
  $candidate = Join-Path $PgBin "$Name.exe"
  if (Test-Path -LiteralPath $candidate) { return $candidate }
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "$Name not found (looked in $PgBin and PATH). Pass -PgBin."
}

function Invoke-Retention([string]$Folder, [string]$DbName) {
  $regex = '^' + [regex]::Escape($DbName) + '-(\d{8})-(\d{6})\.dump$'
  $items = @()
  foreach ($f in Get-ChildItem -LiteralPath $Folder -Filter "*.dump" -File) {
    $m = [regex]::Match($f.Name, $regex)
    if (-not $m.Success) { continue }
    $stamp = [datetime]::ParseExact($m.Groups[1].Value + $m.Groups[2].Value, "yyyyMMddHHmmss", $null)
    $items += [pscustomobject]@{ File = $f; Stamp = $stamp }
  }
  if ($items.Count -eq 0) { return }

  # Newest backup of each calendar day, newest days first.
  $perDay = @($items | Group-Object { $_.Stamp.ToString("yyyyMMdd") } |
    ForEach-Object { $_.Group | Sort-Object Stamp -Descending | Select-Object -First 1 } |
    Sort-Object Stamp -Descending)

  $keep = @{}
  $perDay | Select-Object -First $KeepDaily | ForEach-Object { $keep[$_.File.FullName] = "daily" }
  $perDay | Where-Object { $_.Stamp.DayOfWeek -eq [DayOfWeek]::Sunday } |
    Select-Object -First $KeepWeekly | ForEach-Object { $keep[$_.File.FullName] = "weekly" }
  $perDay | Where-Object { $_.Stamp.Day -eq 1 } |
    Select-Object -First $KeepMonthly | ForEach-Object { $keep[$_.File.FullName] = "monthly" }

  foreach ($item in $items) {
    if ($keep.ContainsKey($item.File.FullName)) { continue }
    Remove-Item -LiteralPath $item.File.FullName -Force
    $sha = $item.File.FullName + ".sha256"
    if (Test-Path -LiteralPath $sha) { Remove-Item -LiteralPath $sha -Force }
    Write-Log "INFO" "Retention: deleted $($item.File.Name)"
  }
}

$exitCode = 0
$partial = $null
try {
  if (Test-InsidePath $Destination $RepoRoot) {
    throw "Refusing to write backups inside the repository ($RepoRoot). Choose a folder outside it."
  }
  if (-not (Test-Path -LiteralPath $Destination)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  }
  $Destination = (Resolve-Path -LiteralPath $Destination).Path
  $script:LogFile = Join-Path $Destination "backup.log"

  $pgDump = Resolve-Tool "pg_dump"
  $pgRestore = Resolve-Tool "pg_restore"
  $conn = ConvertFrom-DatabaseUrl (Get-DatabaseUrl $EnvFile)

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $name = "{0}-{1}.dump" -f $conn.Database, $stamp
  $target = Join-Path $Destination $name
  $partial = "$target.partial"

  Write-Log "INFO" ("Backup started: database {0} on {1}:{2} as {3} -> {4}" -f $conn.Database, $conn.Host, $conn.Port, $conn.User, $target)

  $env:PGPASSWORD = $conn.Password
  & $pgDump --format=custom --compress=6 --no-password `
    --host $conn.Host --port $conn.Port --username $conn.User `
    --file $partial $conn.Database
  if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE" }

  $size = (Get-Item -LiteralPath $partial).Length
  if ($size -le 0) { throw "pg_dump produced an empty file" }

  # The archive must be readable, and must actually contain table data.
  $listing = & $pgRestore --list $partial
  if ($LASTEXITCODE -ne 0) { throw "pg_restore --list could not read the archive (exit $LASTEXITCODE)" }
  $tableData = @($listing | Where-Object { $_ -match ' TABLE DATA ' }).Count
  if ($tableData -eq 0) { throw "Archive contains no TABLE DATA entries" }

  Move-Item -LiteralPath $partial -Destination $target
  $partial = $null

  $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
  # sha256sum-compatible: "<hash> *<file>"
  # (LF line ending, so `sha256sum -c` works as well as Get-FileHash.)
  [System.IO.File]::WriteAllText("$target.sha256", ("{0} *{1}`n" -f $hash, $name), [System.Text.Encoding]::ASCII)

  Write-Log "INFO" ("Backup OK: {0} ({1:N0} bytes, {2} tables with data, sha256 {3})" -f $name, $size, $tableData, $hash)

  Invoke-Retention $Destination $conn.Database
}
catch {
  $exitCode = 1
  Write-Log "ERROR" ("Backup FAILED: " + $_.Exception.Message)
  if ($partial -and (Test-Path -LiteralPath $partial)) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
}
finally {
  Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

exit $exitCode
