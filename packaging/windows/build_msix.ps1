<#
.SYNOPSIS
    Build an unsigned, Microsoft Store-ready MSIX package from dist\adv-tg.

.DESCRIPTION
    Packages the PyInstaller one-folder output (dist\adv-tg) together with the MSIX visual
    assets and AppxManifest.xml, indexes scale/targetsize PNGs into a single resources.pri
    via MakePri.exe, and packs the result with MakeAppx.exe.

    The output .msix is intentionally UNSIGNED because Microsoft Partner Center signs the
    package with Microsoft's Store certificate during certification.

    Pass -Verify (used by GitHub Actions on windows-latest) to test-sign a TEMPORARY copy
    with an ephemeral self-signed certificate, install it with Add-AppxPackage, launch the
    installed app via its AppExecutionAlias (giving it real MSIX package identity) with
    --smoke-test, and remove both the package and the temporary certificate afterward.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$DistDir = "dist\adv-tg",
    [string]$Output = "",
    [string]$IdentityName = "AlbertoPico.AdvancedTimegrapher",
    [string]$Publisher = "CN=2B353A7B-439C-4198-A61A-63000DFBF72B",
    [string]$PublisherDisplayName = "Alberto Pico",
    [switch]$Verify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-ToMsixVersion([string]$RawVersion) {
    # Microsoft Store requires 4 numeric parts Major.Minor.Build.0 (the 4th digit is reserved).
    if ($RawVersion -notmatch '^v?(\d+)\.(\d+)(?:\.(\d+))?') {
        throw "Cannot convert version '$RawVersion' to MSIX Major.Minor.Build.0 format."
    }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
    foreach ($part in @($major, $minor, $patch)) {
        if ($part -lt 0 -or $part -gt 65535) {
            throw "MSIX version component out of 0..65535 range in '$RawVersion'."
        }
    }
    return "$major.$minor.$patch.0"
}

function Find-WindowsSdkBin {
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $versions = Get-ChildItem -Path $root -Directory |
            Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
            Sort-Object { [version]$_.Name } -Descending
        foreach ($ver in $versions) {
            $bin = Join-Path $ver.FullName "x64"
            if ((Test-Path (Join-Path $bin "makeappx.exe")) -and (Test-Path (Join-Path $bin "makepri.exe"))) {
                return $bin
            }
        }
    }
    throw "Windows 10/11 SDK (makeappx.exe and makepri.exe) not found under Windows Kits\10\bin."
}

