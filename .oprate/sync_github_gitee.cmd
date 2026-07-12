@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_github_gitee.ps1" %*
exit /b %errorlevel%
