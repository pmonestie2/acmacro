@echo off
"C:\PYTHON\python.exe" "%~dp0enb-server\tools\run_server.py" %*
exit /b %errorlevel%
