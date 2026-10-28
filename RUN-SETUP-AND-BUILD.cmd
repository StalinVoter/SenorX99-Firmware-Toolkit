@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL-BUILD-PREREQUISITES.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo Setup/build failed with exit code %RC%.
  echo Review setup-build-SenorX99.log in this folder.
  pause
)
exit /b %RC%
