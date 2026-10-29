"""Servidor local do Clef (POST /v1/systemone, GET /health, POST /v1/device, POST /v1/unload).

Carrega os pesos da Cloudflare (Cloudflare/clef-flash ou Cloudflare/clef) com o
`joint_schema_model.py` que vem junto do repositório e responde no mesmo formato da
API System One. O campo `images` aceita data URLs ou base64 puro.

Para não disputar VRAM com o ComfyUI, o modelo é carregado no primeiro pedido e
descarregado depois de `--idle-unload` segundos sem uso.

**Quantização: bf16 ou int4.** `--quant bf16` (padrão) é o modo mais fiel: ~17,8 GB de VRAM
(chega a ~26 GB ao decidir com imagens). `--quant int4` (bitsandbytes, nf4) quantiza os mesmos pesos ao
carregar: ~7,9 GB (pico ~16 GB) e decisões bem mais rápidas, mas **só roda em placa NVIDIA** (CUDA).
Na CPU (`--device cpu`) só o bf16 funciona (~18 GB de RAM). `GET /health` informa `device`, `dtype`,
`quant`, `loaded`, `vram` (só em cuda), `ram_gb` (RSS do processo) e `vram_expected_gb`/`vram_work_gb`, que
é o que o PickFlow usa para decidir se o Clef cabe junto do ComfyUI. `POST /v1/device` troca o device em
tempo de execução; o modelo recarrega no pedido seguinte.

Para escolher a quantização na hora de ligar, use `start_clef.py` (ou o `.bat`/`.sh` que o chama).

    python clef_server/server.py --model Cloudflare/clef-flash --port 8321 --quant int4
    python clef_server/server.py --model Cloudflare/clef-flash --device cpu

Funciona igual no Windows e no Linux. A lógica de carga segue `joint_schema_model.load_release_model`
(a mesma do servidor de referência da Cloudflare); `tests/test_clef_server.py` a exercita com um modelo de
mentira, sem os pesos.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import io
import os
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from PIL import Image


QUANTS = ("bf16", "int4")
#: Quanto o modelo pesa na GPU (GB), medido no clef-flash; ao decidir, o pico sobe VRAM_ACTIVATIONS_GB (7,9 -> 16,4 em int4).
VRAM_WEIGHTS_GB = {"bf16": 17.8, "int4": 7.9}
VRAM_ACTIVATIONS_GB = 8.5


def normalize_quant(value) -> str:
    """"bf16" ou "int4". Vazio, "none" e "bf16" são o modelo sem quantização."""
    q = str(value or "bf16").strip().lower()
    if q in ("", "none", "bf16", "bfloat16"):
        return "bf16"
    if q in ("int4", "4bit", "nf4"):
        return "int4"
    raise ValueError(f"quantization '{value}' does not exist: use bf16 or int4.")


def check_setup(device: str, quant: str) -> str:
    """Por que esta combinação não vai funcionar (texto pronto para o usuário), ou "" se está tudo certo.

    Roda antes de carregar: o erro do bitsandbytes ou do CUDA no meio do `from_pretrained` é bem menos claro."""
    dev = str(device or "cuda").strip().lower()
    if dev.split(":", 1)[0] not in ("cpu", "cuda"):
        return f"device '{device}' is not valid; use cpu, cuda or cuda:0."
    if quant == "int4" and dev.startswith("cpu"):
        return ("int4 only runs on an NVIDIA card (CUDA): on the CPU use bf16 (~18 GB of RAM). "
                "Pick --quant bf16 or --device cuda.")
    if dev.startswith("cuda"):
        try:
            import torch
        except ImportError:
            return "torch is not installed in this environment (run start_clef.py to install it)."
        if not torch.cuda.is_available():
            return ("torch does not see an NVIDIA card (CUDA). Install torch with CUDA "
                    "(start_clef.py does this) or run on the CPU with --device cpu (bf16, ~18 GB of RAM).")
    if quant == "int4":
        try:
            import bitsandbytes  # noqa: F401
        except ImportError:
            return "the bitsandbytes package is not installed (pip install bitsandbytes); int4 needs it."
    return ""


class Holder:
    def __init__(self, model: str, device: str, idle_unload: float, quant: str = "bf16"):
        self.model_id, self.device, self.idle = model, device, idle_unload
        self.quant = normalize_quant(quant)
        self.model = self.processor = self.systemone = None
        self.last_used = 0.0
        self.lock = threading.Lock()

    def load(self):
        if self.model is not None:
            return
        why = check_setup(self.device, self.quant)          # antes de baixar ~19 GB à toa
        if why:
            raise RuntimeError(why)
        from huggingface_hub import snapshot_download

        path = self.model_id
        if not os.path.isdir(path):
            path = snapshot_download(self.model_id)
        if path not in sys.path:
            sys.path.insert(0, path)  # o joint_schema_model.py vem no repositório
        from joint_schema_model import load_release_model, systemone

        print(f"[clef] carregando {self.model_id} em {self.device} ({self.quant})…", flush=True)
        t0 = time.time()
        kwargs = {}
        if self.quant == "int4":  # load_release_model repassa kwargs ao from_pretrained
            import torch
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_quant_type="nf4")
        self.model, self.processor = load_release_model(path, device=self.device, **kwargs)
        self.systemone = systemone
        print(f"[clef] pronto em {time.time() - t0:.0f}s", flush=True)

    def unload(self):
        with self.lock:
            if self.model is None:
                return
            self.model = self.processor = self.systemone = None
            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            print("[clef] descarregado por inatividade", flush=True)

    def run(self, request: dict) -> dict:
        with self.lock:
            self.load()
            out = self.systemone(self.model, self.processor, request)
            self.last_used = time.time()
            return out

    def vram_expected_gb(self) -> float:
        """Quanto o modelo ocupa na GPU: medido agora (se carregado) ou pela quantização.

        É o número que o PickFlow usa para saber se o Clef cabe ao lado do ComfyUI. Estimativas medidas
        no clef-flash: bf16 ~17,8 GB, int4 ~7,9 GB (a cabeça e a visão ficam em bf16)."""
        if self.model is not None:
            try:
                import torch

                if str(self.device).startswith("cuda") and torch.cuda.is_available():
                    return round(torch.cuda.memory_allocated() / 2**30, 2)
            except Exception:  # noqa: BLE001
                pass
        return VRAM_WEIGHTS_GB[self.quant]

    def vram_work_gb(self) -> float:
        """Quanto ele precisa de VRAM para **trabalhar**: os pesos mais as ativações de uma decisão.

        Medido no `clef-flash` com 2 imagens: o pico sobe ~8,5 GB acima dos pesos (7,9 → 16,4 GB em int4;
        em bf16 chega a 25,8 GB). É este número — não o tamanho dos pesos — que diz se o Clef cabe junto
        do ComfyUI enquanto decide."""
        return round(self.vram_expected_gb() + VRAM_ACTIVATIONS_GB, 2)


def decode_image(item) -> Image.Image:
    if isinstance(item, dict):  # {"url": "..."} no estilo OpenAI
        item = item.get("url") or item.get("image") or ""
    if not isinstance(item, str) or not item:
        raise ValueError("imagem inválida")
    if item.startswith("data:"):
        item = item.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(item))).convert("RGB")


def jsonable(x):
    if hasattr(x, "tolist"):
        return x.tolist()
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


def _rss_gb() -> float:
    """Quanto o processo ocupa de RAM, em GB (0.0 se nada deu certo).

    Ordem: `psutil` (está no requirements.txt do servidor, é o caminho bom e
    multiplataforma), `GetProcessMemoryInfo` no Windows, `/proc/self/statm` no POSIX.
    As duas reservas existem para quem rodou o servidor sem instalar o psutil.
    """
    try:
        import psutil

        return psutil.Process().memory_info().rss / 2 ** 30
    except Exception:  # noqa: BLE001 - o /health nunca deve falhar
        pass
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            # K32GetProcessMemoryInfo é o nome atual; GetProcessMemoryInfo no psapi.dll é o antigo
            info = getattr(kernel32, "K32GetProcessMemoryInfo", None)
            if info is None:
                info = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
            info.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            info.restype = wintypes.BOOL
            if info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / 2 ** 30
            return 0.0
        with open("/proc/self/statm", encoding="ascii") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
    except Exception:  # noqa: BLE001
        return 0.0


def _params_gb(model) -> float:
    """Soma dos bytes dos parâmetros, em GB — reserva quando o RSS vem 0 (é melhor que 0)."""
    try:
        return sum(p.numel() * p.element_size() for p in model.parameters()) / 2 ** 30
    except Exception:  # noqa: BLE001
        return 0.0


def _dtype(model) -> str:
    """dtype dos parâmetros ("bfloat16", "int8"…), para o /health mostrar como o Clef roda."""
    try:
        return str(next(model.parameters()).dtype).replace("torch.", "")
    except Exception:  # noqa: BLE001
        return ""


def create_app(holder: Holder) -> FastAPI:
    app = FastAPI(title="Clef (System One)")

    @app.on_event("startup")
    async def _janitor():
        async def loop():
            while True:
                await asyncio.sleep(15)
                if holder.idle and holder.model is not None and time.time() - holder.last_used > holder.idle:
                    await asyncio.to_thread(holder.unload)

        asyncio.create_task(loop())

    @app.get("/health")
    async def health():
        """O PickFlow lê `loaded`, `device` e `ram_gb` (e `vram.allocated_gb` em cuda) para
        decidir se precisa descarregar o Clef antes de gerar.

        `ram_gb` vem sempre: com o Clef na RAM é o número que importa; com o Clef na GPU
        é pequeno, mas o PickFlow ainda compara com a RAM livre do sistema.

        `quant` e `vram_expected_gb` dizem como ele foi carregado e quanto ele ocupa **mesmo
        descarregado** — é com isso que o PickFlow decide se cabe junto do ComfyUI (bf16 ≈ 17,8 GB
        não cabe; int4 ≈ 6 GB cabe) sem precisar de um número fixo nos ajustes.
        """
        loaded = holder.model is not None
        out = {"ok": True, "model": holder.model_id, "loaded": loaded,
               "device": holder.device, "dtype": _dtype(holder.model) if loaded else "",
               "quant": "" if holder.quant in (None, "none", "bf16") else holder.quant,
               "vram_expected_gb": holder.vram_expected_gb(),
               "vram_work_gb": holder.vram_work_gb()}
        ram = _rss_gb()
        if loaded and ram <= 0.0:
            ram = _params_gb(holder.model)   # reserva: a soma dos pesos já ajuda o PickFlow
        out["ram_gb"] = round(ram, 2)
        try:
            import torch
            if loaded and str(holder.device).startswith("cuda") and torch.cuda.is_available():
                out["vram"] = {"allocated_gb": round(torch.cuda.memory_allocated() / 2**30, 2),
                               "reserved_gb": round(torch.cuda.memory_reserved() / 2**30, 2)}
        except Exception:  # noqa: BLE001 - health nunca deve falhar
            pass
        return out

    @app.post("/v1/unload")
    @app.post("/unload")
    async def unload():
        await asyncio.to_thread(holder.unload)
        return {"ok": True, "loaded": holder.model is not None}

    @app.post("/v1/device")
    @app.post("/device")
    async def set_device(req: Request):
        """Troca o device do Clef (cpu | cuda | cuda:N) e descarrega; recarrega no próximo pedido."""
        try:
            body = await req.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"corpo inválido ({e}); mande {{\"device\": \"cpu\"}} ou {{\"device\": \"cuda\"}}.") from e
        if not isinstance(body, dict):
            body = {}
        device = str(body.get("device") or "").strip().lower()
        if device.split(":", 1)[0] not in ("cpu", "cuda"):
            raise HTTPException(422, (f"device '{device}' não serve; use 'cpu' ou 'cuda' "
                                      f"(também vale 'cuda:0'). Nada foi alterado."))
        await asyncio.to_thread(holder.unload)
        holder.device = device
        return {"ok": True, "device": holder.device, "loaded": holder.model is not None}

    @app.post("/v1/systemone")
    async def systemone_endpoint(req: Request):
        body = await req.json()
        if "questions" not in body:
            raise HTTPException(422, "faltou 'questions'")
        try:
            imgs = [decode_image(i) for i in body.get("images") or []]
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"imagem inválida: {e}") from e
        request = {"model": body.get("model", holder.model_id), "state": body.get("state", ""),
                   "questions": body["questions"]}
        if imgs:
            request["images"] = imgs
        try:
            out = await asyncio.to_thread(holder.run, request)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{e.__class__.__name__}: {e}") from e
        return jsonable(out)

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Cloudflare/clef-flash", help="id no Hugging Face ou pasta local")
    ap.add_argument("--device", default="cuda",
                    help=("onde o Clef roda: 'cuda' ou 'cuda:0' usa a VRAM (bf16 ~17,8 GB, int4 ~7,9 GB; disputa com o "
                          "ComfyUI); 'cpu' roda na RAM (~18 GB, só bf16) e libera a GPU. Dá para trocar depois por "
                          "POST /v1/device."))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--idle-unload", type=float, default=120, help="segundos sem uso até descarregar (0 = nunca)")
    ap.add_argument("--quant", choices=["bf16", "int4", "none"], default="bf16",
                    help="bf16 (padrão, mais fiel) ou int4 (bitsandbytes; só em placa NVIDIA). 'none' = bf16.")
    ap.add_argument("--preload", action="store_true", help="carrega o modelo já na partida")
    a = ap.parse_args()
    quant = normalize_quant(a.quant)
    why = check_setup(a.device, quant)
    if why:                                   # falha na partida, com o motivo, e não no primeiro pedido
        print(f"[clef] cannot start like this: {why}", file=sys.stderr, flush=True)
        sys.exit(2)
    holder = Holder(a.model, a.device, a.idle_unload, quant)
    if a.preload:
        holder.load()
        holder.last_used = time.time()
    import uvicorn

    uvicorn.run(create_app(holder), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
