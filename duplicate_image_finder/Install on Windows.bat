@echo off
rem Double-click this file to install Duplicate Image Finder.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

%PY% installer.py %*
goto end

:nopython
echo Python 3.9 or newer is needed.
echo Opening python.org: install Python (tick "Add python.exe to PATH"),
echo then double-click this file again.
start "" "https://www.python.org/downloads/windows/"

:end
echo.
pause
