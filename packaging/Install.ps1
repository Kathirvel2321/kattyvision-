<#
    Install.ps1  (inside the KattyVision folder - not the project's own installer)

    The portable edition: everything the program needs is already in this
    folder, so there is nothing to download and nothing to install system-wide.
    This script only does the per-machine bits a human expects an installer to
    do:

        1. check the bundled Python and the bundled OCR engine actually work
        2. create the working folders and the settings file
        3. put a Desktop icon and a Start Menu entry in place
        4. say clearly what is still missing (usually: the bank seal)

    It is safe to run twice - nothing it does is destructive, and it never
    overwrites settings, documents or the seal.

    Run:  double click "Install.cmd"     (or)
          powershell -ExecutionPolicy Bypass -File .\Install.ps1
    For a validation install without per-user shortcuts, add -NoShortcuts -NoLaunch.
#>
[CmdletBinding()]
param([switch]$Quiet, [switch]$NoLaunch, [switch]$NoShortcuts)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

function Say     { param($m) if (-not $Quiet) { Write-Host $m } }
function Ok      { param($m) Write-Host "  [ ok  ] $m" -ForegroundColor Green }
function Warn    { param($m) Write-Host "  [warn ] $m" -ForegroundColor Yellow }
function Bad     { param($m) Write-Host "  [fail ] $m" -ForegroundColor Red }
function Head    { param($m) Write-Host ""; Write-Host " $m" -ForegroundColor Cyan }

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " Installing KattyVision" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Say "This takes about a minute. Please leave this window open."

# --------------------------------------------------------------- 1. self check
Head "1. Checking what is inside the package"

$python = Join-Path $Root "python\python.exe"
$pythonw = Join-Path $Root "python\pythonw.exe"
if (-not (Test-Path $python) -or -not (Test-Path $pythonw)) {
    Bad "The python\ folder is missing or damaged."
    Bad "Please download and unpack the whole KattyVision folder again."
    exit 1
}
Ok "the program's own Python is here (nothing is installed on your PC)"

# Run the real import test - the only honest way to know it works.
$probe = & $python -c "import sys, tkinter, pymupdf, PIL; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0 -or $probe -notmatch 'ok') {
    Bad "The packaged program could not start:"
    Bad "$probe"
    Bad "Windows may be blocking it. Please download and unpack it again."
    exit 1
}
Ok "the program starts correctly"

# ------------------------------------------------------------------ 2. folders
Head "2. Preparing your folders"

foreach ($folder in @("input", "output", "work", "logs")) {
    $path = Join-Path $Root $folder
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Force -Path $path | Out-Null }
}
Ok "input, output, work and logs are ready"

$config = Join-Path $Root "config.json"
$example = Join-Path $Root "config.example.json"
if (-not (Test-Path $config)) {
    if (Test-Path $example) {
        Copy-Item $example $config
        Ok "settings created (config.json)"
    } else {
        Warn "config.example.json is missing - built-in defaults will be used"
    }
} else {
    Ok "your settings are already here and were left alone"
}

# ---------------------------------------------------------------- 3. shortcuts
Head "3. Creating your icons"

