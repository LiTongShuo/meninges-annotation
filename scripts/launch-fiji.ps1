$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot "config\project.json"
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$configured = [string]$config.tools.fiji_executable
$executable = Join-Path $projectRoot $configured

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Fiji is not ready. Run scripts\install-tools.ps1 first."
}

$installMacro = Join-Path $PSScriptRoot "install-fiji-macro.ps1"
if (-not (Test-Path -LiteralPath $installMacro)) {
    throw "Project macro installer is missing: $installMacro"
}
& $installMacro

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython -m meninges_annotation sync-raw | Out-Null
}

# Fiji is intentionally visible because the user performs interactive ROI work.
Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable)
