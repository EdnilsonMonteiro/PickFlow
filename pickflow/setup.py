"""Primeiros passos: achar o ComfyUI, instalar os nós do PickFlow e baixar LoRAs e modelos.

Nada é baixado nem copiado sem o usuário marcar na tela. Só entram na lista os arquivos de
`defaults/models.yaml` (e de `custom/models.yaml`, se você criar); o cliente nunca manda endereço nem caminho.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from . import encoders, hf, install_nodes
from .comfy import ComfyError
from .store import Store, StoreError, atomic_write

FOLDERS = ("loras", "diffusion_models", "text_encoders", "vae", "clip_vision", "background_removal", "controlnet")
COMBOS = {"loras": ("LoraLoader", "lora_name"), "diffusion_models": ("UNETLoader", "unet_name"),
          "text_encoders": ("CLIPLoader", "clip_name"), "vae": ("VAELoader", "vae_name"),
          "clip_vision": ("CLIPVisionLoader", "clip_name"), "background_removal": ("LoadBackgroundRemovalModel", "bg_removal_name"),
          "controlnet": ("QwenImage21UnionLoader", "union_model")}
NAME_RE = re.compile(r"^\w[\w._ \-]{0,150}\.safetensors$")      # \w aceita letras de qualquer idioma (há LoRAs com nome em chinês)
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
GB = 1024 ** 3
# classes que provam que o nó foi carregado pelo ComfyUI (depois de reiniciar)
NODE_CLASSES = {"qwen_object_mover": "DrawBox", "viggle_turbo.py": "ViggleTurboLora"}
LINKS = {"download": "https://www.comfy.org/download", "github": "https://github.com/comfyanonymous/ComfyUI"}


def base(name: Any) -> str:
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].lower()


# ------------------------------------------------------------------ catálogo
def _url_ok(url: str) -> bool:
    u = urlparse(url)
    return bool(u.hostname) and (u.scheme == "https" or (u.scheme == "http" and u.hostname in LOOPBACK))


def _clean(e: Any) -> dict | None:
    if not isinstance(e, dict):
        return None
    s = lambda k, d="": str(e.get(k) or d)  # noqa: E731
    out = {"id": s("id"), "file": s("file"), "folder": s("folder"), "url": s("url")}
    if not (ID_RE.match(out["id"]) and NAME_RE.match(out["file"]) and out["folder"] in FOLDERS and _url_ok(out["url"])):
        return None
    out.update(group=s("group", "lora" if out["folder"] == "loras" else "model"), title=s("title", out["file"]),
               why=s("why"), license=s("license"), page=s("page"))
    try:
        out["size_gb"] = float(e["size_gb"]) if e.get("size_gb") else None
    except (TypeError, ValueError):
        out["size_gb"] = None
    return out


def catalog(store: Store) -> list[dict]:
    """defaults/models.yaml + custom/models.yaml (mesmo id: o seu vale mais). Entradas inválidas são ignoradas."""
    out: dict[str, dict] = {}
    for layer in (store.defaults_dir, store.custom_dir):
        p = layer / "models.yaml"
        if not p.is_file():
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for raw in data.get("files") or []:
            e = _clean(raw)
            if e:
                out[e["id"]] = e
    return list(out.values())


def _strings(x: Any):
    if isinstance(x, str):
        yield x
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)


def used_by(store: Store) -> dict[str, list[str]]:
    """arquivo (nome-base) -> ids dos workflows que o citam."""
    res: dict[str, set[str]] = {}
    for wid in store.manifests():
        for mode, _m in store.variants(wid):
            try:
                wf = store.workflow_json(wid, mode)
            except (StoreError, OSError, ValueError):
                continue
            for s in _strings(wf):
                if s.lower().endswith(".safetensors") and len(s) < 200:
                    res.setdefault(base(s), set()).add(wid)
    for lid, lo in store.loras().items():            # LoRAs da biblioteca (escolhidas pelo Clef na hora)
        if lo.get("file") and not lo.get("disabled"):
            res.setdefault(base(lo["file"]), set()).add("lora:" + lid)
    return {k: sorted(v) for k, v in res.items()}


# ----------------------------------------------------------- achar o ComfyUI
def is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in LOOPBACK or host.strip("[]") in LOOPBACK:
        return True
    try:
        return host.lower() in {socket.gethostname().lower(), socket.getfqdn().lower()}
    except OSError:
        return False


def valid_root(p: Path) -> bool:
    return (p / "custom_nodes").is_dir() and (p / "models").is_dir()


def normalize_root(p: Path) -> Path:
    p = p.expanduser()
    return p.parent if p.name.lower() in ("custom_nodes", "models") else p


def _roots_from_stats(stats: dict) -> list[Path]:
    argv = [str(a) for a in ((stats.get("system") or {}).get("argv") or [])]
    out: list[Path] = []
    for i, a in enumerate(argv):
        if a == "--base-directory" and i + 1 < len(argv):
            out.append(Path(argv[i + 1]))
        elif a.startswith("--base-directory="):
            out.append(Path(a.split("=", 1)[1]))
    main = next((a for a in argv if a.lower().endswith("main.py")), None)
    if main and Path(main).is_absolute():
        out.append(Path(main).parent)
    return out


def _heuristic_roots() -> list[Path]:
    return [cn.parent for cn in install_nodes.find_custom_nodes()]


def find_root(settings: dict, stats: dict) -> tuple[Path | None, str, str]:
    """(pasta do ComfyUI, de onde veio, aviso). A pasta tem custom_nodes/ e models/."""
    warn = ""
    mine = str((settings.get("comfyui") or {}).get("path") or "").strip()
    if mine:
        p = normalize_root(Path(mine))
        if valid_root(p):
            return p, "ajustes", ""
        warn = f"A pasta informada não parece ser a do ComfyUI (falta custom_nodes ou models): {mine}"
    for p in _roots_from_stats(stats):
        if valid_root(p):
            return p, "comfyui", warn
    for p in _heuristic_roots():
        if valid_root(p):
            return p, "busca", warn
    return None, "", warn


# ---------------------------------------------------------------- instalado?
def in_object_info(oi: dict | None, folder: str, file: str) -> bool | None:
    if not oi:
        return None
    cls, key = COMBOS[folder]
    spec = (((oi.get(cls) or {}).get("input") or {}).get("required") or {}).get(key)
    if not spec:
        return None
    opts = spec[0] if isinstance(spec[0], list) else []
    return any(base(o) == base(file) for o in opts)


def on_disk(root: Path | None, folder: str, file: str) -> bool | None:
    if not root:
        return None
    d = root / "models" / folder
    return d.is_dir() and any(p.name.lower() == file.lower() for p in d.rglob("*") if p.is_file())


# ------------------------------------------------------------------- rede
async def probe_one(client: httpx.AsyncClient, url: str) -> dict:
    """Confere se o endereço existe e qual o tamanho, sem baixar. Hugging Face responde 302 com
    x-linked-size / x-linked-etag (o sha256 do arquivo) na primeira resposta."""
    try:
        r = await client.head(url, follow_redirects=False, headers=hf.auth_headers(url))
        hops = 0
        while r.is_redirect and hops < 5 and "x-linked-size" not in r.headers:
            r = await client.head(urljoin(str(r.url), r.headers["location"]), follow_redirects=False)
            hops += 1
        if r.status_code in (404, 410):
            return {"ok": False, "error": "arquivo não encontrado no endereço"}
        if r.status_code in (401, 403):
            return {"ok": False, "error": hf.denied_message() if hf.is_hf(url) else "o servidor exige login ou aceite de licença"}
        if r.status_code >= 400:
            return {"ok": False, "error": f"o servidor respondeu {r.status_code}"}
        size = r.headers.get("x-linked-size") or r.headers.get("content-length")
        sha = (r.headers.get("x-linked-etag") or "").strip('"')
        return {"ok": True, "size": int(size) if size and size.isdigit() else None,
                "sha": sha if re.fullmatch(r"[0-9a-f]{64}", sha) else None}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"sem conexão ({e.__class__.__name__})"}


@dataclass
class Job:
    id: str
    title: str
    state: str = "fila"       # fila | baixando | pronto | erro | cancelado
    done: int = 0
    total: int = 0
    error: str = ""


class _Cancelled(Exception):
    pass


class Downloader:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.task: asyncio.Task | None = None
        self._cancel = False

    @property
    def busy(self) -> bool:
        return bool(self.task and not self.task.done())

    def start(self, items: list[dict], root: Path):
        self._cancel = False
        self.jobs = {e["id"]: Job(e["id"], e["title"]) for e in items}
        self.task = asyncio.create_task(self._run(items, root))

    def cancel(self):
        self._cancel = True

    async def _run(self, items: list[dict], root: Path):
        for e in items:
            job = self.jobs[e["id"]]
            if self._cancel:
                job.state = "cancelado"
                continue
            try:
                await self._one(job, e, root)
                job.state = "pronto"
            except _Cancelled:
                job.state = "cancelado"
            except Exception as ex:  # noqa: BLE001
                job.state, job.error = "erro", str(ex) or ex.__class__.__name__

    async def _one(self, job: Job, e: dict, root: Path):
        dest_dir = root / "models" / e["folder"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest, part = dest_dir / e["file"], dest_dir / (e["file"] + ".part")
        part.unlink(missing_ok=True)
        job.state = "baixando"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=90)) as c:
            meta = await probe_one(c, e["url"])
            if not meta["ok"]:
                raise RuntimeError(meta["error"])
            size = meta.get("size") or 0
            job.total = size
            if size and shutil.disk_usage(dest_dir).free < size + GB // 2:
                raise RuntimeError(f"pouco espaço em disco: precisa de {size / GB:.1f} GB em {dest_dir}")
            digest = hashlib.sha256()
            try:
                async with c.stream("GET", e["url"], follow_redirects=True, headers=hf.auth_headers(e["url"])) as r:
                    r.raise_for_status()
                    job.total = int(r.headers.get("content-length") or size or 0)
                    with open(part, "wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            if self._cancel:
                                raise _Cancelled()
                            f.write(chunk)
                            digest.update(chunk)
                            job.done += len(chunk)
                if meta.get("sha") and digest.hexdigest() != meta["sha"]:
                    raise RuntimeError("o arquivo baixado não confere com o original (corrompido); tente de novo")
                part.replace(dest)
            except BaseException:
                part.unlink(missing_ok=True)
                raise


# ---------------------------------------------------------------- serviço
class SetupService:
    def __init__(self, store: Store, pipe):
        self.store, self.pipe = store, pipe
        self.dl = Downloader()
        self._used: tuple[tuple, dict] | None = None

    def _used_by(self) -> dict[str, list[str]]:
        """used_by() relê todos os workflows; guarda o resultado enquanto os arquivos não mudarem."""
        sig = []
        for wid in self.store.manifests():
            for mode, _m in self.store.variants(wid):
                try:
                    sig.append((wid, mode, self.store.workflow_path(wid, mode).stat().st_mtime_ns))
                except (StoreError, OSError):
                    sig.append((wid, mode, 0))
        for lid, lo in self.store.loras().items():
            sig.append(("lora:" + lid, lo.get("file"), lo.get("source")))
        key = tuple(sig)
        if not self._used or self._used[0] != key:
            self._used = (key, used_by(self.store))
        return self._used[1]

    async def _env(self, refresh: bool = False) -> dict:
        s = self.store.settings()
        comfy = await self.pipe.comfy()
        ping = await comfy.ping()
        url = s["comfyui"]["url"]
        local = is_local(url)
        root, source, warn = find_root(s, ping.get("stats") or {}) if local else (None, "", "")
        oi = None
        if ping["ok"]:
            try:
                oi = await comfy.object_info(refresh=refresh)
            except ComfyError:
                oi = None
        return {"settings": s, "ping": ping, "url": url, "local": local, "root": root, "source": source,
                "warn": warn, "oi": oi}

    def _nodes(self, env: dict) -> dict:
        root, oi = env["root"], env["oi"]
        files = install_nodes.status(root / "custom_nodes") if root else {}
        items = []
        names = [p.name for p in install_nodes.items()]
        for name in names:
            cls = NODE_CLASSES.get(name)
            loaded = (cls in oi) if (oi is not None and cls) else None
            items.append({"name": name, "files": files.get(name), "loaded": loaded})
        need = [i for i in items if i["files"] in ("falta", "diferente") or (i["files"] is None and i["loaded"] is False)]
        restart = any(i["files"] == "ok" and i["loaded"] is False for i in items)
        return {"items": items, "missing": len(need), "restart_needed": restart}

    def _external(self, env: dict) -> list[dict]:
        """Nodes de outros autores que os workflows pedem (campo requires_nodes do registro).
        O PickFlow não os instala: mostra o que falta e onde pegar (ComfyUI Manager ou o GitHub do autor)."""
        oi, found = env["oi"], {}
        for wid, m in self.store.manifests().items():
            for r in m.get("requires_nodes") or []:
                cls = str(r.get("class") or "")
                if not cls:
                    continue
                e = found.setdefault(cls, {"class": cls, "pack": str(r.get("pack") or cls), "url": str(r.get("url") or ""),
                                           "used_by": [], "loaded": (cls in oi) if oi is not None else None})
                e["used_by"].append(m.get("title", wid))
        return list(found.values())

    def _files(self, env: dict) -> list[dict]:
        by = self._used_by()
        titles = {wid: m.get("title", wid) for wid, m in self.store.manifests().items()}
        titles.update({"lora:" + lid: "LoRA " + (lo.get("title") or lid) for lid, lo in self.store.loras().items()})
        out = []
        chosen = encoders.normalize((env["settings"].get("comfyui") or {}).get("text_encoder"))
        skipped = {base(e["file"]) for k, e in encoders.ENCODERS.items() if chosen != encoders.AUTO and k != chosen}
        for e in catalog(self.store):
            a, b = in_object_info(env["oi"], e["folder"], e["file"]), on_disk(env["root"], e["folder"], e["file"])
            known = [x for x in (a, b) if x is not None]
            installed = any(known) if known else None
            job = self.dl.jobs.get(e["id"])
            out.append({**e, "installed": installed, "job": asdict(job) if job else None,
                        "unused": base(e["file"]) in skipped,          # o outro codificador de texto foi escolhido em Ajustes
                        "used_by": [titles[w] for w in by.get(base(e["file"]), []) if w in titles]})
        return out

    async def overview(self, refresh: bool = False) -> dict:
        env = await self._env(refresh)
        nodes, files, external = self._nodes(env), self._files(env), self._external(env)
        root = env["root"]
        return {
            "comfy": {"url": env["url"], "ok": env["ping"]["ok"], "error": env["ping"].get("error"),
                      "local": env["local"], "root": str(root) if root else None, "root_source": env["source"],
                      "warn": env["warn"], "configured_path": env["settings"]["comfyui"].get("path") or ""},
            "can_install": bool(root),
            "links": LINKS, "nodes": nodes, "files": files, "external": external, "busy": self.dl.busy,
            "dismissed": bool((env["settings"].get("setup") or {}).get("dismissed")),
            "pending": self._pending(nodes, files, external),
        }

    @staticmethod
    def _pending(nodes: dict, files: list[dict], external: list[dict] | None = None) -> dict:
        return {"nodes": nodes["missing"] + sum(1 for e in external or [] if e["loaded"] is False),
                "loras": sum(1 for f in files if f["group"] == "lora" and f["installed"] is False),
                "models": sum(1 for f in files if f["group"] != "lora" and f["installed"] is False and not f.get("unused"))}

    async def summary(self) -> dict:
        """Resumo barato para o /api/status (usa o object_info que já está em memória)."""
        env = await self._env()
        nodes, files = self._nodes(env), self._files(env)
        p = self._pending(nodes, files, self._external(env))
        return {**p, "total": sum(p.values()), "can_install": bool(env["root"]),
                "dismissed": bool((env["settings"].get("setup") or {}).get("dismissed"))}

    async def probe(self) -> dict:
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as c:
            async def one(e):
                async with sem:
                    r = await probe_one(c, e["url"])
                return e["id"], {k: v for k, v in r.items() if k != "sha"}
            return dict(await asyncio.gather(*[one(e) for e in catalog(self.store)]))

    def set_path(self, path: str) -> dict:
        path = (path or "").strip()
        if path:
            p = normalize_root(Path(path))
            if not valid_root(p):
                raise StoreError("Essa pasta não parece ser a do ComfyUI: ela precisa ter as subpastas "
                                 "custom_nodes e models.")
            path = str(p)
        self.store.save_settings({"comfyui": {"path": path}})
        return {"path": path}

    def dismiss(self, value: bool = True):
        self.store.save_settings({"setup": {"dismissed": bool(value)}})

    async def install(self, ids: list[str], nodes: bool, overwrite_nodes: bool = False) -> dict:
        if self.dl.busy:
            raise StoreError("Já há downloads em andamento.")
        env = await self._env()
        root = env["root"]
        if not root:
            raise StoreError("Não achei a pasta do ComfyUI neste computador. Informe a pasta no assistente.")
        cat = {e["id"]: e for e in catalog(self.store)}
        unknown = [i for i in ids if i not in cat]
        if unknown:
            raise StoreError(f"Item desconhecido: {', '.join(unknown)}")
        result: dict[str, Any] = {"nodes": None}
        if nodes:
            result["nodes"] = install_nodes.install(root / "custom_nodes", overwrite_nodes)
        files = {f["id"]: f for f in self._files(env)}
        todo = [cat[i] for i in dict.fromkeys(ids) if files[i]["installed"] is not True]
        if todo:
            self.dl.start(todo, root)
        result["started"] = [e["id"] for e in todo]
        result["skipped"] = [i for i in ids if i not in result["started"]]
        return result

    async def download_lora(self, entry: dict) -> dict:
        """Baixa um .safetensors avulso (link do Hugging Face) para models/loras do ComfyUI."""
        if self.dl.busy:
            raise StoreError("Já há downloads em andamento.")
        env = await self._env()
        if not env["root"]:
            raise StoreError("Não achei a pasta do ComfyUI neste computador. Informe a pasta no assistente (aba Ajustes).")
        e = _clean({**entry, "folder": "loras"})
        if not e or not hf.is_hf(e["url"]):
            raise StoreError("Nome de arquivo ou link inválido para baixar.")
        if on_disk(env["root"], "loras", e["file"]):
            return {"started": False, "installed": True, "file": e["file"]}
        self.dl.start([e], env["root"])
        return {"started": True, "installed": False, "file": e["file"]}

    def jobs(self) -> dict:
        return {"busy": self.dl.busy, "jobs": [asdict(j) for j in self.dl.jobs.values()]}

    def cancel(self):
        self.dl.cancel()
