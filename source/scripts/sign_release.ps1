<#
.SYNOPSIS
    Signs release executables and installers with an Authenticode certificate.

.DESCRIPTION
    Signs every file given in -Path with SHA-256 and an RFC 3161 timestamp, then
    verifies the signature. Without a certificate the script reports that the
    artefact is unsigned and exits 0, so an unsigned build is still shippable
    but never silently looks signed.

    A self-signed certificate removes the "unknown publisher" text but does NOT
    clear SmartScreen reputation; only an OV/EV certificate with accumulated
    reputation does that.

.PARAMETER Path
    One or more files to sign (exe, dll, msi, or the Setup.exe).

.PARAMETER PfxPath
    PFX/P12 certificate file. The password is read from -PfxPassword or the
    SHENLUN_SIGN_PFX_PASSWORD environment variable, never from the command line
    history.

.PARAMETER Thumbprint
    Thumbprint of a certificate already installed in the current user's store.

.PARAMETER TimestampUrl
    RFC 3161 timestamp server; defaults to DigiCert.

.EXAMPLE
    pwsh scripts/sign_release.ps1 -Path source/dist/ShenlunStudio/ShenlunStudio.exe

.EXAMPLE
    $env:SHENLUN_SIGN_PFX_PASSWORD = '...'
    pwsh scripts/sign_release.ps1 -Path output/installer/ShenlunStudio-Setup.exe -PfxPath cert.pfx
#>
param(
    [Parameter(Mandatory = $true)][string[]]$Path,
    [string]$PfxPath = $env:SHENLUN_SIGN_PFX,
    [string]$PfxPassword = $env:SHENLUN_SIGN_PFX_PASSWORD,
    [string]$Thumbprint = $env:SHENLUN_SIGN_THUMBPRINT,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'

function Find-SignTool {
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'),
        (Join-Path $env:ProgramFiles 'Windows Kits\10\bin')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    foreach ($root in $roots) {
        $found = Get-ChildItem -LiteralPath $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    $command = Get-Command 'signtool.exe' -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

$targets = foreach ($item in $Path) {
    $resolved = Resolve-Path -LiteralPath $item -ErrorAction SilentlyContinue
    if (-not $resolved) { throw "Signing target not found: $item" }
    if ((Get-Item -LiteralPath $resolved).PSIsContainer) {
        Get-ChildItem -LiteralPath $resolved -Recurse -File -Include *.exe, *.dll | ForEach-Object { $_.FullName }
    }
    else { $resolved.Path }
}
$targets = @($targets)
if (-not $targets) { throw 'No signable files were found.' }

if (-not $PfxPath -and -not $Thumbprint) {
    Write-Output 'UNSIGNED: no certificate configured (set SHENLUN_SIGN_PFX[_PASSWORD] or SHENLUN_SIGN_THUMBPRINT).'
    Write-Output 'UNSIGNED: artefacts were produced without an Authenticode signature.'
    exit 0
}

$signTool = Find-SignTool
if (-not $signTool) { throw 'signtool.exe was not found; install the Windows SDK signing tools.' }

if ($PfxPath) {
    if (-not (Test-Path -LiteralPath $PfxPath -PathType Leaf)) { throw "PFX file not found: $PfxPath" }
    if (-not $PfxPassword) { throw 'A PFX password is required (SHENLUN_SIGN_PFX_PASSWORD).' }
}

foreach ($file in $targets) {
    Write-Output "Signing $file"
    $arguments = @('sign', '/fd', 'SHA256', '/td', 'SHA256', '/tr', $TimestampUrl)
    if ($PfxPath) {
        $arguments += @('/f', $PfxPath, '/p', $PfxPassword)
    }
    else {
        $arguments += @('/sha1', $Thumbprint)
    }
    $arguments += $file
    & $signTool @arguments
    if ($LASTEXITCODE -ne 0) { throw "signtool sign failed for $file (exit $LASTEXITCODE)." }
    & $signTool verify /pa /v $file
    if ($LASTEXITCODE -ne 0) { throw "Signature verification failed for $file (exit $LASTEXITCODE)." }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    Write-Output ("SIGNED: {0} -> {1} ({2})" -f $file, $signature.Status, $signature.SignerCertificate.Subject)
    if ($signature.Status -ne 'Valid') { throw "Signature status is $($signature.Status) for $file." }
}
