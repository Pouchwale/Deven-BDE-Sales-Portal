<#
  Shared helpers for the portal's database scripts. Imported by:

    backup-production.ps1          Render (production) -> D:\GP3\backups\production
    copy-from-render.ps1           Render -> this PC's development database (manual)
    set-production-credential.ps1  stores the Render URL, encrypted, for this Windows user

  Two connections, never confused:
    Get-ProductionConnection  the Render database. Read only, ever. Refuses
                              anything on this machine.
    Get-LocalDevConnection    DATABASE_URL in backend\.env. Refuses anything
                              that is NOT on this machine, and refuses unless
                              backend\.env says ENV=development explicitly.

  Passwords reach pg_dump/psql only through $env:PGPASSWORD for the duration
  of one call (never on a command line, where other processes can read it),
  and are never printed or logged.
#>

Set-StrictMode -Version 2.0

$script:LogFile = $null
$script:CredentialPath = Join-Path $env:LOCALAPPDATA "BDEPortal\production-database-url.dpapi"
$script:PgBin = "C:\Program Files\PostgreSQL\18\bin"

function Set-DbLogFile([string]$Path) { $script:LogFile = $Path }
function Set-PgBin([string]$Path) { $script:PgBin = $Path }
function Get-CredentialPath { return $script:CredentialPath }

function Write-DbLog([string]$Level, [string]$Message) {
  $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
  $color = switch ($Level) { "ERROR" { "Red" } "WARN" { "Yellow" } "OK" { "Green" } default { "Gray" } }
  Write-Host $line -ForegroundColor $color
  if ($script:LogFile) {
    try { Add-Content -LiteralPath $script:LogFile -Value $line -Encoding ASCII } catch { }
  }
}

function Resolve-PgTool([string]$Name) {
  $candidate = Join-Path $script:PgBin "$Name.exe"
  if (Test-Path -LiteralPath $candidate) { return $candidate }
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "$Name not found (looked in $script:PgBin and PATH)."
}

function Test-LoopbackHost([string]$HostName) {
  $h = $HostName.Trim().Trim('[', ']').ToLowerInvariant()
  return ($h -eq "localhost" -or $h -eq "::1" -or $h.StartsWith("127."))
}

