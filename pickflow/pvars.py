"""Variáveis de prompt: um prompt só que se adapta ao pedido.

Um prompt da biblioteca pode declarar `vars`. Cada variável vira uma pergunta de múltipla escolha para o Clef
(ou é lida do pedido) e o texto final é montado com a resposta, de modo que a decisão do usuário fique
explícita no prompt que vai para o Qwen. Três tipos:

  choice  escolha entre opções; cada opção tem `text` (entra onde está `{chave}`), e pode trazer `loras`,
          `settings`, `needs_extra` (imagens a mais que ela usa) e `expand` (aumentar a tela com cinza para o
          outpaint: frações `left`, `right`, `top`, `bottom` ou `all`)
  count   quantas pessoas/itens (1 a `max`); `{chave}` vira o número e ainda existem
          `{chave_noun}` ("3 people"), `{chave_map}` (quem vira quem, da esquerda para a direita),
          `{chave_refs}` (<image2>, <image3> and <image4>), `{chave_next}` (a primeira imagem depois das referências)
          e `{chave_many}`: o texto `many_text` da variável, que só entra quando a contagem é maior que 1
          (com uma pessoa só o prompt fica com as instruções das LoRAs, sem mapa nem regras de várias pessoas)
  angle   giro de câmera (órbita): lido do pedido ("gire 90 graus para a direita") ou escolhido numa tela
  pick    qual das imagens enviadas (1, 2, 3… na ordem do envio) cumpre um papel (`what_pt`); com `slot`, essa
          imagem vai para a entrada do fluxo com essa chave (ex.: o destino vira a image_1) e as outras seguem na ordem
"""
from __future__ import annotations

import copy
import re
from typing import Any

ORD = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"]
ANGLE_STEPS = (45, 90, 135, 180)
ELEVATIONS = {"low": "low angle", "eye": "eye level", "high": "elevated"}


def var_list(preset: dict) -> list[dict]:
    return [v for v in (preset.get("vars") or []) if isinstance(v, dict) and v.get("key")]


def kind(v: dict) -> str:
    return v.get("kind") or "choice"


def count_max(v: dict, n_sources: int) -> int:
    first = int(v.get("first_ref", 2))
    return max(1, min(int(v.get("max", 8)), n_sources - first + 1))


def options(v: dict) -> list[dict]:
    return [o for o in (v.get("options") or []) if isinstance(o, dict) and o.get("id")]


def default_choice(v: dict) -> str | None:
    if kind(v) == "count":
        return str(v.get("default", 1))
    if kind(v) == "choice":
        opts = options(v)
        return str(v.get("default") or (opts[0]["id"] if opts else "")) or None
    if kind(v) == "pick":
        return str(v.get("default", 1))
    return None


def questions(preset: dict, n_sources: int) -> list[tuple[str, str, dict[str, str]]]:
    """(chave, instrução, {opção: critério}) das variáveis que precisam do Clef."""
    out = []
    for v in var_list(preset):
        key = v["key"]
        if kind(v) == "count":
            top = count_max(v, n_sources)
            if top < 2:
                continue
            # os campos *_pt guardam o texto lido pelo Clef (hoje em inglês; o nome ficou por compatibilidade)
            noun, plural = v.get("noun_pt", "item"), v.get("plural_pt", "items")
            crit = {str(k): v.get("each_pt", "Swap {n} {noun}").format(n=k, noun=noun if k == 1 else plural)
                    for k in range(1, top + 1)}
            out.append((key, v.get("question") or "How many items does the request want to swap?", crit))
        elif kind(v) == "pick":
            if n_sources >= 2:
                what = v.get("what_pt", "the requested image")
                crit = {str(k): f"Image {k} (in upload order) is {what}." for k in range(1, n_sources + 1)}
                out.append((key, v.get("question") or f"Which image is {what}?", crit))
        elif kind(v) == "choice":
            opts = options(v)
            if len(opts) >= 2:
                out.append((key, v.get("question") or f"Which option applies to '{key}'?",
                            {o["id"]: (o.get("when") or o.get("name") or o["id"]) for o in opts}))
    return out


