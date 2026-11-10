@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -I -c "from importlib.metadata import version; import turnloom; assert turnloom.__version__=='0.5.0' and version('rollback-mcp')=='0.2.0' and version('verifydoc')=='0.10.0' and version('ripgrep')=='14.1.0'" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -e ".[studio]"
  if errorlevel 1 goto :failed
)
start "" ".venv\Scripts\pythonw.exe" -I -m turnloom.studio
exit /b 0
:failed
echo Studio setup failed. Check the Python or installation error above.
pause
exit /b 1
