<#
    INSTALL.ps1 - makes the automation behave like a normal installed program.

    It creates:
        * a Desktop icon          "PDF Helper"
        * a Desktop folder icon   "PDF Helper - Documents"  (the input folder)
        * a Start Menu entry      Start > PDF Helper
        * an Uninstall entry      in the same Start Menu folder

    Launched with pythonw.exe, so no black console window ever appears.
    Everything stays in this folder - nothing is written to Program Files,
    nothing is uploaded, no registry keys are added except the shortcuts.

    Run:  double click "Install App.bat"     (or)
          powershell -ExecutionPolicy Bypass -File .\INSTALL.ps1
#>
[CmdletBinding()]
param(
    [switch]$Quiet,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

function Write-Info { param($m) if (-not $Quiet) { Write-Host "[install] $m" } }
function Write-Ok   { param($m) Write-Host "[  ok   ] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[ warn  ] $m" -ForegroundColor Yellow }
function Write-Bad  { param($m) Write-Host "[ fail  ] $m" -ForegroundColor Red }

$AppName = "KattyVision"

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " Installing: $AppName" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------- 0. sanity
if (-not (Test-Path (Join-Path $Root "court_ocr\app.py"))) {
    Write-Bad "court_ocr\app.py is missing - run this from the project folder."
    exit 1
}

# ------------------------------------------------------- 1. pythonw.exe
function Get-PythonwPath {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $candidate = Join-Path (Split-Path $python.Source) "pythonw.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    foreach ($guess in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\pythonw.exe",
        "C:\Python313\pythonw.exe", "C:\Python312\pythonw.exe"
    )) { if (Test-Path $guess) { return $guess } }
    return $null
}

$pythonw = Get-PythonwPath
if (-not $pythonw) {
    Write-Warn "pythonw.exe was not found - the icon will use python.exe instead,"
    Write-Warn "which opens a console window while running."
    $pythonw = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $pythonw) {
    Write-Bad "Python was not found. Install Python 3.10+ (tick 'Add python.exe to PATH'),"
    Write-Bad "then run this installer again."
    exit 1
}
Write-Ok "python: $pythonw"

# ------------------------------------------------- 2. icon (build if needed)
$icon = Join-Path $Root "assets\app.ico"
if (-not (Test-Path $icon)) {
    Write-Info "creating the app icon..."
    & python (Join-Path $Root "tools\make_icon.py") | Out-Null
}
if (Test-Path $icon) { Write-Ok "icon: assets\app.ico" }
else { Write-Warn "icon missing - a default icon will be used" }

# ------------------------------------------------- 3. the launch command
$arguments = "-m court_ocr.app"
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$menuFolder = Join-Path $programs $AppName

function New-AppShortcut {
    # NOTE: the parameter must NOT be called $Args - that name is PowerShell's
    # automatic "unbound arguments" variable, which silently overwrites your
    # value and would leave every shortcut created with an empty Arguments box.
    param(
        [string]$Path,
        [string]$Target,
        [string]$ShortcutArgs,
        [string]$WorkDir,
        [string]$Icon,
        [string]$Description
    )
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    if ($ShortcutArgs) { $link.Arguments = $ShortcutArgs }
    $link.WorkingDirectory = $WorkDir
    if ($Icon -and (Test-Path $Icon)) { $link.IconLocation = "$Icon,0" }
    $link.Description = $Description
    $link.Save()
}

# ------------------------------------------------- 4. create the shortcuts
New-Item -ItemType Directory -Force -Path $menuFolder | Out-Null

$desktopLink = Join-Path $desktop "$AppName.lnk"
New-AppShortcut -Path $desktopLink -Target $pythonw -ShortcutArgs $arguments -WorkDir $Root `
    -Icon $icon -Description "$AppName - local, offline court filing preparation"

$menuLink = Join-Path $menuFolder "$AppName.lnk"
New-AppShortcut -Path $menuLink -Target $pythonw -ShortcutArgs $arguments -WorkDir $Root `
    -Icon $icon -Description "$AppName"

# a folder icon for dropping the case documents
$documentsFolder = Join-Path $Root "input"
New-Item -ItemType Directory -Force -Path $documentsFolder | Out-Null
$folderLink = Join-Path $desktop "Court Filing - Documents.lnk"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($folderLink)
$link.TargetPath = $documentsFolder
$link.Description = "Drop the case documents in here, then open $AppName"
$link.Save()

# uninstall entry in the same Start Menu folder
$uninstallLink = Join-Path $menuFolder "Uninstall (remove icons).lnk"
New-AppShortcut -Path $uninstallLink -Target "powershell.exe" `
    -ShortcutArgs "-NoProfile -ExecutionPolicy Bypass -File `"$Root\UNINSTALL.ps1`"" `
    -WorkDir $Root -Icon $icon -Description "Remove the $AppName shortcuts"

Write-Ok "desktop icon   : $desktopLink"
Write-Ok "start menu     : $menuLink"
Write-Ok "documents icon : $folderLink"

# ------------------------------------------------------------- 5. summary
Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host " INSTALLED" -ForegroundColor Green
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " Look on your Desktop for:"
Write-Host "    [icon]  $AppName                  <- double click to open the app"
Write-Host "    [icon]  Court Filing - Documents    <- drop the case documents here"
Write-Host ""
Write-Host " It is also in the Start Menu under '$AppName'."
Write-Host " To remove the icons later: Start Menu > $AppName > Uninstall."
Write-Host ""
Write-Host " Nothing was installed into Windows - the program stays in:"
Write-Host "    $Root"
Write-Host ""

if (-not $NoLaunch) {
    $answer = Read-Host "Open the app now? (Y/n)"
    if ($answer -eq "" -or $answer -match '^[Yy]') {
        Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $Root
    }
}
