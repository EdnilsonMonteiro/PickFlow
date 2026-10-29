"""Casos de uso repetidos: o Clef dá a nota de parecido e um LLM funde os que você escolher num só, mais geral.

  score_pairs   compara os casos de uso dois a dois. Pré-triagem pela estrutura (`recipe_overlap`, sem custo); depois o
                Clef lê a descrição e os passos de cada par e diz se são o mesmo caso, parecidos ou diferentes.
  merge_recipes o LLM propõe UM caso de uso que cobre todos os escolhidos (papéis no lugar da ordem fixa das imagens,
                descrição geral). A proposta passa por `check_recipe`; nada é gravado antes de você aprovar.
  apply_merge   grava o caso novo e desativa (ou apaga) os originais. Nunca troca um id existente.
"""
from __future__ import annotations

from typing import Any

from .clef import ClefError, choice_question
from .recipe_agent import SCHEMA, Check, check_recipe, parse_json, recipe_line, recipe_overlap, _clip
from .router import Ctx, Router
from .store import Store, StoreError

PAIR_CRITERIA = {
    "same": "Practically the SAME use case: any request one of them serves, the other would serve too. One of the two is unnecessary.",
    "overlap": "SIMILAR: they cover part of the same requests, but each has a use of its own that the other does not cover.",
    "different": "DIFFERENT: they serve different requests.",
}
WEIGHT = {"same": 1.0, "overlap": 0.5, "different": 0.0}
BATCH = 6


def describe(r: dict) -> str:
    steps = " -> ".join(s["workflow"] + (f"({s['prompt']})" if s.get("prompt") else "") for s in r["steps"])
    roles = "; ".join(f"{x['key']}={x.get('label')}" for x in r.get("roles") or [])
    return (f"'{r.get('name')}' (images: {r.get('images') or 'any'}{', roles: ' + roles if roles else ''}). "
            f"When to use: {(r.get('description') or '').strip()[:500]} Steps: {steps}.")


def level(score: float) -> str:
    return "alta" if score >= 0.7 else "média" if score >= 0.45 else "baixa"


def groups_of(pairs: list[dict], above: float = 0.6) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p["score"] >= above:
            parent[find(p["a"])] = find(p["b"])
    out: dict[str, list[str]] = {}
    for x in list(parent):
        out.setdefault(find(x), []).append(x)
    return sorted((sorted(v) for v in out.values() if len(v) > 1), key=lambda v: -len(v))


async def score_pairs(store: Store, clef, ids: list[str] | None = None, max_pairs: int = 60,
                      keep_above: float = 0.3) -> dict[str, Any]:
    """Nota de repetição (0 a 1) de cada par de casos de uso ativos. `source` diz se o Clef participou."""
    recipes = [r for r in store.clef_config()["recipes"] if r.get("enabled", True) and (not ids or r["id"] in ids)]
    pairs = [(a, b) for i, a in enumerate(recipes) for b in recipes[i + 1:]]
    pairs.sort(key=lambda ab: -recipe_overlap(*ab))
    truncated = len(pairs) > max_pairs
    pairs = pairs[:max_pairs]
    out: list[dict] = []
    clef_ok = clef is not None
    router = Router(store, clef) if clef is not None else None
    for k in range(0, len(pairs), BATCH):
        chunk = pairs[k:k + BATCH]
        answers: dict = {}
        if router is not None and clef_ok:
            qs = {f"p{j}": choice_question(
                "Two use cases of an image editing application. Do they repeat each other?\n"
                f"A: {describe(a)}\nB: {describe(b)}", dict(PAIR_CRITERIA)) for j, (a, b) in enumerate(chunk)}
            try:
                answers = await clef.decide(router._state(Ctx("", []), "recipe_similarity"), qs, [])
            except ClefError:
                clef_ok = False
        for j, (a, b) in enumerate(chunk):
            struct = recipe_overlap(a, b)
            ans = answers.get(f"p{j}")
            if ans is not None:
                probs = {c: float(ans.probs.get(c, 0.0)) for c in WEIGHT}
                tot = sum(probs.values()) or 1.0
                probs = {c: round(v / tot, 3) for c, v in probs.items()}
                from_clef = sum(WEIGHT[c] * v for c, v in probs.items())
                score = round(0.75 * from_clef + 0.25 * struct, 3)
            else:
                probs, score = None, struct
            out.append({"a": a["id"], "b": b["id"], "a_name": a.get("name", a["id"]), "b_name": b.get("name", b["id"]),
                        "score": score, "level": level(score), "structure": struct, "clef": probs})
    out = sorted((p for p in out if p["score"] >= keep_above), key=lambda p: -p["score"])
    return {"pairs": out, "groups": groups_of(out), "checked": len(pairs), "truncated": truncated,
            "source": "clef" if clef_ok and clef is not None else "estrutura", "recipes": len(recipes)}


