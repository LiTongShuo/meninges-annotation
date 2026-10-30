$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv $venvDirectory
}

& $venvPython -m pip install --disable-pip-version-check --editable $projectRoot
& $venvPython -m unittest discover -s (Join-Path $projectRoot "tests") -v

