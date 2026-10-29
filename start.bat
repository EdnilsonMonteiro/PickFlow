@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\.ready" (
  echo Creating the Python environment ^(first time only^)...
  if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || (echo Python 3.10+ not found. Install it from https://www.python.org/downloads/ & pause & exit /b 1)
  )
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (pause & exit /b 1)
  echo ok> ".venv\.ready"
)
echo PickFlow at http://127.0.0.1:7860
start "" "http://127.0.0.1:7860"
".venv\Scripts\python.exe" -m pickflow
pause
