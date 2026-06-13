@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo  Flying Snow Velvet LTS - Install and Launch
echo ========================================
echo.

set "CANDIDATE_FILE=%TEMP%\fsv_python_candidates_%RANDOM%_%RANDOM%.txt"
set "PYTHON_FOUND="
set "INSTALL_OK="

REM =============================================
REM  Goal:
REM  1) scan as many real Python executables as possible
REM  2) prefer Python 3.11
REM  3) if one candidate cannot complete startup, try the next one
REM =============================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$seen=@{};" ^
  "$candidates=New-Object System.Collections.Generic.List[object];" ^
  "function Get-PythonInfo([string]$exe){" ^
  "  try{" ^
  "    $psi=New-Object System.Diagnostics.ProcessStartInfo;" ^
  "    $psi.FileName=$exe;" ^
  "    $psi.Arguments='-c ""import sys; print(''.''.join(map(str, sys.version_info[:3]))); print(sys.executable)""';" ^
  "    $psi.UseShellExecute=$false;" ^
  "    $psi.RedirectStandardOutput=$true;" ^
  "    $psi.RedirectStandardError=$true;" ^
  "    $proc=[System.Diagnostics.Process]::Start($psi);" ^
  "    if(-not $proc){return $null}" ^
  "    $stdout=$proc.StandardOutput.ReadToEnd();" ^
  "    $stderr=$proc.StandardError.ReadToEnd();" ^
  "    $proc.WaitForExit();" ^
  "    if($proc.ExitCode -ne 0){return $null}" ^
  "    $lines=@($stdout -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) });" ^
  "    if($lines.Count -lt 2){return $null}" ^
  "    return [pscustomobject]@{Version=$lines[0].Trim();Executable=$lines[1].Trim()};" ^
  "  }catch{return $null}" ^
  "}" ^
  "function Add-Candidate([string]$path){" ^
  "  if([string]::IsNullOrWhiteSpace($path)){return}" ^
  "  try{$resolved=[System.IO.Path]::GetFullPath($path)}catch{return}" ^
  "  if(-not (Test-Path -LiteralPath $resolved -PathType Leaf)){return}" ^
  "  if($resolved -match 'WindowsApps'){return}" ^
  "  $key=$resolved.ToLowerInvariant();" ^
  "  if($seen.ContainsKey($key)){return}" ^
  "  $info=Get-PythonInfo $resolved;" ^
  "  if(-not $info){return}" ^
  "  if($info.Executable -match 'WindowsApps'){return}" ^
  "  $versionText=$info.Version;" ^
  "  if(-not $versionText){return}" ^
  "  $parts=$versionText.Split('.');" ^
  "  if($parts.Count -lt 2){return}" ^
  "  $major=[int]$parts[0];" ^
  "  $minor=[int]$parts[1];" ^
  "  $patch=if($parts.Count -ge 3){[int]$parts[2]}else{0};" ^
  "  if($major -lt 3 -or ($major -eq 3 -and $minor -lt 7)){return}" ^
  "  $seen[$key]=$true;" ^
  "  $candidates.Add([pscustomobject]@{Path=$resolved;Version=$versionText;Major=$major;Minor=$minor;Patch=$patch}) | Out-Null;" ^
  "}" ^
  "try{$launcher=& py -0p; if($LASTEXITCODE -eq 0){foreach($line in $launcher){if($line -match '([A-Za-z]:\\.*python(?:w)?\.exe)$'){Add-Candidate $matches[1]}}}}catch{}" ^
  "foreach($name in @('python','python3')){foreach($cmd in @(Get-Command $name -All -ErrorAction SilentlyContinue)){if($cmd -and $cmd.Source){Add-Candidate $cmd.Source}}}" ^
  "$regRoots=@(" ^
  "  'HKLM:\SOFTWARE\Python\PythonCore'," ^
  "  'HKCU:\SOFTWARE\Python\PythonCore'," ^
  "  'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore'" ^
  ");" ^
  "foreach($root in $regRoots){" ^
  "  if(-not (Test-Path $root)){continue}" ^
  "  foreach($item in Get-ChildItem $root){" ^
  "    $installPath=Join-Path $item.PSPath 'InstallPath';" ^
  "    $props=Get-ItemProperty -LiteralPath $installPath -ErrorAction SilentlyContinue;" ^
  "    if($props){" ^
  "      Add-Candidate $props.ExecutablePath;" ^
  "      $defaultBase=$props.'(default)';" ^
  "      if($defaultBase){Add-Candidate (Join-Path $defaultBase 'python.exe')}" ^
  "    }" ^
  "  }" ^
  "}" ^
  "$appPathRoots=@(" ^
  "  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python.exe'," ^
  "  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python.exe'," ^
  "  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.exe'," ^
  "  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\python3.exe'" ^
  ");" ^
  "foreach($appKey in $appPathRoots){$item=Get-ItemProperty -LiteralPath $appKey -ErrorAction SilentlyContinue; if($item){Add-Candidate $item.'(default)'; Add-Candidate $item.Path}}" ^
  "$patterns=@(" ^
  "  (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\python.exe')," ^
  "  (Join-Path $env:USERPROFILE 'scoop\apps\python*\current\python.exe')," ^
  "  (Join-Path $env:ProgramData 'chocolatey\lib\python*\tools\python.exe')," ^
  "  (Join-Path $env:USERPROFILE 'miniconda3\python.exe')," ^
  "  (Join-Path $env:USERPROFILE 'anaconda3\python.exe')," ^
  "  (Join-Path $env:USERPROFILE 'miniconda3\envs\*\python.exe')," ^
  "  (Join-Path $env:USERPROFILE 'anaconda3\envs\*\python.exe')," ^
  "  'C:\Python3*\python.exe'," ^
  "  'C:\Program Files\Python3*\python.exe'," ^
  "  'C:\Program Files (x86)\Python3*\python.exe'" ^
  ");" ^
  "foreach($pattern in $patterns){foreach($match in Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue){Add-Candidate $match.FullName}}" ^
  "$sorted=$candidates | Sort-Object @{Expression={if($_.Major -eq 3 -and $_.Minor -eq 11){0}else{1}}}, @{Expression={if($_.Major -eq 3){[math]::Abs($_.Minor-11)}else{99}}}, @{Expression={if($_.Major -eq 3){0}else{1}}}, @{Expression={-$_.Major}}, @{Expression={-$_.Minor}}, @{Expression={-$_.Patch}}, Path;" ^
  "$sorted | ForEach-Object { '{0}|{1}' -f $_.Path, $_.Version }" > "%CANDIDATE_FILE%"

