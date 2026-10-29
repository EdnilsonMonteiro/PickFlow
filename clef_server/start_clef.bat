@echo off
setlocal
cd /d "%~dp0"
rem Use um ambiente separado do PickFlow: torch e transformers sao pesados.
rem Na primeira vez: cria o ambiente e instala o torch (CUDA) e o resto sozinho (alguns GB; leva uns minutos).
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || (echo Python 3.10+ nao encontrado. Instale em https://www.python.org/downloads/ & pause & exit /b 1)
  echo Instalando o torch com CUDA 12.8 ^(placas NVIDIA GTX 10 em diante, inclusive RTX 50^)...
  ".venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cu128 || (echo Falha no torch. Veja https://pytorch.org/get-started/locally/ & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
echo Clef em http://127.0.0.1:8321 ^(o modelo e baixado do Hugging Face no primeiro pedido^)
rem Por padrao o Clef roda na CPU (RAM), quantizado em int8: assim ele nao disputa a placa com o Qwen.
rem Para usar a GPU: troque --device cpu por --device cuda (aí use --quant int8 ou int4 para caber junto do Qwen).
".venv\Scripts\python.exe" server.py --model Cloudflare/clef-flash --port 8321 --idle-unload 120 --quant int8 --device cpu
pause
