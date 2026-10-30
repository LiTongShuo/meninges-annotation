$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project environment is missing. Run scripts\bootstrap.ps1 first."
}

& $python -m meninges_annotation validate
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $python -m meninges_annotation render-all
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $python -m meninges_annotation export --format png --format pdf
exit $LASTEXITCODE