function Test-InsidePath([string]$Child, [string]$Parent) {
  $c = [System.IO.Path]::GetFullPath($Child).TrimEnd('\') + '\'
  $p = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
  return $c.StartsWith($p, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-EnvFileValue([string]$Path, [string]$Name) {
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  $value = $null
  foreach ($raw in Get-Content -LiteralPath $Path) {
    if ($raw -match ("^\s*{0}\s*=\s*(.*)$" -f [regex]::Escape($Name))) {
      $value = $Matches[1].Trim().Trim('"').Trim("'")
    }
  }
  return $value
}

function ConvertFrom-DatabaseUrl([string]$Url, [string]$Label) {
  # postgresql[+driver]://user:password@host[:port]/database[?params]
  $pattern = '^postgres(?:ql)?(?:\+[a-z0-9_]+)?://(?<user>[^:@/]+)(?::(?<pw>[^@]*))?@(?<host>\[[^\]]+\]|[^:/?@]+)(?::(?<port>\d+))?/(?<db>[^?]+)(?:\?(?<query>.*))?$'
  if ($Url.Trim() -notmatch $pattern) { throw "$Label is not a PostgreSQL URL." }
  $sslmode = $null
  if ($Matches['query'] -and $Matches['query'] -match '(?:^|&)sslmode=([^&]+)') { $sslmode = $Matches[1] }
  return [pscustomobject]@{
    Label    = $Label
    User     = [System.Uri]::UnescapeDataString($Matches['user'])
    Password = $(if ($Matches['pw']) { [System.Uri]::UnescapeDataString($Matches['pw']) } else { "" })
    Host     = $Matches['host'].Trim('[', ']')
    Port     = $(if ($Matches['port']) { [int]$Matches['port'] } else { 5432 })
    Database = [System.Uri]::UnescapeDataString($Matches['db'])
    SslMode  = $sslmode
  }
}

# Safe to print: no password.
function Format-Connection($Conn) {
  return "{0} ({1}@{2}:{3}/{4})" -f $Conn.Label, $Conn.User, $Conn.Host, $Conn.Port, $Conn.Database
}

# ------------------------------------------------------------- production
function Save-ProductionDatabaseUrl([System.Security.SecureString]$Secure) {
  # DPAPI, current-user scope: only this Windows account on this PC can
  # decrypt it. Outside the repository, so it can never be committed.
  $folder = Split-Path -Parent $script:CredentialPath
  New-Item -ItemType Directory -Force $folder | Out-Null
  ConvertFrom-SecureString -SecureString $Secure | Set-Content -LiteralPath $script:CredentialPath -Encoding ASCII
}

function Get-ProductionConnection {
  $url = $env:RENDER_DATABASE_URL
  if (-not $url) {
    if (-not (Test-Path -LiteralPath $script:CredentialPath)) {
      throw "No production database credential. Run deploy\backup\set-production-credential.ps1 once (or set `$env:RENDER_DATABASE_URL for this shell)."
    }
    $secure = Get-Content -LiteralPath $script:CredentialPath | ConvertTo-SecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $url = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  }
  $conn = ConvertFrom-DatabaseUrl $url "production (Render)"
  if (Test-LoopbackHost $conn.Host) {
    throw "The production credential points at THIS machine. Production is the Render database - refusing."
  }
  # Render's external connections require TLS.
  if (-not $conn.SslMode) { $conn.SslMode = "require" }
  return $conn
}

# --------------------------------------------------------- local development
function Get-LocalDevConnection([string]$EnvFile) {
  if ($env:RENDER) { throw "This shell has RENDER set - refusing to treat anything here as local development." }
  $envValue = Get-EnvFileValue $EnvFile "ENV"
  if ($envValue -ne "development") {
    throw "backend\.env must say ENV=development explicitly (it says: '$envValue'). Refusing to touch a database that is not declared a development one."
  }
  $url = Get-EnvFileValue $EnvFile "DATABASE_URL"
  if (-not $url) { throw "DATABASE_URL not found in $EnvFile" }
  $conn = ConvertFrom-DatabaseUrl $url "local development"
  if (-not (Test-LoopbackHost $conn.Host)) {
    throw "DATABASE_URL in backend\.env is not on this machine ($($conn.Host)). Local development must use local PostgreSQL - refusing."
  }
  return $conn
}

# ------------------------------------------------------------ pg tooling
function Invoke-Pg([string]$Tool, $Conn, [string[]]$Arguments, [switch]$WithDatabase) {
  $exe = Resolve-PgTool $Tool
  $base = @("--host", $Conn.Host, "--port", "$($Conn.Port)", "--username", $Conn.User, "--no-password")
  if ($WithDatabase) { $base += @("--dbname", $Conn.Database) }
  $env:PGPASSWORD = $Conn.Password
  if ($Conn.SslMode) { $env:PGSSLMODE = $Conn.SslMode } else { Remove-Item Env:\PGSSLMODE -ErrorAction SilentlyContinue }
  try {
    $output = & $exe @base @Arguments
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
  }
  finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:\PGSSLMODE -ErrorAction SilentlyContinue
  }
}

function Invoke-PsqlScalar($Conn, [string]$Sql) {
  $r = Invoke-Pg "psql" $Conn @("-At", "-c", $Sql) -WithDatabase
  if ($r.ExitCode -ne 0) { throw "Query failed on $($Conn.Label)." }
  return ($r.Output | Select-Object -First 1)
}

function New-VerifiedDump($Conn, [string]$Folder, [string]$Prefix) {
  New-Item -ItemType Directory -Force $Folder | Out-Null
  $name = "{0}-{1}.dump" -f $Prefix, (Get-Date -Format "yyyyMMdd-HHmmss")
  $target = Join-Path $Folder $name
  $partial = "$target.partial"
  try {
    $r = Invoke-Pg "pg_dump" $Conn @("--format=custom", "--compress=6", "--no-owner", "--no-acl", "--file", $partial, $Conn.Database)
    if ($r.ExitCode -ne 0) { throw "pg_dump of $($Conn.Label) failed (exit $($r.ExitCode))." }
    if ((Get-Item -LiteralPath $partial).Length -le 0) { throw "pg_dump of $($Conn.Label) produced an empty file." }
    $listing = & (Resolve-PgTool "pg_restore") --list $partial
    if ($LASTEXITCODE -ne 0) { throw "The dump of $($Conn.Label) is not readable." }
    $tableData = @($listing | Where-Object { $_ -match ' TABLE DATA ' }).Count
    if ($tableData -eq 0) { throw "The dump of $($Conn.Label) contains no table data." }
    Move-Item -LiteralPath $partial -Destination $target
    $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText("$target.sha256", ("{0} *{1}`n" -f $hash, $name), [System.Text.Encoding]::ASCII)
    return [pscustomobject]@{ Path = $target; Name = $name; Bytes = (Get-Item -LiteralPath $target).Length; Tables = $tableData; Sha256 = $hash }
  }
  finally {
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
  }
}

function Get-RowCounts($Conn) {
  $sql = "SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text::bigint FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;"
  $r = Invoke-Pg "psql" $Conn @("-At", "-F", "|", "-c", $sql) -WithDatabase
  if ($r.ExitCode -ne 0) { throw "Could not count rows in $($Conn.Label)." }
  $counts = @{}
  foreach ($line in $r.Output) {
    if ($line -match '^(.+)\|(\d+)$') { $counts[$Matches[1]] = [int64]$Matches[2] }
  }
  return $counts
}

# A fingerprint of every account's id + password hash + active flag. Equal on
# both sides means every sign-in will behave exactly as it does on the source.
function Get-AccountFingerprint($Conn) {
  return Invoke-PsqlScalar $Conn "SELECT coalesce(md5(string_agg(id::text || ':' || hashed_password || ':' || is_active::text, ',' ORDER BY id)), 'none') FROM users;"
}

# Keeps the newest backup of each of the last $Daily days, the last $Weekly
# Sundays and the last $Monthly first-of-month dumps named <prefix>-<stamp>.dump.
# Hand-named dumps are never touched.
function Invoke-Retention([string]$Folder, [string]$Prefix, [int]$Daily = 14, [int]$Weekly = 8, [int]$Monthly = 6) {
  $regex = '^' + [regex]::Escape($Prefix) + '-(\d{8})-(\d{6})\.dump$'
  $items = @()
  foreach ($f in Get-ChildItem -LiteralPath $Folder -Filter "*.dump" -File) {
    $m = [regex]::Match($f.Name, $regex)
    if (-not $m.Success) { continue }
    $stamp = [datetime]::ParseExact($m.Groups[1].Value + $m.Groups[2].Value, "yyyyMMddHHmmss", $null)
    $items += [pscustomobject]@{ File = $f; Stamp = $stamp }
  }
  if ($items.Count -eq 0) { return }
  $perDay = @($items | Group-Object { $_.Stamp.ToString("yyyyMMdd") } |
    ForEach-Object { $_.Group | Sort-Object Stamp -Descending | Select-Object -First 1 } |
    Sort-Object Stamp -Descending)
  $keep = @{}
  $perDay | Select-Object -First $Daily | ForEach-Object { $keep[$_.File.FullName] = $true }
  $perDay | Where-Object { $_.Stamp.DayOfWeek -eq [DayOfWeek]::Sunday } | Select-Object -First $Weekly | ForEach-Object { $keep[$_.File.FullName] = $true }
  $perDay | Where-Object { $_.Stamp.Day -eq 1 } | Select-Object -First $Monthly | ForEach-Object { $keep[$_.File.FullName] = $true }
  foreach ($item in $items) {
    if ($keep.ContainsKey($item.File.FullName)) { continue }
    Remove-Item -LiteralPath $item.File.FullName -Force
    $sha = $item.File.FullName + ".sha256"
    if (Test-Path -LiteralPath $sha) { Remove-Item -LiteralPath $sha -Force }
    Write-DbLog "INFO" "Retention: deleted $($item.File.Name)"
  }
}

# ------------------------------------------------- the refresh, all or nothing
# Replaces the LOCAL database with the contents of $SourceDump. On any failure
# after the restore, puts $UndoDump back. Never connects to production.
function Invoke-LocalRestore($Local, [string]$SourceDump, [string]$UndoDump, [scriptblock]$Verify) {
  # A running local backend holds connections; pg_restore --clean would wait
  # on their locks. Same-role sessions may be ended without a superuser.
  Invoke-Pg "psql" $Local @("-q", "-At", "-c", "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND usename = current_user;") -WithDatabase | Out-Null

  $r = Invoke-Pg "pg_restore" $Local @("--clean", "--if-exists", "--no-owner", "--no-acl", "--single-transaction", $SourceDump) -WithDatabase
  if ($r.ExitCode -ne 0) {
    throw "pg_restore failed (exit $($r.ExitCode)). It ran as ONE transaction, so the local database is unchanged."
  }
  try {
    & $Verify
  }
  catch {
    $reason = $_.Exception.Message
    Write-DbLog "ERROR" "Verification failed after the restore: $reason - putting the local database back."
    $u = Invoke-Pg "pg_restore" $Local @("--clean", "--if-exists", "--no-owner", "--no-acl", "--single-transaction", $UndoDump) -WithDatabase
    if ($u.ExitCode -ne 0) {
      throw "Verification failed ($reason) AND the automatic undo failed. Restore by hand from: $UndoDump"
    }
    throw "Verification failed ($reason). The local database was put back exactly as it was."
  }
}

Export-ModuleMember -Function *
