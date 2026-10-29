"""Cliente HTTP do ComfyUI (upload, fila, histórico, download)."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

PROBE_PORTS = (8188, 8000, 8001, 8002, 8003, 8199)


class ComfyError(Exception):
    pass


class ComfyClient:
    def __init__(self, base_url: str, cache_dir: Path, upload_subfolder: str = "pickflow",
                 timeout_s: float = 1800):
        self.base_url = base_url.rstrip("/")
        self.cache = Path(cache_dir) / "object_info.json"
        self.subfolder = upload_subfolder
        self.timeout_s = timeout_s
        self.client_id = uuid.uuid4().hex
        self._oi: dict | None = None

    def _http(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    # ------------------------------------------------------------ status
    async def ping(self) -> dict:
        try:
            async with self._http(5) as c:
                r = await c.get("/system_stats")
                r.raise_for_status()
                return {"ok": True, "url": self.base_url, "stats": r.json()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "url": self.base_url, "error": str(e) or e.__class__.__name__}

    @classmethod
    async def detect(cls, preferred: str) -> str | None:
        """Devolve a primeira URL de ComfyUI que responde (a configurada ou portas comuns)."""
        cands = [preferred.rstrip("/")] + [f"http://127.0.0.1:{p}" for p in PROBE_PORTS]
        seen = []
        for u in cands:
            if u in seen:
                continue
            seen.append(u)
            try:
                async with httpx.AsyncClient(timeout=2) as c:
                    r = await c.get(u + "/system_stats")
                    if r.status_code == 200:
                        return u
            except Exception:  # noqa: BLE001
                continue
        return None

    # ------------------------------------------------------ object_info
    async def object_info(self, refresh: bool = False) -> dict:
        if self._oi is not None and not refresh:
            return self._oi
        try:
            async with self._http(120) as c:
                r = await c.get("/object_info")
                r.raise_for_status()
                self._oi = r.json()
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps(self._oi), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            if self.cache.exists() and not refresh:
                self._oi = json.loads(self.cache.read_text(encoding="utf-8"))
            else:
                raise ComfyError(f"Não consegui ler /object_info em {self.base_url}: {e}") from e
        return self._oi

    # ----------------------------------------------------------- imagens
    async def upload(self, data: bytes, name: str) -> str:
        """Envia uma imagem para input/<subfolder>/ e devolve o valor para o widget do LoadImage."""
        try:
            async with self._http(120) as c:
                r = await c.post("/upload/image",
                                 files={"image": (name, data, "image/png")},
                                 data={"type": "input", "subfolder": self.subfolder, "overwrite": "true"})
                r.raise_for_status()
                j = r.json()
        except Exception as e:  # noqa: BLE001
            raise ComfyError(f"Falha no upload da imagem para o ComfyUI: {e}") from e
        sub = j.get("subfolder") or ""
        return f"{sub}/{j['name']}" if sub else j["name"]

    async def view(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        async with self._http(120) as c:
            r = await c.get("/view", params={"filename": filename, "subfolder": subfolder, "type": type_})
            r.raise_for_status()
            return r.content

    # ------------------------------------------------------------- fila
    async def submit(self, api_prompt: dict) -> str:
        async with self._http(60) as c:
            r = await c.post("/prompt", json={"prompt": api_prompt, "client_id": self.client_id})
        if r.status_code != 200:
            raise ComfyError(self._explain(r))
        return r.json()["prompt_id"]

    @staticmethod
    def _explain(r: httpx.Response) -> str:
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return f"ComfyUI respondeu {r.status_code}: {r.text[:300]}"
        err = j.get("error") or {}
        parts = [err.get("message") or f"ComfyUI respondeu {r.status_code}"]
        if err.get("details"):
            parts.append(str(err["details"]))
        for nid, ne in (j.get("node_errors") or {}).items():
            for e in ne.get("errors", []):
                parts.append(f"nó {nid} ({ne.get('class_type')}): {e.get('message')} {e.get('details', '')}".strip())
        return " | ".join(parts)

    async def wait(self, prompt_id: str, on_tick: Callable[[float], Awaitable[None]] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Espera o histórico do prompt e devolve `outputs` (node_id -> saída)."""
        t0 = time.monotonic()
        async with self._http(30) as c:
            while True:
                if cancelled and cancelled():
                    await self.interrupt()
                    raise ComfyError("Execução cancelada.")
                if time.monotonic() - t0 > self.timeout_s:
                    raise ComfyError("Tempo esgotado esperando o ComfyUI.")
                try:
                    r = await c.get(f"/history/{prompt_id}")
                    hist = r.json().get(prompt_id) if r.status_code == 200 else None
                except Exception:  # noqa: BLE001
                    hist = None
                if hist:
                    st = hist.get("status") or {}
                    if st.get("status_str") == "error":
                        raise ComfyError(self._history_error(st))
                    if st.get("completed", True) or hist.get("outputs"):
                        return hist.get("outputs") or {}
                if on_tick:
                    await on_tick(time.monotonic() - t0)
                await asyncio.sleep(0.8)

    @staticmethod
    def _history_error(status: dict) -> str:
        for ev in status.get("messages", []):
            if ev and ev[0] == "execution_error":
                d = ev[1]
                return (f"Erro no nó {d.get('node_id')} ({d.get('node_type')}): "
                        f"{d.get('exception_message', '').strip()}")
        return "O ComfyUI reportou erro na execução."

    async def stats(self) -> dict:
        r = await self.ping()
        return r.get("stats") or {}

    async def free(self):
        try:
            async with self._http(30) as c:
                await c.post("/free", json={"unload_models": True, "free_memory": True})
        except Exception:  # noqa: BLE001
            pass

    async def interrupt(self):
        try:
            async with self._http(10) as c:
                await c.post("/interrupt")
        except Exception:  # noqa: BLE001
            pass
