[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'

Set-Location $frontendRoot
$env:VITE_API_BASE_URL = '/api/v1'
& npm run dev -- --host 127.0.0.1 --port 5174
exit $LASTEXITCODE
