@echo off
rem Starts the Clef server. Everything happens in start_clef.py (creates the environment the first time, asks bf16 or int4 and starts the server);
rem this file only finds Python. To skip the question: start_clef.bat --quant int4
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start_clef.py %*
) else (
  python start_clef.py %*
)
if errorlevel 1 (
  echo.
  echo The Clef did not start. It needs Python 3.10+: https://www.python.org/downloads/
  pause
)
