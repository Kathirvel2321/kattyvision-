<#
    SETUP.ps1 - one time installer for the court filing automation.

    It installs ONLY things that must live outside Python:
        1. Python packages          : pymupdf, pillow   (fast, pure wheels)
        2. Tesseract OCR engine     : free, Apache-2.0  -> replaces iLovePDF premium
        3. Ghostscript (optional)   : free, AGPL        -> extra compression strength

    Run once, from this folder:
        powershell -ExecutionPolicy Bypass -File .\SETUP.ps1

    Options:
        -WithGhostscript   also install Ghostscript (~65 MB, needs admin once)
        -SkipTesseract     keep any existing Tesseract installation
        -SkipPip           skip the Python package step
#>
[CmdletBinding()]
param(
    [switch]$WithGhostscript,
    [switch]$SkipTesseract,
    [switch]$SkipPip
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

function Write-Info { param($m) Write-Host "[setup] $m" }
function Write-Ok   { param($m) Write-Host "[  ok  ] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[ warn ] $m" -ForegroundColor Yellow }
function Write-Bad  { param($m) Write-Host "[ fail ] $m" -ForegroundColor Red }

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " Court filing automation - one time setup" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""

# --------------------------------------------------------------- 1. Python
Write-Info "checking Python..."
try {
    $pyVersion = (& python --version 2>&1) -join ' '
} catch {
    Write-Bad "Python was not found on PATH."
    Write-Host "        Install Python 3.10 or newer from https://www.python.org/downloads/"
    Write-Host "        and tick 'Add python.exe to PATH' during installation."
    exit 1
}
Write-Ok "$pyVersion"

# ------------------------------------------------- 1b. working folders + config
# config.json is per-machine and deliberately NOT in git, so create it from the
# shipped example the first time this PC runs the setup.  Every folder the
# program writes to is created up front, so a fresh install is never missing
# input\, output\, work\ or logs\.
$example = Join-Path $Root "config.example.json"
$config = Join-Path $Root "config.json"
if (-not (Test-Path $config)) {
    if (Test-Path $example) {
        Copy-Item $example $config
        Write-Ok "settings created: config.json (from config.example.json)"
    } else {
        Write-Warn "config.example.json not found - the program will use its built-in defaults"
    }
} else {
    Write-Ok "settings: config.json (kept, not overwritten)"
}
foreach ($folder in @("input", "output", "work", "logs")) {
    $path = Join-Path $Root $folder
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}
Write-Ok "folders ready: input, output, work, logs"

$realSeal = Join-Path $Root "assets\stamp.png"
if (Test-Path $realSeal) {
    Write-Ok "bank seal     : assets\stamp.png"
} else {
    Write-Warn "no real bank seal yet (assets\stamp.png)."
    Write-Host "        A PLACEHOLDER seal will be stamped and the step reported as CHECK."
    Write-Host "        Put the bank's official transparent PNG at assets\stamp.png"
    Write-Host "        before filing anything with the court."
}

# ---------------------------------------------------------- 2. pip packages
if (-not $SkipPip) {
    Write-Info "installing Python packages (pymupdf, pillow)..."
    & python -m pip install --upgrade --quiet pip
    & python -m pip install --upgrade --quiet pymupdf pillow
    $check = & python -c "import pymupdf, PIL; print('pymupdf', pymupdf.__version__)"
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "Python packages could not be installed."
        Write-Host "        Try:  python -m pip install --user pymupdf pillow"
        exit 1
    }
    Write-Ok $check
    # theme for the desktop app - optional, the app falls back to plain tkinter
    & python -m pip install --upgrade --quiet ttkbootstrap
    if ($LASTEXITCODE -eq 0) { Write-Ok "window theme: ttkbootstrap" }
    else { Write-Warn "ttkbootstrap not installed - the window will look plain ttk" }
} else {
    Write-Warn "skipping pip step (-SkipPip)"
}

