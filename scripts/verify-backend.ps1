$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
uv run python -m compileall -q backend tools
if ($LASTEXITCODE) { exit $LASTEXITCODE }
New-Item -ItemType Directory -Force -Path 'state/runtime' | Out-Null
$pytestDir = "state/runtime/pytest-$PID"
$migrationDb = "state/runtime/migrations-$PID.db"
$testExit = 0
try {
    uv run python -m pytest --basetemp $pytestDir
    $testExit = $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $pytestDir -Recurse -Force -ErrorAction SilentlyContinue
}
if ($testExit) { exit $testExit }
$env:DATABASE_URL = "sqlite:///$migrationDb"
try {
    uv run alembic upgrade head
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    uv run alembic check
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
} finally {
    Remove-Item -LiteralPath $migrationDb -Force -ErrorAction SilentlyContinue
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
