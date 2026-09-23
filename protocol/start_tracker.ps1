$ErrorActionPreference = 'Stop'
Start-Process -FilePath 'C:\PYTHON\python.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList ('"' + (Join-Path $PSScriptRoot 'live_tracker.py') + '"')
