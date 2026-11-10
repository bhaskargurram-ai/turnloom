@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)
if not exist ".venv\Scripts\turnloom-studio.exe" (
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 goto :failed
)
start "" ".venv\Scripts\turnloom-studio.exe"
exit /b 0
:failed
echo Studio setup failed. Check the Python or installation error above.
pause
exit /b 1
