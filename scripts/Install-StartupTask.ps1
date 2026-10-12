<#
.SYNOPSIS
    Registers (or removes) the scheduled task that changes the lock screen at logon
    and every few hours after that.

.DESCRIPTION
    Creates a task running as the current user, triggered at every logon with a
    30-second delay, and again every -IntervalHours (12 by default) while the session
    is open. No elevation is required: a standard user may register a task
    that runs under their own account at RunLevel Limited.

    The action uses pythonw.exe from .venv so no console window appears at logon.

.PARAMETER IntervalHours
    Hours between two changes during a session. 0 keeps the logon trigger only.

.PARAMETER Unregister
    Remove the task instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1 -Unregister
#>
param(
    [ValidateRange(0, 744)]
    [int]$IntervalHours = 12,
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
$triggers = @($trigger)

if ($IntervalHours -gt 0) {
    # No -RepetitionDuration: the repetition then runs indefinitely. The first run is
    # one interval from now, since logon already covers the start of the session.
    $triggers += New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddHours($IntervalHours) `
        -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)
}

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
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task '$TaskName' registered for $currentUser."
if ($IntervalHours -gt 0) {
    Write-Host "It runs at logon and every $IntervalHours h."
}
Write-Host "Run it now with: Start-ScheduledTask -TaskName $TaskName"