if not exist "%CANDIDATE_FILE%" goto :no_python

for %%Z in ("%CANDIDATE_FILE%") do if %%~zZ equ 0 goto :no_python

echo [INFO] Python candidates found:
for /f "usebackq tokens=1,2 delims=|" %%A in ("%CANDIDATE_FILE%") do (
    echo   [%%B] %%A
)
echo.

for /f "usebackq tokens=1,2 delims=|" %%A in ("%CANDIDATE_FILE%") do (
    set "PYTHON_FOUND=1"
    set "CURRENT_PY=%%~A"
    set "CURRENT_VER=%%~B"
    echo [INFO] Trying Python !CURRENT_VER!: !CURRENT_PY!
    echo.
    "%%~A" install_deps.py
    set "RC=!errorlevel!"
    if !RC! equ 0 (
        set "INSTALL_OK=1"
        goto :cleanup
    )
    echo.
    echo [WARN] Python !CURRENT_VER! failed with exit code !RC!, switching to next candidate...
    echo.
)

if defined PYTHON_FOUND goto :all_failed

:no_python
echo [ERROR] No usable Python environment found!
echo.
echo Please download and install Python 3.11 from:
echo   https://www.python.org/downloads/release/python-3119/
echo.
echo If you install another version, make sure it is Python 3.7+ and can run from command line.
echo.
goto :cleanup

:all_failed
echo [ERROR] All scanned Python candidates failed to start install_deps.py successfully.
echo.
echo Recommendation:
echo   Install Python 3.11 and retry.
echo   https://www.python.org/downloads/release/python-3119/
echo.

:cleanup
if exist "%CANDIDATE_FILE%" del /q "%CANDIDATE_FILE%" >nul 2>&1
pause
if defined INSTALL_OK (
    exit /b 0
)
exit /b 1
