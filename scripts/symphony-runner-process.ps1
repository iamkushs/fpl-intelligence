function Test-SymphonyCompatibleRunner {
    param([string]$Repository, [string]$LockPath, [string]$StatusBase = 'http://127.0.0.1:4000')
    try {
        $health = Invoke-RestMethod -Uri "$StatusBase/health" -TimeoutSec 2
        $state = Invoke-RestMethod -Uri "$StatusBase/state" -TimeoutSec 2
        if ($health.status -ne 'ok' -or $state.status -ne 'running' -or $state.repository -ne $Repository) { return $false }
        if (-not (Test-Path -LiteralPath $LockPath)) { return $false }
        $ownerPid = 0
        if (-not [int]::TryParse((Get-Content -LiteralPath $LockPath -Raw).Trim(), [ref]$ownerPid)) { return $false }
        return $null -ne (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)
    } catch { return $false }
}

function Stop-SymphonyOwnedProcess {
    param($Process, [bool]$Owned)
    if ($Owned -and $null -ne $Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
        $Process.WaitForExit(5000) | Out-Null
    }
}
