@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual...
  python -m venv .venv || (echo Python 3.10+ nao encontrado. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (pause & exit /b 1)
)
echo PickFlow em http://127.0.0.1:7860
start "" "http://127.0.0.1:7860"
".venv\Scripts\python.exe" -m pickflow
pause
