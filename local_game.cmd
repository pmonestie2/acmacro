@echo off
"C:\PYTHON\python.exe" "%~dp0enb-server\tools\start_proxy.py" %*
exit /b %errorlevel%
