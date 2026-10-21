param(
    [string]$OutputDir = 'E:\ShenlunStudio'
)

$ErrorActionPreference = 'Stop'
$installerRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $installerRoot
$releaseDir = Join-Path $projectRoot 'source\dist\ShenlunStudio'
$issPath = Join-Path $installerRoot 'ShenlunStudio.iss'
$setupName = 'ShenlunStudio-0.9.5-Setup.exe'

if (-not (Test-Path -LiteralPath (Join-Path $releaseDir 'ShenlunStudio.exe') -PathType Leaf)) {
    throw 'Latest portable release is missing ShenlunStudio.exe.'
}
if (-not (Test-Path -LiteralPath (Join-Path $releaseDir '_internal') -PathType Container)) {
    throw 'Latest portable release is missing _internal.'
}

$compilerCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $compiler) {
    $command = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($command) {
        $compiler = $command.Source
    }
}
if (-not $compiler) {
    throw 'Inno Setup 6 compiler (ISCC.exe) was not found.'
}

$resolvedOutput = [IO.Path]::GetFullPath($OutputDir)
if (-not (Test-Path -LiteralPath $resolvedOutput)) {
    New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
}

& $compiler "/O$resolvedOutput" $issPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compiler exited with code $LASTEXITCODE."
}

$setupPath = Join-Path $resolvedOutput $setupName
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw "Compiler did not create $setupPath."
}

$item = Get-Item -LiteralPath $setupPath
$hash = Get-FileHash -LiteralPath $setupPath -Algorithm SHA256
Write-Output "Setup: $($item.FullName)"
Write-Output "Size: $($item.Length) bytes"
Write-Output "SHA256: $($hash.Hash)"