$arguments = "-m court_ocr.app"
if ($NoShortcuts) {
    Ok "shortcuts skipped (-NoShortcuts)"
} else {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $programs = [Environment]::GetFolderPath('Programs')
    $menuFolder = Join-Path $programs "KattyVision"
    New-Item -ItemType Directory -Force -Path $menuFolder | Out-Null

    $icon = Join-Path $Root "assets\app.ico"
    function New-KattyShortcut {
        param([string]$Path, [string]$Description)
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($Path)
        $link.TargetPath = $pythonw            # pythonw = no black console window
        $link.Arguments = $arguments
        $link.WorkingDirectory = $Root
        if (Test-Path $icon) { $link.IconLocation = "$icon,0" }
        $link.Description = $Description
        $link.Save()
    }

    New-KattyShortcut -Path (Join-Path $desktop "KattyVision.lnk") `
        -Description "KattyVision - local, offline court filing preparation"
    New-KattyShortcut -Path (Join-Path $menuFolder "KattyVision.lnk") -Description "KattyVision"
    Ok "Desktop icon and Start Menu entry are ready"

    # the folder to drop the case documents into
    $documentsLink = Join-Path $desktop "KattyVision - Documents.lnk"
    if (Test-Path $documentsLink) { Remove-Item $documentsLink -Force -ErrorAction SilentlyContinue }
    $shell = New-Object -ComObject WScript.Shell
    $folderLink = $shell.CreateShortcut($documentsLink)
    $folderLink.TargetPath = (Join-Path $Root "input")
    $folderLink.Description = "Drop the case documents in here, then open KattyVision"
    $folderLink.Save()
    Ok "a 'KattyVision - Documents' folder icon is on your Desktop"
}

# ------------------------------------------------------------- 4. what is left
Head "4. What is ready, and what is still missing"

$seal = Join-Path $Root "assets\stamp.png"
$sealGood = $false
if (Test-Path $seal) {
    try {
        # Ask the bundled Python rather than guessing: a .txt renamed to .png,
        # or a 0-byte file left by a failed download, both look present to
        # Test-Path but would fail at stamping time.
        $check = & $python -c "from PIL import Image`ntry:`n    im = Image.open(r'$seal'); im.load(); ok = im.width > 50 and im.height > 50`nexcept Exception:`n    ok = False`nprint('yes' if ok else 'no')" 2>&1
        $sealGood = ("$check".Trim() -match 'yes')
    } catch {
        $sealGood = $false
    }
}

$tess = & $python -c "from court_ocr.toolcheck import find_tesseract; t = find_tesseract(); print('yes' if t.available else 'no')" 2>&1
if ("$tess".Trim() -match 'yes') {
    Ok "text recognition (OCR) is included - nothing else to install"
} else {
    Warn "the OCR engine did not start. Everything else works, but scans will"
    Warn "not become searchable. Please tell whoever gave you this program."
}

if ($sealGood) {
    Ok "the bank seal is in place"
} else {
    Write-Host ""
    Write-Host "  !  ONE THING LEFT: the bank seal" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "     For security the seal is never included in this program."
    Write-Host "     Ask for the seal image, save it, and put it here:"
    Write-Host ""
    Write-Host "       $seal" -ForegroundColor White
    Write-Host ""
    Write-Host "     (If Windows hides the file extension, make sure the file is"
    Write-Host "      called stamp.png and not stamp.png.txt)"
    Write-Host ""
    Write-Host "     Until then KattyVision will run, but it will refuse to stamp"
    Write-Host "     a filing with a stand-in - that is on purpose."
}


# ------------------------------------------------------------------ 5. done
Head "All done"
Write-Host ""
if ($NoShortcuts) {
    Write-Host "  Validation install complete; no per-user shortcuts were created."
} else {
    Write-Host "  Your Desktop now has:" -ForegroundColor Green
    Write-Host "     [icon]  KattyVision                 <- double click to open the app"
    Write-Host "     [icon]  KattyVision - Documents     <- put the case PDFs in here"
    Write-Host ""
    Write-Host "  It is also in the Start Menu under 'KattyVision'."
    Write-Host ""
    Write-Host "  Putting a case together:"
    Write-Host "     1. Drop the case documents into the 'KattyVision - Documents' folder"
    Write-Host "     2. Open KattyVision, press RUN"
    Write-Host "     3. Finished files appear in the 'output' folder"
    Write-Host ""
    Write-Host "  Nothing was installed on your PC. To remove the program later, just"
    Write-Host "  delete this whole folder (and the two desktop icons)."
}
Write-Host ""

if (-not $NoLaunch) {
    $answer = Read-Host "Open KattyVision now? (Y/n)"
    if ($answer -eq "" -or $answer -match '^[Yy]') {
        Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $Root
    }
}
exit 0

