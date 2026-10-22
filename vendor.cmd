@echo off
setlocal
if not exist "C:\PYTHON\python.exe" (
    echo Python was not found at C:\PYTHON\python.exe.
    exit /b 1
)
if "%~1"=="" (
    "C:\PYTHON\python.exe" "%~dp0vendor.py" "%~dp0config_player.txt"
) else (
    "C:\PYTHON\python.exe" "%~dp0vendor.py" %*
)
exit /b %errorlevel%