def images_needed(preset: dict, choices: dict[str, Any]) -> int:
    """Quantas imagens o prompt, com estas escolhas, usa de verdade."""
    need = int(preset.get("needs") or 0)
    extra = 0
    top = 0
    for v in var_list(preset):
        c = choices.get(v["key"])
        if kind(v) == "count":
            n = int(c or default_choice(v) or 1)
            top = max(top, int(v.get("first_ref", 2)) - 1 + n)
        elif kind(v) == "choice":
            o = next((o for o in options(v) if o["id"] == (c or default_choice(v))), None)
            extra += int((o or {}).get("needs_extra") or 0)
    return max(need, top + extra) if top else max(need, extra)


def fit(preset: dict, choices: dict[str, Any], n_sources: int) -> dict[str, Any]:
    """Escolhas que pedem mais imagens do que existem são reduzidas: opções com imagem extra (ex.: lugar)
    voltam para a primeira sem imagem extra, e a contagem cai até caber."""
    ch = dict(choices)
    if images_needed(preset, ch) > n_sources:
        for v in var_list(preset):
            if kind(v) == "choice":
                cur = next((o for o in options(v) if o["id"] == (ch.get(v["key"]) or default_choice(v))), None)
                alt = next((x["id"] for x in options(v) if not x.get("needs_extra")), None)
                if cur and cur.get("needs_extra") and alt:
                    ch[v["key"]] = alt
    for v in var_list(preset):
        if kind(v) == "pick":
            try:
                k = int(ch.get(v["key"]) or default_choice(v) or 1)
            except (TypeError, ValueError):
                k = 1
            ch[v["key"]] = str(min(max(1, k), max(1, n_sources)))
    for v in var_list(preset):
        if kind(v) == "count":
            n = min(int(ch.get(v["key"]) or default_choice(v) or 1), count_max(v, n_sources))
            while n > 1 and images_needed(preset, {**ch, v["key"]: str(n)}) > n_sources:
                n -= 1
            ch[v["key"]] = str(max(1, n))
    return ch


# ------------------------------------------------------------------ ângulo
def snap(deg: float) -> int:
    return min(ANGLE_STEPS, key=lambda s: abs(s - abs(deg)))


def parse_angle(text: str) -> dict | None:
    """Lê o giro do pedido. Devolve {'azimuth': graus (+ direita, - esquerda), 'elevation': low|eye|high,
    'complete': bool}; None se o pedido não fala de ângulo. `complete` = dá para montar o prompt sem perguntar."""
    t = (text or "").lower()
    elev = "eye"
    if re.search(r"\b(de baixo|por baixo|low angle|from below|contra-?plong|ângulo baixo|angulo baixo)\b", t):
        elev = "low"
    elif re.search(r"\b(de cima|por cima|elevated|from above|high angle|plong[eê]|ângulo alto|angulo alto)\b", t):
        elev = "high"
    num = re.search(r"(\d{1,3})\s*(?:°|º|graus?|degrees?|deg\b)", t)
    back = re.search(r"\b(de costas|por tr[aá]s|de tr[aá]s|atr[aá]s|parte de tr[aá]s|from behind|the back|rear)\b", t)
    right = re.search(r"\b(direita|right)\b", t)
    left = re.search(r"\b(esquerda|left)\b", t)
    side = re.search(r"\b(de lado|lateral|side)\b", t)
    if back and not num:
        return {"azimuth": 180, "elevation": elev, "complete": True}
    if num:
        deg = min(180, int(num.group(1)))
        if snap(deg) == 180 or (left and not right) or (right and not left):
            sign = -1 if (left and not right) else 1
            return {"azimuth": sign * snap(deg), "elevation": elev, "complete": True}
        return {"azimuth": snap(deg), "elevation": elev, "complete": False}
    if side or left or right:
        sign = -1 if (left and not right) else 1
        return {"azimuth": sign * 90, "elevation": elev, "complete": bool((left or right) and not (left and right))}
    if elev != "eye":
        return {"azimuth": 0, "elevation": elev, "complete": True}
    return None


