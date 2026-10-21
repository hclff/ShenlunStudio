param(
    [string]$SetupPath = ''
)

$ErrorActionPreference = 'Stop'
$installerRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $installerRoot
$releaseDir = Join-Path $projectRoot 'source\dist\ShenlunStudio'
$issPath = Join-Path $installerRoot 'ShenlunStudio.iss'
$buildPath = Join-Path $installerRoot 'build-installer.ps1'

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Match {
    param(
        [string]$Text,
        [string]$Pattern,
        [string]$Message
    )
    Assert-True -Condition ([regex]::IsMatch($Text, $Pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) -Message $Message
}

Assert-True -Condition (Test-Path -LiteralPath $issPath -PathType Leaf) -Message 'Missing installer/ShenlunStudio.iss.'
Assert-True -Condition (Test-Path -LiteralPath $buildPath -PathType Leaf) -Message 'Missing installer/build-installer.ps1.'
Assert-True -Condition (Test-Path -LiteralPath (Join-Path $releaseDir 'ShenlunStudio.exe') -PathType Leaf) -Message 'Latest portable release is missing ShenlunStudio.exe.'
Assert-True -Condition (Test-Path -LiteralPath (Join-Path $releaseDir '_internal') -PathType Container) -Message 'Latest portable release is missing _internal.'

$iss = Get-Content -LiteralPath $issPath -Raw -Encoding utf8
Assert-Match $iss '#define\s+MyAppVersion\s+"0\.9\.5"' 'Installer version macro is not 0.9.5.'
Assert-Match $iss 'AppVersion=\{#MyAppVersion\}' 'Installer does not use the declared application version.'
Assert-Match $iss 'PrivilegesRequired=lowest' 'Installer must use per-user privileges.'
Assert-Match $iss 'DefaultDirName=\{localappdata\}\\Programs\\ShenlunStudio' 'Default installation directory is incorrect.'
Assert-Match $iss 'Source:\s*"\.\.\\source\\dist\\ShenlunStudio\\\*"' 'Installer does not use the latest portable release directory.'
Assert-Match $iss 'Name:\s*"\{localappdata\}\\ShenlunStudio"' 'Uninstall does not remove local user data.'
Assert-Match $iss 'Name:\s*"\{app\}"' 'Uninstall does not remove all installation-directory contents.'
Assert-Match $iss 'CredDeleteW\(' 'Uninstall does not remove Windows credentials.'
Assert-Match $iss "DeleteCredential\('ShenlunStudio'\)" 'Uninstall does not remove the primary credential target.'
Assert-Match $iss "DeleteCredential\('model-api-key@ShenlunStudio'\)" 'Uninstall does not remove the compatibility credential target.'
Assert-Match $iss 'InitializeUninstall\(\)' 'Uninstall is missing the irreversible deletion warning.'
Assert-Match $iss 'MB_DEFBUTTON2' 'Uninstall confirmation must default to No.'

if ($SetupPath) {
    $resolvedSetup = (Resolve-Path -LiteralPath $SetupPath).Path
    $item = Get-Item -LiteralPath $resolvedSetup
    Assert-True -Condition ($item.Length -gt 10MB) -Message 'Setup file is unexpectedly small.'
    $stream = [IO.File]::OpenRead($resolvedSetup)
    try {
        $first = $stream.ReadByte()
        $second = $stream.ReadByte()
    }
    finally {
        $stream.Dispose()
    }
    Assert-True -Condition ($first -eq 0x4D -and $second -eq 0x5A) -Message 'Setup is not a Windows PE file.'
    $hash = (Get-FileHash -LiteralPath $resolvedSetup -Algorithm SHA256).Hash
    Write-Output "Setup: $resolvedSetup"
    Write-Output "Size: $($item.Length) bytes"
    Write-Output "SHA256: $hash"
}

Write-Output 'Installer validation passed.'
