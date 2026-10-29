"""LoRAs como peças soltas: biblioteca (com descrição), pilha efetiva, ajustes do grafo e prompt.

Um workflow "unificado" (ex.: `edit`) tem uma cadeia de LoRAs que o PickFlow reconstrói a cada execução:

    pilha base do workflow  ->  LoRAs do prompt escolhido  ->  LoRAs que o Clef decidiu acrescentar

Cada LoRA da biblioteca (`defaults/loras.yaml` + `custom/loras.yaml`) traz:
  description  o texto que o Clef lê para decidir se usa (você pode editar)
  strength     força padrão (0 desliga)
  prompt       instrução padrão que sempre acompanha a LoRA (ex.: "body_swap: ...")
  adjust       força de OUTRAS LoRAs enquanto esta estiver ligada (ex.: desligar a de realismo)
  settings     ajustes do grafo enquanto esta estiver ligada (tamanho, escalas, passos...)
  auto         o Clef pode acrescentá-la sozinho conforme o pedido
  needs        mínimo de imagens anexadas para ela fazer sentido
  conflicts    ids de LoRAs que NÃO funcionam junto com esta (a regra vale nos dois sentidos)
"""
from __future__ import annotations

import re
from typing import Any

OFF_WORDS = {"off", "false", "no", "nao", "não", "desligado", "none", ""}


def is_off(v: Any) -> bool:
    if v is None or v is False:
        return True
    if isinstance(v, str):
        return v.strip().lower() in OFF_WORDS
    if isinstance(v, (int, float)):
        return v == 0
    return False


def pick_prompt(p: Any, mode: str | None) -> str:
    """O prompt padrão de uma LoRA: um texto só, ou um por modo (`{turbo: ..., quality: ...}`)."""
    if isinstance(p, dict):
        return str(p.get(mode) or p.get("default") or next(iter(p.values()), "") or "").strip()
    return str(p or "").strip()


def lora_item(spec: Any) -> dict:
    """Aceita `"bfs_body"` ou `{id: bfs_body, strength: 0.8, mode: turbo}`."""
    if isinstance(spec, str):
        return {"id": spec}
    return dict(spec)


def _strengths(lib_entry: dict, item: dict) -> tuple[float, float]:
    s = item.get("strength", lib_entry.get("strength", 1.0))
    s = float(s if s is not None else 1.0)
    c = item.get("strength_clip", lib_entry.get("strength_clip", s))
    return s, float(c if c is not None else s)


def clash(lib: dict[str, dict], a: str, b: str) -> bool:
    """a e b não funcionam juntas? (vale se qualquer uma das duas declara a outra em `conflicts`)"""
    return b in ((lib.get(a) or {}).get("conflicts") or []) or a in ((lib.get(b) or {}).get("conflicts") or [])


def effective_loras(lib: dict[str, dict], base: list, preset_items: list, extras: list[str],
                    mode: str | None = None, notes: list[str] | None = None) -> list[dict]:
    """Pilha final, em ordem. Cada item: id, file, title, strength, strength_clip, prompt, origin.

    origin: base | preset | clef. Força 0 tira a LoRA da pilha. Id que não está na biblioteca é ignorado
    (a interface avisa); isso evita travar uma execução por um catálogo desatualizado.

    Conflitos (`conflicts`): ao entrar, uma LoRA tira da pilha as que conflitam com ela (as da base cedem a
    qualquer outra; as do prompt cedem às posteriores do próprio prompt). Uma LoRA que o Clef acrescenta
    nunca derruba uma do prompt: é ela que fica de fora. `notes` recebe uma frase para cada caso.
    """
    stack: dict[str, dict] = {}
    notes = notes if notes is not None else []

    def put(item: dict, origin: str):
        lid = item["id"]
        entry = lib.get(lid)
        if entry is None:
            return
        s, c = _strengths(entry, item)
        explicit = "strength" in item
        cur = stack.get(lid)
        origin = cur["origin"] if cur else origin
        if s > 0 and not cur:
            for other in [o for o in stack if stack[o]["strength"] > 0 and clash(lib, lid, o)]:
                if origin == "clef" and stack[other]["origin"] != "base":
                    notes.append(f"'{entry.get('title') or lid}' ficou de fora: não funciona junto com "
                                 f"'{stack[other]['title']}', que o prompt escolhido já usa.")
                    return
                notes.append(f"'{stack[other]['title']}' foi desligada: não funciona junto com "
                             f"'{entry.get('title') or lid}'.")
                del stack[other]
        stack[lid] = {"id": lid, "file": entry.get("file", ""), "title": entry.get("title") or lid,
                      "strength": s, "strength_clip": c, "prompt": pick_prompt(entry.get("prompt"), mode),
                      "origin": origin}
        if s > 0 and not explicit and origin != "base":
            for other, val in (entry.get("adjust") or {}).items():
                if other in stack:
                    stack[other]["strength"] = stack[other]["strength_clip"] = float(val)

    for it in base or []:
        put(lora_item(it), "base")
    for it in preset_items or []:
        it = lora_item(it)
        if it.get("mode") not in (None, mode):
            continue
        put(it, "preset")
    for lid in extras or []:
        put({"id": lid}, "clef")
    return [v for v in stack.values() if v["strength"] > 0 or v["strength_clip"] > 0]


