@echo off
"C:\PYTHON\python.exe" "%~dp0macro_control.py" resume %*
exit /b %errorlevel%
