@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Grok Register Setup

set "PY="
where py >nul 2>nul
if not errorlevel 1 for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%I"
if defined PY goto :python_ok
where python >nul 2>nul
if errorlevel 1 goto :no_python
for /f "delims=" %%I in ('where python') do if not defined PY set "PY=%%I"

:python_ok
if not defined PY goto :no_python
if not exist ".venv\Scripts\python.exe" (
  echo [..] Creating .venv ...
  "%PY%" -m venv ".venv"
  if errorlevel 1 goto :failed
)

set "VPY=%CD%\.venv\Scripts\python.exe"
set "REQ=%CD%\requirements.txt"
if exist "%CD%\requirements.lock.txt" set "REQ=%CD%\requirements.lock.txt"
if /I "%~1"=="dev" set "REQ=%CD%\requirements-dev.txt"
if /I "%~1"=="--dev" set "REQ=%CD%\requirements-dev.txt"

echo [..] Installing dependencies from %REQ% ...
"%VPY%" -m pip install -U pip
if errorlevel 1 goto :failed
"%VPY%" -m pip install -r "%REQ%"
if errorlevel 1 (
  echo [..] Retrying with Tsinghua mirror ...
  "%VPY%" -m pip install -r "%REQ%" -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 goto :failed

if /I "%~1"=="dev" echo [OK] Development dependencies installed. Run pytest with .venv\Scripts\python.exe -m pytest -q
if /I "%~1"=="--dev" echo [OK] Development dependencies installed. Run pytest with .venv\Scripts\python.exe -m pytest -q
if /I not "%~1"=="dev" if /I not "%~1"=="--dev" echo [OK] Dependencies installed. Run start.bat to launch the panel.
pause
exit /b 0

:no_python
echo [ERROR] Python 3.10+ not found.
pause
exit /b 1

:failed
echo [ERROR] Setup failed.
pause
exit /b 1
