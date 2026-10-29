#!/usr/bin/env python3
"""Liga o servidor do Clef (Windows e Linux): prepara o ambiente na primeira vez, pergunta a quantização e sobe.

Um só script para tudo (por isso não há um .bat por quantização): ele cria `clef_server/.venv`, instala o torch
certo para a sua máquina (CUDA 12.8 se achar uma placa NVIDIA) e o resto do `requirements.txt`, pergunta **bf16 ou
int4** e roda `server.py`. Para pular a pergunta: `--quant bf16` ou `--quant int4`. O que sobrar na linha de comando
vai para o `server.py` (`--port`, `--model`, `--idle-unload`...).

    python clef_server/start_clef.py              # pergunta a quantização
    python clef_server/start_clef.py --quant int4 # sem perguntar
    python clef_server/start_clef.py --install-only

`start_clef.bat` (Windows) e `start_clef.sh` (Linux/macOS) só acham o Python e chamam este arquivo.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
READY = VENV / ".clef_ready"            # o ambiente já foi montado (torch + requirements)
LAST = HERE / ".last_quant"             # a última escolha vira o padrão da próxima pergunta (Enter)
TORCH_CUDA = "https://download.pytorch.org/whl/cu128"     # CUDA 12.8: placas NVIDIA GTX 10 em diante, inclusive RTX 50

#: id -> (nome, o que ocupa). Os números são os medidos no clef-flash (pesos; entre parênteses o pico ao decidir).
QUANTS = {
    "bf16": ("bf16", "most faithful · ~17.8 GB of VRAM (peak ~26 GB when deciding)"),
    "int4": ("int4", "4 bits (nf4) · ~7.9 GB of VRAM (peak ~16 GB), decides much faster"),
}


# --------------------------------------------------------------------------- escolha
def parse_gpu(text: str) -> tuple[str, float] | None:
    """Primeira linha do `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` -> (nome, GB)."""
    for line in (text or "").splitlines():
        name, _, mem = line.rpartition(",")
        try:
            return name.strip() or "NVIDIA", round(float(mem) / 1024, 1)
        except ValueError:
            continue
    return None


def gpu_info() -> tuple[str, float] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_gpu(out.stdout) if out.returncode == 0 else None


def recommend(gpu: tuple[str, float] | None) -> str:
    """Sem placa NVIDIA só o bf16 na CPU funciona; com placa abaixo de 32 GB o int4 é o que deixa o Clef caber junto do Qwen."""
    if gpu is None:
        return "bf16"
    return "bf16" if gpu[1] >= 32 else "int4"


def last_choice() -> str:
    try:
        v = LAST.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return v if v in QUANTS else ""


def parse_choice(answer: str, default: str) -> str:
    """"" -> o padrão; "1"/"bf16" -> bf16; "2"/"int4" -> int4; qualquer outra coisa -> "" (pergunta de novo)."""
    a = (answer or "").strip().lower()
    if not a:
        return default
    order = list(QUANTS)
    if a.isdigit() and 1 <= int(a) <= len(order):
        return order[int(a) - 1]
    return a if a in QUANTS else ""


def choose_quant(wanted: str | None, gpu: tuple[str, float] | None, interactive: bool, device: str = "cuda") -> str:
    """bf16 ou int4. Na CPU não há o que escolher (o int4 precisa de CUDA); com `--quant` também não se pergunta."""
    if wanted:
        return wanted
    if device == "cpu":
        print("On the CPU the Clef runs only in bf16 (~18 GB of RAM); int4 needs an NVIDIA card.")
        return "bf16"
    suggested = recommend(gpu)
    default = last_choice() or suggested
    if not interactive:
        print(f"Quantization: {default} (no terminal to ask on; use --quant bf16 or --quant int4 to choose).")
        return default
    print(f"\nCard: {gpu[0]} ({gpu[1]:g} GB of VRAM)" if gpu else "\nNo NVIDIA card detected by nvidia-smi.")
    print("Clef quantization:")
    for i, (qid, (_name, info)) in enumerate(QUANTS.items(), 1):
        tag = "   <- recommended" if qid == suggested else ""
        print(f"  {i}) {qid}  {info}{tag}")
    while True:
        try:
            ans = input(f"Choose [1/2, Enter = {default}]: ")
        except EOFError:
            return default
        pick = parse_choice(ans, default)
        if pick:
            return pick
        print("Type 1, 2, bf16 or int4.")


def server_command(py: Path | str, quant: str, device: str, rest: list[str]) -> list[str]:
    """A linha que sobe o `server.py`. O que o usuário passou em `rest` vem depois e vale mais (último argumento ganha)."""
    cmd = [str(py), str(HERE / "server.py"), "--model", "Cloudflare/clef-flash", "--port", "8321",
           "--idle-unload", "120", "--quant", quant, "--device", device]
    return cmd + rest


# --------------------------------------------------------------------------- ambiente
def run(cmd: list[str]) -> bool:
    try:
        return subprocess.call(cmd) == 0
    except OSError as e:
        print(f"Could not run {cmd[0]}: {e}")
        return False


def ensure_env(gpu: tuple[str, float] | None) -> bool:
    if not PY.exists():
        print("Creating the Clef Python environment (first time only)...")
        if not run([sys.executable, "-m", "venv", str(VENV)]):
            print("Could not create the environment. It needs Python 3.10+ with the venv module "
                  "(on Debian/Ubuntu: sudo apt install python3-venv).")
            return False
    if READY.exists():
        return True
    pip = [str(PY), "-m", "pip", "install"]
    if gpu:
        print("Installing torch with CUDA 12.8 (a few GB; takes a few minutes)...")
        ok = run(pip + ["torch", "--index-url", TORCH_CUDA])
    else:
        print("Installing torch (CPU build)...")
        ok = run(pip + ["torch"])
    if not ok:
        print("Failed to install torch. See https://pytorch.org/get-started/locally/")
        return False
    if not run(pip + ["-r", str(HERE / "requirements.txt")]):
        return False
    READY.write_text("ok\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Starts the Clef server (asks bf16 or int4).", add_help=True)
    ap.add_argument("--quant", choices=list(QUANTS), help="skips the question")
    ap.add_argument("--device", choices=["cuda", "cpu"], help="default: cuda with an NVIDIA card, otherwise cpu")
    ap.add_argument("--install-only", action="store_true", help="only prepares the environment and exits")
    args, rest = ap.parse_known_args(argv)

    gpu = gpu_info()
    if not ensure_env(gpu):
        return 1
    if args.install_only:
        print("Environment ready.")
        return 0
    device = args.device or ("cuda" if gpu else "cpu")
    quant = choose_quant(args.quant, gpu, sys.stdin.isatty(), device)
    if device == "cpu" and quant == "int4":
        print("int4 does not run on the CPU: use --quant bf16 or --device cuda.")
        return 2
    try:
        LAST.write_text(quant + "\n", encoding="utf-8")
    except OSError:
        pass
    print(f"\nClef at http://127.0.0.1:8321 ({quant}, {device}); the model is downloaded from Hugging Face on the first request.")
    print("That is already the default address in PickFlow's Settings > Clef. Ctrl+C stops it.\n")
    try:
        return subprocess.call(server_command(PY, quant, device, rest))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
