"""Servidor local do Clef (POST /v1/systemone, GET /health, POST /v1/device, POST /v1/unload).

Carrega os pesos da Cloudflare (Cloudflare/clef-flash ou Cloudflare/clef) com o
`joint_schema_model.py` que vem junto do repositório e responde no mesmo formato da
API System One. O campo `images` aceita data URLs ou base64 puro.

Para não disputar VRAM com o ComfyUI, o modelo é carregado no primeiro pedido e
descarregado depois de `--idle-unload` segundos sem uso.

**Modo GPU x modo CPU/RAM.** Em bf16 o Clef ocupa ~17,8 GB dos 24 GB da placa, então
ele e o Qwen (16 GB) não cabem juntos: é preciso descarregar um para o outro entrar.
Rodando na CPU (`--device cpu` com `--quant int8` ou `--quant int4`, ~9,5 GB e ~5,6 GB
de RAM) ele sobra para a GPU e a disputa passa a ser de RAM. `GET /health` informa
`device`, `dtype`, `loaded`, `vram` (só em cuda) e `ram_gb` (RSS do processo, sempre),
que é o que o PickFlow usa para decidir. `POST /v1/device` troca o device em tempo de
execução; o modelo recarrega no pedido seguinte.

    python clef_server/server.py --model Cloudflare/clef-flash --port 8321
    python clef_server/server.py --model Cloudflare/clef --device cpu --quant int8

Observação: escrito a partir da documentação pública do repositório; não foi
executado com os pesos reais no ambiente onde o projeto foi criado.
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


class Holder:
    def __init__(self, model: str, device: str, idle_unload: float, quant: str = "none"):
        self.model_id, self.device, self.idle, self.quant = model, device, idle_unload, quant
        self.model = self.processor = self.systemone = None
        self.last_used = 0.0
        self.lock = threading.Lock()

    def load(self):
        if self.model is not None:
            return
        from huggingface_hub import snapshot_download

        path = self.model_id
        if not __import__("os").path.isdir(path):
            path = snapshot_download(self.model_id)
        if path not in sys.path:
            sys.path.insert(0, path)  # o joint_schema_model.py vem no repositório
        from joint_schema_model import load_release_model, systemone

        print(f"[clef] carregando {self.model_id} em {self.device} ({self.quant})…", flush=True)
        t0 = time.time()
        kwargs = {}
        if self.quant in ("int8", "int4"):  # load_release_model repassa kwargs ao from_pretrained
            import torch
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = (
                BitsAndBytesConfig(load_in_8bit=True) if self.quant == "int8"
                else BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
                                        bnb_4bit_quant_type="nf4"))
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

        É o número que o PickFlow usa para saber se o Clef cabe ao lado do ComfyUI. As estimativas
        são as do README: bf16 ~17,8 GB, int8 ~9,5 GB, int4 ~5,6 GB (mais a cabeça em bf16)."""
        if self.model is not None:
            try:
                import torch

                if str(self.device).startswith("cuda") and torch.cuda.is_available():
                    return round(torch.cuda.memory_allocated() / 2**30, 2)
            except Exception:  # noqa: BLE001
                pass
        return {"": 17.8, "none": 17.8, "int8": 9.6, "int4": 6.0}.get(str(self.quant or "none"), 0.0)

    def vram_work_gb(self) -> float:
        """Quanto ele precisa de VRAM para **trabalhar**: os pesos mais as ativações de uma decisão.

        Medido no `clef-flash` com 2 imagens: o pico sobe ~8,5 GB acima dos pesos (7,9 → 16,4 GB em int4;
        em bf16 chega a 25,8 GB). É este número — não o tamanho dos pesos — que diz se o Clef cabe junto
        do ComfyUI enquanto decide."""
        return round(self.vram_expected_gb() + 8.5, 2)


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
               "quant": "" if holder.quant in (None, "none") else holder.quant,
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
                    help=("onde o Clef roda: 'cuda' ou 'cuda:0' usa a VRAM (~17,8 GB em bf16 e disputa com o ComfyUI); "
                          "'cpu' roda na RAM e libera a GPU — nesse caso use junto --quant int8 (~9,5 GB de RAM) "
                          "ou --quant int4 (~5,6 GB). Dá para trocar depois por POST /v1/device."))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--idle-unload", type=float, default=120, help="segundos sem uso até descarregar (0 = nunca)")
    ap.add_argument("--quant", choices=["none", "int8", "int4"], default="none",
                    help="quantização do backbone via bitsandbytes (precisa do pacote bitsandbytes)")
    ap.add_argument("--preload", action="store_true", help="carrega o modelo já na partida")
    a = ap.parse_args()
    holder = Holder(a.model, a.device, a.idle_unload, a.quant)
    if a.preload:
        holder.load()
        holder.last_used = time.time()
    import uvicorn

    uvicorn.run(create_app(holder), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
