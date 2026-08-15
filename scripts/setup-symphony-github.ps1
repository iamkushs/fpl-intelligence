param([string]$Repository)
$ErrorActionPreference = 'Stop'
if (-not $Repository) { $Repository = (git remote get-url origin) -replace '^.*github\.com[:/]','' -replace '\.git$','' }
$labels = @(
    @('symphony','1D76DB','Eligible for autonomous Symphony implementation'),
    @('symphony-review','0E8A16','Implementation complete; awaiting human review'),
    @('symphony-blocked','D93F0B','External blocker requires human action'),
    @('model-5.5','C5DEF5','Route Symphony work to GPT-5.5'),
    @('model-luna','C5DEF5','Route Symphony work to Luna'),
    @('model-terra','C5DEF5','Route Symphony work to Terra'),
    @('model-sol','C5DEF5','Route Symphony work to Sol')
)
foreach ($label in $labels) { gh label create $label[0] --repo $Repository --color $label[1] --description $label[2] --force; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
Write-Host "Symphony labels are ready in $Repository."
