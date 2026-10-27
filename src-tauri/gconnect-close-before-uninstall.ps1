param(
    [Parameter(Mandatory = $true)]
    [string] $InstallDir,
    [Parameter(Mandatory = $true)]
    [string] $ProcessName
)

$ErrorActionPreference = 'SilentlyContinue'
$installPath = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')

function Stop-InstalledProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,
        [Parameter(Mandatory = $true)]
        [bool] $TryGracefulClose
    )

    $expectedPath = [IO.Path]::GetFullPath((Join-Path $installPath "$Name.exe"))
    $matchingProcesses = @(Get-Process -Name $Name | Where-Object {
        try {
            [string]::Equals([IO.Path]::GetFullPath($_.Path), $expectedPath, [StringComparison]::OrdinalIgnoreCase)
        } catch {
            $false
        }
    })

    foreach ($process in $matchingProcesses) {
        if ($TryGracefulClose -and $process.MainWindowHandle -ne 0) {
            $null = $process.CloseMainWindow()
            if ($process.WaitForExit(10000)) {
                continue
            }
        }

        Stop-Process -Id $process.Id -Force
        $process.WaitForExit(3000)
    }
}

# Only close a process whose executable path is inside this exact installation.
Stop-InstalledProcess -Name $ProcessName -TryGracefulClose $true

# The backend watches the desktop process PID and exits after a crash. Give it
# time to observe the normal app shutdown before removing its executable.
Start-Sleep -Seconds 3
Stop-InstalledProcess -Name 'gconnect-backend' -TryGracefulClose $false
