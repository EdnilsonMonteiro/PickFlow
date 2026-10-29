"""Agente que cria casos de uso (receitas) quando nenhuma rota existente cobre o pedido.

Quando o Clef aponta uma lacuna (`Router.check_gap`), um LLM conversa em rodadas com ferramentas, como um agente:

  show_workflow / show_recipe / list_loras   conhece o catálogo (workflows, prompts, modos, LoRAs, casos de uso)
  ask_clef                                  pergunta de múltipla escolha ao Clef (rápido, vê as imagens)
  draft                                     confere um rascunho (a aplicação diz o que está errado)
  finish / give_up                          entrega o caso de uso final ou desiste

Travas contra excesso: só roda com a lacuna confirmada pelo Clef (ou pedido manual); limite de rodadas e de
consultas ao Clef; o resultado passa por `check_recipe` (as mesmas regras da tela); caso idêntico a um existente é
reaproveitado em vez de criado; o id é sempre inédito (nunca sobrescreve); os criados ficam em teste e o mais antigo
sai quando passa do limite (`agent.max_trial`); o usuário confirma antes de guardar (`agent.confirm`).
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .clef import MAX_OPTIONS
from .router import Ctx, Router, RouterError, recipe_fits, required_images
from .store import ID_RE, Store, StoreError

SOURCE_RE = re.compile(r"^(user:[1-8]|previous|none|role:[a-z0-9_]+)$")
Emit = Callable[[str], Awaitable[None]]


class AgentError(Exception):
    pass


def slug(text: str, prefix: str = "") -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:40] or "caso"
    return (prefix + t)[:60]


# ----------------------------------------------------------- parecido (sem LLM)
def recipe_overlap(a: dict, b: dict) -> float:
    """Quanto dois casos de uso se parecem só pela estrutura (0 a 1): sequência de workflows, prompts, modos e nº de
    imagens. É a pré-triagem barata; quem dá a nota final é o Clef (`recipe_merge.score_pairs`)."""
    wa = [s["workflow"] for s in a.get("steps", [])]
    wb = [s["workflow"] for s in b.get("steps", [])]
    if not wa or not wb:
        return 0.0
    ka = [f"{s['workflow']}:{s.get('prompt') or ''}:{s.get('mode') or ''}" for s in a["steps"]]
    kb = [f"{s['workflow']}:{s.get('prompt') or ''}:{s.get('mode') or ''}" for s in b["steps"]]
    if ka == kb and (not a.get("images") or not b.get("images") or a["images"] == b["images"]):
        return 1.0
    r_wf = difflib.SequenceMatcher(None, wa, wb).ratio()
    r_all = difflib.SequenceMatcher(None, ka, kb).ratio()
    same_n = 1.0 if (not a.get("images") or not b.get("images") or a["images"] == b["images"]) else 0.0
    return round(0.55 * r_wf + 0.3 * r_all + 0.15 * same_n, 3)


def recipe_signature(r: dict) -> tuple:
    """Identidade exata de um caso de uso: mesmos workflows, prompts, modos, instruções e ligações de entrada, na mesma
    ordem. Só isso conta como "o mesmo caso": um passo a mais (ex.: melhorar a imagem antes de colar) já é outro caso."""
    return tuple((s["workflow"], s.get("prompt") or "", s.get("mode") or "", s.get("request") or "",
                  tuple(sorted((s.get("inputs") or {}).items()))) for s in r.get("steps", []))


# --------------------------------------------------------------- conferência
@dataclass
class Check:
    recipe: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    similar: list[dict] = field(default_factory=list)       # [{id, name, score}] parecidos já existentes
    same_as: str | None = None                              # idêntico a um existente que serve para este pedido

    @property
    def ok(self) -> bool:
        return self.recipe is not None and not self.errors


def check_recipe(store: Store, raw: Any, n_imgs: int | None, *, max_steps: int = 4,
                 new: bool = True, prefix: str = "auto_", detect_same: bool = True) -> Check:
    """As regras que um caso de uso criado por LLM precisa cumprir. `new=True` dá um id inédito (auto_...)."""
    out = Check()
    if not isinstance(raw, dict):
        out.errors.append("O caso de uso precisa ser um objeto JSON com name, description e steps.")
        return out
    rec = json.loads(json.dumps(raw))
    taken = {r.get("id") for r in store.clef_config()["recipes"]}
    if new:
        rid = slug(str(rec.get("id") or rec.get("name") or ""), prefix)
        base, n = rid, 2
        while rid in taken:
            rid, n = f"{base}_{n}"[:60], n + 1
        rec["id"] = rid
    if not str(rec.get("name") or "").strip():
        out.errors.append("Falta o nome (name).")
    if len(str(rec.get("description") or "").strip()) < 40:
        out.errors.append("A descrição (description) precisa explicar em geral QUANDO usar o caso de uso, com pelo "
                          "menos uma ou duas frases: é o que o Clef lê para escolher. Não copie o pedido do usuário.")
    if n_imgs and not rec.get("images"):
        rec["images"] = n_imgs
    if n_imgs and rec.get("images") and int(rec["images"]) != n_imgs:
        out.errors.append(f"images deve ser {n_imgs} (as imagens que o usuário enviou), não {rec['images']}.")
    try:
        norm = store.normalize_recipe(rec)
    except (StoreError, ValueError, TypeError, KeyError, AttributeError) as e:
        out.errors.append(str(e))
        return out
    steps = norm["steps"]
    if len(steps) > max_steps:
        out.errors.append(f"No máximo {max_steps} passos (veio {len(steps)}). Junte passos ou escolha o essencial.")
    usable = store.enabled_workflows()
    roles = {r["key"] for r in norm.get("roles") or []}
    used_roles: set[str] = set()
    used_users: set[int] = set()
    for i, st in enumerate(steps, 1):
        wid = st["workflow"]
        tag = f"Passo {i} ({wid})"
        if wid not in usable:
            out.errors.append(f"{tag}: o workflow está desativado.")
            continue
        m = store.manifest(wid)
        slots = {s["key"]: s for s in m.get("images", [])}
        for k, v in (st.get("inputs") or {}).items():
            if k not in slots:
                out.errors.append(f"{tag}: entrada '{k}' não existe. Entradas: {', '.join(slots) or 'nenhuma'}.")
                continue
            if not isinstance(v, str) or not SOURCE_RE.match(v):
                out.errors.append(f"{tag}: origem '{v}' inválida em {k}. Use user:N, previous, role:<papel> ou none.")
                continue
            if v == "previous" and i == 1:
                out.errors.append(f"{tag}: o primeiro passo não tem 'previous'.")
            if v == "none" and slots[k].get("required", True):
                out.errors.append(f"{tag}: a entrada '{k}' é obrigatória; não pode ser 'none'.")
            if v.startswith("user:"):
                n = int(v.split(":")[1])
                used_users.add(n)
                if n_imgs and n > n_imgs:
                    out.errors.append(f"{tag}: user:{n} não existe (o usuário enviou {n_imgs} imagem(ns)).")
            if v.startswith("role:"):
                used_roles.add(v.split(":", 1)[1])
                if v.split(":", 1)[1] not in roles:
                    out.errors.append(f"{tag}: o papel '{v.split(':', 1)[1]}' não foi declarado em roles.")
        missing = [k for k, s in slots.items() if s.get("required", True) and k not in (st.get("inputs") or {})]
        if missing and not (i == 1 and not slots):
            out.warnings.append(f"{tag}: sem origem definida para {', '.join(missing)}; o Clef decide na hora.")
        if st.get("prompt"):
            ids = {p["id"] for p in store.prompts(wid)["presets"]}
            if st["prompt"] not in ids:
                out.errors.append(f"{tag}: o prompt '{st['prompt']}' não existe. Prompts: {', '.join(sorted(ids))}.")
        if st.get("mode") and st["mode"] not in (m.get("modes") or {}):
            out.errors.append(f"{tag}: modo '{st['mode']}' não existe. Modos: {', '.join(m.get('modes') or {}) or 'nenhum'}.")
        if st.get("loras"):
            have = store.loras()
            bad = [x for x in st["loras"] if x not in have]
            if bad:
                out.errors.append(f"{tag}: LoRA inexistente: {', '.join(bad)}.")
    if len(steps) == 1 and not steps[0].get("inputs") and not steps[0].get("request"):
        out.errors.append("Um passo só, sem entradas nem instrução própria, é igual a escolher o workflow solto: "
                          "não precisa de caso de uso. Desista (give_up) ou acrescente o que falta.")
    if roles - used_roles:
        out.warnings.append(f"Papéis sem uso: {', '.join(sorted(roles - used_roles))}.")
    if n_imgs and not roles and used_users and set(range(1, n_imgs + 1)) - used_users:
        out.warnings.append("Imagens que nenhum passo usa: " + ", ".join(
            str(n) for n in sorted(set(range(1, n_imgs + 1)) - used_users)) + ".")
    if roles and n_imgs and n_imgs < len(roles):
        out.errors.append(f"Há {len(roles)} papéis, mas só {n_imgs} imagem(ns).")
    for ex in store.clef_config()["recipes"]:
        sc = recipe_overlap(norm, ex)
        if sc >= 0.5:
            out.similar.append({"id": ex["id"], "name": ex.get("name", ex["id"]), "score": sc})
        if detect_same and usable_ex_of(ex, n_imgs) and recipe_signature(ex) == recipe_signature(norm) and out.same_as is None:
            out.same_as = ex["id"]
    out.similar.sort(key=lambda x: -x["score"])
    out.similar = out.similar[:3]
    out.recipe = norm
    return out


# ------------------------------------------------------------------ catálogo
def usable_ex_of(ex: dict, n_imgs: int | None) -> bool:
    return bool(ex.get("enabled", True)) and (n_imgs is None or recipe_fits(ex, n_imgs))


def workflow_card(store: Store, wid: str, full: bool = False) -> dict:
    m = store.manifest(wid)
    presets = store.active_presets(wid)
    card: dict[str, Any] = {
        "id": wid, "titulo": m.get("title", wid), "grupo": m.get("group", ""),
        "para_que_serve": store.describe(wid)[: 700 if full else 260],
        "entradas": [{"key": s["key"], "nome": s.get("label", s["key"]), "obrigatoria": s.get("required", True)}
                     for s in m.get("images", [])],
        "modos": list((m.get("modes") or {}).keys()),
        "prompts": [{"id": p["id"], "nome": p["name"], "quando": (p.get("when") or "")[: 200 if full else 90]}
                    for p in presets],
    }
    if m.get("internal"):
        card["interno"] = "passo de caso de uso: só roda dentro de uma sequência"
    if m.get("review") == "cutout":
        card["obs"] = "o usuário confere o recorte na tela"
    draws = [p.get("type") for p in m.get("params", []) if p.get("type") in ("box", "crop", "place")]
    if draws:
        card["interage"] = "o usuário desenha/ajusta na tela: " + ", ".join(sorted(set(draws)))
    if full:
        card["variaveis_dos_prompts"] = {
            p["id"]: [{"key": v["key"], "tipo": v.get("kind"), "pergunta": v.get("label") or v.get("ask", "")}
                      for v in p.get("vars") or []] for p in presets if p.get("vars")}
        card["lora_chain"] = bool(m.get("lora_chain"))
    return card


def recipe_line(r: dict) -> dict:
    return {"id": r["id"], "nome": r.get("name"), "imagens": r.get("images"), "ativo": r.get("enabled", True),
            "passos": [f"{s['workflow']}" + (f"({s['prompt']})" if s.get("prompt") else "") for s in r["steps"]],
            "quando": (r.get("description") or "")[:160]}


SCHEMA = """\
FORMATO DE UM CASO DE USO (JSON):
{
  "name": "Nome curto e geral",
  "description": "Quando usar, em termos GERAIS (quantas imagens, que tipo de pedido, o que faz e o que NÃO faz). O Clef lê isto para escolher o caso de uso no futuro. Não copie o pedido atual.",
  "images": 2,                       // quantas imagens o usuário envia (a aplicação confere)
  "roles": [                         // opcional: quem é quem, decidido pelo Clef olhando as imagens e o pedido
    {"key": "pessoa", "label": "Pessoa", "about": "Quem aparece no resultado.", "default": 2},
    {"key": "cenario", "label": "Cenário", "about": "O lugar onde a pessoa vai ficar.", "default": 1}
  ],
  "steps": [                         // 1 a __MAX__ passos, executados em ordem
    {
      "workflow": "id_do_workflow",   // só ids do catálogo
      "prompt": "id_do_prompt",       // um dos prompts do workflow (omita para o Clef escolher)
      "mode": "turbo",                // opcional: um dos modos do workflow
      "inputs": {"image_1": "role:pessoa", "image_2": "user:1"},
                                      // chave = entrada do workflow; valor = user:N (imagem N enviada),
                                      // previous (resultado do passo anterior), role:<papel> ou none (só entrada opcional)
      "request": "Instrução só deste passo (omita para usar o pedido inteiro do usuário). {request} insere o pedido."
    }
  ]
}
REGRAS: ids do catálogo; o 1º passo não usa previous; entradas obrigatórias precisam de origem; prefira roles quando a
ordem das imagens pode mudar entre pedidos; use "request" quando um passo faz SÓ uma parte do pedido (ex.: "Colorize this
photo and improve its quality; keep the person identical"), em inglês curto e imperativo, pois vira o prompt do modelo de imagem.
Se um workflow solto já resolve, desista. Não invente workflows, prompts, modos nem LoRAs."""

SYSTEM = """\
Você é o agente que monta CASOS DE USO novos no PickFlow, uma aplicação que edita imagens com workflows do ComfyUI.
Um caso de uso é uma sequência de workflows (cada passo recebe imagens do usuário ou o resultado do passo anterior).
O Clef é um modelo rápido que vê as imagens e decide perguntas de múltipla escolha; ele já escolheu um plano e apontou
que ele NÃO cobre o pedido inteiro. Sua missão: entregar UM caso de uso válido que cumpra o pedido, ou desistir.

Como trabalhar, uma ferramenta por resposta, SEMPRE em JSON puro, um objeto por vez:
{"action": "show_workflow", "id": "edit"}                      detalhes de um workflow (prompts, variáveis, entradas)
{"action": "show_recipe", "id": "colar_e_fundir"}             um caso de uso existente completo
{"action": "list_loras"}                                       LoRAs disponíveis (ids e quando usar)
{"action": "ask_clef", "question": "...", "options": {"a": "descrição", "b": "descrição"}}
                                                               o Clef escolhe entre 2 e 26 opções olhando as imagens
{"action": "draft", "recipe": { ... }}                         a aplicação confere e diz os erros e avisos
{"action": "finish", "recipe": { ... }}                        entrega final (omita recipe para usar o último rascunho válido)
{"action": "give_up", "reason": "..."}                         se um caso existente já serve ou não há como montar
Inclua em cada resposta um campo curto "thought" (por que esta ferramenta).

Princípios:
1. Reaproveite: os casos de uso e workflows do catálogo já fazem muita coisa. Só crie o que falta e junte pedaços existentes.
2. Pense nos dados que passam entre passos: o que o passo 1 produz, quem recebe no passo 2.
3. Tarefas que dependem de ver a imagem (qual imagem é a pessoa? tem pessoa nela? qual prompt do workflow?) vão para o
   Clef com ask_clef; você não vê as imagens. Use poucas consultas (limite __CLEF__).
4. Seja econômico: poucos passos, sem etapas que o pedido não exige. Você tem __ROUNDS__ respostas no total.
5. Descreva o caso de uso de forma geral para ser reaproveitado, mas de modo que se distinga dos existentes.
6. O Clef lê inglês: escreva em INGLÊS as perguntas e opções do ask_clef e, no caso de uso, "description", o "about" de
   cada papel e o "request" dos passos. "name" e "label" (texto de tela) podem ficar no idioma do pedido.

""" + SCHEMA


@dataclass
class Built:
    recipe: dict | None = None
    reason: str = ""
    reuse: str | None = None            # id de um caso de uso existente que já faz o mesmo
    warnings: list[str] = field(default_factory=list)
    similar: list[dict] = field(default_factory=list)
    rounds: int = 0
    log: list[str] = field(default_factory=list)


def parse_json(text: str) -> Any:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("sem JSON")
    return json.loads(m.group(0))


def _clip(obj: Any, limit: int = 5000) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…(cortado)"


class RecipeAgent:
    def __init__(self, store: Store, llm):
        self.store, self.llm = store, llm

    def settings(self) -> dict:
        return self.store.settings().get("agent") or {}

    def intro(self, ctx: Ctx, gap: dict | None, hint: str = "") -> str:
        enabled = self.store.enabled_workflows()
        wfs = [workflow_card(self.store, w) for w in enabled]
        imgs = [{"n": i + 1, "arquivo": im.name, "tamanho": f"{im.width}x{im.height}",
                 "conteudo": ctx.contents.get(i + 1, "não identificado")} for i, im in enumerate(ctx.images)]
        plan = f" O plano do Clef foi '{gap['route_name']}', que não cobre tudo." if gap else ""
        return (f"PEDIDO DO USUÁRIO: {ctx.request}\n\nIMAGENS ENVIADAS ({len(ctx.images)}): {_clip(imgs, 1500)}\n"
                f"{plan}{(' ' + hint) if hint else ''}\n\nWORKFLOWS ATIVOS (use só estes ids):\n{_clip(wfs, 9000)}\n\n"
                f"CASOS DE USO JÁ EXISTENTES:\n{_clip([recipe_line(r) for r in self.store.enabled_recipes()], 5000)}\n\n"
                "Comece pela ferramenta que preferir.")

    async def build(self, ctx: Ctx, router: Router, gap: dict | None = None, emit: Emit | None = None,
                    hint: str = "") -> Built:
        cfg = self.settings()
        rounds = max(2, int(cfg.get("max_rounds", 8)))
        max_clef = int(cfg.get("max_clef_calls", 6))
        max_steps = int(cfg.get("max_steps", 4))
        n = len(ctx.images)
        say = emit or (lambda _t: _noop())
        out = Built()
        system = (SYSTEM.replace("__MAX__", str(max_steps)).replace("__CLEF__", str(max_clef))
                  .replace("__ROUNDS__", str(rounds)))
        msgs: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": self.intro(ctx, gap, hint)}]
        last_ok: Check | None = None
        clef_calls = 0
        for rnd in range(1, rounds + 1):
            out.rounds = rnd
            try:
                text = await self.llm.chat(msgs, temperature=0.2, max_tokens=1800, json_mode=True)
            except Exception as e:  # noqa: BLE001
                out.reason = f"O LLM não respondeu: {e}"
                return out
            msgs.append({"role": "assistant", "content": text})
            try:
                act = parse_json(text)
                name = str(act.get("action", ""))
            except (ValueError, AttributeError):
                msgs.append({"role": "user", "content": 'Responda só com um objeto JSON, ex.: {"action": "list_loras"}.'})
                continue
            out.log.append(name + (f": {act.get('thought')}" if act.get("thought") else ""))
            left = rounds - rnd
            if name == "give_up":
                out.reason = str(act.get("reason") or "o agente desistiu")
                return out
            if name in ("draft", "finish"):
                raw = act.get("recipe")
                if raw is None and last_ok is not None and name == "finish":
                    raw = last_ok.recipe
                ck = check_recipe(self.store, raw, n, max_steps=max_steps)
                if ck.ok:
                    last_ok = ck
                if name == "finish" and ck.ok:
                    out.warnings, out.similar = ck.warnings, ck.similar
                    if ck.same_as:
                        out.reuse = ck.same_as
                        out.reason = f"já existe um caso de uso igual ({ck.same_as})"
                        return out
                    out.recipe = ck.recipe
                    return out
                msgs.append({"role": "user", "content": _clip({
                    "resultado": "ok" if ck.ok else "com erros", "erros": ck.errors, "avisos": ck.warnings,
                    "parecidos_existentes": ck.similar,
                    "idêntico_a": ck.same_as, "respostas_restantes": left,
                    "proximo": ("Se estiver bom, use finish." if ck.ok else "Corrija e envie draft ou finish de novo.")
                                + (" Se for igual a um existente, use give_up." if ck.same_as else "")})})
            elif name == "show_workflow":
                wid = str(act.get("id", ""))
                msgs.append({"role": "user", "content": _clip(
                    workflow_card(self.store, wid, True) if wid in self.store.enabled_workflows()
                    else {"erro": f"workflow '{wid}' inexistente ou desativado"})})
            elif name == "show_recipe":
                rid = str(act.get("id", ""))
                r = next((x for x in self.store.clef_config()["recipes"] if x["id"] == rid), None)
                msgs.append({"role": "user", "content": _clip(r or {"erro": f"caso de uso '{rid}' inexistente"})})
            elif name == "list_loras":
                msgs.append({"role": "user", "content": _clip([
                    {"id": i, "titulo": l.get("title"), "quando": (l.get("description") or "")[:160], "auto": l.get("auto")}
                    for i, l in self.store.loras().items()], 6000)})
            elif name == "ask_clef":
                opts = act.get("options")
                if clef_calls >= max_clef:
                    msgs.append({"role": "user", "content": f"Limite de {max_clef} consultas ao Clef atingido; decida com o que já sabe."})
                elif not (isinstance(opts, dict) and 2 <= len(opts) <= MAX_OPTIONS and act.get("question")):
                    msgs.append({"role": "user", "content": f"ask_clef precisa de question e options (2 a {MAX_OPTIONS} opções {{id: descrição}})."})
                else:
                    clef_calls += 1
                    await say("Agente: consultando o Clef…")
                    try:
                        ans = await router.consult(ctx, str(act["question"]), {str(k): str(v) for k, v in opts.items()})
                        msgs.append({"role": "user", "content": _clip({
                            "escolha": ans.choice, "confianca": round(ans.confidence, 3),
                            "probabilidades": {k: round(p, 3) for k, p in ans.top(5)},
                            "consultas_restantes": max_clef - clef_calls})})
                    except RouterError as e:
                        msgs.append({"role": "user", "content": f"O Clef não respondeu: {e}"})
            else:
                msgs.append({"role": "user", "content": f"Ferramenta desconhecida '{name}'. Use show_workflow, show_recipe, "
                                                        "list_loras, ask_clef, draft, finish ou give_up."})
            if left == 1:
                msgs.append({"role": "user", "content": "ÚLTIMA resposta: entregue com finish (ou give_up)."})
        if last_ok is not None:                      # acabaram as rodadas: vale o último rascunho válido
            if last_ok.same_as:
                out.reuse, out.reason = last_ok.same_as, f"já existe um caso de uso igual ({last_ok.same_as})"
            else:
                out.recipe, out.warnings, out.similar = last_ok.recipe, last_ok.warnings, last_ok.similar
            return out
        out.reason = f"O agente usou as {rounds} respostas sem chegar a um caso de uso válido."
        return out


async def _noop():
    return None
