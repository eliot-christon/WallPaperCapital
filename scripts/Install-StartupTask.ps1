<#
.SYNOPSIS
    Registers (or removes) the scheduled task that changes the lock screen at logon.

.DESCRIPTION
    Creates a task running as the current user, triggered at every logon with a
    30-second delay. No elevation is required: a standard user may register a task
    that runs under their own account at RunLevel Limited.

    The action uses pythonw.exe from .venv so no console window appears at logon.

.PARAMETER Unregister
    Remove the task instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1 -Unregister
#>
param(
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'

$TaskName = 'WallPaperCapital-LockScreen'
$repoRoot = Split-Path -Parent $PSScriptRoot

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Task '$TaskName' removed."
    }
    else {
        Write-Host "Task '$TaskName' is not registered, nothing to do."
    }
    exit 0
}

$pythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "pythonw.exe not found: $pythonw. Run 'uv sync' first."
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# `-m wallpaper_capital` rather than the console script: pythonw.exe keeps the task
# silent, where the generated wpcapital.exe would flash a console at every logon.
$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument '-m wallpaper_capital lockscreen' `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
# Let OneDrive and the network settle before touching the lock screen.
$trigger.Delay = 'PT30S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description 'Picks a random wallpaper tagged capital/country and applies it as the lock screen.' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task '$TaskName' registered for $currentUser."
Write-Host "Run it now with: Start-ScheduledTask -TaskName $TaskName"
