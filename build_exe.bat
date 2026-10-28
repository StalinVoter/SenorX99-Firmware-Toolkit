@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "BUILD_PY=%~dp0.build-venv\Scripts\python.exe"
if not exist "%BUILD_PY%" (
  echo Run RUN-SETUP-AND-BUILD.cmd to create the pinned build environment.
  exit /b 1
)
for %%F in (SenorX99-v0.91-Windows-x64.zip SenorX99-v0.91-Windows-x64.zip.sha256) do if exist "%%F" del /q "%%F"
for %%F in (SenorX99.exe SenorX99-CLI.exe) do if exist "dist\%%F" del /q "dist\%%F"
if exist "build" rmdir /s /q "build"
mkdir "build"
if errorlevel 1 exit /b 1
rem Keep test temporary files inside this fresh build, away from shared TEMP ACLs.
"%BUILD_PY%" -m pytest -q --tb=short --maxfail=1 --basetemp "%~dp0build\pytest-temp" --junitxml "build\source-tests.xml"
if errorlevel 1 exit /b 1
"%BUILD_PY%" validate_build_inputs.py
if errorlevel 1 exit /b 1
"%BUILD_PY%" -m PyInstaller --noconfirm --clean --workpath "build\pyinstaller" SenorX99.spec
if errorlevel 1 exit /b 1
"%BUILD_PY%" scripts\verify_exe.py "dist\SenorX99.exe" "build\verified-gui.json"
if errorlevel 1 exit /b 1
"%BUILD_PY%" scripts\verify_exe.py "dist\SenorX99-CLI.exe" "build\verified-cli.json"
if errorlevel 1 exit /b 1
"%BUILD_PY%" scripts\stage_runtime.py
if errorlevel 1 exit /b 1
"%BUILD_PY%" scripts\copy_licenses.py
if errorlevel 1 exit /b 1
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0TEST-PACKAGED-RUNTIME.ps1" -Distribution "%~dp0dist" -Reports "%~dp0build"
if errorlevel 1 exit /b 1
"%BUILD_PY%" scripts\package_release.py
if errorlevel 1 exit /b 1
echo Ready: SenorX99-v0.91-Windows-x64.zip
exit /b 0
