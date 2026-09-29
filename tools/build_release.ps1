<#
    build_release.ps1 - builds the portable KattyVision package.

    WHAT THIS PRODUCES
        release\KattyVision-<version>\        a folder you can zip and send
        release\KattyVision-<version>-win64.zip

    WHY IT IS BUILT LIKE THIS
        The people using this program are not programmers.  The whole point is
        that a colleague should be able to unzip one file, double click
        Install.cmd and be working - without installing Python, without admin
        rights, and without an internet connection.

        So the package carries its own two things that would normally have to be
        installed on the machine:

            python\     a private copy of Python, with every library the app
                        needs already inside it.  Verified relocatable: copy the
                        folder anywhere and it still runs.  (The small
                        "embeddable" build from python.org was tried first and
                        rejected - it has no tkinter, so the GUI cannot start.)

            tesseract\  a copy of the OCR engine and the English language data.
                        Without this, scans would not become searchable and the
                        user would have to install something themselves.

        Everything else is the app itself.  What is deliberately NOT in the
        package:
            assets\stamp.png   the real bank seal - confidential, machine
                               specific, and the one file that must never be
                               shipped or committed anywhere.
            config.json        per-machine settings; the installer creates it.
            input/ output/     the user's own case documents and results.

    UPDATES
        Rebuild with a higher -Version and send the new zip.  The user unpacks
        it over their existing folder: only the files in the zip are touched, so
        their documents, settings and seal are left exactly as they were.

    Usage:
        powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
        powershell -ExecutionPolicy Bypass -File tools\build_release.ps1 -Version 1.1.0
        powershell -ExecutionPolicy Bypass -File tools\build_release.ps1 -NoOcr
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$NoOcr,          # leave the OCR engine out (much smaller, needs internet)
    [switch]$NoZip           # build the folder only
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
Set-Location $Root

function Head  { param($m) Write-Host ""; Write-Host " $m" -ForegroundColor Cyan }
function Ok    { param($m) Write-Host "  [ ok  ] $m" -ForegroundColor Green }
function Warn  { param($m) Write-Host "  [warn ] $m" -ForegroundColor Yellow }
function Bad   { param($m) Write-Host "  [fail ] $m" -ForegroundColor Red }
function Size  { param($p) if (Test-Path $p) {
    $s = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    return "{0:N0} MB" -f ($s / 1MB) } return "0 MB" }

# --------------------------------------------------------------- 0. what/where
if (-not $Version) {
    $init = Get-Content (Join-Path $Root "court_ocr\__init__.py") -Raw
    if ($init -match '__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
    else { $Version = "0.0.0" }
}
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$outDir = Join-Path $Root "release"
$build = Join-Path $outDir "KattyVision-$Version"
$zipPath = Join-Path $outDir "KattyVision-$Version-win64.zip"

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " Building KattyVision $Version" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan

if (Test-Path $build) { Remove-Item $build -Recurse -Force }
New-Item -ItemType Directory -Force -Path $build | Out-Null
Ok "working in $build"

# ---------------------------------------------------------- 1. private Python
Head "1. Copying Python into the package"

$sourcePython = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $sourcePython) { Bad "python.exe not found on PATH - cannot build."; exit 1 }
$sourceRoot = Split-Path -Parent $sourcePython
$version = & $sourcePython -c "import sys; print('%d.%d' % sys.version_info[:2])"
Ok "using $sourceRoot (Python $version)"

$pyTarget = Join-Path $build "python"
Write-Host "        copying, this takes a minute..."
Copy-Item $sourceRoot $pyTarget -Recurse -Force
Ok "copied ($(Size $pyTarget))"

