[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('normal', 'debug')]
    [string]$Mode
)

$ErrorActionPreference = 'Stop'

try {
    $projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
    $configPath = Join-Path $projectRoot 'py.ini'
    if (-not [IO.File]::Exists($configPath)) {
        throw "py.ini not found: $configPath"
    }

    $settings = @{}
    foreach ($rawLine in [IO.File]::ReadAllLines($configPath, [Text.Encoding]::UTF8)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#') -or $line.StartsWith(';') -or $line.StartsWith('[')) {
            continue
        }
        $separator = $line.IndexOf('=')
        if ($separator -lt 1) {
            continue
        }
        $key = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        $settings[$key] = $value
    }

    $pythonKey = if ($Mode -eq 'normal') { 'pythonw_executable' } else { 'python_executable' }
    if (-not $settings.ContainsKey($pythonKey)) {
        throw "$pythonKey not found in py.ini"
    }

    $pythonPath = [Environment]::ExpandEnvironmentVariables([string]$settings[$pythonKey]).Trim().Trim([char]34)
    if (-not [IO.Path]::IsPathRooted($pythonPath)) {
        $pythonPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $pythonPath))
    }
    if (-not [IO.File]::Exists($pythonPath)) {
        throw "Python executable not found: $pythonPath"
    }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pythonPath
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.Arguments = '"lib\core\qt_desktop_pet.py"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = ($Mode -eq 'normal')
    $startInfo.EnvironmentVariables['PYTHONPATH'] = '.;config;lib'

    $process = [Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw 'Python process could not be started'
    }
    if ($Mode -eq 'debug') {
        $process.WaitForExit()
        exit $process.ExitCode
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
