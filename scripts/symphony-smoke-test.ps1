param([int]$TimeoutSeconds = 3600, [switch]$RecordRuntime)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
. "$PSScriptRoot/symphony-runner-process.ps1"

foreach ($commandName in @('gh','git','codex','uv')) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) { throw "Required command not found: $commandName" }
}
gh auth status 2>$null
if ($LASTEXITCODE) { throw 'GitHub CLI authentication is required.' }
uv run python -m tools.symphony_runner validate WORKFLOW.md
if ($LASTEXITCODE) { exit $LASTEXITCODE }

$repo = (git remote get-url origin) -replace '^.*github\.com[:/]','' -replace '\.git$',''
$dataRoot = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'FPLSymphony' } else { Join-Path $HOME 'AppData/Local/FPLSymphony' }
$lockPath = Join-Path $dataRoot 'runner.lock'
$existingRunner = Test-SymphonyCompatibleRunner -Repository $repo -LockPath $lockPath
$body = 'Opt-in Windows Symphony smoke test. Do not modify product behavior. Use one ## Codex Workpad. Change only tooling/symphony-smoke.env so SMOKE_TEST_SEQUENCE equals this issue number; run .\scripts\verify-all.ps1; review the diff; record successful verification and HOST HANDOFF READY in the Workpad; let the host runner sync, commit, push, create a PR, and transition labels; keep the issue open; do not merge.'
$issueUrl = gh issue create --repo $repo --title 'Windows Symphony smoke test: harmless handoff' --label symphony --body $body
$issueNumber = [int]($issueUrl -split '/')[-1]
Write-Host "Created $issueUrl"

$runner = $null
$runnerOut = Join-Path $env:TEMP "fpl-symphony-smoke-$issueNumber.out"
$runnerErr = Join-Path $env:TEMP "fpl-symphony-smoke-$issueNumber.err"
if ($existingRunner) {
    Write-Host 'Using the healthy runner that already owns this state root.'
} else {
    $runner = Start-Process -FilePath 'uv' -ArgumentList @('run','python','-m','tools.symphony_runner','run','WORKFLOW.md') -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $runnerOut -RedirectStandardError $runnerErr -PassThru
}
try {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds 15
        $issue = gh issue view $issueNumber --repo $repo --json labels,comments,state | ConvertFrom-Json
        $labels = @($issue.labels.name)
        $workpads = @($issue.comments | Where-Object { $_.body.StartsWith('## Codex Workpad') })
        $prs = @(gh pr list --repo $repo --state open --search "$issueUrl in:body" --json number,url | ConvertFrom-Json)
        $workspaceRoot = $env:SYMPHONY_WORKSPACE_ROOT
        if (-not $workspaceRoot) { $workspaceRoot = Join-Path $env:LOCALAPPDATA 'FPLSymphony/workspaces' }
        $workspace = Join-Path $workspaceRoot "GH-$issueNumber"
        if ($labels -contains 'symphony-review' -and $labels -notcontains 'symphony' -and $workpads.Count -eq 1 -and $prs.Count -gt 0 -and (Test-Path $workspace)) {
            & "$PSScriptRoot/verify-markdown.ps1"
            Write-Host "Windows Symphony smoke test passed: $($prs[0].url)"
            if ($RecordRuntime) {
                $runnerRef = (git rev-parse HEAD).Trim()
                $codexVersion = ((codex --version) -split '\s+')[-1]
                @(
                    '# Recorded by scripts/symphony-smoke-test.ps1 after a successful full Windows smoke test.'
                    'SYMPHONY_IMPLEMENTATION=fpl-windows-python'
                    "SYMPHONY_REF=$runnerRef"
                    "CODEX_VERSION=$codexVersion"
                ) | Set-Content -LiteralPath (Join-Path $repoRoot 'tooling/symphony-runtime.env') -Encoding ascii
                Write-Host "Recorded tested runner $runnerRef and Codex $codexVersion. Review and commit the runtime file."
            }
            exit 0
        }
    } while ((Get-Date) -lt $deadline -and ($null -eq $runner -or -not $runner.HasExited))
    throw "Smoke test did not complete. Inspect $issueUrl, $runnerOut and $runnerErr."
} finally {
    Stop-SymphonyOwnedProcess -Process $runner -Owned ($null -ne $runner)
}
