# VLC Protocol Handler Setup for Windows
# This script registers the vlc:// protocol handler in Windows Registry
# The script will automatically request administrator privileges if needed

param(
    [string]$VLCPath = "",
    [switch]$Elevated
)

# Check for admin privileges and self-elevate if needed
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Requesting administrator privileges..." -ForegroundColor Yellow

    # Build arguments to pass to elevated process
    $scriptPath = $MyInvocation.MyCommand.Definition
    $arguments = "-ExecutionPolicy Bypass -File `"$scriptPath`" -Elevated"
    if ($VLCPath -ne "") {
        $arguments += " -VLCPath `"$VLCPath`""
    }

    try {
        Start-Process PowerShell -Verb RunAs -ArgumentList $arguments -Wait
    } catch {
        Write-Host "ERROR: Failed to elevate privileges." -ForegroundColor Red
        Write-Host "Please right-click on the script and select 'Run with PowerShell' as Administrator." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Press any key to exit..."
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
    exit
}

Write-Host "=== VLC Protocol Handler Setup ===" -ForegroundColor Cyan
Write-Host ""

# Find VLC installation
$vlcLocations = @(
    "C:\Program Files\VideoLAN\VLC\vlc.exe",
    "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    "$env:LOCALAPPDATA\Programs\VideoLAN\VLC\vlc.exe"
)

$vlcExe = ""

# Check if user provided a path
if ($VLCPath -ne "" -and (Test-Path $VLCPath)) {
    $vlcExe = $VLCPath
} else {
    # Search common locations
    foreach ($loc in $vlcLocations) {
        if (Test-Path $loc) {
            $vlcExe = $loc
            break
        }
    }
}

# If still not found, ask user
if ($vlcExe -eq "") {
    Write-Host "VLC was not found in the default locations." -ForegroundColor Yellow
    Write-Host "Please enter the full path to vlc.exe:"
    $vlcExe = Read-Host

    if (-not (Test-Path $vlcExe)) {
        Write-Host "ERROR: File not found: $vlcExe" -ForegroundColor Red
        Write-Host "Please install VLC from https://www.videolan.org/" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Press any key to exit..."
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
        exit 1
    }
}

Write-Host "Found VLC at: $vlcExe" -ForegroundColor Green
Write-Host ""

# Get VLC directory for the wrapper script
$vlcDir = Split-Path -Parent $vlcExe

try {
    Write-Host "Creating URL handler wrapper script..." -ForegroundColor Cyan

    # Create a wrapper PowerShell script that strips the vlc:// prefix and decodes the URL
    # This is needed because VLC doesn't understand the vlc:// protocol itself
    $wrapperPath = Join-Path $vlcDir "vlc-url-handler.ps1"
    $wrapperContent = @'
param([string]$inputUrl)
# Debug log
$logFile = "$env:TEMP\vlc-handler.log"
"$(Get-Date) - Input: $inputUrl" | Out-File $logFile -Append

# Strip vlc:// prefix
$cleanUrl = $inputUrl -replace '^vlc://', ''
"$(Get-Date) - After strip: $cleanUrl" | Out-File $logFile -Append

# URL decode (browsers encode special characters like :// to %3A%2F%2F)
Add-Type -AssemblyName System.Web
$cleanUrl = [System.Web.HttpUtility]::UrlDecode($cleanUrl)
"$(Get-Date) - After decode: $cleanUrl" | Out-File $logFile -Append

# Fix missing colon - Windows strips it from http:// and https://
$cleanUrl = $cleanUrl -replace '^http//', 'http://'
$cleanUrl = $cleanUrl -replace '^https//', 'https://'
"$(Get-Date) - After fix: $cleanUrl" | Out-File $logFile -Append

# Launch VLC with the clean URL
"$(Get-Date) - Launching VLC" | Out-File $logFile -Append
& '{VLC_PATH}' "$cleanUrl"
'@
    $wrapperContent = $wrapperContent.Replace("{VLC_PATH}", $vlcExe)
    Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding UTF8

    # Create a small batch launcher that calls the PowerShell script
    # (Registry needs to point to an exe or batch file)
    $launcherPath = Join-Path $vlcDir "vlc-url-handler.cmd"
    $launcherContent = @"
@echo off
powershell -ExecutionPolicy Bypass -NoProfile -File "$wrapperPath" %1
"@
    Set-Content -Path $launcherPath -Value $launcherContent -Encoding ASCII

    Write-Host "Created wrapper: $wrapperPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "Registering vlc:// protocol handler..." -ForegroundColor Cyan

    # Create the vlc protocol key
    $vlcKey = "HKCR:\vlc"

    # Check if HKCR: drive exists, if not create it
    if (-not (Test-Path "HKCR:")) {
        New-PSDrive -Name HKCR -PSProvider Registry -Root HKEY_CLASSES_ROOT | Out-Null
    }

    # Remove existing key if present
    if (Test-Path $vlcKey) {
        Remove-Item -Path $vlcKey -Recurse -Force
    }

    # Create vlc key
    New-Item -Path $vlcKey -Force | Out-Null
    Set-ItemProperty -Path $vlcKey -Name "(Default)" -Value "URL:VLC Protocol"
    Set-ItemProperty -Path $vlcKey -Name "URL Protocol" -Value ""

    # Create DefaultIcon key
    $iconKey = "$vlcKey\DefaultIcon"
    New-Item -Path $iconKey -Force | Out-Null
    Set-ItemProperty -Path $iconKey -Name "(Default)" -Value "`"$vlcExe`",0"

    # Create shell\open\command key - point to wrapper script
    $shellKey = "$vlcKey\shell"
    New-Item -Path $shellKey -Force | Out-Null

    $openKey = "$shellKey\open"
    New-Item -Path $openKey -Force | Out-Null

    $commandKey = "$openKey\command"
    New-Item -Path $commandKey -Force | Out-Null
    Set-ItemProperty -Path $commandKey -Name "(Default)" -Value "`"$launcherPath`" `"%1`""

    Write-Host ""
    Write-Host "SUCCESS! VLC protocol handler registered." -ForegroundColor Green
    Write-Host ""
    Write-Host "Files created:" -ForegroundColor Cyan
    Write-Host "  $wrapperPath" -ForegroundColor White
    Write-Host "  $launcherPath" -ForegroundColor White
    Write-Host ""
    Write-Host "Registry entries created:" -ForegroundColor Cyan
    Write-Host "  HKEY_CLASSES_ROOT\vlc" -ForegroundColor White
    Write-Host "  HKEY_CLASSES_ROOT\vlc\DefaultIcon" -ForegroundColor White
    Write-Host "  HKEY_CLASSES_ROOT\vlc\shell\open\command" -ForegroundColor White
    Write-Host ""
    Write-Host "You can now use vlc:// links in your browser." -ForegroundColor Green
    Write-Host "Your browser may ask for permission the first time." -ForegroundColor Yellow

} catch {
    Write-Host "ERROR: Failed to register protocol handler." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Press any key to exit..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
