$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceMacro = Join-Path $projectRoot "fiji\macros\meninges_annotation.ijm"
$startupMacro = Join-Path $projectRoot ".tools\Fiji.app\macros\StartupMacros.fiji.ijm"
$beginMarker = "// BEGIN PROJECT MENINGES ANNOTATION MACROS"
$endMarker = "// END PROJECT MENINGES ANNOTATION MACROS"

if (-not (Test-Path -LiteralPath $sourceMacro)) {
    throw "Project macro not found: $sourceMacro"
}
if (-not (Test-Path -LiteralPath $startupMacro)) {
    throw "Fiji StartupMacros file not found: $startupMacro"
}

$sourceText = Get-Content -LiteralPath $sourceMacro -Raw
$macroProjectRoot = $projectRoot.Replace('\', '/').TrimEnd('/')
$sourceText = $sourceText.Replace('__HANDOFF_PROJECT_ROOT__', $macroProjectRoot)
$startupText = Get-Content -LiteralPath $startupMacro -Raw
$block = $beginMarker + [Environment]::NewLine +
    $sourceText.Trim() + [Environment]::NewLine +
    $endMarker

$beginIndex = $startupText.IndexOf(
    $beginMarker,
    [System.StringComparison]::Ordinal
)
if ($beginIndex -ge 0) {
    $endIndex = $startupText.IndexOf(
        $endMarker,
        $beginIndex,
        [System.StringComparison]::Ordinal
    )
    if ($endIndex -lt 0) {
        throw "Fiji StartupMacros contains an incomplete project macro block."
    }
    $endIndex += $endMarker.Length
    $updatedText = $startupText.Substring(0, $beginIndex) +
        $block +
        $startupText.Substring($endIndex)
} else {
    $updatedText = $startupText.TrimEnd() +
        [Environment]::NewLine +
        [Environment]::NewLine +
        $block +
        [Environment]::NewLine
}

$utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($startupMacro, $updatedText, $utf8WithoutBom)

$verified = Get-Content -LiteralPath $startupMacro -Raw
if (-not $verified.Contains($beginMarker) -or -not $verified.Contains($endMarker)) {
    throw "Fiji macro installation verification failed."
}

Write-Host "Meninges macros installed into Fiji Startup Macros."
