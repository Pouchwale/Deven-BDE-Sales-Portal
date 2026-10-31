<#
.SYNOPSIS
  Schedules the nightly PRODUCTION backup (Render PostgreSQL -> this PC).

.DESCRIPTION
  Creates (or replaces) the task "BDE Portal - production backup", running
  backup-production.ps1 every day at 02:00 (or as soon as the PC is on, if it
  was off then). It only READS production and writes dumps to
  D:\GP3\backups\production. It never touches the local development database:
  refreshing local from production is a manual step (copy-from-render.ps1).

  It also removes the old task "BDE Portal - PostgreSQL backup", which dumped
  this PC's database every night - a stale development copy since the portal
  moved to Render, while production itself had no backup.

  Needs the production credential first: set-production-credential.ps1. The
  credential is encrypted for the signed-in Windows user, so the task runs as
  that user (Interactive: while signed in, which is how this PC is used).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\register-backup-task.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup\register-backup-task.ps1 -At 01:30
#>
[CmdletBinding()]
param(
  [string]$TaskName = "BDE Portal - production backup",
  [string]$LegacyTaskName = "BDE Portal - PostgreSQL backup",
  [string]$At = "02:00",
  [string]$Destination = "D:\GP3\backups\production"
)

$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "backup-production.ps1"
if (-not (Test-Path -LiteralPath $script)) { throw "Not found: $script" }

$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Destination "{1}"' -f $script, $Destination
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
# Interactive, not S4U: an S4U task has no access to the user's DPAPI keys,
# so it could not decrypt the production credential.
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$description = "Nightly read-only backup of the PRODUCTION (Render) database to $Destination. Log: $Destination\backup.log"

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Principal $principal -Description $description -Force | Out-Null
Write-Host "Registered '$TaskName' (daily at $At, as $userId)."

if (Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $LegacyTaskName -Confirm:$false
  Write-Host "Removed '$LegacyTaskName' (it backed up the local development copy, not production)."
}

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime, LastRunTime, LastTaskResult
Write-Host "Run it now to test:  Start-ScheduledTask -TaskName '$TaskName'"
