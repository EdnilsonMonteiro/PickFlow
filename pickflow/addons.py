"""Addons: extensões suas, em `addons/<nome>/` (a pasta fica fora do git).

Um addon pode, juntos ou separados:

  * trazer **recursos** no mesmo formato de `defaults/` e `custom/`: `workflows/`, `registry/`, `prompts/`,
    `loras.yaml` e `clef_prompt.yaml` (novos fluxos, prompts, LoRAs, casos de uso). A ordem é
    `custom/` > `addons/` > `defaults/`;
  * trazer **código Python** em `addon.py`, com `register(ctx)`: ganchos que mexem no pedido e no prompt, rotas
    HTTP próprias, **ações** (fluxos feitos em Python, sem ComfyUI) e acesso ao **LLM** configurado em Ajustes;
  * trazer **interface**: arquivos em `web/` (JS e CSS) carregados sozinhos, que usam `window.PickFlow`
    (telas novas, botões, telas de entrada do usuário, eventos da execução).

Veja `addons/README.md` e a skill `.claude/skills/pickflow-addons`.
"""
from __future__ import annotations

import base64
import importlib.util
import inspect
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import yaml

from . import llm as llmcfg
from fastapi import APIRouter

HOOKS = {
    "request": "texto do pedido, antes de o Clef ler (devolva o novo texto)",
    "prompt": "prompt final de cada passo, antes de ir ao ComfyUI (devolva o novo texto)",
    "plan": "depois de o Clef decidir o plano (observador)",
    "step_done": "depois de cada passo (observador)",
    "run_done": "ao fim da execução (observador)",
}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


class AddonError(Exception):
    pass


# ------------------------------------------------------------------ LLM
class LLM:
    """O LLM que a pessoa configurou em Ajustes (qualquer servidor compatível com a API de chat da OpenAI:
    llama-server, Ollama, LM Studio...). Serve para os addons e para o redator de prompts.

        text = await ctx.llm.ask("Traduza para inglês: gato", system="Responda só com a tradução.")
        data = await ctx.llm.ask_json("Liste 3 cores", system="Responda em JSON: {\\"cores\\": []}")
        text = await ctx.llm.ask("O que há nesta foto?", images=[png_bytes])      # se o modelo enxerga
    """

    def __init__(self, get_cfg: Callable[[], dict], override: dict | None = None):
        self._get_cfg = get_cfg
        self._override = override or {}

    @property
    def cfg(self) -> dict:
        return {**llmcfg.resolve(self._get_cfg()), **self._override}

    @property
    def configured(self) -> bool:
        c = self.cfg
        return bool(c.get("url"))

    @property
    def enabled(self) -> bool:
        """True quando o redator está ligado em Ajustes (o addon pode usar o LLM mesmo assim, se `url` existir)."""
        return bool(self.cfg.get("enabled"))

    async def chat(self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 600,
                   model: str | None = None, json_mode: bool = False) -> str:
        c = self.cfg
        if model:
            c = {**c, "model": model}
        try:
            return await llmcfg.chat(c, messages, temperature=temperature, max_tokens=max(max_tokens, 1500) if "api.openai.com" in str(c.get("url")) else max_tokens,
                                     json_mode=json_mode)
        except llmcfg.LLMError as e:
            raise AddonError(str(e)) from e

    async def ask(self, user: str, *, system: str | None = None, images: list[bytes] | None = None, **kw) -> str:
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b).decode()}}
                for b in images]
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        return await self.chat(msgs, **kw)

    async def ask_json(self, user: str, *, system: str | None = None, **kw) -> Any:
        text = await self.ask(user, system=system, json_mode=True, **kw)
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        try:
            return json.loads(m.group(0) if m else text)
        except ValueError as e:
            raise AddonError(f"O LLM não devolveu JSON válido: {text[:200]}") from e


# --------------------------------------------------------------- contexto
@dataclass
class ActionCall:
    """O que uma ação (fluxo em Python) recebe. `images` tem o PNG de cada entrada do fluxo, pelo nome da entrada;
    `drawings` o que o usuário desenhou (caixas, posições, máscara); `status(texto)` escreve na conversa;
    `ask(tipo, **dados)` abre uma tela do addon (PickFlow.registerInput) e devolve a resposta."""
    request: str
    images: dict[str, bytes]
    drawings: dict[str, Any]
    vars: dict[str, Any]
    prompt: str
    llm: LLM
    settings: dict
    status: Callable[[str], Awaitable[None]]
    ask: Callable[..., Awaitable[Any]] | None = None     # `await call.ask("meu_tipo", title="...", **dados)` abre a tela registrada no JS
    data: dict = field(default_factory=dict)

    def pil(self, key: str = "image_1"):
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(self.images[key]))
        im.load()
        return im


