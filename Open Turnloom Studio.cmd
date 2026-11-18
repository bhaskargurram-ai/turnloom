@echo off
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "%PY%" goto :check
set "BOOT=py -3"
where py >nul 2>nul
if errorlevel 1 set "BOOT=python"
%BOOT% -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto :nopython
%BOOT% -m venv .venv
if errorlevel 1 goto :failed
:check
rem Reinstall when the checkout is newer than what the environment holds.
"%PY%" -I -c "import sys, turnloom; from importlib.metadata import version; sys.exit(version('turnloom') != turnloom.__version__)" >nul 2>nul
if not errorlevel 1 goto :launch
echo Setting up Turnloom Studio. This takes a minute the first time.
"%PY%" -m pip install --disable-pip-version-check -e ".[studio]"
if errorlevel 1 goto :failed
:launch
start "" ".venv\Scripts\pythonw.exe" -I -m turnloom.studio %*
exit /b 0
:nopython
echo Turnloom needs Python 3.11 or newer. Install it from https://www.python.org/downloads/ and run this file again.
pause
exit /b 1
:failed
echo Studio setup failed. Check the Python or installation error above.
pause
exit /b 1
