@echo off
chcp 65001 >nul 2>&1
setlocal DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1

set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" set "POWERSHELL_EXE=powershell.exe"

"%POWERSHELL_EXE%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0lib\script\app\windows_launcher.ps1" -Mode normal
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    pause
)
exit /b %RC%
