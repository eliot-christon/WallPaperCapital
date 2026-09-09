<#
.SYNOPSIS
    Enregistre (ou supprime) la tache planifiee qui change l'ecran de verrouillage au logon.

.DESCRIPTION
    Cree une tache s'executant sous l'identite de l'utilisateur courant, declenchee a
    chaque ouverture de session avec 30 secondes de delai. Aucune elevation n'est
    requise : un utilisateur standard peut enregistrer une tache qui tourne sous son
    propre compte en RunLevel Limited.

    L'action utilise pythonw.exe du .venv afin qu'aucune console n'apparaisse au logon.

.PARAMETER Unregister
    Supprime la tache au lieu de la creer.

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
        Write-Host "Tache '$TaskName' supprimee."
    }
    else {
        Write-Host "Tache '$TaskName' absente, rien a faire."
    }
    exit 0
}

$pythonw = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "pythonw.exe introuvable : $pythonw. Lancer 'uv sync' d'abord."
}

$script = Join-Path $repoRoot 'lockscreen.py'
if (-not (Test-Path -LiteralPath $script)) {
    throw "lockscreen.py introuvable : $script"
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute $pythonw -Argument 'lockscreen.py' -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
# Laisse OneDrive et le reseau se stabiliser avant de toucher a l'ecran de verrouillage.
$trigger.Delay = 'PT30S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description 'Choisit un fond aleatoire tague capitale/pays et l applique comme ecran de verrouillage.' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Tache '$TaskName' enregistree pour $currentUser."
Write-Host "Test immediat : Start-ScheduledTask -TaskName $TaskName"
