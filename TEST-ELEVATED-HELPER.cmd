@echo off
setlocal
cd /d "%~dp0"
echo This test requests administrator approval for the BIOS helper.
echo It exchanges a diagnostic message only. It does not access the BIOS.
echo Start this script normally to test the normal-user to administrator handoff.
set "TEST_APP=%~dp0SenorX99.exe"
if not exist "%TEST_APP%" set "TEST_APP=%~dp0dist\SenorX99.exe"
if not exist "%TEST_APP%" (
  echo Build the application first, or put this script beside both release EXEs.
  pause
  exit /b 1
)
"%TEST_APP%" --bios-helper-self-test --runtime-report "%~dp0LOGS\elevated-helper-test.json"
set "TEST_EXIT=%ERRORLEVEL%"
if exist "%~dp0LOGS\elevated-helper-test.json" type "%~dp0LOGS\elevated-helper-test.json"
echo.
echo Helper test exit code: %TEST_EXIT%
echo Report: %~dp0LOGS\elevated-helper-test.json
pause
exit /b %TEST_EXIT%
