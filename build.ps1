param(
    [string]$DistPath = "dist-v2",
    [string]$WorkPath = "build-v2"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$specPath = Join-Path $projectRoot "StarModeFeed.spec"
$resolvedDist = Join-Path $projectRoot $DistPath
$resolvedWork = Join-Path $projectRoot $WorkPath

Push-Location $projectRoot
try {
    python -m PyInstaller `
        --clean `
        --noconfirm `
        --distpath $resolvedDist `
        --workpath $resolvedWork `
        $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$artifact = Join-Path $resolvedDist "StarModeFeed.exe"
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Expected build artifact was not produced: $artifact"
}

$hash = Get-FileHash -LiteralPath $artifact -Algorithm SHA256
Write-Output "Artifact: $artifact"
Write-Output "Bytes: $((Get-Item -LiteralPath $artifact).Length)"
Write-Output "SHA256: $($hash.Hash)"
