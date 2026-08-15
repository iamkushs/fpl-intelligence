$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$actual = @(git ls-files '*.md' | Sort-Object)
$expected = @('AGENTS.md', 'WORKFLOW.md')
if (($actual -join "`n") -ne ($expected -join "`n")) {
    throw "Tracked Markdown policy violation. Expected AGENTS.md and WORKFLOW.md; got: $($actual -join ', ')"
}
Write-Host 'Tracked Markdown policy verified.'
