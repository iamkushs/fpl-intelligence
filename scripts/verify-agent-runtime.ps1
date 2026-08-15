param([switch]$AllowMissingGitHubAuth)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$failed = $false

function Report-Command([string]$Name, [string]$Command, [string[]]$Arguments) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        Write-Host "${Name}: MISSING"
        $script:failed = $true
        return
    }
    try {
        $firstLine = (& $Command @Arguments 2>&1 | Select-Object -First 1)
        Write-Host "${Name}: $firstLine"
    } catch {
        Write-Host "${Name}: UNAVAILABLE ($($_.Exception.Message))"
        $script:failed = $true
    }
}

Write-Host "platform: $([System.Environment]::OSVersion.VersionString)"
Write-Host "powershell: $($PSVersionTable.PSVersion)"
Report-Command git git @('--version')
Report-Command gh gh @('--version')
Report-Command codex codex @('--version')
Report-Command node node @('--version')
Report-Command npm npm @('--version')
Report-Command uv uv @('--version')
if (Get-Command uv -ErrorAction SilentlyContinue) { Report-Command project-python uv @('run','python','--version') }

if ((Get-Command gh -ErrorAction SilentlyContinue) -and ((gh auth status 2>$null) -or $LASTEXITCODE -eq 0)) {
    Write-Host 'github-auth: available'
} else {
    Write-Host 'github-auth: unavailable'
    if (-not $AllowMissingGitHubAuth) { $failed = $true }
}

$runtime = @{}
Get-Content 'tooling/symphony-runtime.env' | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object {
    $key, $value = $_ -split '=', 2
    $runtime[$key] = $value
}
$installedCodex = if (Get-Command codex -ErrorAction SilentlyContinue) { ((codex --version) -split '\s+')[-1] } else { '' }
$testedCodex = $runtime['CODEX_VERSION']
if (-not $testedCodex -or $testedCodex -eq 'UNTESTED') {
    Write-Host 'codex-tested-version: UNTESTED (pin after a successful end-to-end smoke test)'
} elseif ($installedCodex -ne $testedCodex) {
    Write-Error "Codex version mismatch: expected $testedCodex, found $installedCodex" -ErrorAction Continue
    $failed = $true
} else { Write-Host "codex-tested-version: $testedCodex (matches)" }

Write-Host "symphony-implementation: $($runtime['SYMPHONY_IMPLEMENTATION'])"
$runnerRef = $runtime['SYMPHONY_REF']
if (-not $runnerRef -or $runnerRef -in @('UNTESTED','UNPINNED')) {
    Write-Host 'symphony-ref: UNTESTED (record after a successful end-to-end smoke test)'
} else { Write-Host "symphony-ref: $runnerRef" }

if ($failed) { exit 1 }