MERGE_SYSTEM = """\
Você funde casos de uso repetidos do PickFlow (edição de imagens com workflows do ComfyUI) em UM caso de uso mais geral.
Você recebe 2 a 5 casos de uso. Entregue um único caso que atenda a TODOS os pedidos que os originais atendiam, sem
atender o que eles não atendiam.

Como unificar: se os originais diferem só na ORDEM das imagens (ex.: fundo primeiro ou pessoa primeiro), use "roles"
(o Clef decide quem é quem olhando as imagens e o pedido) e entradas "role:<papel>". Se diferem em passos opcionais,
fique com a sequência que serve a todos ou diga que não dá. Escreva a descrição em termos gerais, citando os casos
que ela substitui. Mantenha só workflows, prompts, modos e LoRAs que existem nos originais.

Responda SÓ com JSON:
{"mergeable": true, "reason": "por que dá para fundir", "notes": "o que mudou em relação aos originais", "recipe": { ... }}
ou {"mergeable": false, "reason": "por que são casos diferentes demais"}.

""" + SCHEMA


async def merge_recipes(store: Store, llm, ids: list[str]) -> dict[str, Any]:
    all_r = {r["id"]: r for r in store.clef_config()["recipes"]}
    ids = list(dict.fromkeys(ids))
    missing = [i for i in ids if i not in all_r]
    if missing:
        raise StoreError(f"Caso de uso inexistente: {', '.join(missing)}.")
    if not 2 <= len(ids) <= 5:
        raise StoreError("Escolha de 2 a 5 casos de uso para fundir.")
    src = [{k: v for k, v in all_r[i].items() if k != "generated"} for i in ids]
    cfg = store.settings().get("agent") or {}
    max_steps = max(int(cfg.get("max_steps", 4)), *(len(r["steps"]) for r in src))
    counts = {r.get("images") for r in src}
    n_imgs = next(iter(counts)) if len(counts) == 1 and None not in counts else None
    msgs = [{"role": "system", "content": MERGE_SYSTEM.replace("__MAX__", str(max_steps))},
            {"role": "user", "content": "CASOS DE USO A FUNDIR:\n" + _clip(src, 9000)}]
    last_errors: list[str] = []
    for attempt in range(3):
        text = await llm.chat(msgs, temperature=0.2, max_tokens=2000, json_mode=True)
        msgs.append({"role": "assistant", "content": text})
        try:
            data = parse_json(text)
        except (ValueError, AttributeError):
            msgs.append({"role": "user", "content": "Responda só com o objeto JSON pedido."})
            last_errors = ["O LLM não devolveu JSON."]
            continue
        if data.get("mergeable") is False:
            return {"mergeable": False, "reason": str(data.get("reason") or "são casos diferentes demais"),
                    "replaces": ids}
        ck: Check = check_recipe(store, data.get("recipe"), n_imgs, max_steps=max_steps, prefix="", detect_same=False)
        if ck.ok:
            return {"mergeable": True, "reason": str(data.get("reason") or ""), "notes": str(data.get("notes") or ""),
                    "recipe": ck.recipe, "warnings": ck.warnings, "replaces": ids}
        last_errors = ck.errors
        msgs.append({"role": "user", "content": "O caso de uso tem erros, corrija e responda de novo no mesmo formato:\n"
                                                + _clip(ck.errors, 2000)})
    return {"mergeable": False, "reason": "O LLM não chegou a um caso de uso válido: " + "; ".join(last_errors),
            "replaces": ids}


def apply_merge(store: Store, recipe: dict, replaces: list[str], mode: str = "disable") -> dict:
    """Grava o caso fundido e desativa (padrão) ou apaga os originais. Id inédito sempre; nada é sobrescrito."""
    cfg = store.clef_config()
    existing = {r["id"] for r in cfg["recipes"]}
    missing = [i for i in replaces if i not in existing]
    if missing:
        raise StoreError(f"Caso de uso inexistente: {', '.join(missing)}.")
    rid, n = recipe.get("id") or "caso_fundido", 2
    base = rid
    while rid in existing:
        rid, n = f"{base}_{n}", n + 1
    new = store.normalize_recipe({**recipe, "id": rid, "enabled": True})
    new.pop("generated", None)
    recipes = []
    for r in cfg["recipes"]:
        if r["id"] in replaces:
            if mode == "delete":
                continue
            r = {**r, "enabled": False}
        recipes.append(r)
    recipes.append(new)
    cfg["recipes"] = recipes
    store.save_clef_config(cfg)
    return new
