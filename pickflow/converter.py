"""Converte um workflow salvo no editor do ComfyUI (nodes/links) para o formato
de API (`POST /prompt`), usando o `/object_info` do ComfyUI em execução.

O conversor nunca altera o arquivo de origem: trabalha numa cópia em memória.

Regras que ele aplica (as mesmas do frontend do ComfyUI):
- `widgets_values` é consumido na ordem dos widgets declarados em `object_info`,
  incluindo o valor extra de `control_after_generate` depois de uma seed;
- nós mutados (mode 2) somem; nós em bypass (mode 4) e `Reroute` repassam a
  entrada de mesmo tipo;
- nós só de interface (notas) são ignorados;
- subgraphs são "achatados": os nós de dentro viram nós normais com id `<subgraph>:<nó>` (o mesmo
  formato do frontend), e os valores dos widgets promovidos do subgraph entram nos nós de dentro;
- combos dinâmicos (ex.: `format` + `format.bit_depth` do SaveImageAdvanced) vêm de `widgets_values_named`.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

FRONTEND_ONLY = {
    "MarkdownNote", "Note", "Label (rgthree)", "Fast Groups Muter (rgthree)",
    "Fast Groups Bypasser (rgthree)", "Fast Muter (rgthree)", "Fast Bypasser (rgthree)",
}
RGTHREE_POWER_LORA = "Power Lora Loader (rgthree)"
LOADER_TYPES = {"LoadImage"}
OUTPUT_TYPES = {"SaveImage", "PreviewImage", "SaveImageAdvanced"}


def is_loader(oi: dict, class_type: str) -> bool:
    """O nó recebe uma imagem enviada ao ComfyUI? O LoadImage, ou qualquer nó cujo combo `image` aceita upload (ex.: o
    Crop + Rotate + Pad do AusBoss): o valor é o nome do arquivo em input/, que não está na lista de arquivos do nó."""
    if class_type in LOADER_TYPES:
        return True
    spec = (((oi.get(class_type) or {}).get("input") or {}).get("required") or {}).get("image")
    return bool(spec and len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("image_upload"))


def node_id(x: Any) -> Any:
    """Id de nó vindo do registro: `12` -> 12; `"459:458"` (nó dentro de subgraph) fica texto."""
    return int(x) if isinstance(x, int) or str(x).isdigit() else str(x)


PICKFLOW_NODES = {"DrawBox", "MoveBoxes", "DrawMoveBoxes", "ViggleTurboLora", "ViggleTurboSigmas"}


class ConversionError(Exception):
    """Falha ao converter um workflow para o formato de API."""


@dataclass
class Widget:
    name: str
    kind: str                 # INT | FLOAT | STRING | BOOLEAN | COMBO
    opts: dict
    options: list
    required: bool
    has_control: bool = False


def flatten_subgraphs(wf: dict) -> tuple[dict, dict[tuple[str, str], Any], dict[tuple[Any, str], list]]:
    """Troca cada nó-subgraph pelos nós de dentro. Devolve (workflow achatado, valores a injetar).

    `routes[(id_do_subgraph, entrada)]` lista os (nó de dentro, widget) que recebem essa entrada, para que
    um valor dado ao nó de fora (ex.: o prompt) chegue ao lugar certo.
    `inject[(id_do_nó_de_dentro, widget)]` é o valor que o nó de fora deu à entrada do subgraph
    ligada àquele widget. Entradas de subgraph que o nó de fora não expõe ficam com o valor salvo dentro.
    """
    defs = {sg["id"]: sg for sg in ((wf.get("definitions") or {}).get("subgraphs") or [])}
    inject: dict[tuple[str, str], Any] = {}
    routes: dict[tuple[Any, str], list] = {}
    if not defs:
        return wf, inject, routes
    nodes = [copy.deepcopy(n) for n in wf["nodes"]]
    links: dict[Any, list] = {l[0]: list(l) for l in wf.get("links", [])}
    for _ in range(8):                                   # subgraphs dentro de subgraphs
        todo = [n for n in nodes if n["type"] in defs and n.get("mode", 0) != 2]
        if not todo:
            break
        for outer in todo:
            nodes.remove(outer)
            sg = defs[outer["type"]]
            oid = outer["id"]
            named = outer.get("widgets_values_named") or {}
            in_by_name = {i["name"]: i for i in outer.get("inputs") or []}
            sub_in = sg.get("inputs") or []
            sublinks = {l["id"]: l for l in sg.get("links") or []}
            flat = lambda x: f"{oid}:{x}"                # noqa: E731
            exits: dict[int, tuple[Any, int]] = {}       # saída do subgraph -> (nó de dentro, slot)
            for sl in sublinks.values():
                if sl["target_id"] == -20:
                    exits[sl["target_slot"]] = (flat(sl["origin_id"]), sl["origin_slot"])
            for n0 in sg.get("nodes") or []:
                n = copy.deepcopy(n0)
                n["id"] = flat(n["id"])
                for i in n.get("inputs") or []:
                    lid = i.get("link")
                    if lid is None:
                        continue
                    sl = sublinks.get(lid)
                    if sl is None:
                        i["link"] = None
                        continue
                    if sl["origin_id"] != -10:
                        nl = flat(f"L{lid}")
                        links[nl] = [nl, flat(sl["origin_id"]), sl["origin_slot"], n["id"], sl["target_slot"], sl["type"]]
                        i["link"] = nl
                        continue
                    name = sub_in[sl["origin_slot"]]["name"]
                    out_in = in_by_name.get(name)
                    if out_in is not None and out_in.get("link") is not None:
                        o = links.get(out_in["link"])
                        if o:                            # ligação de fora entra no nó de dentro
                            nl = flat(f"L{lid}")
                            links[nl] = [nl, o[1], o[2], n["id"], sl["target_slot"], sl["type"]]
                            i["link"] = nl
                            continue
                    i["link"] = None
                    # widget do nó de dentro, ou entrada de conexão que recebe um valor fixo (ex.: o texto
                    # que o interruptor repassa): em ambos os casos o valor do nó de fora vira o dele
                    target = i["widget"]["name"] if i.get("widget") else i["name"]
                    routes.setdefault((oid, name), []).append((n["id"], target))
                    if name in named:
                        inject[(n["id"], target)] = named[name]
                nodes.append(n)
            # ligações que saíam do nó-subgraph passam a sair do nó de dentro
            for lid, l in links.items():
                if l[1] == oid and l[2] in exits:
                    l[1], l[2] = exits[l[2]]
    return {**wf, "nodes": nodes, "links": list(links.values())}, inject, routes


def is_api_format(wf: dict) -> bool:
    return "nodes" not in wf and bool(wf) and all(
        isinstance(v, dict) and "class_type" in v for v in wf.values())


def is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) and isinstance(v[0], (str, int))


def widget_list(info: dict) -> list[Widget]:
    """Widgets de um nó, na ordem em que o frontend os cria."""
    inputs = info.get("input") or {}
    order = info.get("input_order") or {}
    out: list[Widget] = []
    for section in ("required", "optional"):
        defs = inputs.get(section) or {}
        keys = order.get(section) or list(defs.keys())
        for key in keys:
            d = defs.get(key)
            if not d:
                continue
            t = d[0]
            opts = d[1] if len(d) > 1 and isinstance(d[1], dict) else {}
            if isinstance(t, list):
                kind, options = "COMBO", t
            elif t == "COMBO":
                kind, options = "COMBO", list(opts.get("options") or [])
            elif t in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                kind, options = t, []
            elif opts.get("socketless") and isinstance(opts.get("default"), (dict, list)):
                kind, options = "JSON", []      # widget do frontend que guarda um objeto (ex.: o editor do Create Layered Image)
            else:
                continue            # tipo de conexão (IMAGE, MODEL, ...)
            if opts.get("forceInput"):
                continue
            control = bool(opts.get("control_after_generate")) or (
                kind == "INT" and key in ("seed", "noise_seed"))
            out.append(Widget(key, kind, opts, options, section == "required", control))
    return out


def coerce(w: Widget, value: Any) -> Any:
    default = w.opts.get("default")
    try:
        if w.kind == "INT":
            if isinstance(value, bool) or value == "":
                raise ValueError
            return int(value)
        if w.kind == "FLOAT":
            if isinstance(value, bool) or value == "":
                raise ValueError
            return float(value)
        if w.kind == "BOOLEAN":
            if isinstance(value, str):
                return value.strip().lower() in ("true", "1", "yes", "on")
            return bool(value)
        if w.kind == "STRING":
            return value if isinstance(value, str) else ("" if value is None else str(value))
        if w.kind == "JSON":
            return value if isinstance(value, (dict, list)) else default
        return value
    except (TypeError, ValueError):
        return default


def required_inputs(info: dict) -> set[str]:
    return set(((info.get("input") or {}).get("required") or {}).keys())


class Converter:
    def __init__(self, object_info: dict, randomize_seeds: bool = True, rng: random.Random | None = None):
        self.oi = object_info
        self.bypass: set[str] = set()
        self.randomize = randomize_seeds
        self.rng = rng or random.Random()
        self.fixes: list[tuple[str, str, str, str, str]] = []   # (nó, tipo, widget, antes, depois)

    # ------------------------------------------------------------------ API
    def convert(self, wf: dict, overrides: dict[tuple[int, str], Any] | None = None,
                drop_nodes: set | None = None, loader_fallback: str | None = None,
                keep_loaders: set | None = None, bypass: set | None = None,
                lora_chain: Any = None, loras: list[dict] | None = None) -> dict[str, dict]:
        """`bypass`: nós que repassam a entrada (como "ignorar" no editor). `lora_chain` + `loras`: trocam a
        cadeia de LoRAs do workflow pela lista dada (ver `rewire_loras`)."""
        overrides = overrides or {}
        drop_nodes = {str(d) for d in (drop_nodes or ())}
        keep_loaders = {str(k) for k in (keep_loaders or ())}
        self.bypass = {str(b) for b in (bypass or ())}
        self._chain, self._loras = lora_chain, loras
        if is_api_format(wf):
            return self._from_api(wf, overrides, drop_nodes, loader_fallback, keep_loaders)
        if "nodes" not in wf:
            raise ConversionError("Arquivo não parece um workflow do ComfyUI.")
        wf, inject, routes = flatten_subgraphs(wf)
        overrides = {**inject, **overrides}
        for key, targets in routes.items():
            if key in overrides:
                for t in targets:
                    overrides[t] = overrides[key]
        nodes = {n["id"]: n for n in wf["nodes"]}
        links = {l[0]: l for l in wf.get("links", [])}
        api: dict[str, dict] = {}
        missing: list[str] = []
        for nid, n in nodes.items():
            if self._skipped(n):
                continue
            if self._passthrough(n):           # Reroute (só de interface: o ComfyUI novo nem o lista), ignorado ou em bypass
                continue
            if n["type"] not in self.oi:
                missing.append(n["type"])
                continue
            api[str(nid)] = self._build_node(n, nodes, links, overrides)
        if missing:
            names = sorted(set(missing))
            hint = ""
            if any(m in PICKFLOW_NODES for m in names):
                hint = (" — alguns deles vêm com o PickFlow (pasta comfyui_nodes/): rode install_nodes.bat "
                        "(ou ./install_nodes.sh) e reinicie o ComfyUI.")
            raise ConversionError("Nós não instalados no ComfyUI: " + ", ".join(names) + hint)
        self._finish(api, drop_nodes, loader_fallback, keep_loaders)
        return api

    # ------------------------------------------------------------ internals
    def _skipped(self, n: dict) -> bool:
        return n["type"] in FRONTEND_ONLY or n.get("mode", 0) == 2

    def _passthrough(self, n: dict) -> bool:
        if n["type"] == "Reroute" or n.get("mode", 0) == 4 or str(n["id"]) in self.bypass:
            return True
        if n["type"] == RGTHREE_POWER_LORA:
            return not any(isinstance(v, dict) and v.get("on") and v.get("lora") not in (None, "None")
                           for v in (n.get("widgets_values") or []))
        return False

    def _resolve(self, origin_id: int, slot: int, nodes: dict, links: dict):
        seen: set[int] = set()
        while True:
            n = nodes.get(origin_id)
            if n is None or origin_id in seen:
                return None
            seen.add(origin_id)
            if self._skipped(n):
                return None
            if n["type"] in self.oi and not self._passthrough(n):
                return [str(origin_id), slot]
            inp = self._passthrough_input(n, slot)
            if inp is None or inp.get("link") is None:
                return None
            l = links.get(inp["link"])
            if l is None:
                return None
            origin_id, slot = l[1], l[2]

    @staticmethod
    def _passthrough_input(n: dict, out_slot: int):
        ins = n.get("inputs") or []
        outs = n.get("outputs") or []
        if n["type"] == "Reroute":
            return ins[0] if ins else None
        otype = outs[out_slot].get("type") if out_slot < len(outs) else None
        if out_slot < len(ins) and ins[out_slot].get("type") == otype:
            return ins[out_slot]
        for i in ins:
            if i.get("type") == otype:
                return i
        return ins[0] if ins else None

    def _build_node(self, n: dict, nodes: dict, links: dict, overrides: dict) -> dict:
        info = self.oi[n["type"]]
        inputs: dict[str, Any] = {}
        linked: dict[str, Any] = {}
        for i in n.get("inputs") or []:
            if i.get("link") is None:
                continue
            l = links.get(i["link"])
            if l is None:
                continue
            src = self._resolve(l[1], l[2], nodes, links)
            if src is not None:
                linked[i["name"]] = src
        if n["type"] == RGTHREE_POWER_LORA:
            inputs.update(self._rgthree(n))
        else:
            self._apply_widgets(n, info, inputs, linked, overrides)
        for i in n.get("inputs") or []:                  # valor fixo numa entrada de conexão (vindo de subgraph)
            k = i["name"]
            if i.get("link") is None and k not in inputs and (n["id"], k) in overrides and not i.get("widget"):
                inputs[k] = overrides[(n["id"], k)]
        inputs.update(linked)
        return {"class_type": n["type"], "inputs": inputs, "_meta": {"title": n.get("title") or n["type"]}}

    def _apply_widgets(self, n, info, inputs, linked, overrides):
        raw = n.get("widgets_values")
        widgets = widget_list(info)
        values: dict[str, Any] = {}
        if isinstance(raw, dict):
            values = dict(raw)
        else:
            raw = list(raw or [])
            idx = 0
            for w in widgets:
                if idx >= len(raw):
                    break
                v = raw[idx]
                idx += 1
                values[w.name] = v
                if w.has_control and idx < len(raw):
                    values["__control__" + w.name] = raw[idx]
                    idx += 1
        named = n.get("widgets_values_named")
        if isinstance(named, dict) and named:             # nomeado é mais confiável que a posição
            for w in widgets:
                if w.name in named:
                    values[w.name] = named[w.name]
                if w.has_control and "control_after_generate" in named:
                    values["__control__" + w.name] = named["control_after_generate"]
        nid = n["id"]
        for w in widgets:
            ov_key = (nid, w.name)
            if w.name in linked and ov_key not in overrides:
                continue
            if ov_key in overrides:
                v = overrides[ov_key]
                if w.kind in ("INT", "FLOAT", "BOOLEAN"):          # 1 -> 1.0 num campo FLOAT, "off" nunca chega aqui
                    c = coerce(w, v)
                    v = c if c is not None else v
                inputs[w.name] = v
                continue
            if w.name in values:
                v = coerce(w, values[w.name])
            elif "default" in w.opts:
                v = w.opts["default"]
            elif w.required:
                v = w.options[0] if (w.kind == "COMBO" and w.options) else None
            else:
                continue
            if w.has_control and self.randomize and values.get("__control__" + w.name) == "randomize":
                v = self.rng.randrange(0, 2 ** 50)
            if v is not None:
                inputs[w.name] = v
        self._dynamic(n, info, inputs, linked, overrides)

    @staticmethod
    def _dynamic(n, info, inputs, linked, overrides):
        """Combos dinâmicos (`format` -> `format.bit_depth`...): o frontend salva o valor de cada
        subcampo em `widgets_values_named`, e a API espera as mesmas chaves com ponto."""
        named = n.get("widgets_values_named")
        if not isinstance(named, dict):
            return
        parents = {k for sec in ("required", "optional") for k, d in ((info.get("input") or {}).get(sec) or {}).items()
                   if d and d[0] == "COMFY_DYNAMICCOMBO_V3"}
        for key, val in named.items():
            if key.split(".", 1)[0] not in parents:
                continue
            ov = (n["id"], key)
            if key in linked and ov not in overrides:
                continue
            inputs[key] = overrides.get(ov, val)

    @staticmethod
    def _rgthree(n: dict) -> dict:
        out: dict[str, Any] = {}
        k = 0
        for v in n.get("widgets_values") or []:
            if isinstance(v, dict) and "lora" in v:
                k += 1
                out[f"lora_{k}"] = v
            elif isinstance(v, dict) and v.get("type") == "PowerLoraLoaderHeaderWidget":
                out["PowerLoraLoaderHeaderWidget"] = v
        out["➕ Add Lora"] = ""
        return out

    # ---------------------------------------------------------- API in/out
    def _from_api(self, wf, overrides, drop_nodes, loader_fallback, keep_loaders):
        api = copy.deepcopy(wf)
        for (nid, name), value in overrides.items():
            node = api.get(str(nid))
            if node is None:
                raise ConversionError(f"Nó {nid} não existe no workflow de API.")
            node["inputs"][name] = value
        if self.randomize:
            for nid, node in api.items():
                for k in ("seed", "noise_seed"):
                    if isinstance(node.get("inputs", {}).get(k), int) and not any(str(a) == nid and b == k for a, b in overrides):
                        node["inputs"][k] = self.rng.randrange(0, 2 ** 50)
        self._finish(api, drop_nodes, loader_fallback, keep_loaders)
        return api

    def _finish(self, api: dict, drop_nodes: set, loader_fallback: str | None, keep_loaders: set):
        if getattr(self, "_chain", None) and getattr(self, "_loras", None) is not None:
            self.rewire_loras(api, self._chain, self._loras)
        self.prune(api, drop_nodes)
        self.keep_reachable(api)
        if loader_fallback:
            self._fix_loaders(api, loader_fallback, keep_loaders)
        self._fix_combos(api)

    def rewire_loras(self, api: dict, chain: Any, loras: list[dict]):
        """Troca a cadeia de LoRAs do grafo por uma nova, na ordem dada.

        `chain`: lista de ids dos nós de LoRA do workflow, ou `{nodes: [...], model: id, clip: id}` com os nós
        de onde a cadeia parte (por padrão, deduzidos da própria cadeia). Quem usava a saída da cadeia passa a
        usar a da nova; com a lista vazia, usa direto a origem (nenhuma LoRA)."""
        spec = chain if isinstance(chain, dict) else {"nodes": chain}
        ids = {str(c) for c in spec.get("nodes") or []}
        present = {c for c in ids if c in api}
        model_src = [str(spec["model"]), 0] if spec.get("model") is not None else None
        clip_src = [str(spec["clip"]), 0] if spec.get("clip") is not None else None
        for c in sorted(present):
            ins = api[c]["inputs"]
            if model_src is None and is_link(ins.get("model")) and str(ins["model"][0]) not in ids:
                model_src = list(ins["model"])
            if clip_src is None and is_link(ins.get("clip")) and str(ins["clip"][0]) not in ids:
                clip_src = list(ins["clip"])
        if model_src is None:
            raise ConversionError("Não achei de onde a cadeia de LoRAs do workflow parte (lora_chain).")
        cur_model, cur_clip = model_src, clip_src
        new: dict[str, dict] = {}
        for i, lo in enumerate(loras, 1):
            nid = f"lora_{i}"
            inputs = {"lora_name": lo["file"], "strength_model": float(lo["strength"]), "model": cur_model}
            if clip_src is not None:
                inputs["strength_clip"] = float(lo.get("strength_clip", lo["strength"]))
                inputs["clip"] = cur_clip
            else:
                inputs["strength_clip"] = 0.0
            new[nid] = {"class_type": "LoraLoader", "inputs": inputs, "_meta": {"title": f"LoRA: {lo.get('title', lo['file'])}"}}
            cur_model = [nid, 0]
            cur_clip = [nid, 1] if clip_src is not None else None
        for node in api.values():
            for k, v in list(node["inputs"].items()):
                if is_link(v) and str(v[0]) in ids:
                    cls = api[str(v[0])]["class_type"] if str(v[0]) in api else ""
                    is_clip = v[1] == 1 and cls != "LoraLoaderModelOnly"
                    target = cur_clip if is_clip else cur_model
                    if target is None:
                        del node["inputs"][k]
                    else:
                        node["inputs"][k] = list(target)
        for c in ids:
            api.pop(c, None)
        api.update(new)

    @staticmethod
    def _base(name: str) -> str:
        return str(name).replace("\\", "/").rsplit("/", 1)[-1].lower()

    def _fix_combos(self, api: dict):
        """Modelo/LoRA salvo no workflow sem a subpasta onde ele está instalado (ou o contrário):
        se exatamente um arquivo instalado tem o mesmo nome-base, usa o caminho certo."""
        for nid, node in api.items():
            if is_loader(self.oi, node["class_type"]):
                continue
            for w in widget_list(self.oi.get(node["class_type"], {})):
                v = node["inputs"].get(w.name)
                if w.kind != "COMBO" or not w.options or not isinstance(v, str) or not v or v in w.options:
                    continue
                hits = [o for o in w.options if isinstance(o, str) and self._base(o) == self._base(v)]
                if len(hits) == 1:
                    node["inputs"][w.name] = hits[0]
                    self.fixes.append((nid, node["class_type"], w.name, v, hits[0]))

    def prune(self, api: dict, drop: set):
        """Remove nós descartados e, em cascata, os que dependem deles de forma obrigatória."""
        removed = {str(d) for d in drop}
        changed = True
        while changed:
            changed = False
            for nid, node in list(api.items()):
                if nid in removed:
                    continue
                req = required_inputs(self.oi.get(node["class_type"], {}))
                for key, val in list(node["inputs"].items()):
                    if is_link(val) and str(val[0]) in removed:
                        if key in req and "." not in key:
                            removed.add(nid)
                        else:
                            del node["inputs"][key]
                        changed = True
                        if nid in removed:
                            break
        for r in removed:
            api.pop(r, None)

    def keep_reachable(self, api: dict):
        """Mantém só os nós de que alguma saída (SaveImage, PreviewImage...) depende.
        Nós soltos no workflow (sem entrada, sem uso) deixam de ir para o ComfyUI."""
        roots = [nid for nid, n in api.items()
                 if self.oi.get(n["class_type"], {}).get("output_node") or n["class_type"] in OUTPUT_TYPES]
        if not roots:
            return
        keep: set[str] = set()
        stack = list(roots)
        while stack:
            nid = stack.pop()
            if nid in keep or nid not in api:
                continue
            keep.add(nid)
            for v in api[nid]["inputs"].values():
                if is_link(v):
                    stack.append(str(v[0]))
        for nid in list(api):
            if nid not in keep:
                del api[nid]

    def _fix_loaders(self, api: dict, fallback: str, keep: set):
        """LoadImage sem imagem nossa: se o arquivo salvo no workflow não existe, usa `fallback`."""
        for nid, node in api.items():
            if not is_loader(self.oi, node["class_type"]) or str(nid) in keep:
                continue
            info = self.oi.get(node["class_type"], {})
            opts = []
            for w in widget_list(info):
                if w.name == "image":
                    opts = w.options
            cur = node["inputs"].get("image")
            if opts and cur not in opts:
                node["inputs"]["image"] = fallback


def widget_names(object_info: dict, class_type: str) -> list[str]:
    return [w.name for w in widget_list(object_info.get(class_type, {}))]
