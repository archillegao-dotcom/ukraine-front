@echo off
REM Ukraine front OSINT tracker - one-click launcher for Windows (double-click).
REM This file is deliberately ASCII-only so it works under any cmd code page.
REM First run creates .venv and installs dependencies (needs internet, 1-3 min).
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not defined UF_PORT set "UF_PORT=8765"

if exist ".venv\Scripts\python.exe" goto :have_venv

set "PY="
REM 1) Python launcher "py -3" (installed by python.org installer even when python is not on PATH)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :found
REM 2) python on PATH
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :found
REM 3) python3 on PATH
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python3"
if defined PY goto :found

echo [!] Python 3.10 or newer was not found.
echo     Please install it from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" in the installer, then run start.bat again.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:found
echo ^>^> Creating virtual environment .venv using %PY%
%PY% -m venv .venv
if errorlevel 1 goto :venv_fail

:have_venv
fc /b requirements.txt ".venv\req_stamp.txt" >nul 2>nul
if not errorlevel 1 goto :run
echo ^>^> Installing dependencies (first run takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_fail
copy /y requirements.txt ".venv\req_stamp.txt" >nul

:run
echo ^>^> Starting http://localhost:%UF_PORT%/  (close this window or press Ctrl+C to stop)
".venv\Scripts\python.exe" -m ukraine_front.app --port %UF_PORT% %*
pause
exit /b 0

:venv_fail
echo [!] Could not create the virtual environment.
pause
exit /b 1

:pip_fail
echo [!] Dependency installation failed. Check your internet connection and try again.
pause
exit /b 1
