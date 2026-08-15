$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

foreach ($commandName in @('git', 'uv', 'python', 'npm', 'codex')) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) { throw "Required command not found: $commandName" }
}

git config --local rerere.enabled true
git config --local rerere.autoupdate true
uv sync --frozen
if ($LASTEXITCODE) { exit $LASTEXITCODE }
npm --prefix frontend ci
if ($LASTEXITCODE) { exit $LASTEXITCODE }
New-Item -ItemType Directory -Force -Path 'state/storage','state/runtime','logs/runtime' | Out-Null
Write-Host 'Windows agent workspace bootstrap complete.'