function Escape-XmlAttr([string]$Value) {
    return [System.Security.SecurityElement]::Escape($Value)
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$distFull = if ([System.IO.Path]::IsPathRooted($DistDir)) { $DistDir } else { Join-Path $repoRoot $DistDir }
if (-not (Test-Path (Join-Path $distFull "adv-tg.exe"))) {
    throw "PyInstaller output not found: expected adv-tg.exe inside '$distFull'."
}

$msixVersion = Convert-ToMsixVersion $Version
if (-not $Output) {
    $Output = Join-Path $repoRoot "dist\adv-tg-$Version-windows-x64.msix"
} elseif (-not [System.IO.Path]::IsPathRooted($Output)) {
    $Output = Join-Path $repoRoot $Output
}

$sdkBin = Find-WindowsSdkBin
$makePri = Join-Path $sdkBin "makepri.exe"
$makeAppx = Join-Path $sdkBin "makeappx.exe"
$signTool = Join-Path $sdkBin "signtool.exe"
Write-Host "Using Windows SDK tools from: $sdkBin"
Write-Host "MSIX Identity: Name=$IdentityName Publisher=$Publisher Version=$msixVersion"

$templatePath = Join-Path $PSScriptRoot "msix\AppxManifest.xml.in"
$assetsSrc = Join-Path $PSScriptRoot "msix\Assets"
if (-not (Test-Path $templatePath)) { throw "Missing manifest template: $templatePath" }
if (-not (Test-Path $assetsSrc)) { throw "Missing MSIX assets directory: $assetsSrc" }

$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("adv-tg-msix-" + [guid]::NewGuid().ToString("N"))
$stageDir = Join-Path $workRoot "stage"
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

try {
    # 1. Stage PyInstaller bundle and visual assets
    Copy-Item -Path (Join-Path $distFull "*") -Destination $stageDir -Recurse -Force
    $assetsDst = Join-Path $stageDir "Assets"
    New-Item -ItemType Directory -Force -Path $assetsDst | Out-Null
    Copy-Item -Path (Join-Path $assetsSrc "*.png") -Destination $assetsDst -Force

    # 2. Render AppxManifest.xml
    $manifestXml = Get-Content -Path $templatePath -Raw -Encoding UTF8
    $manifestXml = $manifestXml.Replace("@MSIX_IDENTITY_NAME@", (Escape-XmlAttr $IdentityName))
    $manifestXml = $manifestXml.Replace("@MSIX_PUBLISHER@", (Escape-XmlAttr $Publisher))
    $manifestXml = $manifestXml.Replace("@MSIX_PUBLISHER_DISPLAY_NAME@", (Escape-XmlAttr $PublisherDisplayName))
    $manifestXml = $manifestXml.Replace("@MSIX_VERSION@", (Escape-XmlAttr $msixVersion))
    $manifestPath = Join-Path $stageDir "AppxManifest.xml"
    [System.IO.File]::WriteAllText($manifestPath, $manifestXml, [System.Text.UTF8Encoding]::new($false))

    # 3. Build single self-contained resources.pri from a clean folder containing ONLY
    # AppxManifest.xml + Assets\ (so makepri never chokes on third-party filenames under _internal\)
    $priStage = Join-Path $workRoot "pri-stage"
    New-Item -ItemType Directory -Force -Path (Join-Path $priStage "Assets") | Out-Null
    Copy-Item -Path $manifestPath -Destination (Join-Path $priStage "AppxManifest.xml") -Force
    Copy-Item -Path (Join-Path $assetsDst "*.png") -Destination (Join-Path $priStage "Assets") -Force

    $priConfig = Join-Path $workRoot "priconfig.xml"
    & $makePri createconfig /cf $priConfig /dq "en-US" /o
    if ($LASTEXITCODE -ne 0) { throw "makepri createconfig failed with exit code $LASTEXITCODE" }

    [xml]$priXml = Get-Content -Path $priConfig -Raw -Encoding UTF8
    $packagingNode = $priXml.SelectSingleNode("//packaging")
    if ($null -ne $packagingNode -and $null -ne $packagingNode.ParentNode) {
        [void]$packagingNode.ParentNode.RemoveChild($packagingNode)
    }
    $priXml.Save($priConfig)

    $priOut = Join-Path $stageDir "resources.pri"
    & $makePri new /pr $priStage /cf $priConfig /mn (Join-Path $priStage "AppxManifest.xml") /in $IdentityName /of $priOut /o
    if ($LASTEXITCODE -ne 0) { throw "makepri new failed with exit code $LASTEXITCODE" }
    if (-not (Test-Path $priOut)) { throw "makepri did not produce $priOut" }

    # 4. Pack unsigned Store-ready .msix
    $outParent = Split-Path -Path $Output -Parent
    if ($outParent) { New-Item -ItemType Directory -Force -Path $outParent | Out-Null }
    & $makeAppx pack /v /o /d $stageDir /p $Output
    if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed with exit code $LASTEXITCODE" }
    Write-Host "Built unsigned Store-ready MSIX: $Output"

    # 5. Optional CI verification: test-sign a copy, install, smoke-test via AppExecutionAlias, uninstall
    if ($Verify) {
        if (-not (Test-Path $signTool)) { throw "signtool.exe not found in $sdkBin" }
        $verifyMsix = Join-Path $workRoot "verify.msix"
        Copy-Item -Path $Output -Destination $verifyMsix -Force

        $cert = $null
        $cerPath = Join-Path $workRoot "test-sign.cer"
        $installedFullName = $null
        try {
            $cert = New-SelfSignedCertificate `
                -Type Custom `
                -Subject $Publisher `
                -KeyUsage DigitalSignature `
                -FriendlyName "adv-tg MSIX CI verification" `
                -CertStoreLocation "Cert:\CurrentUser\My" `
                -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
            Export-Certificate -Cert $cert -FilePath $cerPath -Force | Out-Null
            Import-Certificate -FilePath $cerPath -CertStoreLocation "Cert:\LocalMachine\TrustedPeople" | Out-Null

            & $signTool sign /fd SHA256 /sha1 $cert.Thumbprint $verifyMsix
            if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }

            # Remove any leftover installation of the same identity before installing
            Get-AppxPackage -Name $IdentityName -ErrorAction SilentlyContinue | ForEach-Object {
                Remove-AppxPackage -Package $_.PackageFullName -ErrorAction SilentlyContinue
            }

            Add-AppxPackage -Path $verifyMsix
            $pkg = Get-AppxPackage -Name $IdentityName | Select-Object -First 1
            if ($null -eq $pkg) { throw "Package '$IdentityName' not found after Add-AppxPackage." }
            $installedFullName = $pkg.PackageFullName
            Write-Host "Installed MSIX for verification: $installedFullName"

            # Launch through the AppExecutionAlias so Windows grants full MSIX package identity
            $aliasExe = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\adv-tg.exe"
            if (-not (Test-Path $aliasExe)) {
                throw "AppExecutionAlias not found at $aliasExe"
            }
            $msixLogDir = Join-Path $workRoot "msix-smoke-logs"
            New-Item -ItemType Directory -Force -Path $msixLogDir | Out-Null
            $prevLogDir = $env:ADV_TG_LOG_DIR
            $env:ADV_TG_LOG_DIR = $msixLogDir
            try {
                $p = Start-Process -FilePath $aliasExe -ArgumentList "--smoke-test" -PassThru
                if (-not $p.WaitForExit(120000)) {
                    try { $p.Kill() } catch {}
                    Get-ChildItem $msixLogDir -ErrorAction SilentlyContinue | ForEach-Object {
                        Write-Host "=== $($_.Name) ==="
                        Get-Content $_.FullName -Tail 80
                    }
                    throw "MSIX smoke test timed out after 120s"
                }
                if ($p.ExitCode -ne 0) {
                    Get-ChildItem $msixLogDir -ErrorAction SilentlyContinue | ForEach-Object {
                        Write-Host "=== $($_.Name) ==="
                        Get-Content $_.FullName -Tail 80
                    }
                    throw "MSIX smoke test failed with exit code $($p.ExitCode)"
                }
            } finally {
                if ($null -eq $prevLogDir) { Remove-Item Env:ADV_TG_LOG_DIR -ErrorAction SilentlyContinue }
                else { $env:ADV_TG_LOG_DIR = $prevLogDir }
            }
            Write-Host "MSIX smoke test passed via AppExecutionAlias!"
        } finally {
            if ($installedFullName) {
                Remove-AppxPackage -Package $installedFullName -ErrorAction SilentlyContinue
            }
            if ($null -ne $cert) {
                Remove-Item -Path ("Cert:\LocalMachine\TrustedPeople\" + $cert.Thumbprint) -ErrorAction SilentlyContinue
                Remove-Item -Path ("Cert:\CurrentUser\My\" + $cert.Thumbprint) -ErrorAction SilentlyContinue
            }
        }
    }
} finally {
    Remove-Item -Path $workRoot -Recurse -Force -ErrorAction SilentlyContinue
}
