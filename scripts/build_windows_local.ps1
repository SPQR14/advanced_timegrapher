<#
.SYNOPSIS
    Local Windows build & packaging script for Advanced Timegrapher.

.DESCRIPTION
    Compiles the native C++20 engine (advtg_native.dll), freezes the application with
    PyInstaller, runs the headless smoke test with a strict timeout, and optionally builds
    the Inno Setup installer (.exe) and Microsoft Store package (.msix).

    Optionally uploads the resulting Windows installer directly to an existing GitHub
    Release (using `gh release upload`) to offload Windows builds from GitHub Actions.

.EXAMPLE
    .\scripts\build_windows_local.ps1
    .\scripts\build_windows_local.ps1 -Target zen5 -SkipMsix
    .\scripts\build_windows_local.ps1 -UploadRelease v2.1.0
#>
[CmdletBinding()]
param(
    [ValidateSet("x86_64_universal", "native", "zen5", "auto")]
    [string]$Target = "x86_64_universal",

    [switch]$CleanNative,
    [switch]$SkipInstaller,
    [switch]$SkipMsix,
    [switch]$VerifyMsix,
    [string]$UploadRelease = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    $venvScripts = Join-Path $repoRoot ".venv\Scripts"
    $python = if (Test-Path (Join-Path $venvScripts "python.exe")) {
        Join-Path $venvScripts "python.exe"
    } else {
        "python"
    }
    if (Test-Path $venvScripts) {
        $env:PATH = "$venvScripts;$env:PATH"
    }

    # Read version from src/adv_tg/__init__.py
    $initText = Get-Content -Path "src\adv_tg\__init__.py" -Raw -Encoding UTF8
    if ($initText -notmatch '(?m)^__version__\s*=\s*"([^"]+)"') {
        throw "Could not read __version__ from src/adv_tg/__init__.py"
    }
    $version = $Matches[1]
    Write-Host "=== Advanced Timegrapher $version — Local Windows Build (target=$Target) ===" -ForegroundColor Cyan

    # 1. Native C++20 engine
    $nativeArgs = @("scripts\build_native.py", "--target", $Target)
    if ($CleanNative) { $nativeArgs += "--clean" }
    & $python @nativeArgs
    if ($LASTEXITCODE -ne 0) { throw "Native C++20 build failed" }

    & $python scripts\check_native_deps.py src\adv_tg\_native\advtg_native.dll
    if ($LASTEXITCODE -ne 0) { throw "advtg_native.dll is not self-contained" }

    $env:PYTHONPATH = "src"
    & $python -c "from adv_tg import native_bridge as nb; from adv_tg.core.native_session import native_session_available as ok; assert nb.is_native_available() and ok(), 'native engine not loaded'; print(nb.native_arch_info())"
    if ($LASTEXITCODE -ne 0) { throw "Native engine failed to load" }

    # 2. PyInstaller bundle
    Write-Host "=== Freezing with PyInstaller ===" -ForegroundColor Cyan
    & $python -m PyInstaller packaging\adv-tg.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

    # Verify SciPy, ICU and native DLL
    $scipyFound = Get-ChildItem dist -Recurse -Filter "scipy*" -ErrorAction SilentlyContinue
    if ($scipyFound) { throw "SciPy leaked into bundle: $scipyFound" }
    $icuFound = Get-ChildItem dist\adv-tg\_internal -Filter "icu*" -ErrorAction SilentlyContinue
    if ($icuFound) { throw "Third-party ICU DLLs leaked into bundle: $icuFound" }
    & $python scripts\check_native_deps.py dist\adv-tg
    if ($LASTEXITCODE -ne 0) { throw "Bundled native DLL check failed" }

    # 3. Headless smoke test (2-minute timeout)
    Write-Host "=== Running frozen smoke test ===" -ForegroundColor Cyan
    $logDir = Join-Path $env:TEMP "adv-tg-local-smoke"
    Remove-Item -Recurse -Force $logDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:ADV_TG_LOG_DIR = $logDir
    $p = Start-Process -FilePath "dist\adv-tg\adv-tg.exe" -ArgumentList "--smoke-test" -PassThru
    if (-not $p.WaitForExit(120000)) {
        try { $p.Kill() } catch {}
        Get-ChildItem $logDir -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "=== $($_.Name) ===" -ForegroundColor Yellow
            Get-Content $_.FullName -Tail 60
        }
        throw "Smoke test timed out after 120s"
    }
    if ($p.ExitCode -ne 0) {
        Get-ChildItem $logDir -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "=== $($_.Name) ===" -ForegroundColor Yellow
            Get-Content $_.FullName -Tail 60
        }
        throw "Smoke test failed with exit code $($p.ExitCode)"
    }
    Write-Host "Smoke test PASSED!" -ForegroundColor Green

    # 4. Inno Setup installer (.exe)
    $setupExe = "dist\adv-tg-$version-windows-x64-setup.exe"
    if (-not $SkipInstaller) {
        $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        if (Test-Path $iscc) {
            Write-Host "=== Building Inno Setup installer ===" -ForegroundColor Cyan
            & $iscc "/DMyAppVersion=$version" packaging\windows\installer.iss
            if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
            Write-Host "Built installer: $setupExe" -ForegroundColor Green
        } else {
            Write-Warning "Inno Setup 6 not installed ($iscc). Skipping setup.exe (install via: winget install JRSoftware.InnoSetup)."
        }
    }

    # 5. Microsoft Store MSIX (.msix)
    $msixFile = "dist\adv-tg-$version-windows-x64.msix"
    if (-not $SkipMsix) {
        try {
            Write-Host "=== Building Microsoft Store MSIX ===" -ForegroundColor Cyan
            $msixParams = @{ Version = $version }
            if ($VerifyMsix) { $msixParams["Verify"] = $true }
            & packaging\windows\build_msix.ps1 @msixParams
            Write-Host "Built MSIX: $msixFile" -ForegroundColor Green
        } catch {
            Write-Warning "MSIX build skipped/failed: $_"
        }
    }

    # 6. Optional GitHub Release upload
    if ($UploadRelease) {
        if (-not (Test-Path $setupExe)) {
            throw "Cannot upload to ${UploadRelease}: $setupExe was not built"
        }
        Write-Host "=== Uploading $setupExe to GitHub Release $UploadRelease ===" -ForegroundColor Cyan
        gh release upload $UploadRelease $setupExe --clobber
        if ($LASTEXITCODE -ne 0) { throw "gh release upload failed" }
        Write-Host "Uploaded $setupExe to $UploadRelease!" -ForegroundColor Green
    }
} finally {
    Pop-Location
}
