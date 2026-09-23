@echo off
setlocal
cd /d "%~dp0"
if not exist "C:\PYTHON\python.exe" (
    echo Python was not found at C:\PYTHON\python.exe.
    pause
    exit /b 1
)
"C:\PYTHON\python.exe" "%~dp0try_space.py"
set "space_exit=%errorlevel%"
pause
exit /b %space_exit%
