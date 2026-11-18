@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv missing. Run setup-dev.bat first.
  exit /b 1
)
".venv\Scripts\python.exe" -m pytest -q
exit /b %ERRORLEVEL%
