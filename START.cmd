@echo off
setlocal
cd /d "%~dp0"

rem Prefer the Python launcher (py.exe); fall back to python.exe.
rem The Microsoft Store "python" alias only prints a message, so test that it really runs Python 3.
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo  Python 3 was not found on this PC.
  echo  Install it from https://www.python.org/downloads/windows/
  echo  During setup tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" selected.
  echo  Then run START.cmd again.
  echo.
  pause
  exit /b 1
)

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python is installed but tkinter is missing.
  echo  Re-run the Python installer, choose Modify, and enable "tcl/tk and IDLE".
  echo.
  pause
  exit /b 1
)

%PY% gui.py
if errorlevel 1 pause
