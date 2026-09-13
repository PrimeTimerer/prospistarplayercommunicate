#requires -Version 7.0
param(
    [Parameter(Mandatory)][ValidateSet("Create", "Verify")][string]$Mode,
    [Parameter(Mandatory)][ValidatePattern("^[a-zA-Z0-9][a-zA-Z0-9-]{2,79}$")][string]$Name,
    [string]$ReportPath,
    [ValidatePattern("^$|^[a-fA-F0-9]{64}$")][string]$ExpectedInstalledHash = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$backupBase = Join-Path $projectRoot "backups"
$backupRoot = [IO.Path]::GetFullPath((Join-Path $backupBase $Name))
$manifestPath = Join-Path $backupRoot "manifest.json"
$installedPath = Join-Path $projectRoot "StarModeFeed.exe"
if (-not $backupRoot.StartsWith($backupBase + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The backup must remain under this project's backups directory."
}

function Write-NewReport([string]$Target, [object]$Value) {
    $resolved = [IO.Path]::GetFullPath($Target)
    $artifactRoot = Join-Path $projectRoot "artifacts"
    if (-not ($resolved.StartsWith($backupRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
              $resolved.StartsWith($artifactRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
        throw "Reports must remain under the selected backup or project artifacts directory."
    }
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $resolved) -PathType Container)) {
        throw "Create the report's intended parent directory first."
    }
    $stream = [IO.File]::Open($resolved, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
    try { $writer.WriteLine(($Value | ConvertTo-Json -Depth 15)) }
    finally { $writer.Dispose() }
}

function Hash-File([string]$Target) {
    return (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash
}

function Resolve-ScopedTree([string]$Target) {
    if ([string]::IsNullOrWhiteSpace($Target)) { throw "An empty state tree is not a valid release scope." }
    $resolved = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Target))
    $broad = @([IO.Path]::GetPathRoot($resolved), $projectRoot, $env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA,
               (Join-Path $env:USERPROFILE "Desktop"), (Join-Path $env:USERPROFILE "Documents"))
    if ($broad.Where({ $_ -and [string]::Equals($_.TrimEnd("\", "/"), $resolved.TrimEnd("\", "/"), [StringComparison]::OrdinalIgnoreCase) }).Count) {
        throw "Refusing a broad directory as the configured state tree: $resolved"
    }
    if ($resolved.StartsWith($backupBase, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The backup directory cannot be an input state tree."
    }
    return $resolved
}

if ($Mode -eq "Create") {
    if (Test-Path -LiteralPath $backupRoot) { throw "A release backup is create-only; select a new name." }
    $profileRoot = Join-Path $env:LOCALAPPDATA "StarModeFeed"
    $configPath = Join-Path $profileRoot "config.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { $configPath = Join-Path $projectRoot "config.json" }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw "Resolve the configured profile before preserving a release." }
    $settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
    $dataRoot = Resolve-ScopedTree $(if ($settings.data_dir) { $settings.data_dir } else { Join-Path $profileRoot "data" })
    $outputRoot = Resolve-ScopedTree $(if ($settings.output_dir) { $settings.output_dir } else { Join-Path $profileRoot "output" })
    $specs = [Collections.Generic.List[object]]::new()
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    function Add-File([string]$Source, [string]$Relative) {
        if ([string]::IsNullOrWhiteSpace($Source)) { return }
        $sourcePath = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Source))
        if ((Test-Path -LiteralPath $sourcePath -PathType Leaf) -and $seen.Add($sourcePath)) {
            $specs.Add([pscustomobject]@{ path = $sourcePath; relative = $Relative })
        }
    }
    Add-File $configPath "profile/config.json"
    Add-File (Join-Path $profileRoot "persona.txt") "profile/persona.txt"
    Add-File (Join-Path $projectRoot "config.json") "legacy/config.json"
    Add-File (Join-Path $projectRoot "persona.txt") "legacy/persona.txt"
    Add-File $settings.save_path "game-save/StarPlayer.dat"
    Add-File $installedPath "StarModeFeed-pre-release.exe"
    Add-File $settings.ledger_path "legacy/ledger.json"
    $trees = @(
        [pscustomobject]@{ path = $dataRoot; relative = "profile/data" },
        [pscustomobject]@{ path = $outputRoot; relative = "output" }
    )
    foreach ($tree in $trees) {
        if (Test-Path -LiteralPath $tree.path -PathType Container) {
            foreach ($file in Get-ChildItem -LiteralPath $tree.path -Recurse -File) {
                Add-File $file.FullName (Join-Path $tree.relative ([IO.Path]::GetRelativePath($tree.path, $file.FullName)))
            }
        }
    }
    if ($specs.Count -gt 10000) { throw "The resolved release scope is unexpectedly large; inspect it before copying." }
    $sourceCommit = (& git -C $projectRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot resolve the source revision." }
    New-Item -ItemType Directory -Path $backupRoot | Out-Null
    $files = foreach ($spec in $specs) {
        $destination = [IO.Path]::GetFullPath((Join-Path $backupRoot $spec.relative))
        if (-not $destination.StartsWith($backupRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "A backup destination escaped the selected directory."
        }
        $before = Hash-File $spec.path
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $spec.path -Destination $destination
        if ((Hash-File $destination) -ne $before -or (Hash-File $spec.path) -ne $before) {
            throw "A source changed while it was being preserved; keep this partial backup and retry with a new name."
        }
        [pscustomobject]@{ path = $spec.path; backup_path = $destination; bytes = (Get-Item -LiteralPath $destination).Length; sha256 = $before }
    }
    $manifest = [ordered]@{
        created_at_utc = [DateTime]::UtcNow.ToString("o"); source_commit = $sourceCommit
        config_path = $configPath; save_path = $settings.save_path; trees = $trees; files = @($files)
    }
    Write-NewReport $manifestPath $manifest
    [pscustomobject]@{ mode = "Create"; manifest = $manifestPath; files = @($files).Count; backups_verified = $true }
    exit 0
}

if (-not $ReportPath) { throw "Verify requires a new report path under project artifacts." }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$rows = foreach ($file in $manifest.files) {
    $backupOk = (Test-Path -LiteralPath $file.backup_path -PathType Leaf) -and (Hash-File $file.backup_path) -eq $file.sha256
    $present = Test-Path -LiteralPath $file.path -PathType Leaf
    $currentHash = if ($present) { Hash-File $file.path } else { $null }
    $same = $present -and $currentHash -eq $file.sha256
    $expectedExe = [string]::Equals($file.path, $installedPath, [StringComparison]::OrdinalIgnoreCase) -and
                   $ExpectedInstalledHash -and $currentHash -eq $ExpectedInstalledHash
    [pscustomobject]@{ path = $file.path; backup_ok = $backupOk; present = $present; unchanged = $same
                       intended_executable_change = [bool]($expectedExe -and -not $same)
                       sha256 = $currentHash; ok = [bool]($backupOk -and ($same -or $expectedExe)) }
}
$originalPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($file in $manifest.files) { [void]$originalPaths.Add($file.path) }
$added = foreach ($tree in $manifest.trees) {
    $resolved = Resolve-ScopedTree $tree.path
    if (Test-Path -LiteralPath $resolved -PathType Container) {
        foreach ($file in Get-ChildItem -LiteralPath $resolved -Recurse -File) {
            if (-not $originalPaths.Contains($file.FullName)) { $file.FullName }
        }
    }
}
$result = [ordered]@{
    checked_at_utc = [DateTime]::UtcNow.ToString("o"); manifest = $manifestPath
    ok = (@($rows | Where-Object { -not $_.ok }).Count -eq 0 -and @($added).Count -eq 0)
    files = @($rows); added_files = @($added)
    unchanged = @($rows | Where-Object unchanged).Count
    intended_executable_changes = @($rows | Where-Object intended_executable_change).Count
}
Write-NewReport $ReportPath $result
[pscustomobject]@{ mode = "Verify"; ok = $result.ok; files = @($rows).Count; unchanged = $result.unchanged
                  intended_executable_changes = $result.intended_executable_changes; added_files = @($added).Count }
if (-not $result.ok) { throw "Preservation verification failed; do not promote or silently restore user state." }