def angle_phrase(spec: dict | None) -> str:
    spec = spec or {}
    az = int(spec.get("azimuth") or 0)
    elev = ELEVATIONS.get(spec.get("elevation") or "eye", "eye level")
    n = snap(az) if az else 0
    if n == 0:
        return f"<orbit> keep the camera angle, {elev}." if elev != "eye level" else "<orbit> keep the camera angle."
    if n == 180:
        return f"<orbit> rotate the camera 180 degrees, {elev}."
    return f"<orbit> rotate the camera {n} degrees to the {'right' if az > 0 else 'left'}, {elev}."


def has_angle(preset: dict) -> dict | None:
    return next((v for v in var_list(preset) if kind(v) == "angle"), None)


# ------------------------------------------------------------------ montagem
def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _count_tokens(v: dict, n: int) -> dict[str, str]:
    key, first = v["key"], int(v.get("first_ref", 2))
    noun, plural = v.get("noun", "person"), v.get("plural", "people")
    refs = [f"<image{first + i}>" for i in range(n)]
    if n == 1:
        mp = v.get("map_one") or f"Replace the {noun} in <image1> (the leftmost one if there are several) with the {noun} from {refs[0]}."
    else:
        pairs = ", ".join(f"the {ORD[i]} {noun} from the left becomes the {noun} in {refs[i]}" for i in range(n))
        mp = (v.get("map_many") or "Replace the first {n} {plural} of <image1>, counting from LEFT to RIGHT, with the "
              "{plural} from the reference images, in order: {pairs}. Leave any other {plural} in <image1> unchanged.")
        mp = mp.format(n=n, plural=plural, noun=noun, pairs=pairs, refs=_join(refs))
    return {key: str(n), f"{key}_noun": f"{n} {noun if n == 1 else plural}", f"{key}_refs": _join(refs),
            f"{key}_map": mp, f"{key}_next": f"<image{first + n}>"}


def render(preset: dict, choices: dict[str, Any]) -> dict:
    """Prompt com as variáveis resolvidas: texto montado e LoRAs/ajustes das opções escolhidas acrescentados."""
    vs = var_list(preset)
    if not vs:
        return preset
    out = copy.deepcopy(preset)
    tokens: dict[str, str] = {}
    for v in vs:                                          # contagens primeiro: as opções podem usá-las
        if kind(v) == "count":
            n = max(1, int(choices.get(v["key"]) or default_choice(v) or 1))
            tokens.update(_count_tokens(v, n))
    loras = list(out.get("loras") or [])
    settings = dict(out.get("settings") or {})
    for v in vs:
        k = kind(v)
        if k == "choice":
            o = next((o for o in options(v) if o["id"] == choices.get(v["key"])), None) or next(
                (o for o in options(v) if o["id"] == default_choice(v)), None)
            if o:
                tokens[v["key"]] = _fill(o.get("text", ""), tokens)
                loras += list(o.get("loras") or [])
                settings.update(o.get("settings") or {})
                if "rewrite" in o:                      # a opção pode ligar o redator de prompt (ex.: completar o corpo)
                    out["rewrite"] = bool(o["rewrite"])
                if o.get("note"):
                    out["writer_note"] = str(o["note"])
                if o.get("expand"):
                    out["expand"] = dict(o["expand"])
                if o.get("ask_expand"):                 # sem margem sugerida: a pessoa define tudo na tela
                    out["expand_ask"] = True
            else:
                tokens[v["key"]] = ""
        elif k == "angle":
            tokens[v["key"]] = angle_phrase(choices.get(v["key"]))
    for v in vs:                                          # texto que só vale com mais de uma pessoa/item
        if kind(v) == "count":
            many = int(tokens.get(v["key"], "1")) > 1
            tokens[f"{v['key']}_many"] = _fill(v.get("many_text", ""), tokens) if many else ""
    lines = []
    for tl in str(out.get("text", "")).split("\n"):        # linha que só tinha variáveis e ficou vazia some
        filled = _fill(tl, tokens)
        if tl.strip() and not filled.strip():
            continue
        lines.append(filled)
    out["text"] = "\n".join(lines)
    if loras:
        out["loras"] = loras
    if settings:
        out["settings"] = settings
    out["needs"] = images_needed(preset, choices)
    return out


def _fill(text: str, tokens: dict[str, str]) -> str:
    return re.sub(r"\{([a-z0-9_]+)\}", lambda m: tokens.get(m.group(1), m.group(0)), text)
