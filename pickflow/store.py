"""Leitura e escrita da configuração em disco (YAML), sempre com escrita atômica.

Duas camadas, de propósito:

  defaults/   o que vem no repositório (rastreado pelo git)  — nunca é escrito pelo app
  custom/     o que é seu (ignorado pelo git)                 — tudo que você edita na interface vai para cá

Dentro de cada camada há as mesmas coisas:
  workflows/          arquivos .json do ComfyUI
  registry/<id>.yaml  ligação técnica do workflow (nós, imagens, saída); raramente muda
  prompts/<id>.yaml   biblioteca de prompts daquele workflow
  clef_prompt.yaml    o "prompt do Clef": instruções, descrição de cada workflow, quais prompts
                      ele pode usar e os casos de uso (receitas)

O que está em custom/ vale mais do que o que está em defaults/ (mesmo id = o seu substitui o padrão).
Ao salvar pela interface só vai para custom/ o que difere do padrão; assim atualizar o repositório
continua trazendo as novidades dos padrões que você não mexeu, e "restaurar padrão" é só apagar o seu.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,60}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": "qwen21",          # modelo de imagem usado quando o chat não escolhe outro (veja families.py)
    "server": {"host": "127.0.0.1", "port": 7860},
    "comfyui": {"url": "http://127.0.0.1:8188", "upload_subfolder": "pickflow", "timeout_s": 1800,
                "free_vram_before_clef": False, "free_vram_after_run": False, "path": "",
                "max_input_mp": 2.0, "text_encoder": "auto"},     # auto | fp8 | int8 (veja encoders.py)
    "setup": {"dismissed": False},
    "ui": {"language": "en"},
    "addons": {"disabled": [], "config": {}},
    "agent": {"enabled": False, "confirm": True, "gap_min": 0.6, "max_rounds": 8, "max_clef_calls": 6,
              "max_steps": 4, "max_trial": 5, "max_generated": 20},
    "clef": {"mode": "systemone", "url": "http://127.0.0.1:8321", "path": "/v1/systemone",
             "model": "clef", "timeout_s": 120, "max_images": 4, "max_steps": 3,
             "ask_below": 0.45, "refine_below": 0.6, "then_min": 0.5, "then_none_weight": 1.5,
             "engine_main_weight": 1.5, "engine_min": 0.5, "analyze_images": True, "api_key": "",
             "unload_policy": "auto", "need_vram_gb": 16, "need_ram_gb": 10, "margin_gb": 2,
             "make_room_for_clef": True, "clef_vram_gb": 20, "max_loras": 2, "device": "cpu",
             # aprendizado com o feedback do usuário (data/feedback.jsonl): exemplos parecidos, correção da
             # probabilidade e a pergunta na tela quando o Clef fica em dúvida entre dois prompts
             "learn": {"enabled": True, "weight": 0.5, "examples": 3, "examples_per_flow": 1,
                       "min_similarity": 0.55, "ask_preset_below": 0.5}},
    "writer": {"enabled": False, "url": "", "model": "", "timeout_s": 120, "providers": [], "active": {},
               "mode": "refine",
               "system": ("You rewrite a user's request into one short, precise English instruction for the "
                          "Qwen-Image-2.1 editing model. Keep names, numbers and positions. Output only the "
                          "instruction, no quotes."),
               "refine_system": ("You write the final prompt for the Qwen-Image-2.1 image editing model. You get a DRAFT "
                                 "(built from fixed instructions that the selected LoRAs need, plus the user's raw "
                                 "request), the user's request, and the list of active LoRAs. Rewrite the draft in English "
                                 "so it carries out the user's request. Keep every fixed LoRA instruction (such as "
                                 "'body_swap: ...' lines and the <image1>/<image2> references) unchanged, and fold the user's "
                                 "wishes in where they belong. Keep names, numbers and positions. Do not invent details. "
                                 "Output only the final prompt, no quotes, no explanations.")},
}


class _Dumper(yaml.SafeDumper):
    pass


def _str_repr(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_repr)


def dump_yaml(data: Any, header: str = "") -> str:
    body = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=110)
    return (header.rstrip() + "\n\n" if header else "") + body


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class StoreError(ValueError):
    pass


def norm_preset(p: dict) -> dict:
    """Prompt da biblioteca: id, nome, quando usar, texto e (opcionais) LoRAs fixas, ajustes do grafo e
    mínimo de imagens. Os campos opcionais só aparecem quando usados, para os arquivos ficarem enxutos."""
    out = {"id": str(p["id"]), "name": str(p.get("name") or p["id"]), "when": str(p.get("when") or ""),
           "text": str(p.get("text") or ""), "rewrite": bool(p.get("rewrite", False))}
    if p.get("loras"):
        items = []
        for it in p["loras"]:
            if isinstance(it, str):
                items.append(it)
            elif isinstance(it, dict) and it.get("id"):
                items.append({k: it[k] for k in ("id", "strength", "strength_clip", "mode") if k in it and it[k] is not None})
        out["loras"] = items
    if p.get("settings"):
        out["settings"] = dict(p["settings"])
    if p.get("needs"):
        out["needs"] = int(p["needs"])
    if p.get("requires"):
        out["requires"] = [str(x) for x in p["requires"]]      # LoRAs que precisam estar ativas para o prompt existir
    if p.get("auto_loras") is False:
        out["auto_loras"] = False         # o Clef não acrescenta LoRAs a este prompt (ex.: copiar estilo não é trocar corpo)
    if isinstance(p.get("verify"), dict) and p["verify"]:
        out["verify"] = copy.deepcopy(p["verify"])   # conferência do resultado (ex.: o objeto mesmo saiu do lugar de origem)
    if p.get("slots") == "order":
        out["slots"] = "order"            # as imagens entram nas entradas na ordem em que foram anexadas
    if isinstance(p.get("vars"), list) and p["vars"]:
        out["vars"] = copy.deepcopy([v for v in p["vars"] if isinstance(v, dict) and v.get("key")])
    if p.get("models"):
        out["models"] = [str(x) for x in (p["models"] if isinstance(p["models"], list) else [p["models"]])]
    return out


def norm_lora(d: dict) -> dict:
    lid = str(d.get("id", "")).strip()
    if not ID_RE.match(lid):
        raise StoreError(f"Id de LoRA inválido: '{lid}'. Use letras minúsculas, números e _.")
    out: dict[str, Any] = {"id": lid, "title": str(d.get("title") or lid), "file": str(d.get("file") or "").strip(),
                           "description": str(d.get("description") or ""),
                           "strength": float(d.get("strength", 1.0)), "auto": bool(d.get("auto", False))}
    if not out["file"] and not d.get("disabled"):
        raise StoreError("Informe o arquivo da LoRA (o nome como aparece na pasta models/loras do ComfyUI).")
    if d.get("strength_clip") is not None:
        out["strength_clip"] = float(d["strength_clip"])
    if isinstance(d.get("prompt"), dict):        # um texto por modo (turbo, quality...)
        pm = {str(k): str(v).strip() for k, v in d["prompt"].items() if str(v or "").strip()}
        if pm:
            out["prompt"] = pm
    elif str(d.get("prompt") or "").strip():
        out["prompt"] = str(d["prompt"]).strip()
    if d.get("adjust"):
        out["adjust"] = {str(k): float(v) for k, v in dict(d["adjust"]).items()}
    if d.get("settings"):
        out["settings"] = dict(d["settings"])
    if d.get("needs"):
        out["needs"] = int(d["needs"])
    if d.get("group"):
        out["group"] = str(d["group"])
    if d.get("conflicts"):
        out["conflicts"] = [str(x) for x in (d["conflicts"] if isinstance(d["conflicts"], list) else [d["conflicts"]])]
    if d.get("catalog"):
        out["catalog"] = str(d["catalog"])
    if d.get("models"):
        out["models"] = [str(x) for x in (d["models"] if isinstance(d["models"], list) else [d["models"]])]
    if d.get("disabled"):
        out["disabled"] = True
    return out


def apply_mode(m: dict, mode: str | None) -> dict:
    """Manifesto com um modo aplicado. Os campos do modo (file, outputs, lora_chain, set, drop...) substituem
    os do manifesto base. Sem `mode`, vale o primeiro modo (o padrão). Sem `modes`, devolve o próprio manifesto."""
    modes = m.get("modes") or {}
    if not modes:
        return m
    mode = mode if mode in modes else next(iter(modes))
    out = {k: v for k, v in (m.get("_base") or m).items() if k not in ("mode", "_base")}
    out["_base"] = copy.deepcopy(out)
    patch = copy.deepcopy(modes[mode] or {})
    if isinstance(patch.get("knobs"), dict) and isinstance(out.get("knobs"), dict):
        patch["knobs"] = {**out["knobs"], **patch["knobs"]}      # ajustes do modo somam aos do workflow
    patch.pop("title", None)             # o título do modo é só o rótulo do modo (Rápido, Qualidade); o do workflow não muda
    out.update(patch)
    out["modes"] = modes
    out["mode"] = mode
    out["mode_title"] = (modes[mode] or {}).get("title") or mode
    return out


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.defaults_dir = self.root / "defaults"
        self.custom_dir = self.root / "custom"
        self.data_dir = self.root / "data"
        self.addons_dir = self.root / "addons"
        self.action_checker = None           # o gerenciador de addons põe aqui uma função(ref) -> existe?

    # --------------------------------------------------------------- layout
    def addon_dirs(self) -> list[Path]:
        """Pastas dos addons ligados (`addons/<nome>/`), em ordem alfabética; quem vem depois vale mais."""
        if not self.addons_dir.is_dir():
            return []
        off = set(self.settings().get("addons", {}).get("disabled") or [])
        return sorted(p for p in self.addons_dir.iterdir()
                      if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in off)

    def layers(self, kind: str) -> list[Path]:
        """Pastas de um tipo (workflows, registry, prompts), da mais forte para a mais fraca:
        custom/ (suas edições), addons/<nome>/ (do último para o primeiro) e defaults/ (o que vem no repositório)."""
        return [self.custom_dir / kind, *(d / kind for d in reversed(self.addon_dirs())), self.defaults_dir / kind]

    def ensure_custom_dirs(self):
        for kind in ("workflows", "registry", "prompts"):
            (self.custom_dir / kind).mkdir(parents=True, exist_ok=True)

    def _find(self, kind: str, name: str) -> Path | None:
        for d in self.layers(kind):
            p = d / name
            if p.is_file():
                try:
                    p.resolve().relative_to(d.resolve())
                except ValueError:
                    continue        # nome tentando sair da pasta (..)
                return p
        return None

    @staticmethod
    def _read(p: Path | None) -> dict:
        if not p or not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # ------------------------------------------------------------ settings
    @property
    def settings_path(self) -> Path:
        return self.custom_dir / "settings.yaml"

    def settings(self) -> dict:
        return deep_merge(DEFAULT_SETTINGS, self._read(self.settings_path))

    def save_settings(self, patch: dict) -> dict:
        new = deep_merge(self.settings(), patch)
        atomic_write(self.settings_path,
                     dump_yaml(new, "# Configurações do PickFlow (também editáveis na interface)."))
        return new

    # ----------------------------------------------------------- manifests
    def manifests(self) -> dict[str, dict]:
        files = [(layer, p) for layer, d in (
            [("default", self.defaults_dir / "registry")]
            + [(f"addon:{a.name}", a / "registry") for a in self.addon_dirs()]
            + [("custom", self.custom_dir / "registry")])
            for p in sorted(d.glob("*.yaml"))]
        sig = tuple((layer, p.name, p.stat().st_mtime_ns) for layer, p in files)
        if getattr(self, "_mf_sig", None) != sig:
            out = {}
            for layer, p in files:                # custom vem depois e substitui o padrão do mesmo id
                m = self._read(p)
                if m.get("id"):
                    m["source"] = layer
                    out[m["id"]] = apply_mode(m, None)
            self._mf_sig, self._mf = sig, out
        return copy.deepcopy(self._mf)

    def manifest(self, wid: str, mode: str | None = None) -> dict:
        m = self.manifests().get(wid)
        if not m:
            raise StoreError(f"Workflow desconhecido: {wid}")
        if mode and mode != m.get("mode"):
            if mode not in (m.get("modes") or {}):
                raise StoreError(f"O workflow '{wid}' não tem o modo '{mode}'.")
            return apply_mode(m, mode)
        return m

    def variants(self, wid: str) -> list[tuple[str | None, dict]]:
        """Cada modo do workflow já aplicado (um workflow sem modos devolve uma só variante)."""
        m = self.manifest(wid)
        modes = list((m.get("modes") or {}).keys())
        return [(md, self.manifest(wid, md)) for md in modes] or [(None, m)]

    def workflow_path(self, wid: str, mode: str | None = None) -> Path:
        m = self.manifest(wid, mode)
        if m.get("kind") == "action":
            raise StoreError(f"'{wid}' é uma ação de addon (Python): não tem arquivo de workflow.")
        path = self._find("registry", f"{wid}.api.json") or self._find("workflows", str(m.get("file", "")))
        if not path:
            raise StoreError(f"Arquivo do workflow não encontrado: {m.get('file')} "
                             f"(procurei em custom/workflows e defaults/workflows)")
        return path

    def workflow_json(self, wid: str, mode: str | None = None) -> dict:
        return json.loads(self.workflow_path(wid, mode).read_text(encoding="utf-8"))

    # ------------------------------------------------------------- prompts
    def _prompts_raw(self, wid: str) -> dict:
        data = self._read(self._find("prompts", f"{wid}.yaml"))
        data.setdefault("presets", [])
        if not data["presets"] and (self.manifests().get(wid) or {}).get("kind") == "action":      # fluxo em Python: só o pedido
            m = self.manifests()[wid]
            data["presets"] = [norm_preset({"id": "padrao", "name": m.get("title", wid), "text": "{request}",
                                            "when": m.get("description") or m.get("title", wid), "rewrite": False})]
        if not data.get("default") and data["presets"]:
            data["default"] = data["presets"][0]["id"]
        return data

    def prompts(self, wid: str) -> dict:
        return self._prompts_raw(wid)

    def prompts_customized(self, wid: str) -> bool:
        return (self.custom_dir / "prompts" / f"{wid}.yaml").exists()

    def save_prompts(self, wid: str, data: dict) -> dict:
        self.manifest(wid)
        presets = []
        seen = set()
        for pr in data.get("presets", []):
            pid = str(pr.get("id", "")).strip()
            if not ID_RE.match(pid):
                raise StoreError(f"Id de prompt inválido: '{pid}'. Use letras minúsculas, números e _.")
            if pid in seen:
                raise StoreError(f"Id de prompt repetido: {pid}")
            seen.add(pid)
            presets.append(norm_preset({**pr, "id": pid}))
        if not presets:
            raise StoreError("Cada workflow precisa de pelo menos um prompt.")
        default = data.get("default") if data.get("default") in seen else presets[0]["id"]
        out = {"default": default, "presets": presets}
        target = self.custom_dir / "prompts" / f"{wid}.yaml"
        base = self._read(self.defaults_dir / "prompts" / f"{wid}.yaml")
        if base and self._norm_prompts(base) == out:
            target.unlink(missing_ok=True)          # igual ao padrão: não precisa de cópia sua
        else:
            atomic_write(target, dump_yaml(out, f"# Seus prompts do workflow '{wid}'. "
                                                f"Use {{request}} onde o pedido do usuário entra."))
        return out

    @staticmethod
    def _norm_prompts(d: dict) -> dict:
        presets = [norm_preset(p) for p in d.get("presets", [])]
        default = d.get("default") if any(p["id"] == d.get("default") for p in presets) else (
            presets[0]["id"] if presets else None)
        return {"default": default, "presets": presets}

    # ---------------------------------------------------------- LoRAs
    def loras(self) -> dict[str, dict]:
        """Biblioteca de LoRAs: defaults/loras.yaml e, por cima (mesmo id = o seu vale mais), custom/loras.yaml.
        Uma LoRA sua com `disabled: true` esconde a padrão do mesmo id."""
        out: dict[str, dict] = {}
        for layer, d in ([("default", self.defaults_dir)] + [(f"addon:{a.name}", a) for a in self.addon_dirs()]
                         + [("custom", self.custom_dir)]):
            for e in (self._read(d / "loras.yaml").get("loras") or []):
                if not isinstance(e, dict) or not e.get("id"):
                    continue
                if e.get("disabled"):
                    out.pop(e["id"], None)
                    continue
                out[e["id"]] = {**norm_lora(e), "source": layer}
        return out

    def _custom_loras(self) -> list[dict]:
        return [e for e in (self._read(self.custom_dir / "loras.yaml").get("loras") or []) if isinstance(e, dict)]

    def _write_custom_loras(self, items: list[dict]):
        p = self.custom_dir / "loras.yaml"
        if not items:
            p.unlink(missing_ok=True)
            return
        atomic_write(p, dump_yaml({"loras": items}, "# Suas LoRAs (descrição, força, prompt padrão). Mesmo id de defaults/loras.yaml = a sua vale mais.\n"
                                                    "# Editável aqui ou na aba LoRAs da interface."))

    def save_lora(self, data: dict) -> dict:
        item = norm_lora(data)
        base = next((norm_lora(e) for e in (self._read(self.defaults_dir / "loras.yaml").get("loras") or [])
                     if e.get("id") == item["id"]), None)
        mine = [e for e in self._custom_loras() if e.get("id") != item["id"]]
        if base != item:                    # igual ao padrão: não precisa de cópia sua
            mine.append(item)
        self._write_custom_loras(mine)
        return self.loras().get(item["id"], item)

    def delete_lora(self, lid: str) -> str:
        """LoRA sua: some. LoRA padrão: volta ao padrão se você editou; se não, é escondida."""
        mine = [e for e in self._custom_loras() if e.get("id") != lid]
        in_defaults = any(e.get("id") == lid for e in (self._read(self.defaults_dir / "loras.yaml").get("loras") or []))
        had_mine = len(mine) != len(self._custom_loras())
        if in_defaults and not had_mine:
            mine.append({"id": lid, "disabled": True, "file": "-"})
            res = "hidden"
        else:
            res = "reset" if in_defaults else "removed"
        self._write_custom_loras(mine)
        return res

    def hidden_loras(self) -> list[dict]:
        """LoRAs padrão que você escondeu (entram em custom/loras.yaml como `disabled`)."""
        gone = {e.get("id") for e in self._custom_loras() if e.get("disabled")}
        return [norm_lora(e) for e in (self._read(self.defaults_dir / "loras.yaml").get("loras") or [])
                if e.get("id") in gone]

    def restore_lora(self, lid: str) -> str:
        """Tira a LoRA da lista de escondidas (a sua cópia editada, se houver, continua)."""
        mine = self._custom_loras()
        keep = [e for e in mine if not (e.get("id") == lid and e.get("disabled"))]
        if len(keep) == len(mine):
            raise StoreError(f"A LoRA '{lid}' não está escondida.")
        self._write_custom_loras(keep)
        return "restored"

    # ---------------------------------------------------------- clef prompt
    @staticmethod
    def _norm_entry(cfg: dict) -> dict:
        e = {"enabled": bool(cfg.get("enabled", True)), "description": str(cfg.get("description") or "")}
        if cfg.get("prompts") is not None:
            e["prompts"] = list(cfg["prompts"])
        return e

    def _clef_layer(self, p: Path) -> dict:
        d = self._read(p)
        out: dict[str, Any] = {}
        if "instructions" in d:
            out["instructions"] = str(d.get("instructions") or "")
        if d.get("workflows"):
            out["workflows"] = {w: self._norm_entry(c or {}) for w, c in d["workflows"].items()}
        if "recipes" in d:
            out["recipes"] = d.get("recipes") or []
        if d.get("hidden_recipes"):
            out["hidden_recipes"] = [str(x) for x in d["hidden_recipes"]]
        return out

    def _addon_clef(self) -> dict:
        """O que os addons somam ao prompt do Clef: descrições de workflows, casos de uso (mesmo id = o do addon
        que vem depois vale mais) e `instructions_append` (texto acrescentado às instruções gerais)."""
        wfs: dict[str, Any] = {}
        recipes: dict[str, Any] = {}
        extra: list[str] = []
        for a in self.addon_dirs():
            d = self._read(a / "clef_prompt.yaml")
            for w, c in (d.get("workflows") or {}).items():
                wfs[w] = self._norm_entry(c or {})
            for r in d.get("recipes") or []:
                if isinstance(r, dict) and r.get("id"):
                    recipes[r["id"]] = r
            if d.get("instructions_append"):
                extra.append(str(d["instructions_append"]).strip())
        return {"workflows": wfs, "recipes": list(recipes.values()), "append": extra}

    def clef_config(self) -> dict:
        base = self._clef_layer(self.defaults_dir / "clef_prompt.yaml")
        mine = self._clef_layer(self.custom_dir / "clef_prompt.yaml")
        add = self._addon_clef()
        known = self.manifests()
        wfs = {**(base.get("workflows") or {}), **add["workflows"], **(mine.get("workflows") or {})}
        recipes = copy.deepcopy(mine["recipes"] if "recipes" in mine else base.get("recipes", []))
        have = {r.get("id") for r in recipes}
        hidden = set(mine.get("hidden_recipes") or [])
        recipes += [copy.deepcopy(r) for r in base.get("recipes", [])          # padrão novo aparece mesmo com a sua lista
                    if r.get("id") not in have and r.get("id") not in hidden]
        have = {r.get("id") for r in recipes}
        recipes += [copy.deepcopy(r) for r in add["recipes"] if r["id"] not in have]      # casos de uso de addons sempre aparecem
        have = {r.get("id") for r in recipes}
        recipes += [copy.deepcopy(r) for r in self.generated_recipes() if r["id"] not in have]   # criados pelo agente (nunca trocam um existente)
        instr = mine.get("instructions", base.get("instructions", ""))
        if add["append"]:
            instr = instr.rstrip() + "\n" + "\n".join(add["append"])
        return {"instructions": instr, "workflows": {w: c for w, c in wfs.items() if w in known}, "recipes": recipes}

    def clef_customized(self, wid: str) -> bool:
        return wid in (self._clef_layer(self.custom_dir / "clef_prompt.yaml").get("workflows") or {})

    def _write_clef_custom(self, mine: dict):
        p = self.custom_dir / "clef_prompt.yaml"
        if not mine:
            p.unlink(missing_ok=True)
            return
        atomic_write(p, dump_yaml(mine, "# Suas mudanças no prompt do Clef (só o que difere de defaults/clef_prompt.yaml).\n"
                                        "# Editável aqui ou na interface."))

    def normalize_recipe(self, r: dict, known: dict | None = None) -> dict:
        """Valida e normaliza um caso de uso (a mesma regra para a tela, para o agente e para a fusão)."""
        known = known if known is not None else self.manifests()
        rid = str(r.get("id", "")).strip()
        if not ID_RE.match(rid):
            raise StoreError(f"Id de caso de uso inválido: '{rid}'.")
        steps = []
        for s in r.get("steps") or []:
            if s.get("workflow") not in known:
                raise StoreError(f"Caso de uso '{rid}' aponta para workflow inexistente: {s.get('workflow')}")
            st: dict[str, Any] = {"workflow": s["workflow"]}
            if s.get("prompt"):
                st["prompt"] = s["prompt"]
            if s.get("inputs"):
                st["inputs"] = dict(s["inputs"])
            if s.get("mode"):
                st["mode"] = str(s["mode"])
            if s.get("loras") is not None:
                st["loras"] = [str(x) for x in s["loras"]]
            if isinstance(s.get("vars"), dict) and s["vars"]:
                st["vars"] = {str(k): s["vars"][k] for k in s["vars"]}
            if str(s.get("request") or "").strip():
                st["request"] = str(s["request"]).strip()
            steps.append(st)
        if not steps:
            raise StoreError(f"O caso de uso '{rid}' precisa de pelo menos um passo.")
        rec = {"id": rid, "name": str(r.get("name") or rid), "enabled": bool(r.get("enabled", True)),
               "description": str(r.get("description") or ""), "steps": steps}
        if r.get("images"):
            rec["images"] = max(1, int(r["images"]))
        if r.get("models"):
            rec["models"] = [str(x) for x in (r["models"] if isinstance(r["models"], list) else [r["models"]])]
        roles = []
        for ro in r.get("roles") or []:
            rk = str(ro.get("key", "")).strip()
            if not ID_RE.match(rk):
                raise StoreError(f"Papel inválido no caso de uso '{rid}': '{rk}'.")
            role = {"key": rk, "label": str(ro.get("label") or rk), "about": str(ro.get("about") or "")}
            if ro.get("default"):
                role["default"] = int(ro["default"])
            roles.append(role)
        if roles:
            rec["roles"] = roles
        if isinstance(r.get("generated"), dict) and r["generated"]:
            rec["generated"] = copy.deepcopy(r["generated"])
        return rec

    # ------------------------------------------------ casos de uso do agente
    @property
    def generated_path(self) -> Path:
        return self.custom_dir / "generated_recipes.yaml"

    def generated_recipes(self) -> list[dict]:
        """Casos de uso criados pelo agente (`custom/generated_recipes.yaml`, fora do git). Cada um traz `generated`:
        `status` (trial = em teste, pode ser trocado por outro mais novo; kept = você mandou manter), `created`, `request`."""
        out = []
        for r in self._read(self.generated_path).get("recipes") or []:
            if isinstance(r, dict) and r.get("id"):
                out.append(r)
        return out

    def _write_generated(self, recipes: list[dict]):
        if not recipes:
            self.generated_path.unlink(missing_ok=True)
            return
        atomic_write(self.generated_path, dump_yaml(
            {"recipes": recipes}, "# Casos de uso criados pelo agente (PickFlow). Pode editar na interface (Casos de uso).\n"
                                  "# status: trial = em teste (o mais antigo sai quando passa do limite); kept = mantido."))

    def _save_generated(self, rec: dict):
        cur = self.generated_recipes()
        rec = self.normalize_recipe(rec)
        if any(r["id"] == rec["id"] for r in cur):
            cur = [rec if r["id"] == rec["id"] else r for r in cur]
        else:
            cur.append(rec)
        self._write_generated(cur)

    def add_generated(self, recipe: dict, request: str, status: str = "trial", max_trial: int = 5) -> dict:
        """Guarda um caso de uso novo do agente. NUNCA troca um existente: o id é sempre inédito (com sufixo, se preciso)
        e, passando de `max_trial` em teste, sai o mais antigo que ainda está em teste (os mantidos nunca saem)."""
        import time
        taken = {r.get("id") for r in self.clef_config()["recipes"]}
        rid, n = recipe["id"], 2
        while rid in taken:
            rid, n = f"{recipe['id']}_{n}", n + 1
        rec = self.normalize_recipe({**recipe, "id": rid})
        rec["generated"] = {"status": status, "created": int(time.time()), "request": request[:300]}
        cur = self.generated_recipes() + [rec]
        trials = [r for r in cur if (r.get("generated") or {}).get("status") != "kept"]
        while len(trials) > max(1, int(max_trial)):
            old = min(trials, key=lambda r: (r.get("generated") or {}).get("created", 0))
            cur = [r for r in cur if r["id"] != old["id"]]
            trials = [r for r in trials if r["id"] != old["id"]]
        self._write_generated(cur)
        return rec

    def keep_generated(self, rid: str) -> dict:
        cur = self.generated_recipes()
        for r in cur:
            if r["id"] == rid:
                r.setdefault("generated", {})["status"] = "kept"
                self._write_generated(cur)
                return r
        raise StoreError(f"Não há caso de uso criado pelo agente com o id '{rid}'.")

    def remove_generated(self, rid: str):
        self._write_generated([r for r in self.generated_recipes() if r["id"] != rid])

    def save_clef_config(self, data: dict) -> dict:
        known = self.manifests()
        wfs = {}
        for wid, cfg in (data.get("workflows") or {}).items():
            if wid not in known:
                raise StoreError(f"Workflow desconhecido: {wid}")
            entry = {"enabled": bool(cfg.get("enabled", True)),
                     "description": str(cfg.get("description") or "")}
            if cfg.get("prompts") is not None:
                valid = {p["id"] for p in self.prompts(wid)["presets"]}
                bad = [x for x in cfg["prompts"] if x not in valid]
                if bad:
                    raise StoreError(f"Prompts inexistentes em {wid}: {', '.join(bad)}")
                entry["prompts"] = list(cfg["prompts"])
            wfs[wid] = entry
        recipes = []
        rids = set()
        for r in data.get("recipes") or []:
            rec = self.normalize_recipe(r, known)
            if rec["id"] in rids:
                raise StoreError(f"Id de caso de uso repetido: {rec['id']}")
            rids.add(rec["id"])
            recipes.append(rec)
        gen = {g["id"]: g for g in self.generated_recipes()}           # criados pelo agente: ficam na camada deles
        mine_recipes = [r for r in recipes if r["id"] not in gen]
        for r in recipes:
            if r["id"] in gen:
                self._save_generated({**r, "generated": gen[r["id"]].get("generated") or {}})
        for gid in list(gen):
            if gid not in rids:
                self.remove_generated(gid)
        full_recipes = recipes
        recipes = [{k: v for k, v in r.items() if k != "generated"} for r in mine_recipes]
        out = {"instructions": str(data.get("instructions") or ""), "workflows": wfs, "recipes": full_recipes}

        base = self._clef_layer(self.defaults_dir / "clef_prompt.yaml")
        base_w = base.get("workflows") or {}
        mine: dict[str, Any] = {}
        if out["instructions"] != base.get("instructions", ""):
            mine["instructions"] = out["instructions"]
        diff = {w: e for w, e in wfs.items() if e != base_w.get(w)}
        if diff:
            mine["workflows"] = diff
        if recipes != (base.get("recipes") or []):
            mine["recipes"] = recipes
        hidden = sorted({r.get("id") for r in base.get("recipes") or []} - rids)
        if hidden:
            mine["hidden_recipes"] = hidden             # casos padrão que você apagou não voltam sozinhos
        # entradas suas de workflows que não foram enviadas agora continuam como estavam
        old = self._clef_layer(self.custom_dir / "clef_prompt.yaml").get("workflows") or {}
        for w, e in old.items():
            if w in known and w not in wfs:
                mine.setdefault("workflows", {})[w] = e
        self._write_clef_custom(mine)
        return out

    # ---------------------------------------------------- restaurar / remover
    def reset_workflow(self, wid: str) -> str:
        """Padrão: descarta as suas edições (prompts e descrição) e volta ao que vem no repositório.
        Workflow seu (sem padrão): tira da lista (os arquivos .json continuam na pasta)."""
        m = self.manifest(wid)
        (self.custom_dir / "prompts" / f"{wid}.yaml").unlink(missing_ok=True)
        mine = self._clef_layer(self.custom_dir / "clef_prompt.yaml")
        (mine.get("workflows") or {}).pop(wid, None)
        if not mine.get("workflows"):
            mine.pop("workflows", None)
        if m.get("source") == "custom" and not (self.defaults_dir / "registry" / f"{wid}.yaml").exists():
            (self.custom_dir / "registry" / f"{wid}.yaml").unlink(missing_ok=True)
            (self.custom_dir / "registry" / f"{wid}.api.json").unlink(missing_ok=True)
            self._write_clef_custom(mine)
            return "removed"
        self._write_clef_custom(mine)
        return "reset"

    # -------------------------------------------------------------- merged
    def active_presets(self, wid: str) -> list[dict]:
        lib = self.prompts(wid)
        sel = (self.clef_config()["workflows"].get(wid) or {}).get("prompts")
        have = self.loras()
        presets = [p for p in lib["presets"] if all(x in have for x in p.get("requires") or [])]   # LoRA escondida: o prompt some
        if not presets:
            presets = lib["presets"]
        if sel is None:
            return presets
        chosen = [p for p in presets if p["id"] in sel]
        return chosen or [p for p in presets if p["id"] == lib.get("default")] or presets[:1]

    def for_model(self, model: str | None):
        """O Store visto por um modelo de imagem (`settings.model` quando `model` é vazio): o roteador só enxerga os
        workflows, casos de uso, prompts e LoRAs dele. Veja `families.ModelView`."""
        from .families import ModelView, normalize
        return ModelView(self, normalize(model, normalize(self.settings().get("model"))))

    def enabled_workflows(self) -> dict[str, dict]:
        cfg = self.clef_config()["workflows"]
        out = {}
        for wid, m in self.manifests().items():
            if (cfg.get(wid) or {}).get("enabled", True):
                out[wid] = m
        return out

    def describe(self, wid: str) -> str:
        c = (self.clef_config()["workflows"].get(wid) or {}).get("description")
        return c or self.manifest(wid).get("title", wid)

    def enabled_recipes(self) -> list[dict]:
        usable = self.enabled_workflows()
        return [r for r in self.clef_config()["recipes"]
                if r.get("enabled", True) and all(s.get("workflow") in usable for s in r.get("steps", []))]
