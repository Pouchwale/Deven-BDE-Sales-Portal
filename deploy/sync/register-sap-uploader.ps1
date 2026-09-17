<#
.SYNOPSIS
  Runs the SAP uploader automatically: at sign-in and every 15 minutes.

.DESCRIPTION
  Registers a Windows Scheduled Task for the current user that runs
  deploy\sync\sap_uploader.py. Each run uploads the workbook to the live portal
  only if it changed since the last successful upload.

  Log:   %LOCALAPPDATA%\bde-portal\sap-uploader.log
  Remove: Unregister-ScheduledTask -TaskName "BDE Portal - SAP uploader"

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\sync\register-sap-uploader.ps1
#>
[CmdletBinding()]
param(
  [string]$TaskName = "BDE Portal - SAP uploader",
  [int]$EveryMinutes = 15
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Script = Join-Path $PSScriptRoot "sap_uploader.py"
$Config = Join-Path $PSScriptRoot "sap-uploader.env"

if (-not (Test-Path $Config)) {
  throw "Create $Config first (copy sap-uploader.env.example and fill it in)."
}

$Python = Join-Path $RepoRoot "backend\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $Python)) { $Python = (Get-Command pythonw.exe -ErrorAction Stop).Source }

$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $PSScriptRoot
$triggers = @(
  (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
  (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
     -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes))
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
  -Description "Uploads the SAP workbook to the live BDE & Sales Portal when it changes." -Force | Out-Null

Write-Host "Registered '$TaskName': at sign-in and every $EveryMinutes minutes."
Write-Host "Log: $env:LOCALAPPDATA\bde-portal\sap-uploader.log"
