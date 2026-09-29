<#
    UNINSTALL.ps1 - removes the Desktop and Start Menu icons.

    Your documents, settings and output files are NOT touched.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "Removing the KattyVision shortcuts (old 'PDF Helper' / 'Court Filing Automation' ones too)..." -ForegroundColor Cyan

# Close a running instance first - a live pythonw.exe can hold handles to the
# shortcuts/logs and make removal appear to "not work".
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'court_ocr' }
if ($running) {
    foreach ($proc in $running) {
        Write-Host "  closing running app (pid $($proc.ProcessId))..."
        try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep -Milliseconds 500
}

$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "KattyVision.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "KattyVision - Documents.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "PDF Helper.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "PDF Helper - Documents.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "KattyVision.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "KattyVision"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "PDF Helper.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "PDF Helper"),
    # previous name, in case the old install is still around
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "Court Filing Automation.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "Court Filing - Documents.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "Court Filing Automation.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Programs')) "Court Filing Automation"),
    # a shortcut the user pinned to the taskbar lives in its own folder and is
    # NOT removed by deleting the Desktop / Start Menu copies
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\KattyVision.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\PDF Helper.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Court Filing Automation.lnk")
)
$startMenuFolder = Join-Path ([Environment]::GetFolderPath('Programs')) "KattyVision"
if (Test-Path $startMenuFolder) { $targets += $startMenuFolder }
$oldStartMenuFolder = Join-Path ([Environment]::GetFolderPath('Programs')) "Court Filing Automation"
if (Test-Path $oldStartMenuFolder) { $targets += $oldStartMenuFolder }

$removed = 0
foreach ($target in $targets) {
    if (Test-Path $target) {
        Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
        Write-Host "  removed: $target"
        $removed++
    }
}

Write-Host ""
Write-Host "Done. $removed item(s) removed." -ForegroundColor Green
Write-Host "Your case documents, settings and results are untouched in:"
Write-Host "  $Root"
Write-Host ""
