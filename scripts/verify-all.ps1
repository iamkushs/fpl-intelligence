$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
& "$PSScriptRoot/verify-markdown.ps1"
& "$PSScriptRoot/verify-backend.ps1"
& "$PSScriptRoot/verify-frontend.ps1"
