<#
.SYNOPSIS
    Applique une image comme ecran de verrouillage Windows, ou interroge l'image courante.

.DESCRIPTION
    Pont vers l'API WinRT Windows.System.UserProfile.LockScreen. Agit sur l'utilisateur
    courant et ne demande aucune elevation.

    IMPORTANT : ce script doit tourner sous Windows PowerShell 5.1 (powershell.exe) et non
    sous PowerShell 7 (pwsh.exe), car la projection WinRT s'appuie sur l'assembly
    System.Runtime.WindowsRuntime, absente de .NET moderne.

.PARAMETER ImagePath
    Chemin complet du fichier image a appliquer.

.PARAMETER Query
    Ecrit sur stdout le chemin de l'image d'ecran de verrouillage actuelle, puis sort.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Set-LockScreen.ps1 -Query

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Set-LockScreen.ps1 -ImagePath C:\tmp\a.jpg
#>
[CmdletBinding(DefaultParameterSetName = 'Set')]
param(
    [Parameter(ParameterSetName = 'Set', Mandatory = $true)]
    [string]$ImagePath,

    [Parameter(ParameterSetName = 'Query', Mandatory = $true)]
    [switch]$Query
)

$ErrorActionPreference = 'Stop'

function Get-AsTaskMethod {
    <#
        Recupere par reflexion la bonne surcharge de WindowsRuntimeSystemExtensions::AsTask.
        Il en existe plusieurs ; on distingue IAsyncOperation<T> (renvoie un resultat) de
        IAsyncAction (ne renvoie rien) sur le nom du type du parametre unique.
    #>
    param([Parameter(Mandatory = $true)][string]$ParameterTypeName)

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq $ParameterTypeName
    } | Select-Object -First 1

    if ($null -eq $method) {
        throw "Surcharge AsTask introuvable pour $ParameterTypeName"
    }
    return $method
}

try {
    # Le chargement d'un type WinRT se fait via la syntaxe [Namespace.Type,Assembly,ContentType=WindowsRuntime].
    [Windows.System.UserProfile.LockScreen, Windows.System.UserProfile, ContentType = WindowsRuntime] | Out-Null

    if ($Query) {
        $current = [Windows.System.UserProfile.LockScreen]::OriginalImageFile
        if ($null -ne $current) {
            # OriginalImageFile est un Uri (file:///C:/...) : on rend un chemin exploitable.
            Write-Output $current.LocalPath
        }
        exit 0
    }

    if (-not (Test-Path -LiteralPath $ImagePath -PathType Leaf)) {
        throw "Fichier introuvable : $ImagePath"
    }
    # WinRT exige un chemin absolu et normalise.
    $fullPath = (Resolve-Path -LiteralPath $ImagePath).ProviderPath

    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    Add-Type -AssemblyName System.Runtime.WindowsRuntime

    $asTaskOperation = (Get-AsTaskMethod -ParameterTypeName 'IAsyncOperation`1').MakeGenericMethod(
        [Windows.Storage.StorageFile]
    )
    $storageFile = $asTaskOperation.Invoke(
        $null,
        @([Windows.Storage.StorageFile]::GetFileFromPathAsync($fullPath))
    ).GetAwaiter().GetResult()

    # L'attente est indispensable : sans elle le script se termine avant que Windows
    # n'ait recopie le fichier dans SystemData, et l'ecran de verrouillage ne change pas.
    $asTaskAction = Get-AsTaskMethod -ParameterTypeName 'IAsyncAction'
    $asTaskAction.Invoke(
        $null,
        @([Windows.System.UserProfile.LockScreen]::SetImageFileAsync($storageFile))
    ).GetAwaiter().GetResult() | Out-Null

    Write-Output 'OK'
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