# ------------------------------------------------------------- 3. Tesseract
function Get-TesseractPath {
    $candidates = @()
    $found = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($found) { $candidates += $found.Source }
    $candidates += @(
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
        "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe",
        "C:\Tesseract-OCR\tesseract.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

if (-not $SkipTesseract) {
    $tess = Get-TesseractPath
    if ($tess) {
        Write-Ok "Tesseract already installed: $tess"
    } else {
        Write-Info "installing Tesseract OCR engine (free, ~50 MB, one time)..."
        $installed = $false
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            try {
                & winget install --id UB-Mannheim.TesseractOCR -e --silent `
                    --accept-package-agreements --accept-source-agreements `
                    --disable-interactivity | Out-Null
                if ($LASTEXITCODE -eq 0) { $installed = $true }
            } catch {
                Write-Warn "winget could not install Tesseract: $($_.Exception.Message)"
            }
        } else {
            Write-Warn "winget is not available - downloading the installer directly"
        }

        if (-not $installed) {
            $url = "https://github.com/UB-Mannheim/tesseract/releases/download/" +
                   "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
            $exe = Join-Path $env:TEMP "tesseract-setup.exe"
            try {
                Write-Info "downloading $url"
                Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
                $target = "$env:LOCALAPPDATA\Programs\Tesseract-OCR"
                Write-Info "running the installer silently into $target"
                Start-Process -FilePath $exe -ArgumentList "/S", "/D=$target" -Wait
                $installed = [bool](Get-TesseractPath)
            } catch {
                Write-Warn "automatic install failed: $($_.Exception.Message)"
            }
        }

        $tess = Get-TesseractPath
        if ($tess) {
            $ver = (& $tess --version) -join ' | '
            Write-Ok "Tesseract installed: $ver"
        } else {
            Write-Bad "Tesseract could not be installed automatically."
            Write-Host "        Install it manually from:"
            Write-Host "        https://github.com/UB-Mannheim/tesseract/wiki"
            Write-Host "        then set 'paths.tesseract_exe' in config.json."
        }
    }
} else {
    Write-Warn "skipping Tesseract (-SkipTesseract)"
}

# ------------------------------------------------------------ 4. Ghostscript
function Get-GhostscriptPath {
    $found = Get-Command gswin64c -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $candidates = @()
    $candidates += Get-ChildItem "$env:ProgramFiles\gs\*\bin\gswin64c.exe" -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\gs\*\bin\gswin64c.exe" -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "C:\gs\*\bin\gswin64c.exe" -ErrorAction SilentlyContinue
    if ($candidates.Count -gt 0) { return $candidates[0].FullName }
    return $null
}

$gs = Get-GhostscriptPath
if ($gs) {
    Write-Ok "Ghostscript already installed: $gs"
} elseif ($WithGhostscript) {
    Write-Info "installing Ghostscript (~65 MB, free)..."
    $url = "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/" +
           "gs10080/gs10080w64.exe"
    $exe = Join-Path $env:TEMP "gs-setup.exe"
    try {
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
        $target = "$env:LOCALAPPDATA\Programs\gs"
        Start-Process -FilePath $exe -ArgumentList "/S", "/D=$target" -Wait
        $gs = Get-GhostscriptPath
        if ($gs) { Write-Ok "Ghostscript installed: $gs" }
        else { Write-Warn "Ghostscript installer finished but the program was not found." }
    } catch {
        Write-Warn "Ghostscript install failed: $($_.Exception.Message)"
        Write-Host "        Optional - download manually from https://www.ghostscript.com/releases/gsdnld.html"
    }
} else {
    Write-Warn "Ghostscript not installed (optional). Re-run with -WithGhostscript for maximum compression."
}

# ---------------------------------------------------- 5. check the install
Write-Host ""
Write-Info "running the built-in check..."
Write-Host ""
& python -m court_ocr.cli --doctor
$doctorExit = $LASTEXITCODE

# ------------------------------------------------------------ 6. next steps
Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
if ($doctorExit -eq 0) {
    Write-Host " SETUP COMPLETE - everything runs locally, nothing is uploaded." -ForegroundColor Green
} else {
    Write-Host " SETUP FINISHED WITH WARNINGS - see the notes above." -ForegroundColor Yellow
}
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " How to use it"
Write-Host " -------------"
Write-Host "  1. Put the case documents in the  input\<CASE NAME>\  folder."
Write-Host "  2. Double click  'Run Court OCR.bat'  and press Run."
Write-Host "  3. Finished files appear in  output\<CASE NAME>\."
Write-Host ""
Write-Host " Command line alternative:"
Write-Host '  python -m court_ocr.cli "input\MY CASE" -c "S.A. 245/2026" --cover'
Write-Host ""
Write-Host " Want a practice run with fake documents?"
Write-Host "  python tools\make_sample_pdf.py"
Write-Host ""