def merged_settings(lib: dict[str, dict], stack: list[dict], preset_settings: dict | None,
                    defaults: dict | None = None) -> dict:
    """Ajustes do grafo: padrão do workflow < LoRAs ligadas (na ordem) < prompt escolhido."""
    out = dict(defaults or {})
    for it in stack:
        if it["origin"] != "base":
            out.update((lib.get(it["id"]) or {}).get("settings") or {})
    out.update(preset_settings or {})
    return out


def knob_plan(knobs: dict, settings: dict) -> tuple[dict[tuple[Any, str], Any], set[str]]:
    """Traduz ajustes (`resolution: 1024`, `scene_mp: off`) em substituições de widget e nós ignorados."""
    overrides: dict[tuple[Any, str], Any] = {}
    bypass: set[str] = set()
    for name, spec in (knobs or {}).items():
        if name not in settings:
            continue
        value = settings[name]
        nodes = spec.get("nodes") or [spec["node"]]
        if spec.get("bypass") and is_off(value):
            bypass.update(str(n) for n in nodes)
            continue
        for n in nodes:
            overrides[(_nid(n), spec["widget"])] = value
    return overrides, bypass


def _nid(x: Any) -> Any:
    return int(x) if isinstance(x, int) or str(x).isdigit() else str(x)


def resolve(lib: dict[str, dict], m: dict, preset: dict, extras: list[str], mode: str | None) -> dict:
    """Tudo que um prompt + modo + LoRAs do Clef mudam no grafo de `m` (o manifesto do modo já aplicado)."""
    notes: list[str] = []
    stack = effective_loras(lib, m.get("lora_base"), preset.get("loras"), extras, mode, notes) if m.get("lora_chain") else []
    defaults = {k: v["default"] for k, v in (m.get("knobs") or {}).items() if "default" in v}
    settings = merged_settings(lib, stack, preset.get("settings"), defaults)
    overrides, bypass = knob_plan(m.get("knobs") or {}, settings)
    return {"stack": stack, "settings": settings, "overrides": overrides, "bypass": bypass, "notes": notes}


# ------------------------------------------------------------------ prompt
def _clean(text: str) -> str:
    out = re.sub(r"[ \t]+\n", "\n", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def compose(text: str, request: str, stack: list[dict]) -> str:
    """Junta, sem LLM, o texto do prompt, o pedido e as instruções das LoRAs.

    `{request}` é o pedido; `{loras}` é onde entram as instruções de TODAS as LoRAs ligadas.
    Sem `{loras}` no texto, só as instruções das LoRAs que o Clef acrescentou vão antes do texto
    (as da pilha do prompt já estão escritas nele).
    """
    snippets_all = [i["prompt"] for i in stack if i["prompt"] and i["origin"] != "base"]
    snippets_clef = [i["prompt"] for i in stack if i["prompt"] and i["origin"] == "clef"]
    req = request.strip()
    if "{loras}" in text:
        out = text.replace("{loras}", "\n".join(snippets_all))
    elif snippets_clef:
        out = "\n".join(snippets_clef) + "\n" + text
    else:
        out = text
    had_req = "{request}" in out
    out = out.replace("{request}", req)
    if had_req and not req:
        out = re.sub(r"\s+([.,;:])", r"\1", out)
        out = re.sub(r"(?im)^\s*(extra instructions|instruções extras)\s*:\s*$", "", out)
    return _clean(out)


def describe_stack(stack: list[dict], lib: dict[str, dict]) -> str:
    rows = []
    for i in stack:
        if i["origin"] == "base":
            continue
        d = (lib.get(i["id"]) or {}).get("description") or i["title"]
        rows.append(f"- {i['title']}: {d}")
    return "\n".join(rows)
