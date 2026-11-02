<#
.SYNOPSIS
  One-time setup for the signed SAP push (this PC -> live portal).

.DESCRIPTION
  1. Makes a long random secret.
  2. Saves it encrypted for THIS Windows account (DPAPI) at
     %LOCALAPPDATA%\BDEPortal\sap-push-secret.dpapi - only this account on
     this PC can read it back. Nothing is written into the repository.
  3. Copies it to the clipboard (it is never printed), so it can be pasted
     into Render as the environment variable SAP_PUSH_SECRET.
  4. Writes deploy\sync\sap-uploader.env (git-ignored) with the live address
     and the workbook path, if that file does not exist yet.

  Run it again to replace the secret (then update SAP_PUSH_SECRET on Render
  to the new value too).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File deploy\sync\set-sap-push-secret.ps1
#>
[CmdletBinding()]
param(
  [string]$PortalUrl = "https://deven-bde-sales-portal-backend.onrender.com",
  [string]$SapFile = "C:\Users\deven\OneDrive\Desktop\GP3\BDE & SALES PORTAL SAP DATA.xlsm"
)

$ErrorActionPreference = "Stop"

# 48 random bytes from the OS generator, as URL-safe text (64 characters).
$bytes = New-Object byte[] 48
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")

$folder = Join-Path $env:LOCALAPPDATA "BDEPortal"
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$path = Join-Path $folder "sap-push-secret.dpapi"
ConvertTo-SecureString -String $secret -AsPlainText -Force |
  ConvertFrom-SecureString |
  Set-Content -LiteralPath $path -Encoding ASCII

Set-Clipboard -Value $secret
$secret = $null

$config = Join-Path $PSScriptRoot "sap-uploader.env"
if (-not (Test-Path -LiteralPath $config)) {
  @(
    "# Written by set-sap-push-secret.ps1. Git-ignored.",
    "PORTAL_URL=$PortalUrl",
    "SAP_FILE=$SapFile",
    "CHECK_MINUTES=15"
  ) | Set-Content -LiteralPath $config -Encoding UTF8
  Write-Host "Wrote $config"
}

Write-Host ""
Write-Host "Secret saved (encrypted for $env:USERNAME): $path"
Write-Host "It is on your CLIPBOARD now. Next:"
Write-Host "  1. Render dashboard -> the portal backend service -> Environment"
Write-Host "  2. Add variable  SAP_PUSH_SECRET  and paste (Ctrl+V) as its value, then Save"
Write-Host "     (Render redeploys by itself; wait for it to finish)"
Write-Host "  3. Then run: powershell -NoProfile -ExecutionPolicy Bypass -File deploy\sync\register-sap-uploader.ps1"
