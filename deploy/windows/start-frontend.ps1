<#
.SYNOPSIS
  Builds (optionally) and starts the BDE & Sales Portal frontend in production mode.

.DESCRIPTION
  -Build  runs `npm ci` and `npm run build` first. Do this after every code
          update, BEFORE (re)starting the service. BACKEND_INTERNAL_URL is
          read at build time, so it must be set for the build.
  Without -Build it only starts `next start` on 127.0.0.1:3000 and refuses to
  run if there is no production build.

  Never `next dev` in production.
#>
[CmdletBinding()]
param(
  [switch]$Build,
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 3000,
  [string]$BackendInternalUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Frontend = Join-Path $RepoRoot "frontend"

$npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if (-not $npm) { throw "npm not found. Install Node.js LTS (20.9+) and reopen the shell." }

$env:NODE_ENV = "production"
$env:NEXT_TELEMETRY_DISABLED = "1"
if (-not $env:BACKEND_INTERNAL_URL) { $env:BACKEND_INTERNAL_URL = $BackendInternalUrl }

Set-Location -LiteralPath $Frontend

if ($Build) {
  # A developer's frontend\.env.local on the same machine is read by
  # `next build` too. Process variables win over .env files, so pin anything
  # development-only off, and say so.
  foreach ($file in @(".env", ".env.local", ".env.production", ".env.production.local")) {
    $path = Join-Path $Frontend $file
    if ((Test-Path -LiteralPath $path) -and (Select-String -LiteralPath $path -Pattern '^\s*NEXT_PUBLIC_(SHOW_DEMO_HINT|DEMO_PASSWORD)\s*=' -Quiet)) {
      Write-Host "WARNING: $file sets a development-only NEXT_PUBLIC_*DEMO* variable; it is forced off for this build. Remove it from the production host."
    }
  }
  $env:NEXT_PUBLIC_SHOW_DEMO_HINT = "false"
  Remove-Item Env:\NEXT_PUBLIC_DEMO_PASSWORD -ErrorAction SilentlyContinue

  Write-Host "npm ci..."
  # devDependencies are needed for the build (TypeScript, Tailwind, ESLint).
  & $npm.Source ci --include=dev
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  Write-Host "npm run build (BACKEND_INTERNAL_URL=$env:BACKEND_INTERNAL_URL)..."
  & $npm.Source run build
  if ($LASTEXITCODE -ne 0) { throw "next build failed" }
}

if (-not (Test-Path -LiteralPath (Join-Path $Frontend ".next\BUILD_ID"))) {
  throw "No production build in frontend\.next. Run: deploy\windows\start-frontend.ps1 -Build"
}

$next = Join-Path $Frontend "node_modules\.bin\next.cmd"
if (-not (Test-Path -LiteralPath $next)) { throw "node_modules missing. Run with -Build." }

Write-Host "Starting frontend on http://${BindHost}:${Port}..."
& $next start --port $Port --hostname $BindHost
exit $LASTEXITCODE