class AddonContext:
    """O que `register(ctx)` recebe: tudo que um addon pode usar."""

    def __init__(self, mgr: "AddonManager", name: str, path: Path, meta: dict):
        self.mgr, self.name, self.dir, self.meta = mgr, name, path, meta
        self.store = mgr.store
        self.app = mgr.app
        self.router = APIRouter()
        self.hooks: dict[str, list[Callable]] = {}
        self.actions: dict[str, Callable] = {}
        self.llm = LLM(lambda: mgr.store.settings().get("writer", {}), meta.get("llm") or {})
        self.config: dict = {**(meta.get("config") or {}),
                             **(mgr.store.settings().get("addons", {}).get("config", {}).get(name) or {})}

    # -- ganchos
    def hook(self, event: str):
        """Decorador: `@ctx.hook("prompt")`. Pode ser `def` ou `async def`. Eventos: ver `HOOKS`."""
        if event not in HOOKS:
            raise AddonError(f"Gancho desconhecido '{event}'. Use um de: {', '.join(HOOKS)}.")

        def deco(fn):
            self.hooks.setdefault(event, []).append(fn)
            return fn
        return deco

    def action(self, name: str):
        """Decorador: o fluxo `kind: action` com `action: <este nome>` no registry roda esta função
        (`def f(call: ActionCall)` ou `async def`) e devolve imagens (bytes PNG, PIL.Image ou lista deles)."""
        def deco(fn):
            self.actions[name] = fn
            return fn
        return deco

    # -- dados do addon (sobrevivem ao reiniciar)
    @property
    def data_path(self) -> Path:
        return self.store.data_dir / "addons" / f"{self.name}.json"

    def load_data(self) -> dict:
        try:
            return json.loads(self.data_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_data(self, data: dict) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def log(self, msg: str) -> None:
        print(f"[addon {self.name}] {msg}", file=sys.stderr)


@dataclass
class Addon:
    name: str
    path: Path
    meta: dict
    enabled: bool = True
    error: str = ""
    ctx: AddonContext | None = None

    def info(self) -> dict:
        c = self.ctx
        web = self.meta.get("web") or {}
        return {"name": self.name, "title": self.meta.get("title") or self.name,
                "description": self.meta.get("description", ""), "version": str(self.meta.get("version", "")),
                "enabled": self.enabled, "error": self.error,
                "has_code": (self.path / "addon.py").is_file(),
                "resources": [k for k in ("workflows", "registry", "prompts") if (self.path / k).is_dir()]
                             + [f for f in ("loras.yaml", "clef_prompt.yaml") if (self.path / f).is_file()],
                "hooks": sorted(c.hooks) if c else [], "actions": sorted(c.actions) if c else [],
                "scripts": [f"/addons/{self.name}/web/{f}" for f in _web_files(self, "scripts", ".js")],
                "styles": [f"/addons/{self.name}/web/{f}" for f in _web_files(self, "styles", ".css")]} | (
            {"web": bool(web) or (self.path / "web").is_dir()})


def _web_files(a: Addon, key: str, ext: str) -> list[str]:
    """Arquivos de interface do addon: os listados em `web.scripts`/`web.styles`, ou todos os `web/*.js|css`."""
    listed = (a.meta.get("web") or {}).get(key)
    if listed:
        return [str(f) for f in listed if str(f).endswith(ext) and ".." not in str(f)]
    d = a.path / "web"
    if not d.is_dir():
        return []
    names = sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix == ext)
    return ["main.js"] + [n for n in names if n != "main.js"] if "main.js" in names and ext == ".js" else names


