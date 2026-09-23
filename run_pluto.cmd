@echo off
setlocal
cd /d "%~dp0"
if not exist "C:\PYTHON\python.exe" (
    echo Python was not found at C:\PYTHON\python.exe.
    pause
    exit /b 1
)
if "%~1"=="" (
    "C:\PYTHON\python.exe" "%~dp0pluto.py" "%~dp0config_player.txt"
) else (
    "C:\PYTHON\python.exe" "%~dp0pluto.py" %*
)
set "pluto_exit=%errorlevel%"
if not "%pluto_exit%"=="0" pause
exit /b %pluto_exit%
