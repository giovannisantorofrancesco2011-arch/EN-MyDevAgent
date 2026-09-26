@echo off
rem MyDevAgent - one-command start (Windows), without activating the virtual environment.
rem Usage, from your project folder:   C:\path\to\mydevagent\run.bat   [mydevagent options]
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
rem python -m and not mydevagent.exe: the exe stops working if the folder is moved or renamed
"%PY%" -c "import mydevagent.cli" >nul 2>&1 || call :repair || goto failed
"%PY%" -m mydevagent.cli %*
if errorlevel 1 goto failed
exit /b 0

:repair
if not exist "%PY%" goto install
echo Repairing the MyDevAgent installation (folder moved or updated)...
"%PY%" -m pip install -q -e "%HERE%.[server,search]" && "%PY%" -c "import mydevagent.cli" >nul 2>&1 && exit /b 0
:install
echo MyDevAgent isn't installed yet: starting the installation (only once).
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\install.ps1"
exit /b %errorlevel%

:failed
echo.
echo MyDevAgent exited with an error: the message is above.
pause
exit /b 1