class AddonManager:
    def __init__(self, store, app=None):
        self.store = store
        self.app = app
        self.addons: dict[str, Addon] = {}
        store.action_checker = lambda ref: self.action(ref) is not None

    # -- descoberta
    @property
    def dir(self) -> Path:
        return self.store.addons_dir

    def discover(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(p for p in self.dir.iterdir()
                      if p.is_dir() and not p.name.startswith((".", "_")) and NAME_RE.match(p.name))

    def discover_all(self) -> list[Addon]:
        return list(self.addons.values())

    def load_all(self) -> None:
        disabled = set(self.store.settings().get("addons", {}).get("disabled") or [])
        self.addons = {}
        for p in self.discover():
            meta = {}
            try:
                meta = yaml.safe_load((p / "addon.yaml").read_text(encoding="utf-8")) or {} if (p / "addon.yaml").is_file() else {}
            except Exception as e:  # noqa: BLE001
                self.addons[p.name] = Addon(p.name, p, {}, True, f"addon.yaml inválido: {e}")
                continue
            a = Addon(p.name, p, meta, p.name not in disabled)
            self.addons[p.name] = a
            if a.enabled and (p / "addon.py").is_file():
                self._load_code(a)

    def _load_code(self, a: Addon) -> None:
        mod_name = f"pickflow_addon_{re.sub(r'[^a-z0-9_]', '_', a.name)}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, a.path / "addon.py",
                                                          submodule_search_locations=[str(a.path)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)                    # type: ignore[union-attr]
            ctx = AddonContext(self, a.name, a.path, a.meta)
            reg = getattr(mod, "register", None)
            if reg is None:
                raise AddonError("addon.py precisa definir register(ctx).")
            reg(ctx)
            a.ctx = ctx
            if self.app is not None and any(True for _ in ctx.router.routes):
                self.app.include_router(ctx.router, prefix=f"/api/addons/{a.name}")
        except Exception as e:  # noqa: BLE001 - um addon quebrado não derruba o PickFlow
            a.error = f"{type(e).__name__}: {e}"
            a.ctx = None
            print(f"[addon {a.name}] não carregou:\n{traceback.format_exc()}", file=sys.stderr)

    # -- consulta
    def status(self) -> list[dict]:
        return [a.info() for a in self.addons.values()]

    def web_file(self, name: str, rel: str) -> Path | None:
        a = self.addons.get(name)
        if not a or not a.enabled:
            return None
        base = (a.path / "web").resolve()
        p = (base / rel).resolve()
        return p if base in p.parents and p.is_file() else None

    def action(self, ref: str) -> Callable | None:
        """`"<addon>.<nome>"` ou só `"<nome>"` (a primeira que achar)."""
        if "." in ref:
            an, _, nm = ref.partition(".")
            a = self.addons.get(an)
            return a.ctx.actions.get(nm) if a and a.ctx else None
        for a in self.addons.values():
            if a.ctx and ref in a.ctx.actions:
                return a.ctx.actions[ref]
        return None

    # -- ganchos
    async def _call(self, a: Addon, fn: Callable, *args):
        r = fn(*args)
        return await r if inspect.isawaitable(r) else r

    async def run_hook(self, event: str, value: Any, info: dict | None = None, *, chain: bool = False) -> Any:
        """Chama os ganchos `event` de todos os addons ligados. Com `chain`, o retorno de um (se for do mesmo
        tipo de `value`) vira a entrada do próximo. Erro num gancho é registrado e ignorado."""
        for a in self.addons.values():
            if not a.enabled or not a.ctx:
                continue
            for fn in a.ctx.hooks.get(event, []):
                try:
                    r = await self._call(a, fn, value, info or {})
                except Exception as e:  # noqa: BLE001
                    a.error = f"gancho '{event}': {type(e).__name__}: {e}"
                    print(f"[addon {a.name}] erro no gancho {event}:\n{traceback.format_exc()}", file=sys.stderr)
                    continue
                if chain and r is not None and isinstance(r, type(value)):
                    value = r
        return value

    async def prompt(self, text: str, info: dict) -> str:
        return await self.run_hook("prompt", text, info, chain=True)

    async def request(self, text: str, info: dict) -> str:
        return await self.run_hook("request", text, info, chain=True)

    async def observe(self, event: str, value: Any, info: dict | None = None) -> None:
        await self.run_hook(event, value, info)


def new_addon(addons_dir: Path, name: str, template: Path) -> Path:
    """Cria `addons/<name>/` a partir do modelo (`defaults/addon_template`)."""
    if not NAME_RE.match(name):
        raise AddonError("Nome de addon inválido: use letras minúsculas, números, _ ou -.")
    dest = addons_dir / name
    if dest.exists():
        raise AddonError(f"Já existe addons/{name}.")
    import shutil
    ident = name.replace("-", "_")
    shutil.copytree(template, dest)
    for p in sorted(dest.rglob("*"), key=lambda q: -len(q.parts)):          # nomes de arquivo primeiro (do mais fundo)
        if "__ID__" in p.name:
            p.rename(p.with_name(p.name.replace("__ID__", ident)))
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".yaml", ".js", ".md", ".css"):
            p.write_text(p.read_text(encoding="utf-8").replace("__NAME__", name).replace("__ID__", ident), encoding="utf-8")
    return dest
