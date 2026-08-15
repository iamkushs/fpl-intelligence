$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
npm --prefix frontend ci
if ($LASTEXITCODE) { exit $LASTEXITCODE }
npm --prefix frontend run typecheck
if ($LASTEXITCODE) { exit $LASTEXITCODE }
npm --prefix frontend run build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
