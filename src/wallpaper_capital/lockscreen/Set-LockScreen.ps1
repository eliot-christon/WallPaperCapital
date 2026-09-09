<#
.SYNOPSIS
    Applies an image as the Windows lock screen, or reports the current one.

.DESCRIPTION
    Bridge to the WinRT API Windows.System.UserProfile.LockScreen. It acts on the
    current user and needs no elevation.

    IMPORTANT: this script must run under Windows PowerShell 5.1 (powershell.exe) and
    not under PowerShell 7 (pwsh.exe), because the WinRT projection relies on the
    System.Runtime.WindowsRuntime assembly, which modern .NET does not ship.

.PARAMETER ImagePath
    Full path of the image file to apply.

.PARAMETER Query
    Writes the path of the current lock screen image to stdout, then exits.

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
        Fetches, by reflection, the right overload of
        WindowsRuntimeSystemExtensions::AsTask. Several exist; IAsyncOperation<T>
        (returns a result) is told apart from IAsyncAction (returns nothing) by the
        type name of the single parameter.
    #>
    param([Parameter(Mandatory = $true)][string]$ParameterTypeName)

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq $ParameterTypeName
    } | Select-Object -First 1

    if ($null -eq $method) {
        throw "No AsTask overload found for $ParameterTypeName"
    }
    return $method
}

try {
    # A WinRT type is loaded through the [Namespace.Type,Assembly,ContentType=WindowsRuntime] syntax.
    [Windows.System.UserProfile.LockScreen, Windows.System.UserProfile, ContentType = WindowsRuntime] | Out-Null

    if ($Query) {
        $current = [Windows.System.UserProfile.LockScreen]::OriginalImageFile
        if ($null -ne $current) {
            # OriginalImageFile is a Uri (file:///C:/...); return a usable path.
            Write-Output $current.LocalPath
        }
        exit 0
    }

    if (-not (Test-Path -LiteralPath $ImagePath -PathType Leaf)) {
        throw "File not found: $ImagePath"
    }
    # WinRT requires an absolute, normalised path.
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

    # Waiting is essential: without it the script exits before Windows has copied
    # the file into SystemData, and the lock screen does not change.
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
