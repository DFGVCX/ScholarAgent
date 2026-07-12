param(
    [string]$Version = "0.2.0",
    [switch]$SkipToolInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Assert-CommandSucceeded([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path $Python)) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv"
}

Push-Location $RepoRoot
try {
    if (-not $SkipToolInstall) {
        & $Python -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
        Assert-CommandSucceeded "Build tool installation"
    }

    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            npm ci
            Assert-CommandSucceeded "Frontend dependency installation"
        }
        $AssetsPath = Join-Path $RepoRoot "frontend\dist\assets"
        $ResolvedFrontend = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "frontend"))
        $ResolvedAssets = [System.IO.Path]::GetFullPath($AssetsPath)
        if (-not $ResolvedAssets.StartsWith($ResolvedFrontend, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean assets outside frontend: $ResolvedAssets"
        }
        if (Test-Path $ResolvedAssets) {
            Remove-Item -LiteralPath $ResolvedAssets -Recurse -Force
        }
        npm run build
        Assert-CommandSucceeded "Frontend build"
    }
    finally { Pop-Location }

    if (-not $SkipTests) {
        & $Python -m unittest discover -s tests -p "test_*.py"
        Assert-CommandSucceeded "Python test suite"
    }

    & $Python -m PyInstaller `
        (Join-Path $RepoRoot "release\ScholarAgent.spec") `
        --noconfirm --clean `
        --distpath (Join-Path $RepoRoot "release\dist") `
        --workpath (Join-Path $RepoRoot "release\build")
    Assert-CommandSucceeded "PyInstaller build"

    $Executable = Join-Path $RepoRoot "release\dist\ScholarAgent\ScholarAgent.exe"
    if (-not (Test-Path $Executable)) { throw "PyInstaller output is missing: $Executable" }

    $Process = Start-Process -FilePath $Executable -ArgumentList "--no-browser" -PassThru -WindowStyle Hidden
    $EndpointPath = Join-Path $env:LOCALAPPDATA "ScholarAgent\runtime\endpoint.json"
    $Healthy = $false
    try {
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Path $EndpointPath) {
                $Endpoint = Get-Content -Raw $EndpointPath | ConvertFrom-Json
                try {
                    $Health = Invoke-RestMethod -Uri $Endpoint.health_url -TimeoutSec 2
                    if ($Health.status -eq "ok") { $Healthy = $true; break }
                }
                catch { }
            }
            if ($Process.HasExited) { break }
        }
    }
    finally {
        & $Executable --stop --quiet
        if (-not $Process.HasExited) { $Process.WaitForExit(10000) | Out-Null }
    }
    if (-not $Healthy) { throw "Packaged executable failed its health-check smoke test." }

    $IsccCandidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Command) { $Iscc = $Command.Source }
    }
    if (-not $Iscc) {
        throw "Inno Setup 6 is required. Install it with: winget install JRSoftware.InnoSetup"
    }

    & $Iscc "/DMyAppVersion=$Version" (Join-Path $RepoRoot "release\installer.iss")
    Assert-CommandSucceeded "Inno Setup build"
    $Installer = Join-Path $RepoRoot "release\output\ScholarAgent-Setup-$Version.exe"
    if (-not (Test-Path $Installer)) { throw "Installer output is missing: $Installer" }
    $Hash = Get-FileHash -Algorithm SHA256 $Installer
    Write-Host "Release ready: $Installer"
    Write-Host "SHA256: $($Hash.Hash)"
}
finally {
    Pop-Location
}
