@echo off
REM ------------------------------------------------------------------
REM  DSA Review launcher (Windows). Double-click this file to start.
REM  First run: creates a private Python environment in .venv and
REM  installs the one dependency (fsrs). Later runs just start the app.
REM ------------------------------------------------------------------
setlocal
cd /d "%~dp0"
if not exist "app\server.py" (
    echo run.bat has to be started from the DSA Review folder, next to the app folder.
    echo If you opened it inside a .zip file: right-click the zip, choose Extract All,
    echo then double-click run.bat in the extracted folder.
    pause
    exit /b 1
)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Skip setup only if it finished: an interrupted install, or a Python update
REM that broke .venv, just runs the setup again.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import fsrs" >nul 2>nul && goto run
)

echo Setting up DSA Review for the first time...
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo Could not find Python 3.10 or newer.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
    echo then double-click run.bat again.
    echo.
    pause
    exit /b 1
)

%PY% -m venv --clear .venv
if errorlevel 1 (
    echo Failed to create the virtual environment. Delete the .venv folder and try again.
    pause
    exit /b 1
)
echo Installing fsrs - this needs internet and takes about a minute...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo Failed to install requirements. Check your internet connection and try again.
    rmdir /s /q .venv
    pause
    exit /b 1
)
echo Setup complete.
echo.

:run
".venv\Scripts\python.exe" app\server.py %*
if errorlevel 1 pause
endlocal
