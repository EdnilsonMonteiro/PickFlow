"""Decide, com o Clef, qual workflow (ou sequência) atende ao pedido.

O Clef só devolve probabilidades. Por isso o roteador transforma cada decisão em
perguntas `choice`:
  plan:  `route` (workflow ou caso de uso) e `then` (precisa de mais um workflow depois?)
  bind:  `preset` (qual prompt do workflow) e `slot_<imagem>` (qual imagem vai em cada entrada)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .clef import MAX_OPTIONS, Answer, ClefError, choice_question
from . import pvars
from .i18n import en
from .loras import clash, lora_item
from .store import Store

NONE = "none"


@dataclass
class ImageRef:
    id: str
    name: str
    data: bytes
    width: int = 0
    height: int = 0
    quality: str = ""        # medidas da foto em inglês ("1600x1200, 1.9 MP, sharp detail"): o Clef lê isto


@dataclass
class Source:
    key: str            # user_1, user_2, previous
    label: str
    data: bytes
    url: str = ""


@dataclass
class Step:
    workflow: str
    prompt: str | None = None            # id do prompt fixado (caso de uso) ou None para o Clef escolher
    inputs: dict[str, str] = field(default_factory=dict)   # slot -> user:1 | previous | none
    mode: str | None = None              # modo fixado (turbo, quality...) ou None para o Clef escolher
    loras: list[str] | None = None       # LoRAs fixadas pelo caso de uso (None = o Clef decide)
    vars: dict[str, Any] | None = None   # variáveis do prompt fixadas pelo caso de uso
    request: str | None = None           # instrução só deste passo (no lugar do pedido inteiro); aceita {request}


@dataclass
class Ctx:
    request: str
    images: list[ImageRef]
    history: list[dict] = field(default_factory=list)
    contents: dict[int, str] = field(default_factory=dict)      # nº da imagem -> o que o Clef viu nela


@dataclass
class PlanResult:
    steps: list[Step]
    decisions: list[dict] = field(default_factory=list)
    ambiguous: list[dict] | None = None         # candidatos quando o Clef ficou em dúvida
    gap: dict | None = None                     # o Clef achou que nenhuma rota cobre o pedido todo (chama o agente)
    route: str = ""                             # rota FINAL que o plano vai executar (sem o "@modo"): o feedback usa esta


@dataclass
class Binding:
    preset_id: str
    slots: dict[str, str | None]                # slot -> chave da fonte (ou None)
    decisions: list[dict] = field(default_factory=list)
    mode: str | None = None                     # modo do workflow (turbo, quality...)
    loras: list[str] = field(default_factory=list)   # LoRAs que o Clef acrescentou (além das do prompt)
    vars: dict[str, Any] = field(default_factory=dict)   # respostas das variáveis do prompt (quantidade, escopo...)


class RouterError(Exception):
    pass


DRAW_TYPES = ("box", "crop", "place")      # parâmetros que o usuário desenha ou ajusta na tela


def required_images(m: dict) -> int:
    return sum(1 for s in m.get("images", []) if s.get("required", True))


def needs_drawing(m: dict) -> bool:
    return any(p.get("type") in DRAW_TYPES for p in m.get("params", [])) or any(
        s.get("mask") for s in m.get("images", []))


def decision_view(qid: str, label: str, ans: Answer, names: dict[str, str], source: str) -> dict:
    return {"question": qid, "label": label, "source": source, "chosen": ans.choice,
            "chosen_name": names.get(ans.choice or "", ans.choice), "confidence": round(ans.confidence, 3),
            "options": [{"id": k, "name": names.get(k, k), "p": round(p, 3)} for k, p in ans.top(5)]}


def weigh_option(ans: Answer, option: str, weight: float) -> Answer:
    """Multiplica a probabilidade de uma opção (e renormaliza). Na etapa seguinte, `none` pesa mais: um segundo
    workflow só entra quando o Clef está claramente convencido de que o pedido precisa dele."""
    if weight == 1 or option not in ans.probs:
        return ans
    probs = {k: p * (weight if k == option else 1.0) for k, p in ans.probs.items()}
    total = sum(probs.values()) or 1.0
    probs = {k: p / total for k, p in probs.items()}
    best = max(probs, key=probs.get)
    return Answer(best, probs[best], probs)


CONTENT_KINDS = {                      # as perguntas ao Clef são em inglês (o texto de tela fica no catálogo)
    "person": "One or more people in focus (a photo of people, a portrait, a full body).",
    "place": "A place, setting or scene (landscape, room, street, background) WITHOUT people in focus.",
    "garment": "Clothing or an outfit ON ITS OWN, with nobody wearing it (a garment, a dress, pants or a shirt laid "
               "out or on a hanger, a product photo of clothes).",
    "object": "An object, product or animal in focus, without a person.",
    "other": "Something else: a drawing, text, a screenshot, an illustration.",
}
DESCRIBE_QUESTION = "What is shown in image {}?"
CONTENT_LABEL = {"person": "person", "place": "place/setting without people",
                 "garment": "clothing alone (nobody wearing it)", "object": "object", "other": "other"}


def recipe_fits(r: dict, n_imgs: int) -> bool:
    """O caso de uso serve para esta quantidade de imagens? `images` fixa o número exato; sem ele, vale quando
    todas as imagens que os passos citam existem."""
    pinned = [int(v.split(":")[1]) for st in r.get("steps", []) for v in (st.get("inputs") or {}).values()
              if isinstance(v, str) and v.startswith("user:")]
    if r.get("images"):
        return n_imgs == int(r["images"]) and max(pinned, default=0) <= n_imgs
    return max(pinned, default=0) <= n_imgs


def best_roles(roles: list[dict], probs: dict[str, dict[str, float]], n_imgs: int) -> dict[str, int]:
    """Quem é quem: a combinação (uma imagem por papel, sem repetir) de maior probabilidade conjunta. A posição
    padrão de cada papel (ordem de envio) desempata e pesa pouco quando o Clef tem certeza."""
    import itertools
    import math
    keys = [r["key"] for r in roles]
    best, best_score = None, -1e18
    for combo in itertools.permutations(range(1, n_imgs + 1), len(keys)):
        sc = 0.0
        for k, i, role in zip(keys, combo, roles):
            sc += math.log(max(probs.get(k, {}).get(f"user_{i}", 0.0), 1e-3))
            if i == int(role.get("default") or (keys.index(k) + 1)):
                sc += 0.2
        if sc > best_score:
            best, best_score = combo, sc
    return dict(zip(keys, best or range(1, len(keys) + 1)))


PASTE_FAMILY = {"transfer_object", "recipe__colar_e_fundir", "recipe__colar_sem_fundir", "edit", "colar_fundir",
                "troca_pessoa", "troca_roupa"}
SWAP_ROUTE = "troca_pessoa"             # o fluxo padrão de trocar a pessoa; o editor ('edit') é a reserva (veja `fallback`)
PASTE_QUESTION = ("The user sent two images and wants to bring something from one into the other. Read the request "
                  "carefully: what happens to the thing that is brought in?")
PASTE_CRITERIA = {                      # "outra" primeiro: sem resposta clara, nada muda
    "outra": "Something else: copy the style, swap ONLY the face, the head or the clothing of a person for the other "
             "image's, or an edit that does not bring a whole person or object from one image into the other.",
    "trocar": "The request is a PERSON SWAP: the person in the scene becomes the person from the other image and the request "
              "says it is about the person's body, face or head ('face and body', 'body swap', 'swap the bodies', "
              "'swap their faces', 'head swap', 'change who this person is').",
    "substituir": "The WHOLE person or object from the other image goes IN PLACE OF someone or something that IS ALREADY "
                  "in the scene, which must go away ('in place of', 'replace', 'swap X for Y', 'instead of', 'I want X "
                  "where Y is').",
    "segurar": "Someone in the scene must HOLD, use or have in their hand the thing from the other image ('make him hold', "
               "'in her hand').",
    "acrescentar": "The person, character or object from the other image is ADDED to the scene somewhere (next to, behind, "
                   "on top of, sitting, on the beach, on the table) and everything already in the scene stays; nobody "
                   "leaves.",
}
PASTE_NAMES = {"trocar": "troca a pessoa (corpo e/ou rosto)", "substituir": "substitui algo que já está na cena", "segurar": "alguém segura ou usa",
               "acrescentar": "é acrescentado (o resto fica)", "outra": "outra edição"}


class Router:
    def __init__(self, store: Store, clef, feedback=None):
        self.store = store
        self.clef = clef
        self.feedback = feedback      # FeedbackStore (opcional): casos parecidos e correção das probabilidades

    # ---------------------------------------------------------------- state
    def _state(self, ctx: Ctx, task: str, extra: dict | None = None) -> dict:
        st: dict[str, Any] = {
            "task": task,
            "instructions": self.store.clef_config().get("instructions", ""),
            "request": ctx.request,
            "attached_images": [{"index": i + 1, "name": im.name, "size": f"{im.width}x{im.height}",
                                 **({"content": ctx.contents[i + 1]} if i + 1 in ctx.contents else {}),
                                 **({"quality": im.quality} if im.quality else {})}
                                for i, im in enumerate(ctx.images)],
            "conversation": ctx.history[-6:],
        }
        st.update(extra or {})
        return st

    # ------------------------------------------------------------- feedback
    def learn_cfg(self) -> dict:
        """Ajustes do aprendizado com o feedback do usuário (`settings.clef.learn`)."""
        cfg = dict((self.store.settings().get("clef") or {}).get("learn") or {})
        cfg.setdefault("enabled", True)
        cfg.setdefault("weight", 0.5)
        cfg.setdefault("examples", 3)
        cfg.setdefault("examples_per_flow", 1)
        cfg.setdefault("min_similarity", 0.55)
        cfg.setdefault("ask_preset_below", 0.5)
        return cfg

    def _examples(self, ctx: Ctx, names: dict[str, str]) -> dict | None:
        """Casos parecidos que o usuário aprovou (ou corrigiu), como exemplo no `state`. É o ganho mais barato do
        feedback: o modelo fica congelado e o prior anda. Um "era outro workflow" entra como exemplo da rota que a
        pessoa apontou — é justamente o caso em que o Clef errou. Sem histórico (ou com "exemplos" em 0), não manda
        nada."""
        cfg = self.learn_cfg()
        k = int(cfg.get("examples") or 0)
        if not self.feedback or k <= 0 or not (ctx.request or "").strip():
            return None
        try:
            # `examples` funde os casos: pedidos que apontam para o mesmo fluxo viram UMA linha com a contagem, então
            # as 3 vagas do estado mostram 3 lições diferentes (e um caso novo entra se for mais parecido)
            got = (self.feedback.examples(ctx.request, k=k, min_score=float(cfg.get("min_similarity", 0.55)),
                                          max_per_target=int(cfg.get("examples_per_flow", 1)))
                   if hasattr(self.feedback, "examples")
                   else self.feedback.similar(ctx.request, k=k, verdicts=("up", "other"),
                                              min_score=float(cfg.get("min_similarity", 0.55))))
        except Exception:  # noqa: BLE001 - aprender é um extra: nunca derruba o pedido
            return None
        out = []
        for g in got or []:
            route = str(g.get("target") or g.get("picked") or g.get("route") or "")
            if not route:
                continue
            line = {"request": str(g.get("request") or "")[:200], "chosen_flow": names.get(route, route)}
            if int(g.get("count") or 1) > 1:
                line["similar_past_requests"] = int(g["count"])
            out.append(line)
        return {"similar_requests": out} if out else None

    def _correct(self, ans: Answer, arms: dict[str, float], kind: str, prefix: str, label: str,
                 names: dict[str, str], decisions: list[dict]) -> Answer:
        """Corrige as probabilidades com o histórico (Beta-Binomial semeado pela probabilidade do Clef). Quando
        muda a resposta, registra a decisão com `source: histórico` para a pessoa ver por quê."""
        cfg = self.learn_cfg()
        if not self.feedback or not cfg.get("enabled") or not arms:
            return ans
        try:
            probs = self.feedback.apply(arms, kind=kind, weight=float(cfg["weight"]), prefix=prefix)
        except Exception:  # noqa: BLE001
            return ans
        if not probs:
            return ans
        best = max(probs, key=probs.get)
        out = Answer(best, float(probs[best]), probs)
        if best != ans.choice:
            decisions.append(decision_view(f"{kind}_learn", label, out, names, "histórico"))
        return out

    async def _maybe_ask(self, ask, qid: str, title: str, label: str, ans: Answer, names: dict[str, str],
                         below: float, decisions: list[dict]) -> Answer:
        """Com o Clef em dúvida entre opções, pergunta na tela em vez de arriscar: quem conhece a foto é a pessoa.
        Fechar a janela (ou "seguir a primeira opção") mantém a resposta do modelo. No modo de teste (`mock`) não
        pergunta: a resposta é sintética e a janela só atrapalharia."""
        if getattr(self.clef, "mode", "") != "systemone":
            return ans
        if ask is None or below <= 0 or ans.confidence >= below or len(ans.probs) < 2:
            return ans
        try:
            pick = await ask({"kind": "choose", "title": title,
                              "options": [{"id": k, "name": names.get(k, k), "p": round(p, 3)}
                                          for k, p in ans.top(3)]})
        except Exception:  # noqa: BLE001 - a tela é um extra: se falhar, vale o Clef
            return ans
        if not pick or pick == "__auto__" or pick not in ans.probs:
            return ans
        decisions.append({"question": qid + "_user", "label": label, "source": "user", "chosen": pick,
                          "chosen_name": names.get(pick, pick), "confidence": 1.0,
                          "options": [{"id": pick, "name": names.get(pick, pick), "p": 1.0}]})
        return Answer(pick, 1.0, {pick: 1.0})

    def _images(self, ctx: Ctx, extra: list[bytes] | None = None) -> list[bytes]:
        return [im.data for im in ctx.images] + list(extra or [])

    # ------------------------------------------------------------ describe
    def workflow_blurb(self, wid: str) -> str:
        m = self.store.manifest(wid)
        desc = self.store.describe(wid)
        slots = ", ".join(f"image {i + 1} = {en(s.get('label', s['key']))}" for i, s in enumerate(m.get("images", [])))
        extra = []
        if slots:
            extra.append("inputs: " + slots)
        if needs_drawing(m):
            extra.append("the user draws boxes/a mask on the image")
        return desc + (f" [{'; '.join(extra)}]" if extra else "")

    # ----------------------------------------------------------- choices
    async def _ask(self, ctx: Ctx, task: str, specs: list[tuple[str, str, dict[str, tuple[str, str]]]],
                   extra_state: dict | None = None, extra_images: list[bytes] | None = None) -> dict[str, Answer]:
        """specs: (qid, instruções, {opção: (descrição, grupo)}). Divide em 2 etapas se passar de 26 opções."""
        state = self._state(ctx, task, extra_state)
        imgs = self._images(ctx, extra_images)
        small = {q: (ins, opts) for q, ins, opts in specs if len(opts) <= MAX_OPTIONS}
        out: dict[str, Answer] = {}
        if small:
            qs = {q: choice_question(ins, {k: d for k, (d, _g) in opts.items()}) for q, (ins, opts) in small.items()}
            out.update(await self.clef.decide(state, qs, imgs))
        for q, ins, opts in specs:
            if q in small:
                continue
            out[q] = await self._two_stage(state, imgs, q, ins, opts)
        return out

    async def _two_stage(self, state, imgs, qid, ins, opts) -> Answer:
        groups: dict[str, list[str]] = {}
        for k, (_d, g) in opts.items():
            groups.setdefault(g or "outros", []).append(k)
        gcrit = {g: "; ".join(opts[k][0].split(".")[0] for k in ks[:4]) for g, ks in groups.items()}
        gans = (await self.clef.decide(state, {"group": choice_question(ins + " First choose the family.", gcrit)},
                                       imgs))["group"]
        members = groups[gans.choice]
        if len(members) == 1:
            return Answer(members[0], gans.confidence, {members[0]: gans.confidence})
        members = members[:MAX_OPTIONS]
        ians = (await self.clef.decide(state, {"item": choice_question(
            ins, {k: opts[k][0] for k in members})}, imgs))["item"]
        probs = {k: p * gans.confidence for k, p in ians.probs.items()}
        return Answer(ians.choice, probs.get(ians.choice, 0.0), probs)

    # ----------------------------------------------------------------- plan
    def steps_from_route(self, route: str) -> list[Step]:
        if route.startswith("recipe__"):
            rid = route[len("recipe__"):]
            for r in self.store.clef_config()["recipes"]:
                if r["id"] == rid:
                    return self.steps_from_recipe(r)
            raise RouterError(f"Caso de uso desconhecido: {rid}")
        wid, _, mode = route.partition("@")              # "edit@quality" fixa o modo
        wid = self._resolve(wid)
        m = self.store.manifest(wid)
        if mode and mode not in (m.get("modes") or {}):
            raise RouterError(f"O workflow '{wid}' não tem o modo '{mode}'.")
        return [Step(wid, mode=mode or None)]

    def _resolve(self, wid: str) -> str:
        """O workflow que faz este papel para o modelo escolhido no chat (o próprio `wid` quando é do modelo ou quando
        não há equivalente; sem modelo diferente do padrão o Store de verdade não tem `resolve` e nada muda)."""
        resolve = getattr(self.store, "resolve", None)
        return (resolve(wid) if resolve else None) or wid

    def steps_from_recipe(self, r: dict) -> list[Step]:
        return [Step(self._resolve(s["workflow"]), s.get("prompt"), dict(s.get("inputs") or {}), s.get("mode"),
                     list(s["loras"]) if s.get("loras") is not None else None,
                     dict(s["vars"]) if s.get("vars") else None, s.get("request") or None) for s in r["steps"]]

    async def consult(self, ctx: Ctx, question: str, options: dict[str, str], task: str = "agent_consult",
                      images: bool = True, qid: str = "q") -> Answer:
        """Uma pergunta de múltipla escolha ao Clef (o agente usa para decidir passo a passo)."""
        q = choice_question(question, options)
        try:
            return (await self.clef.decide(self._state(ctx, task), {qid: q}, self._images(ctx) if images else []))[qid]
        except ClefError as e:
            raise RouterError(str(e)) from e

    async def check_gap(self, ctx: Ctx, route: str, then: str | None, route_opts: dict[str, tuple[str, str]],
                        names: dict[str, str]) -> dict | None:
        """O plano escolhido cumpre o pedido TODO? O Clef responde `covered` ou `gap`. Só devolve algo quando o
        Clef aponta lacuna com probabilidade >= `agent.gap_min` (é o que acorda o agente)."""
        cfg = self.store.settings().get("agent") or {}
        if not cfg.get("enabled"):
            return None
        desc = route_opts.get(route, ("", ""))[0] or names.get(route, route)
        plan_txt = f"'{en(names.get(route, route))}': {desc}"
        if then and then != NONE:
            plan_txt += f" and then '{en(names.get(then, then))}'"
        crit = {"covered": "The chosen plan does by itself EVERYTHING the request asks for, in the order it was asked.",
                "gap": "The request needs a step the plan does not have: preparing an image BEFORE using it (improve "
                       "quality, colorize, cut out, change the pose or style), several chained changes, or a combination "
                       "that no option in the list covers."}
        try:
            ans = await self.consult(ctx, f"Chosen plan: {plan_txt}. Does this plan fulfill the whole request, including "
                                          "everything the user asked to do BEFORE or AFTER the main step?",
                                     crit, "route_gap")
        except RouterError:
            return None
        p = float(ans.probs.get("gap", 0.0))
        if ans.choice == "gap" and p >= float(cfg.get("gap_min", 0.6)):
            return {"p": round(p, 3), "route": route, "route_name": names.get(route, route)}
        return None

    async def plan(self, ctx: Ctx, forced: str | None = None) -> PlanResult:
        settings = self.store.settings()["clef"]
        max_steps = int(settings.get("max_steps", 3))
        if forced:
            steps = self.steps_from_route(forced)
            name = self._route_name(forced)
            decisions = [{"question": "route", "label": "Rota", "source": "user", "chosen": forced,
                          "chosen_name": name, "confidence": 1.0,
                          "options": [{"id": forced, "name": name, "p": 1.0}]}]
            await self._roles_for(ctx, forced, steps, decisions)
            return PlanResult(steps, decisions, route=forced.partition("@")[0])
        n_imgs = len(ctx.images)
        enabled = self.store.enabled_workflows()
        names: dict[str, str] = {}
        route_opts: dict[str, tuple[str, str]] = {}
        for wid, m in enabled.items():
            if m.get("internal"):                       # passos de um caso de uso: não são escolhidos sozinhos
                continue
            if required_images(m) <= n_imgs:
                route_opts[wid] = (self.workflow_blurb(wid), m.get("group", ""))
                names[wid] = m.get("title", wid)
        for r in self.store.enabled_recipes():
            wids = [self._resolve(s["workflow"]) for s in r["steps"]]        # o equivalente do modelo escolhido
            first = self.store.manifests().get(wids[0])
            if first and required_images(first) <= n_imgs and recipe_fits(r, n_imgs) and all(w in enabled for w in wids):
                seq = " -> ".join(en(self.store.manifest(w).get("title", w)) for w in wids)
                rid = "recipe__" + r["id"]
                route_opts[rid] = (f"{r.get('description') or en(r['name'])} [sequence: {seq}]", "use cases")
                names[rid] = f"{r['name']} (sequência)"
        if not route_opts:
            raise RouterError("Nenhum workflow ativo serve para este pedido com "
                              f"{n_imgs} imagem(ns). Anexe a(s) imagem(ns) necessária(s) ou ative mais workflows.")
        decisions: list[dict] = []
        if len(route_opts) == 1:
            (route,) = route_opts
            decisions.append({"question": "route", "label": "Rota", "source": "único", "chosen": route,
                              "chosen_name": names[route], "confidence": 1.0,
                              "options": [{"id": route, "name": names[route], "p": 1.0}]})
            steps = self.steps_from_route(route)
            await self._roles_for(ctx, route, steps, decisions)
            return PlanResult(steps, decisions, route=route.partition("@")[0])

        await self.describe_images(ctx)
        specs = [("route", "Which image editing flow best fulfills the user's request?", route_opts)]
        then_opts: dict[str, tuple[str, str]] = {}
        if max_steps > 1:
            then_opts[NONE] = ("No extra step: the first flow already does the whole request. This is the normal answer.", "")
            for wid, m in enabled.items():
                if m.get("internal"):
                    continue
                # a etapa seguinte recebe o resultado NO LUGAR de uma das fotos, não como foto a mais: com 1 foto,
                # "transferir entre imagens" depois de editar não tem de onde tirar a 2ª (Clef real dava 14-35% a ela)
                if required_images(m) <= max(n_imgs, 1):
                    then_opts[wid] = (self.workflow_blurb(wid), m.get("group", ""))
                    names.setdefault(wid, m.get("title", wid))
            if len(then_opts) > 1:
                specs.append(("then", "After the first step, does the result still need to go through ANOTHER flow to "
                                      "do everything the user asked? Only if the request clearly asks for a second, "
                                      "different operation after the first one (for example 'remove the background and "
                                      "then make it a painting'). A single change, however detailed, needs no second "
                                      "step: choose 'none'.", then_opts))
        names[NONE] = "nenhuma"
        extra_state = self._examples(ctx, names)          # pedidos parecidos que o usuário aprovou
        try:
            ans = await self._ask(ctx, "route_image_request", specs, extra_state)
        except ClefError as e:
            raise RouterError(str(e)) from e
        route_ans = ans["route"]
        decisions.append(decision_view("route", "Rota", route_ans, names, self.clef.mode))
        if route_ans.confidence < float(settings.get("refine_below", 0.6)) and len(route_opts) > 2:
            route_ans = await self._refine_route(ctx, route_ans, route_opts, names, decisions)
        route_ans = self._correct(route_ans, {k: p for k, p in route_ans.probs.items()}, "route", "",
                                  "Rota (pelo seu histórico)", names, decisions)
        asked_route = route_ans.choice                         # o `then` foi respondido pensando nesta rota
        route_ans, intent = await self._paste_intent(ctx, route_ans, route_opts, names, decisions)
        route_ans, pin = await self._engine_gate(ctx, route_ans, route_opts, names, decisions)
        steps = self.steps_from_route(route_ans.choice)
        if pin and steps and steps[0].workflow == pin["workflow"]:       # a reserva já vem com o prompt certo
            steps[0].prompt = pin["prompt"]
        if intent and steps and steps[0].workflow == "transfer_object":         # substituir tira o que está na caixa verde
            steps[0].vars = {**(steps[0].vars or {}), "relacao": "substituir" if intent == "substituir" else "adicionar"}
        await self._roles_for(ctx, route_ans.choice, steps, decisions)
        ambiguous = None
        if route_ans.confidence < float(settings.get("ask_below", 0.45)):
            ambiguous = [{"id": k, "name": names.get(k, k), "p": round(p, 3)} for k, p in route_ans.top(3)]
        then = ans.get("then")
        if then is not None:
            then = weigh_option(then, NONE, float(settings.get("then_none_weight", 1.5)))
            decisions.append(decision_view("then", "Etapa seguinte", then, names, self.clef.mode))
            if (not route_ans.choice.startswith("recipe__") and then.choice not in (None, NONE)
                    and then.choice not in (route_ans.choice, asked_route) and len(steps) < max_steps
                    and then.confidence >= float(settings.get("then_min", 0.5))):
                steps.append(Step(then.choice))
        gap = await self.check_gap(ctx, route_ans.choice, then.choice if then is not None else None, route_opts, names)
        if gap:
            decisions.append({"question": "gap", "label": "Falta alguma etapa?", "source": self.clef.mode,
                              "chosen": "gap", "chosen_name": "sim: nenhuma rota cobre o pedido todo",
                              "confidence": gap["p"], "options": [
                                  {"id": "gap", "name": "falta uma etapa", "p": gap["p"]},
                                  {"id": "covered", "name": "o plano cobre tudo", "p": round(1 - gap["p"], 3)}]})
        return PlanResult(steps[:max_steps] if not route_ans.choice.startswith("recipe__") else steps,
                          decisions, ambiguous, gap, route=route_ans.choice.partition("@")[0])

    async def _roles_for(self, ctx: Ctx, route: str, steps: list[Step], decisions: list[dict]) -> None:
        recipe = self.recipe_of(route)
        if recipe and recipe.get("roles"):
            await self.describe_images(ctx)
            await self.resolve_roles(ctx, recipe, steps, decisions)

    async def _refine_route(self, ctx: Ctx, first: Answer, route_opts: dict, names: dict[str, str],
                            decisions: list[dict]) -> Answer:
        """Segunda análise quando a primeira ficou em dúvida: o Clef compara só os 3 candidatos mais prováveis,
        agora sabendo quantas imagens foram enviadas e o que há em cada uma. Com menos opções a decisão fica mais
        nítida; se ainda assim não melhorar, vale a primeira."""
        top = [k for k, _p in first.top(3) if k in route_opts]
        if len(top) < 2:
            return first
        seen = "; ".join(f"image {i}: {c}" for i, c in sorted(ctx.contents.items())) or "content not identified"
        q = choice_question(
            f"The user sent {len(ctx.images)} image(s) ({seen}). Read the request again and compare only these options: "
            "how many images each one needs, whether it swaps a person/face/body or not, and whether the request mentions "
            "a place, setting or background. Which one fulfills the whole request?", {k: route_opts[k][0] for k in top})
        try:
            second = (await self.clef.decide(self._state(ctx, "route_image_request_2"), {"route": q},
                                             self._images(ctx)))["route"]
        except ClefError:
            return first
        decisions.append(decision_view("route_2", "Rota (2ª análise)", second, names, self.clef.mode))
        return second if second.confidence >= first.confidence else first

    async def _paste_intent(self, ctx: Ctx, route_ans: Answer, route_opts: dict, names: dict[str, str],
                            decisions: list[dict]) -> tuple[Answer, str | None]:
        """Duas imagens e a rota caiu entre levar algo de uma para a outra (transferir, colar, fundir ou o editor): uma
        pergunta curta ao Clef decide pelo que acontece com a coisa trazida. Nos testes ela acertou 28 de 29 frases,
        enquanto a escolha entre todas as rotas trocava "no lugar de" e "segurar" por colagem ou pelo editor.
          substituir / segurar -> transfer_object (só ele tira o que estava lá e redesenha a mão segurando)
          acrescentar          -> Colar e fundir (ou Só colar, se já era essa): lugar e tamanho na tela, identidade igual
          trocar               -> o editor (prompt "Trocar pessoas": corpo e/ou rosto); o pedido diz que é troca de pessoa
          outra                -> fica a rota escolhida (estilo, rosto, roupa...)
        Testado no Clef real: "replace the woman in image 1 with the man in image 2, face and body" caía em `substituir`
        e ia para o transferir objeto (e o `then` ainda acrescentava o editor depois)."""
        if len(ctx.images) != 2 or route_ans.choice not in PASTE_FAMILY:
            return route_ans, None
        try:
            a = await self.consult(ctx, PASTE_QUESTION, PASTE_CRITERIA, "paste_intent", qid="paste_intent")
        except RouterError:
            return route_ans, None
        decisions.append(decision_view("paste_intent", "O que acontece com o que vem da outra imagem", a, PASTE_NAMES, self.clef.mode))
        if a.confidence < 0.6 or a.choice == "outra":
            return route_ans, (a.choice if a.choice != "outra" and a.confidence >= 0.6 else None)
        if a.choice == "trocar":
            target = SWAP_ROUTE if SWAP_ROUTE in route_opts else "edit"
        elif a.choice in ("substituir", "segurar"):
            target = "transfer_object"
        elif route_ans.choice == "recipe__colar_sem_fundir":
            target = route_ans.choice
        else:
            target = "recipe__colar_e_fundir"
        if target not in route_opts or target == route_ans.choice:
            return route_ans, a.choice
        decisions.append({"question": "route_intent", "label": "Rota (pela intenção)", "source": self.clef.mode,
                          "chosen": target, "chosen_name": names.get(target, target), "confidence": a.confidence,
                          "options": [{"id": target, "name": names.get(target, target), "p": a.confidence},
                                      {"id": route_ans.choice, "name": names.get(route_ans.choice, route_ans.choice),
                                       "p": round(1 - a.confidence, 3)}]})
        return Answer(target, a.confidence, {target: a.confidence, route_ans.choice: 1 - a.confidence}), a.choice

    async def _engine_gate(self, ctx: Ctx, route_ans: Answer, route_opts: dict, names: dict[str, str],
                           decisions: list[dict]) -> tuple[Answer, dict | None]:
        """Fluxo prioritário com reserva. Um workflow pode declarar `fallback` no registro: o fluxo dele é o padrão e só nos
        casos de exceção (rosto ocupado com uma lata, corpo cortado, várias pessoas...) o pedido vai para `fallback.workflow`
        (+ `fallback.prompt`). O Clef responde UMA pergunta com a opção `main` (o fluxo padrão) e as exceções; `main` pesa a
        favor (`clef.engine_main_weight`) e a exceção só vence com probabilidade acima de `clef.engine_min`. Sem resposta do
        Clef, a reserva desligada ou rota escolhida pelo usuário, fica o fluxo padrão."""
        wid = route_ans.choice or ""
        fb = (self.store.manifests().get(wid) or {}).get("fallback") if not wid.startswith("recipe__") else None
        if not isinstance(fb, dict) or fb.get("workflow") not in route_opts or not fb.get("options"):
            return route_ans, None
        main = fb.get("main") or {}
        crit = {"main": main.get("when") or "Normal case: use the default flow."}
        label = {"main": main.get("name") or "Fluxo padrão"}
        for k, o in fb["options"].items():
            crit[k] = (o or {}).get("when") or k
            label[k] = (o or {}).get("name") or k
        try:
            a = await self.consult(ctx, str(fb.get("question") or "Is the default flow right for this request?"), crit,
                                   "engine_gate", qid="engine")
        except RouterError:
            return route_ans, None
        cfg = self.store.settings()["clef"]
        a = weigh_option(a, "main", float(cfg.get("engine_main_weight", 1.5)))
        decisions.append(decision_view("engine", "Fluxo de troca", a, label, self.clef.mode))
        if a.choice in (None, "main") or a.confidence < float(cfg.get("engine_min", 0.5)):
            return route_ans, None
        target = fb["workflow"]
        decisions.append({"question": "route_fallback", "label": "Rota (reserva)", "source": self.clef.mode, "chosen": target,
                          "chosen_name": names.get(target, target), "confidence": round(a.confidence, 3),
                          "options": [{"id": target, "name": names.get(target, target), "p": round(a.confidence, 3)},
                                      {"id": wid, "name": names.get(wid, wid), "p": round(1 - a.confidence, 3)}]})
        return (Answer(target, a.confidence, {target: a.confidence, wid: 1 - a.confidence}),
                {"workflow": target, "prompt": fb.get("prompt")})

    def recipe_of(self, route: str) -> dict | None:
        if not route.startswith("recipe__"):
            return None
        rid = route[len("recipe__"):]
        return next((r for r in self.store.clef_config()["recipes"] if r["id"] == rid), None)

    async def describe_images(self, ctx: Ctx) -> None:
        """Primeira análise, curta: o que há em cada imagem (pessoa, lugar sem pessoas, objeto...). Esse rótulo
        entra no estado de todas as perguntas seguintes, assim o Clef não depende de o usuário dizer 'cena'
        ou 'fundo' para saber qual imagem é qual. O Clef aceita poucas imagens por requisição (`max_images`, 4 no
        modelo): cada janela leva só as perguntas das suas imagens — perguntar por 8 imagens mandando 4 faria ele
        responder no escuro sobre as outras."""
        if len(ctx.images) < 2 or ctx.contents or not self.store.settings()["clef"].get("analyze_images", True):
            return
        datas = self._images(ctx)
        cap = int(getattr(self.clef, "max_images", 0) or 0)            # 0 = sem limite
        cap = max(1, min(cap or 8, 8))
        state = self._state(ctx, "describe_images")
        for start in range(0, len(datas), cap):
            window = datas[start:start + cap]
            qs = {f"img_{i + 1}": choice_question(DESCRIBE_QUESTION.format(i + 1), dict(CONTENT_KINDS))
                  for i in range(start, start + len(window))}
            try:
                ans = await self.clef.decide(state, qs, window)
            except ClefError:
                continue                                              # a análise é um reforço: sem ela o fluxo segue
            for q, a in ans.items():
                if a.choice in CONTENT_KINDS and a.confidence >= 0.4:
                    ctx.contents[int(q.split("_")[1])] = CONTENT_LABEL[a.choice]

    async def resolve_roles(self, ctx: Ctx, recipe: dict, steps: list[Step],
                            decisions: list[dict]) -> None:
        """Casos de uso com `roles` (cena, pessoa, fundo...) dizem quem é quem pelo conteúdo e pelo pedido,
        não pela ordem de envio: o Clef responde uma pergunta por papel e as entradas `role:<papel>` dos passos
        viram `user:N`."""
        roles = [r for r in recipe.get("roles") or [] if isinstance(r, dict) and r.get("key")]
        used = {v.split(":", 1)[1] for st in steps for v in st.inputs.values() if isinstance(v, str) and v.startswith("role:")}
        roles = [r for r in roles if r["key"] in used]
        n = len(ctx.images)
        if not roles:
            return
        if n < len(roles):
            raise RouterError(f"O caso de uso '{recipe.get('name')}' precisa de {len(roles)} imagens.")
        crit = {f"user_{i}": f"Image {i}" + (f" ({ctx.contents[i]})" if i in ctx.contents else "")
                for i in range(1, n + 1)}
        qs = {f"role_{r['key']}": choice_question(
            f"Use case '{en(recipe.get('name'))}'. Which of the uploaded images is: {en(r.get('label') or r['key'])}? "
            f"{r.get('about', '')} If the request says 'image N' for this role, follow it. Also use the names the request "
            "gives to things ('the chair', 'the dog', 'her') to find which image each one is in. Each image has only one "
            "role; the upload order is just a hint.".strip(), crit) for r in roles}
        try:
            ans = await self.clef.decide(self._state(ctx, "assign_roles", {"recipe": recipe.get("name")}), qs,
                                         self._images(ctx))
        except ClefError as e:
            raise RouterError(str(e)) from e
        probs = {r["key"]: ans[f"role_{r['key']}"].probs for r in roles}
        chosen = best_roles(roles, probs, n)
        names = {f"user_{i}": f"Imagem {i}" for i in range(1, n + 1)}       # na tela (o catálogo traduz)
        for r in roles:
            decisions.append(decision_view(f"role_{r['key']}", f"Papel: {r.get('label') or r['key']}",
                                           ans[f"role_{r['key']}"], names, self.clef.mode)
                             | {"chosen": f"user_{chosen[r['key']]}", "chosen_name": names[f"user_{chosen[r['key']]}"]})
        for st in steps:
            for k, v in list(st.inputs.items()):
                if isinstance(v, str) and v.startswith("role:"):
                    role = v.split(":", 1)[1]
                    st.inputs[k] = f"user:{chosen[role]}" if role in chosen else NONE

    def _route_name(self, route: str) -> str:
        if route.startswith("recipe__"):
            rid = route[len("recipe__"):]
            return next((r["name"] for r in self.store.clef_config()["recipes"] if r["id"] == rid), route)
        return self.store.manifest(route.partition("@")[0]).get("title", route)

    # ----------------------------------------------------------------- bind
    async def bind(self, ctx: Ctx, step: Step, sources: list[Source], mode: str | None = None,
                   ask=None) -> Binding:
        """`mode`: o modo que o usuário escolheu no chat (turbo, quality...). Vale mais que a pergunta ao Clef;
        um modo que este workflow não tem é ignorado (cada workflow declara os seus em `modes`).
        `ask`: função async que abre uma pergunta na tela; usada quando o Clef fica em dúvida entre dois prompts ou
        entre duas opções de uma variável (quem conhece a foto é a pessoa)."""
        m = self.store.manifest(step.workflow)
        slots = m.get("images", [])
        presets = self.store.active_presets(step.workflow)
        presets = [p for p in presets if int(p.get("needs") or 1) <= len(sources)] or presets
        decisions: list[dict] = []
        by_key = {s.key: s for s in sources}
        state_extra = {"workflow": m.get("title"), "sources": [{"key": s.key, "label": s.label} for s in sources]}

        # 1) o que o caso de uso já fixou
        slot_map: dict[str, str | None] = {}
        for k, v in (step.inputs or {}).items():
            if v == NONE:
                slot_map[k] = None
            else:
                key = "previous" if v == "previous" else v.replace(":", "_")
                if key in by_key:
                    slot_map[k] = key
        preset_id = step.prompt if step.prompt and any(p["id"] == step.prompt for p in presets) else None
        if preset_id is None and len(presets) == 1:
            preset_id = presets[0]["id"]

        # 2) primeira rodada: prompt, modo e LoRAs que o Clef pode acrescentar
        questions: dict[str, dict] = {}
        names: dict[str, str] = {}
        if preset_id is None:
            crit = {p["id"]: (p.get("when") or p["name"]) for p in presets}
            questions["preset"] = choice_question(
                f"Which prompt of the flow '{en(m.get('title'))}' best fits this request?", crit)
            names.update({p["id"]: p["name"] for p in presets})
        modes = m.get("modes") or {}
        forced = mode if mode in modes else None                  # escolhido pelo usuário na tela do chat
        mode = forced or (step.mode if step.mode in modes else None)
        if forced:
            title = (modes.get(forced) or {}).get("title") or forced
            decisions.append({"question": "mode", "label": "Modo", "source": "user", "chosen": forced,
                              "chosen_name": title, "confidence": 1.0,
                              "options": [{"id": forced, "name": title, "p": 1.0}]})
        known = next((p for p in presets if p["id"] == preset_id), None) if preset_id else None
        var_mode = known is not None and any(o.get("mode") for v in pvars.var_list(known) for o in pvars.options(v))
        if mode is None and len(modes) >= 2 and not var_mode:     # a opção da variável já diz o modo (ex.: o que recortar)
            questions["mode"] = choice_question(
                f"Which mode should the flow '{en(m.get('title'))}' run in? Prefer the default unless the request says otherwise.",
                {k: (v.get("when") or v.get("title") or k) for k, v in modes.items()})
            names.update({k: (v.get("title") or k) for k, v in modes.items()})
        lora_cands = self._lora_candidates(m, len(sources)) if step.loras is None else {}
        for lid, lo in lora_cands.items():
            questions[f"lora_{lid}"] = choice_question(
                f"Does the user's request need the LoRA '{en(lo.get('title')) or lid}'? {lo.get('description', '')}".strip(),
                {NONE: "No: the request does not need this LoRA.",
                 "usar": f"Yes, use it: {lo.get('description') or en(lo.get('title')) or lid}"})
        answers: dict[str, Answer] = await self._decide(ctx, questions, state_extra, sources)
        if "preset" in answers:
            raw = answers["preset"]
            decisions.append(decision_view("preset", "Prompt", raw, names, self.clef.mode))
            # o histórico do feedback corrige a probabilidade e, se ainda ficar perto, o Clef pergunta na tela
            a = self._correct(raw, {p["id"]: raw.probs.get(p["id"], 0.0) for p in presets}, "preset",
                              f"{step.workflow}/", "Prompt (pelo seu histórico)", names, decisions)
            a = await self._maybe_ask(ask, "preset",
                                      f"O Clef ficou em dúvida no prompt de '{en(m.get('title'))}'. Qual usar?",
                                      "Prompt", a, names,
                                      float(self.learn_cfg().get("ask_preset_below", 0.5)), decisions)
            preset_id = a.choice
        preset_id = preset_id or presets[0]["id"]
        if "mode" in answers:
            a = answers["mode"]
            first = next(iter(modes))
            mode = a.choice if a.choice in modes and a.confidence >= 0.5 else first
            decisions.append(decision_view("mode", "Modo", a, names, self.clef.mode))
        mode = mode or (next(iter(modes)) if modes else None)
        extras = self._pick_loras(step, answers, lora_cands, preset_id, m, mode, decisions)

        # 3) segunda rodada, já com o prompt conhecido: variáveis do prompt (quantas pessoas, corpo/cabeça...)
        preset = next((p for p in self.store.prompts(step.workflow)["presets"] if p["id"] == preset_id), {})
        var_choices = await self._ask_vars(ctx, step, preset, sources, state_extra, decisions, ask=ask)
        if step.mode not in modes and forced is None:      # opção de variável com `mode` escolhe o modo do workflow
            for v in pvars.var_list(preset):
                o = next((o for o in pvars.options(v) if o["id"] == var_choices.get(v["key"])), None)
                if o and o.get("mode") in modes:
                    mode = o["mode"]
        order = preset.get("slots") == "order"
        needed = self._images_needed(step, preset_id, extras, var_choices)

        # 4) entradas de imagem
        open_slots = [s for s in slots if s["key"] not in slot_map]
        if order:
            for v in pvars.var_list(preset):              # `pick` com `slot`: a imagem escolhida vai para essa entrada
                if pvars.kind(v) == "pick" and v.get("slot") and v["slot"] not in slot_map and sources:
                    i = min(max(1, int(var_choices.get(v["key"]) or 1)), len(sources)) - 1
                    if any(s["key"] == v["slot"] for s in open_slots) and sources[i].key not in slot_map.values():
                        slot_map[v["slot"]] = sources[i].key
            open_slots = [s for s in open_slots if s["key"] not in slot_map]
            if not step.inputs:                           # caso de uso que fixou entradas: o resto fica vazio
                used = {v for v in slot_map.values() if v}
                free = [src for src in sources if src.key not in used]
                for s, src in zip([x for x in open_slots if not x.get("mask")], free):
                    slot_map[s["key"]] = src.key
            for s in open_slots:
                slot_map.setdefault(s["key"], None)
            slot_answers: dict[str, Answer] = {}
        else:
            ask = open_slots[:max(1, len(sources))]       # entradas além do número de imagens ficam vazias
            sq: dict[str, dict] = {}
            if len(sources) >= 2:
                for s in ask:
                    crit = {src.key: en(src.label) for src in sources}
                    if not s.get("required", True):
                        crit[NONE] = "Do not use this input (optional)."
                    if len(crit) >= 2:
                        sq[f"slot_{s['key']}"] = choice_question(
                            f"Which image should go into the input '{en(s.get('label', s['key']))}' of the flow "
                            f"'{en(m.get('title'))}'? {en(s.get('hint', ''))}".strip(), crit)
            slot_answers = await self._decide(ctx, sq, state_extra, sources)
            probs = {s["key"]: slot_answers[f"slot_{s['key']}"].probs for s in ask if f"slot_{s['key']}" in slot_answers}
            slot_map.update(self._assign(ask, probs, sources, slot_map))
            for s in open_slots:
                slot_map.setdefault(s["key"], None)
            self._compact_optional(slots, open_slots, slot_map)
        self._fill_needed(slots, slot_map, sources, needed)
        for s in open_slots:
            a = slot_answers.get(f"slot_{s['key']}")
            if a:
                nm = {src.key: src.label for src in sources}
                nm[NONE] = "não usar"
                decisions.append(decision_view(f"slot_{s['key']}", f"Entrada: {s.get('label', s['key'])}", a, nm,
                                               self.clef.mode))
        return Binding(preset_id, slot_map, decisions, mode, extras, var_choices)

    async def _decide(self, ctx: Ctx, questions: dict[str, dict], state_extra: dict, sources: list[Source]) -> dict[str, Answer]:
        if not questions:
            return {}
        try:
            return await self.clef.decide(self._state(ctx, "bind_workflow", state_extra), questions,
                                          [s.data for s in sources])
        except ClefError as e:
            raise RouterError(str(e)) from e

    async def _ask_vars(self, ctx: Ctx, step: Step, preset: dict, sources: list[Source], state_extra: dict,
                        decisions: list[dict], ask=None) -> dict[str, Any]:
        """Respostas das variáveis do prompt. O caso de uso pode fixá-las; o resto o Clef responde. Uma variável com
        `ask_below` abre a pergunta na tela quando o Clef fica abaixo dessa confiança."""
        vs = pvars.var_list(preset)
        if not vs:
            return {}
        out: dict[str, Any] = {k: v for k, v in (step.vars or {}).items() if any(x["key"] == k for x in vs)}
        qs, titles = {}, {}
        for key, ins, crit in pvars.questions(preset, len(sources)):
            if key in out:
                continue
            qs[f"var_{key}"] = choice_question(ins, crit)
            titles[key] = (crit, next((v.get("label") or key for v in vs if v["key"] == key), key))
        ans = await self._decide(ctx, qs, {**state_extra, "preset": preset.get("name")}, sources) if qs else {}
        for v in vs:
            key = v["key"]
            raw = ans.get(f"var_{key}")
            if raw is not None:
                shown = {o["id"]: o.get("name") or o["id"] for o in pvars.options(v)} if pvars.kind(v) == "choice" else {}
                choices = {k: shown.get(k) or str(d) for k, d in titles[key][0].items()}
                a = raw
                below = float(v.get("ask_below") or 0)
                if below > 0 and pvars.kind(v) == "choice":
                    a = await self._maybe_ask(ask, f"var_{key}", f"O Clef ficou em dúvida: {titles[key][1]}",
                                              titles[key][1], raw, shown, below, decisions)
                ok = a.choice in titles[key][0] and a.confidence >= float(v.get("min_confidence", 0.4))
                out[key] = a.choice if ok else pvars.default_choice(v)
                decisions.append(decision_view(f"var_{key}", titles[key][1], raw, choices, self.clef.mode))
            elif key not in out and pvars.kind(v) in ("choice", "count", "pick"):
                out[key] = pvars.default_choice(v)
        return pvars.fit(preset, out, len(sources))

    def _images_needed(self, step: Step, preset_id: str, extras: list[str], var_choices: dict | None = None) -> int:
        """Quantas imagens o prompt escolhido (com as respostas das variáveis e as LoRAs que ele traz ou o Clef
        ligou) usa de verdade."""
        lib = self.store.loras()
        preset = next((p for p in self.store.prompts(step.workflow)["presets"] if p["id"] == preset_id), {})
        rendered = pvars.render(preset, var_choices or {}) if preset.get("vars") else preset
        ids = [lora_item(i)["id"] for i in (rendered.get("loras") or [])] + list(extras)
        return max([int(rendered.get("needs") or 0)] + [int((lib.get(i) or {}).get("needs") or 0) for i in ids])

    @staticmethod
    def _fill_needed(slots: list[dict], slot_map: dict[str, str | None], sources: list[Source], need: int):
        """O prompt usa N imagens: entradas opcionais que o Clef deixou vazias recebem, em ordem, as imagens que
        sobraram (senão 'troque o corpo' com duas imagens rodaria sem a pessoa de referência)."""
        used = {v for v in slot_map.values() if v}
        unused = [s.key for s in sources if s.key not in used]
        filled = sum(1 for s in slots if slot_map.get(s["key"]))
        for s in slots:
            if filled >= need or not unused:
                break
            if not slot_map.get(s["key"]) and not s.get("mask"):
                slot_map[s["key"]] = unused.pop(0)
                filled += 1

    # ---------------------------------------------------------------- LoRAs
    def _lora_candidates(self, m: dict, n_sources: int) -> dict[str, dict]:
        """LoRAs que o Clef pode acrescentar a este workflow (só os que têm cadeia de LoRAs dinâmica)."""
        if not m.get("lora_chain"):
            return {}
        return {lid: lo for lid, lo in self.store.loras().items()
                if lo.get("auto") and int(lo.get("needs") or 0) <= n_sources}

    def _pick_loras(self, step: Step, answers: dict[str, Answer], cands: dict[str, dict], preset_id: str,
                    m: dict, mode: str | None, decisions: list[dict]) -> list[str]:
        if step.loras is not None:
            return [x for x in step.loras if x in self.store.loras()]
        lib = self.store.loras()
        preset = next((p for p in self.store.prompts(step.workflow)["presets"] if p["id"] == preset_id), {})
        mm = self.store.manifest(step.workflow, mode)
        if preset.get("auto_loras") is False:             # prompt que não aceita LoRAs do Clef (estilo, exposição, restaurar...)
            return []
        have_preset = {lora_item(i)["id"] for i in (preset.get("loras") or [])}
        for v in pvars.var_list(preset):                  # as LoRAs das opções das variáveis também são do prompt
            for o in pvars.options(v):
                have_preset |= {lora_item(i)["id"] for i in (o.get("loras") or [])}
        have = {lora_item(i)["id"] for i in (mm.get("lora_base") or [])} | have_preset
        taken = {lib[i]["group"] for i in have if i in lib and lib[i].get("group")}      # grupos já ocupados
        chosen: list[tuple[float, str]] = []
        for lid, lo in cands.items():
            a = answers.get(f"lora_{lid}")
            if a is None:
                continue
            if lid in have or (lo.get("group") and lo["group"] in taken):
                continue                        # já faz parte da pilha do prompt (ou faz o mesmo papel): não decide
            if any(clash(lib, lid, o) for o in have_preset):
                continue                        # não funciona junto com uma LoRA que o prompt escolhido já usa
            decisions.append(decision_view(f"lora_{lid}", f"LoRA: {lo.get('title') or lid}", a,
                                           {"usar": "usar", NONE: "não usar"}, self.clef.mode))
            if a.choice == "usar":
                chosen.append((a.probs.get("usar", a.confidence), lid))
        cap = int(self.store.settings()["clef"].get("max_loras", 2))
        chosen.sort(reverse=True)
        out, groups = [], set(taken)
        for _p, lid in chosen:                  # uma LoRA por grupo (ex.: troca de corpo OU de cabeça)
            g = lib[lid].get("group")
            if (g and g in groups) or any(clash(lib, lid, o) for o in out):
                continue
            groups.add(g)
            out.append(lid)
        return out[:cap]

    @staticmethod
    def _compact_optional(slots: list[dict], open_slots: list[dict], slot_map: dict[str, str | None]):
        """Entradas opcionais preenchidas ficam juntas, sem buracos (imagem 2 vazia e imagem 3 cheia viraria
        'imagem 2' no prompt do modelo). As vazias saem do grafo e não custam tempo."""
        open_keys = {s["key"] for s in open_slots}
        opt = [s["key"] for s in slots if not s.get("required", True) and s["key"] in open_keys
               and not s.get("mask")]
        vals = [slot_map[k] for k in opt if slot_map.get(k)]
        for k, v in zip(opt, vals + [None] * (len(opt) - len(vals))):
            slot_map[k] = v

    @staticmethod
    def _assign(open_slots: list[dict], probs: dict[str, dict[str, float]], sources: list[Source],
                taken: dict[str, str | None]) -> dict[str, str | None]:
        """Atribui fontes às entradas: sem repetir imagem quando houver imagens suficientes."""
        keys = [s.key for s in sources]
        used = {v for v in taken.values() if v}
        out: dict[str, str | None] = {}

        def best(slot):
            return max(probs[slot["key"]].values()) if slot["key"] in probs and probs[slot["key"]] else 0.0

        for s in sorted(open_slots, key=lambda s: (not s.get("required", True), -best(s))):
            ranked = sorted(probs.get(s["key"], {}).items(), key=lambda kv: -kv[1])
            chosen: str | None = "__unset__"
            for k, _p in ranked:
                if k == NONE:
                    if not s.get("required", True):
                        chosen = None
                        break
                    continue
                if k in used and (any(x not in used for x in keys) or not s.get("required", True)):
                    continue            # entrada opcional nunca repete imagem: fica vazia e sai do grafo
                chosen = k
                break
            if chosen == "__unset__":
                free = [k for k in keys if k not in used]
                if s.get("required", True) or free:
                    chosen = (free or keys)[0] if (free or keys) else None
                else:
                    chosen = None
            out[s["key"]] = chosen
            if chosen:
                used.add(chosen)
        return out
