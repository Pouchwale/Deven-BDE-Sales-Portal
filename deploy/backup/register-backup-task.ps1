<#
.SYNOPSIS
  Registers the nightly database backup as a Windows Scheduled Task.

.DESCRIPTION
  Creates (or replaces) the task "BDE Portal - PostgreSQL backup" for the
  current user, running backup-postgres.ps1 every day at 02:00. If the PC was
  off or asleep at 02:00 the task runs as soon as it can (StartWhenAvailable).

  Logon type: the task first tries S4U ("run whether the user is logged on or
  not", no stored password). Windows only allows that from an elevated shell;
  without elevation it falls back to Interactive, which runs only while this
  user is signed in. On a server, run this script once from an elevated
  PowerShell so backups do not depend on somebody being logged in.

  Weekly restore test: restore-check.ps1 needs the PostgreSQL superuser
  password, which should not be stored in a task. Run it by hand once a week
  (and after every PostgreSQL upgrade):
    powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\restore-check.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\register-backup-task.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\register-backup-task.ps1 -At 01:30 -Destination E:\pg-backups
#>
[CmdletBinding()]
param(
  [string]$TaskName = "BDE Portal - PostgreSQL backup",
  [string]$At = "02:00",
  [string]$Destination = "D:\GP3\backups\postgres"
)

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "backup-postgres.ps1"
if (-not (Test-Path -LiteralPath $script)) { throw "Not found: $script" }

$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Destination "{1}"' -f $script, $Destination
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
$description = "Nightly pg_dump of the BDE & Sales Portal database with retention. Log: $Destination\backup.log"

$registered = $null
try {
  $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Limited
  $registered = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description $description -Force
  Write-Host "Registered '$TaskName' (S4U: runs whether or not $userId is signed in)."
}
catch {
  Write-Host "S4U registration not permitted ($($_.Exception.Message.Trim())). Falling back to Interactive."
  $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
  $registered = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description $description -Force
  Write-Host "Registered '$TaskName' (Interactive: runs only while $userId is signed in)."
  Write-Host "Re-run this script from an elevated PowerShell to make it run when nobody is signed in."
}

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, @{ n = "LogonType"; e = { $_.Principal.LogonType } }
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime, LastRunTime, LastTaskResult
Write-Host "Run it now to test:  Start-ScheduledTask -TaskName '$TaskName'"
