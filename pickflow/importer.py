"""Cadastra um workflow seu: olha o .json salvo no editor do ComfyUI e monta o registro (rascunho).

O rascunho acerta o caso comum — imagens de entrada (LoadImage), o nó do prompt e a saída —
e o resto (caixas, máscaras, nós a descartar) continua sendo feito à mão em `custom/registry/<id>.yaml`.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from .converter import FRONTEND_ONLY, OUTPUT_TYPES
from .store import ID_RE, Store, StoreError, atomic_write, dump_yaml

PROMPT_NODES = {  # tipo -> widget que recebe o texto
    "PrimitiveStringMultiline": "value",
    "TextEncodeQwenImage21": "prompt",
    "TextEncodeQwenImageEditPlus": "prompt",
    "TextEncodeQwenImageEdit": "prompt",
    "CLIPTextEncode": "text",
    "PrimitiveString": "value",
}


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")
    t = re.sub(r"^qwen2_1_", "", t)
    return (t or "workflow")[:40]


def _title(n: dict) -> str:
    return str(n.get("title") or n["type"])


def analyze(wf: dict) -> dict:
    """Candidatos de entrada de imagem, prompt e saída de um workflow no formato do editor."""
    if "nodes" not in wf:
        raise StoreError("Esse arquivo não parece um workflow do ComfyUI salvo pelo editor "
                         "(Workflow → Save). O formato de API (Export API) ainda não é aceito aqui.")
    # nós de dentro de subgraph ficam fora da lista: quem aparece é o nó-subgraph, com os widgets promovidos
    nodes = [n for n in wf["nodes"] if n["type"] not in FRONTEND_ONLY and n.get("mode", 0) != 2]
    images = [{"node": n["id"], "title": _title(n)} for n in nodes if n["type"] == "LoadImage"]
    prompts: list[dict] = []
    for n in nodes:
        w = PROMPT_NODES.get(n["type"])
        if not w:       # nó (ou subgraph) com um widget "prompt" promovido
            w = "prompt" if any(isinstance(i.get("widget"), dict) and i["widget"].get("name") == "prompt"
                                for i in n.get("inputs") or []) else None
        if not w or any(i["name"] == w and i.get("link") is not None for i in n.get("inputs") or []):
            continue    # o texto vem de outro nó: o candidato é aquele
        named = n.get("widgets_values_named")
        if isinstance(named, dict):
            val = named.get(w) or ""
        else:
            val = next((v for v in n.get("widgets_values") or [] if isinstance(v, str)), "")
        prompts.append({"node": n["id"], "widget": w, "title": _title(n), "text": str(val)[:80]})
    prompts.sort(key=lambda p: p["node"] != next((x["node"] for x in prompts if x["text"]), None))   # com texto primeiro
    outputs = [{"node": n["id"], "title": _title(n)} for n in nodes if n["type"] in OUTPUT_TYPES]
    # LoadImage que ninguém usa é só enfeite do workflow: não vira entrada
    used = {l[1] for l in wf.get("links", [])}
    images = [i for i in images if i["node"] in used] or images
    order = _image_order(wf)
    images.sort(key=lambda i: (order.get(i["node"], 10 ** 6), i["node"]))   # imagem 1, 2, 3... como o modelo vê
    return {"images": images, "prompts": prompts, "outputs": outputs}


_IMG_INPUT = re.compile(r"image_?(\d+)$", re.I)


def _image_order(wf: dict) -> dict[int, int]:
    """Número da entrada 'image_N' do nó de texto-e-imagem para onde cada carregador vai (passando por escalas)."""
    nodes = {n["id"]: n for n in wf.get("nodes", [])}
    links = {l[0]: l for l in wf.get("links", [])}
    out: dict[int, int] = {}
    for n in nodes.values():
        if n["type"] != "LoadImage":
            continue
        seen: set = set()
        stack = [n["id"]]
        best = None
        while stack:
            cur = stack.pop()
            for o in nodes[cur].get("outputs") or []:
                for lid in o.get("links") or []:
                    l = links.get(lid)
                    if not l or l[3] not in nodes:
                        continue
                    tgt = nodes[l[3]]
                    inputs = tgt.get("inputs") or []
                    name = inputs[l[4]]["name"] if l[4] < len(inputs) else ""
                    m = _IMG_INPUT.search(name)
                    if m:
                        best = int(m.group(1)) if best is None else min(best, int(m.group(1)))
                    elif l[3] not in seen:
                        seen.add(l[3])
                        stack.append(l[3])
        if best is not None:
            out[n["id"]] = best
    return out


def list_importable(store: Store) -> list[dict]:
    """.json em custom/workflows (inclusive subpastas) que ainda não estão no registro."""
    used = {str(m.get("file")).replace("\\", "/") for m in store.manifests().values() if m.get("file")}
    base = store.custom_dir / "workflows"
    out = []
    if base.is_dir():
        for p in sorted(base.rglob("*.json")):
            rel = p.relative_to(base).as_posix()
            if rel not in used:
                out.append({"file": rel, "nsfw": rel.upper().startswith("NSFW/")})
    return out


def draft(store: Store, file: str) -> dict:
    p = store._find("workflows", file)
    if not p:
        raise StoreError(f"Arquivo não encontrado em custom/workflows: {file}")
    import json
    info = analyze(json.loads(p.read_text(encoding="utf-8")))
    wid = slugify(Path(file).stem)
    existing = set(store.manifests())
    base, k = wid, 2
    while wid in existing:
        wid, k = f"{base}_{k}", k + 1
    return {"file": file, "id": wid, "title": Path(file).stem, **info}


def register(store: Store, body: dict) -> dict:
    wid = str(body.get("id", "")).strip()
    if not ID_RE.match(wid):
        raise StoreError("Id inválido. Use letras minúsculas, números e _ (ex.: meu_workflow).")
    if wid in store.manifests():
        raise StoreError(f"Já existe um workflow com o id '{wid}'.")
    file = str(body.get("file", ""))
    if not store._find("workflows", file):
        raise StoreError(f"Arquivo não encontrado em custom/workflows: {file}")
    outputs = [int(o) for o in body.get("outputs") or []]
    if not outputs:
        raise StoreError("Escolha pelo menos um nó de saída (SaveImage / PreviewImage).")
    m: dict[str, Any] = {"id": wid, "file": file, "title": str(body.get("title") or wid),
                         "group": str(body.get("group") or "meus"), "images": []}
    for i, im in enumerate(body.get("images") or [], 1):
        m["images"].append({"key": f"image_{i}", "node": int(im["node"]), "label": str(im.get("label") or f"Imagem {i}"),
                            "required": bool(im.get("required", True)), "hint": str(im.get("hint") or "")})
    pr = body.get("prompt")
    if pr:
        m["prompt"] = {"node": int(pr["node"]), "widget": str(pr.get("widget") or "value")}
    m["outputs"] = outputs
    header = (f"# Ligação técnica do seu workflow '{wid}'. Caixas, máscaras e nós a descartar (drop) "
              "você acrescenta aqui à mão; veja defaults/registry/ para exemplos.")
    atomic_write(store.custom_dir / "registry" / f"{wid}.yaml", dump_yaml(m, header))
    prompt = {"default": "livre", "presets": [{
        "id": "livre", "name": "Pedido do usuário", "when": "Quando o pedido combina com este workflow.",
        "text": "{request}", "rewrite": bool(body.get("rewrite", False))}]}
    store.save_prompts(wid, prompt)
    return store.manifest(wid)
