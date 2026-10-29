"""Modelos de imagem (famílias) e o que vale para cada um.

O PickFlow nasceu para o Qwen-Image 2.1 e esse continua sendo o padrão. Para suportar outro modelo sem mexer na
interface nem em nenhum arquivo existente, tudo carrega uma **tag** `models` (lista de ids de família) e a ausência
dela quer dizer `[qwen21]`:

- **registro** (`registry/<id>.yaml`): `models: [moss_image]`; `implements: edit` diz que este workflow faz o papel do
  `edit` para o outro modelo (um caso de uso que cita `edit` roda o equivalente do modelo escolhido);
- **prompt** (preset em `prompts/<id>.yaml`), **LoRA** e **caso de uso**: `models: [qwen21, moss_image]` para valer em
  mais de um. `"*"` vale para qualquer modelo (recortes, 3D e outros fluxos que não dependem do modelo de imagem).

Quem escolhe o modelo é o chat (ou `settings.model`). `ModelView` é uma visão do `Store` para um modelo: o roteador
só enxerga o que serve a ele. A lista de modelos que a tela oferece (`available`) só traz famílias que têm workflow
no PickFlow E cujo arquivo existe na pasta do ComfyUI; o padrão aparece sempre.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

DEFAULT = "qwen21"
ANY = "*"
#: (classe do carregador, campo) no `object_info` do ComfyUI que lista os modelos de imagem instalados
LOADERS = (("UNETLoader", "unet_name"), ("CheckpointLoaderSimple", "ckpt_name"))
#: subpastas de `models/` com os modelos de imagem (quando só se conhece a pasta do ComfyUI)
FOLDERS = ("diffusion_models", "unet", "checkpoints")


def tags(item: dict | None, default: list[str] | None = None) -> list[str]:
    """Ids de família de um registro, prompt, LoRA ou caso de uso. Sem tag vale `default` (o padrão do projeto)."""
    raw = (item or {}).get("models")
    if raw is None:
        raw = (item or {}).get("model")
    if isinstance(raw, str):
        raw = [raw]
    out = [str(x).strip() for x in (raw or []) if str(x).strip()]
    return out or list(default if default is not None else [DEFAULT])


def matches(item_tags: list[str], model: str) -> bool:
    return ANY in item_tags or model in item_tags


def normalize(model: str | None, fallback: str = DEFAULT) -> str:
    m = str(model or "").strip()
    return m if m and all(c.isalnum() or c == "_" for c in m) else fallback


# ------------------------------------------------------------------ catálogo
def load(store) -> dict[str, dict]:
    """Famílias conhecidas: `defaults/families.yaml`, addons e `custom/families.yaml` (mesmo id = o seu vale mais).
    Uma tag que ninguém declarou vira uma família sem padrão de arquivo (ver `available`)."""
    out: dict[str, dict] = {}
    layers = [store.defaults_dir] + list(store.addon_dirs()) + [store.custom_dir]
    for d in layers:
        for e in (store._read(Path(d) / "families.yaml").get("families") or []):
            if isinstance(e, dict) and e.get("id"):
                match = e.get("match") or []
                match = [match] if isinstance(match, str) else list(match)
                out[str(e["id"])] = {"id": str(e["id"]), "title": str(e.get("title") or e["id"]),
                                     "match": [str(x) for x in match], "default": bool(e.get("default"))}
    out.setdefault(DEFAULT, {"id": DEFAULT, "title": "Qwen-Image 2.1", "match": [], "default": True})
    return out


# ------------------------------------------------------------------ detecção
def model_files(oi: dict | None, root: Path | None) -> set[str] | None:
    """Nomes (minúsculos, sem pasta) dos modelos de imagem instalados, lidos do ComfyUI em execução e da pasta dele.
    `None` = não deu para saber (ComfyUI desligado e pasta desconhecida)."""
    names: set[str] = set()
    known = False
    for cls, key in LOADERS:
        spec = (((oi or {}).get(cls) or {}).get("input") or {}).get("required", {}).get(key)
        if spec and isinstance(spec[0], list):
            known = True
            names.update(Path(str(o).replace("\\", "/")).name.lower() for o in spec[0])
    if root:
        for folder in FOLDERS:
            d = Path(root) / "models" / folder
            if d.is_dir():
                known = True
                names.update(p.name.lower() for p in d.rglob("*") if p.is_file())
    return names if known else None


def installed(family: dict, files: set[str] | None) -> bool | None:
    """A família está instalada? O padrão sempre; sem padrão de arquivo (`match`), vale o que o registro declarou."""
    if family.get("default") or not family.get("match"):
        return True
    if files is None:
        return None
    return any(fnmatch.fnmatch(f, pat.lower()) for f in files for pat in family["match"])


def available(store, files: set[str] | None) -> list[dict]:
    """Os modelos que o chat pode oferecer: o padrão, mais as famílias com workflow ligado no PickFlow cujo arquivo
    está no ComfyUI. Não saber (`files` None) esconde as outras: melhor não oferecer do que oferecer o que não roda."""
    fams = load(store)
    count: dict[str, int] = {}
    for m in store.enabled_workflows().values():
        for t in tags(m):
            if t != ANY:
                count[t] = count.get(t, 0) + 1
    out = []
    for fid in sorted(set(fams) | set(count), key=lambda k: (not fams.get(k, {}).get("default"), fams.get(k, {}).get("title", k).lower())):
        fam = fams.get(fid) or {"id": fid, "title": fid, "match": [], "default": False}
        if not fam["default"] and not count.get(fid):
            continue
        if installed(fam, files) is not True:
            continue
        out.append({"id": fid, "title": fam["title"], "default": fam["default"], "workflows": count.get(fid, 0)})
    return out


# ------------------------------------------------------------------ a visão
def resolve(manifests: dict[str, dict], wid: str, model: str) -> str | None:
    """O workflow que faz o papel de `wid` para o modelo: ele mesmo, se for do modelo, ou o que declara `implements`."""
    m = manifests.get(wid)
    if m is not None and matches(tags(m), model):
        return wid
    for k, mm in manifests.items():
        if mm.get("implements") == wid and matches(tags(mm), model):
            return k
    return None


class ModelView:
    """O `Store` visto por um modelo. Tudo o que não é filtrado (settings, manifestos, arquivos) vem do Store de verdade."""

    def __init__(self, store, model: str):
        self._s = store
        self.model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._s, name)

    def resolve(self, wid: str) -> str | None:
        return resolve(self._s.manifests(), wid, self.model)

    def enabled_workflows(self) -> dict[str, dict]:
        return {w: m for w, m in self._s.enabled_workflows().items() if matches(tags(m), self.model)}

    def enabled_recipes(self) -> list[dict]:
        usable = self.enabled_workflows()
        out = []
        for r in self._s.clef_config()["recipes"]:
            if not r.get("enabled", True) or not matches(tags(r), self.model):
                continue
            if all(self.resolve(s.get("workflow")) in usable for s in r.get("steps", [])):
                out.append(r)
        return out

    def loras(self) -> dict[str, dict]:
        return {k: v for k, v in self._s.loras().items() if matches(tags(v), self.model)}

    def prompts(self, wid: str) -> dict:
        """Os prompts do workflow para este modelo: os do arquivo dele (sem tag valem para o modelo do próprio
        workflow) mais os do workflow que ele `implements` que declaram este modelo."""
        data = self._s.prompts(wid)
        mans = self._s.manifests()
        m = mans.get(wid) or {}
        own = tags(m)
        presets = [p for p in data["presets"] if matches(tags(p, own), self.model)]
        target = m.get("implements")
        if target and target != wid and target in mans:
            have = {p["id"] for p in presets}
            presets += [p for p in self._s.prompts(target)["presets"]
                        if p.get("models") and p["id"] not in have and matches(tags(p), self.model)]
        if not presets:                                  # nada marcado para este modelo: melhor o arquivo inteiro do que travar
            return data
        default = data.get("default")
        return {**data, "presets": presets, "default": default if any(p["id"] == default for p in presets) else presets[0]["id"]}