# Remove everything the machine happened to have installed, so the package only
# ever contains the libraries we ask for below.
$sitePackages = Join-Path $pyTarget "Lib\site-packages"
if (Test-Path $sitePackages) {
    Get-ChildItem $sitePackages -Force | Where-Object { $_.Name -ne 'pip' } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
# The .pyc caches are machine-specific and only make the zip bigger.
Get-ChildItem $pyTarget -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Ok "cleaned out everything this PC had installed"

# Install the libraries into that private copy, not onto this machine.
Head "2. Installing the program's libraries into the copy"
$privatePython = Join-Path $pyTarget "python.exe"
$env:PYTHONNOUSERSITE = "1"
$pipArgs = @("-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "pymupdf>=1.24.0", "pillow>=10.0.0", "ttkbootstrap>=1.10", "tkinterdnd2>=0.4.0")
& $privatePython @pipArgs
if ($LASTEXITCODE -ne 0) { Bad "pip install failed - the package would not work."; exit 1 }
Ok "pymupdf, pillow, ttkbootstrap and tkinterdnd0 installed inside the package"

# Trim what no user will ever touch.  None of this is imported at run time, and
# every megabyte here is a megabyte a colleague has to download.  The list is
# deliberately conservative: CPython's own test suite, the pip bootstrap, IDLE
# and the console scripts.  The standard library, tkinter and the Tcl/Tk data
# all stay, because the GUI genuinely needs them.
foreach ($junk in @("Lib\test", "Lib\idlelib", "Lib\ensurepip", "Lib\tkinter\test",
                    "Scripts", "tcl\tcl8.6\tests", "tk\tk8.5\tests")) {
    $path = Join-Path $pyTarget $junk
    if (Test-Path $path) { Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue }
}
Get-ChildItem $pyTarget -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Ok "trimmed the parts nobody uses ($(Size $pyTarget))"

# Prove the private copy really is self-contained before anything else is added.
$verify = & $privatePython -c "import sys, tkinter, pymupdf, PIL, ttkbootstrap, tkinterdnd2; print('OK')" 2>&1
if ("$verify" -notmatch 'OK') {
    Bad "the private Python cannot start the app: $verify"
    exit 1
}
Ok "verified: the copied Python runs the app on its own"

# ------------------------------------------------------------------ 3. the app
Head "3. Copying the program"
Copy-Item (Join-Path $Root "court_ocr") $build -Recurse -Force
Get-ChildItem $build -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Ok "court_ocr\ (the program)"

New-Item -ItemType Directory -Force -Path (Join-Path $build "assets") | Out-Null
foreach ($file in @("app.ico", "logo.png", "logo_64.png", "stamp_PLACEHOLDER.png")) {
    $src = Join-Path $Root "assets\$file"
    if (Test-Path $src) { Copy-Item $src (Join-Path $build "assets\$file") -Force }
}
if (Test-Path (Join-Path $build "assets\stamp.png")) {
    Bad "the real seal was copied into the package - remove it and build again"
    exit 1
}
Ok "assets\ (icons, logo and the clearly-labelled placeholder seal)"
Ok "the real bank seal was NOT included, as it must not be"

Copy-Item (Join-Path $Root "config.example.json") $build -Force
foreach ($file in @("Install.cmd", "Install.ps1", "Start KattyVision.cmd", "README-FIRST.txt")) {
    Copy-Item (Join-Path $Root "packaging\$file") $build -Force
}
Ok "the installer, the launcher and the instructions"

foreach ($folder in @("input", "output", "work", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $build $folder) | Out-Null
}
Ok "empty input\, output\, work\ and logs\ folders"

# ---------------------------------------------------------------- 4. OCR engine
Head "4. The OCR engine"
if ($NoOcr) {
    Warn "skipped (-NoOcr). The package will be much smaller, but scans will"
    Warn "not become searchable until Tesseract is installed on that PC."
} else {
    $tess = @(
        "C:\Program Files\Tesseract-OCR",
        "C:\Program Files (x86)\Tesseract-OCR",
        "C:\Tesseract-OCR",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR"
    ) | Where-Object { Test-Path (Join-Path $_ "tesseract.exe") } | Select-Object -First 1
    if (-not $tess) {
        Bad "Tesseract is not installed on this PC, so it cannot be copied."
        Bad "Run SETUP.ps1 once (it installs Tesseract), then build again - or"
        Bad "build with -NoOcr to leave it out."
        exit 1
    }
    $tessTarget = Join-Path $build "tesseract"
    New-Item -ItemType Directory -Force -Path $tessTarget | Out-Null
    # Only what is needed to recognise text. The training tools, the Java jars
    # and the .html manuals are ~70 MB of dead weight in a folder nobody opens.
    Copy-Item (Join-Path $tess "*.dll") $tessTarget -Force
    Copy-Item (Join-Path $tess "tesseract.exe") $tessTarget -Force
    $dataTarget = Join-Path $tessTarget "tessdata"
    New-Item -ItemType Directory -Force -Path $dataTarget | Out-Null
    foreach ($data in @("eng.traineddata", "osd.traineddata", "pdf.ttf",
                        "eng.user-patterns", "eng.user-words")) {
        $src = Join-Path $tess "tessdata\$data"
        if (Test-Path $src) { Copy-Item $src $dataTarget -Force }
    }
    Ok "copied Tesseract + English data ($(Size $tessTarget))"
}

# ------------------------------------------------------------------- 5. finish
Head "5. Finishing"
"version = $Version`nbuilt  = $stamp`nfor     = Windows 10/11, 64-bit" |
    Set-Content -Path (Join-Path $build "VERSION.txt") -Encoding UTF8

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
if (-not $NoZip) {
    Write-Host "        zipping, please wait (a few minutes, it is a big folder)..."
    # tar.exe rather than Compress-Archive: the package holds ~5,000 files and
    # Compress-Archive both runs very slowly on that and intermittently dies
    # with "cannot access the file ... because it is being used by another
    # process" - almost always a virus scanner holding a freshly read file.
    # bsdtar handles that quietly, and Windows ships it for exactly this.
    Push-Location $build
    try {
        & tar.exe -a -c -f $zipPath "."
        if ($LASTEXITCODE -ne 0) { throw "tar exited $LASTEXITCODE" }
    } finally { Pop-Location }
    if (-not (Test-Path $zipPath)) { Bad "the zip was not created"; exit 1 }
    Ok "zip created ($(Size $zipPath))"
}

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Green
Write-Host " BUILD FINISHED" -ForegroundColor Green
Write-Host "===============================================================" -ForegroundColor Green
Write-Host "  folder : $build"
if (-not $NoZip) { Write-Host "  zip    : $zipPath" }
Write-Host ""
Write-Host "  Before you send it to anyone, TEST it on a machine that has never"
Write-Host "  seen this program: unzip it somewhere new, double click Install.cmd,"
Write-Host "  and check it opens."
Write-Host ""

