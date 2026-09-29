<#
    INSTALL.ps1 - makes the automation behave like a normal installed program.

    It creates:
        * a Desktop icon          "KattyVision"
        * a Desktop folder icon   "KattyVision - Documents"  (the input folder)
        * a Start Menu entry      Start > KattyVision
        * an Uninstall entry      in the same Start Menu folder

    It also REPAIRS a machine that has been through an earlier version of this
    installer: the old "Court Filing Automation" and "PDF Helper" shortcuts are
    removed, because Windows names a taskbar button after the shortcut that
    launched it - so as long as those files existed, the app kept showing up as
    "Court Filing Automation" no matter what the code was called.

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
#  make_brand_icon.py, NOT make_icon.py: the latter draws the old generic
#  document-and-seal glyph, and assets/app.ico is the single file Windows takes
#  the taskbar, window and shortcut icon from.  Rebuilding it from the wrong
#  script is exactly why a fresh install showed the wrong picture in the taskbar
#  while the header showed the real brand mark.
$icon = Join-Path $Root "assets\app.ico"
$brandArt = Join-Path $Root "brand\kattyvision.png"
if ((-not (Test-Path $icon)) -and (Test-Path $brandArt)) {
    Write-Info "creating the app icon from the brand mark..."
    & python (Join-Path $Root "tools\make_brand_icon.py") | Out-Null
}
if ((Test-Path $icon) -and (Test-Path $brandArt)) {
    Write-Ok "icon: assets\app.ico (built from brand\kattyvision.png)"
} elseif (Test-Path $icon) {
    Write-Ok "icon: assets\app.ico"
} else {
    Write-Warn "icon missing - a default icon will be used"
}

# ------------------------------------------------- 3. the launch command
$arguments = "-m court_ocr.app"
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$menuFolder = Join-Path $programs $AppName

# ------------------------------------------------- 3b. taskbar identity
#  Windows decides what a taskbar button is called and which icon it shows by
#  looking the process's AppUserModelID up among the installed shortcuts.  With
#  no shortcut carrying that ID, the button is grouped under pythonw.exe and
#  takes its name from whichever shortcut happened to be clicked - which is how
#  this app spent months calling itself "Court Filing Automation" in the
#  taskbar.  court_ocr/app.py sets the matching ID (APP_ID) on the process.
$AppUserModelID = "KattyVision.Desktop.1"

function Set-ShortcutAppId {
    # Stamps PKEY_AppUserModel_ID onto a .lnk through IPropertyStore.  This is
    # the documented way to give a program a stable taskbar identity; the
    # WScript.Shell shortcut object simply has no property for it.
    param([string]$Path, [string]$AppId)
    if (-not (Test-Path $Path)) { return $false }
    try {
        if (-not ([System.Management.Automation.PSTypeName]'KattyVisionAppId').Type) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class KattyVisionAppId
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    private static extern int SHGetPropertyStoreFromParsingName(
        string pszPath, IntPtr pbc, int flags, ref Guid riid,
        out IPropertyStore ppv);

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    private interface IPropertyStore
    {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PROPERTYKEY pkey);
        void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        void Commit();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct PROPERTYKEY { public Guid fmtid; public uint pid; }

    [StructLayout(LayoutKind.Explicit)]
    private struct PROPVARIANT
    {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }

    public static void Set(string path, string appId)
    {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore store;
        // 2 = GPS_READWRITE
        int hr = SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out store);
        if (hr != 0) { Marshal.ThrowExceptionForHR(hr); }
        try
        {
            PROPERTYKEY key = new PROPERTYKEY();
            key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
            key.pid = 5;                       // PKEY_AppUserModel_ID
            PROPVARIANT pv = new PROPVARIANT();
            pv.vt = 31;                        // VT_LPWSTR
            pv.pointerValue = Marshal.StringToCoTaskMemUni(appId);
            try
            {
                store.SetValue(ref key, ref pv);
                store.Commit();
            }
            finally { Marshal.FreeCoTaskMem(pv.pointerValue); }
        }
        finally { Marshal.ReleaseComObject(store); }
    }
}
'@
        }
        [KattyVisionAppId]::Set($Path, $AppId)
        return $true
    } catch {
        # Never fatal: without the stamp the app still runs, it just groups the
        # way Windows decides rather than the way we asked.
        Write-Warn "could not brand the taskbar entry for $(Split-Path $Path -Leaf) - the app still works."
        return $false
    }
}


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
    # ... and give it the taskbar identity, so Windows can name the button
    # after THIS shortcut instead of guessing from pythonw.exe.
    if ($AppUserModelID) { Set-ShortcutAppId -Path $Path -AppId $AppUserModelID | Out-Null }
}

# ------------------------------------- 3c. remove the previous names' icons
#  These files are the actual reason the app still called itself "Court Filing
#  Automation": an old install left them behind, they launch the very same
#  command, and Windows names the taskbar button after the shortcut the user
#  clicked.  Deleting them is what makes the rename finally stick.
$legacyNames = @(
    "Court Filing Automation.lnk",
    "Court Filing - Documents.lnk",
    "PDF Helper.lnk",
    "PDF Helper - Documents.lnk"
)
$legacyFolders = @("Court Filing Automation", "PDF Helper")
$pinned = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
$legacyRemoved = 0
foreach ($name in $legacyNames) {
    foreach ($place in @($desktop, $pinned, $menuFolder)) {
        $stale = Join-Path $place $name
        if (Test-Path $stale) {
            Remove-Item -Recurse -Force $stale -ErrorAction SilentlyContinue
            Write-Info "removed the old shortcut: $stale"
            $legacyRemoved++
        }
    }
}
foreach ($folder in $legacyFolders) {
    foreach ($place in @($programs, $desktop)) {
        $stale = Join-Path $place $folder
        if (Test-Path $stale) {
            Remove-Item -Recurse -Force $stale -ErrorAction SilentlyContinue
            Write-Info "removed the old menu entry: $stale"
            $legacyRemoved++
        }
    }
}
if ($legacyRemoved -gt 0) {
    Write-Ok "tidied up $legacyRemoved leftover shortcut(s) from earlier versions"
} else {
    Write-Ok "no leftover shortcuts from earlier versions"
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
$folderLink = Join-Path $desktop "$AppName - Documents.lnk"
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
Write-Host "    [icon]  $AppName - Documents      <- drop the case documents here"
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
