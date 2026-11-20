@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3 -m venv .venv
    )
    if errorlevel 1 goto failed
)
if not exist ".venv\checker-tray-dependencies-ready" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
    type nul > ".venv\checker-tray-dependencies-ready"
)
if /i "%~1"=="--check" (
    ".venv\Scripts\python.exe" -B run_local.py --check
    if errorlevel 1 goto failed
    goto done
)
start "" ".venv\Scripts\pythonw.exe" -B "%~dp0checker_tray.py"
if not errorlevel 1 goto done
:failed
echo The checker could not start. Read the message above.
if /i "%~1"=="--check" exit /b 1
pause
:done
popd
