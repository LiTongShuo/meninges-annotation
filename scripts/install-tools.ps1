$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot "config\project.json"
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$archive = [string]$config.tools.fiji_archive
$relativeExecutable = [string]$config.tools.fiji_executable
$executable = Join-Path $projectRoot $relativeExecutable
$toolsDirectory = Join-Path $projectRoot ".tools"

if (Test-Path -LiteralPath $executable) {
    Write-Host "Fiji is ready: $executable"
    & (Join-Path $PSScriptRoot "install-fiji-macro.ps1")
    exit 0
}

if (-not (Test-Path -LiteralPath $archive)) {
    throw "Fiji archive not found: $archive"
}

if (-not (Test-Path -LiteralPath $toolsDirectory)) {
    New-Item -ItemType Directory -Path $toolsDirectory | Out-Null
}

$existing = Get-ChildItem -LiteralPath $toolsDirectory -Force
if ($existing.Count -gt 0) {
    throw "The tools directory is not empty, but Fiji is incomplete: $toolsDirectory"
}

Write-Host "Extracting portable Fiji..."
Expand-Archive -LiteralPath $archive -DestinationPath $toolsDirectory

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Extraction finished, but the Fiji launcher was not found: $executable"
}

Write-Host "Fiji is ready: $executable"
& (Join-Path $PSScriptRoot "install-fiji-macro.ps1")
