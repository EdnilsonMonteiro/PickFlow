"""Poses de pessoas: ler o resultado do DWPose, escolher quem é pessoa de verdade e desenhar o esqueleto no formato OpenPose.

O ComfyUI (nó DWPreprocessor) devolve, para a foto, uma lista de pessoas com 18 pontos do corpo (nariz, pescoço, ombros,
cotovelos, punhos, quadris, joelhos, tornozelos, olhos e orelhas), em pixels. O PickFlow guarda tudo em FRAÇÕES da imagem
(0 a 1), deixa a pessoa mexer nos pontos numa tela e desenha o mapa de pose (fundo preto, ossos coloridos) que o ControlNet
Union do Qwen-Image lê. Cada ponto é `[x, y, c]` (`c` = confiança; 0 = o detector não viu esse ponto).

As MÃOS vão junto, depois dos 18 pontos do corpo: 21 pontos da mão esquerda e 21 da direita (`HAND_L`, `HAND_R`). A tela só mexe no
corpo (18 pontos); quando o braço mexe, a mão acompanha o antebraço (`with_hands`) e vai desenhada, com os dedos, no mapa de pose.
Sem os dedos no mapa o ControlNet só tem o ponto do punho e desenha uma "luva" sem dedos (testado no ComfyUI real).
"""
from __future__ import annotations

import colorsys
import io
import json
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

NAMES = ["nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow", "l_wrist", "r_hip", "r_knee",
         "r_ankle", "l_hip", "l_knee", "l_ankle", "r_eye", "l_eye", "r_ear", "l_ear"]
# ossos do OpenPose (pares de índices, a ordem e as cores são as do controlnet_aux: o ControlNet foi treinado com elas)
LIMBS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13), (1, 0),
         (0, 14), (14, 16), (0, 15), (15, 17)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0), (0, 255, 85),
          (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
          (255, 0, 255), (255, 0, 170), (255, 0, 85)]
# quem é filho de quem: mexer num ponto leva os filhos junto (a tela usa o mesmo desenho)
PARENT = {1: None, 0: 1, 2: 1, 3: 2, 4: 3, 5: 1, 6: 5, 7: 6, 8: 1, 9: 8, 10: 9, 11: 1, 12: 11, 13: 12, 14: 0, 15: 0, 16: 14, 17: 15}
MIN_CONF = 0.3
BODY = 18
HAND_L, HAND_R = 18, 39                     # onde começa cada mão (21 pontos: punho, polegar, indicador, médio, anelar, mindinho)
HAND_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (0, 9), (9, 10), (10, 11), (11, 12), (0, 13),
              (13, 14), (14, 15), (15, 16), (0, 17), (17, 18), (18, 19), (19, 20)]
# mão aberta e relaxada, de frente, no referencial do antebraço: (adiante, lado do polegar) em frações do comprimento da mão
# (do punho à ponta do dedo médio); usada quando o detector não viu a mão original e o braço mexeu
HAND_TEMPLATE = [(0.0, 0.0), (0.10, 0.18), (0.28, 0.32), (0.42, 0.42), (0.54, 0.50),
                 (0.52, 0.17), (0.72, 0.19), (0.85, 0.20), (0.96, 0.21),
                 (0.55, 0.03), (0.78, 0.03), (0.92, 0.03), (1.0, 0.03),
                 (0.52, -0.11), (0.73, -0.14), (0.86, -0.16), (0.95, -0.18),
                 (0.47, -0.24), (0.62, -0.29), (0.72, -0.33), (0.80, -0.36)]
HAND_FOREARM = 0.75                         # comprimento da mão em relação ao antebraço (cotovelo a punho)


def _flat(people_json: Any) -> tuple[list[dict], int, int]:
    """Aceita o texto/objeto do `openpose_json` do ComfyUI: `[{people: [...], canvas_width, canvas_height}]` (ou só um item)."""
    if isinstance(people_json, (str, bytes)):
        people_json = json.loads(people_json)
    if isinstance(people_json, list):
        people_json = people_json[0] if people_json else {}
    if not isinstance(people_json, dict):
        return [], 0, 0
    return list(people_json.get("people") or []), int(people_json.get("canvas_width") or 0), int(people_json.get("canvas_height") or 0)


def parse(people_json: Any, width: int = 0, height: int = 0) -> list[list[list[float]]]:
    """Pessoas do DWPose -> lista de 18 pontos `[x, y, c]` em frações. Ponto não visto (c < 0,3) fica `[0, 0, 0]`."""
    raw, cw, ch = _flat(people_json)
    cw, ch = cw or width, ch or height
    out = []
    for p in raw:
        k = p.get("pose_keypoints_2d") or []
        pts = []
        for i in range(18):
            x, y, c = (k[3 * i:3 * i + 3] + [0, 0, 0])[:3] if len(k) >= 3 * (i + 1) else (0, 0, 0)
            if c >= MIN_CONF and cw and ch and (x > 0 or y > 0):
                pts.append([max(0.0, min(1.0, x / cw)), max(0.0, min(1.0, y / ch)), round(float(c), 3)])
            else:
                pts.append([0.0, 0.0, 0.0])
        if "hand_left_keypoints_2d" in p or "hand_right_keypoints_2d" in p:
            for key in ("hand_left_keypoints_2d", "hand_right_keypoints_2d"):
                k = p.get(key) or []
                for i in range(21):
                    x, y, c = (k[3 * i:3 * i + 3] + [0, 0, 0])[:3] if len(k) >= 3 * (i + 1) else (0, 0, 0)
                    if c >= MIN_CONF and cw and ch and (x > 0 or y > 0):
                        pts.append([max(0.0, min(1.0, x / cw)), max(0.0, min(1.0, y / ch)), 1.0])
                    else:
                        pts.append([0.0, 0.0, 0.0])
        out.append(pts)
    return out


def parse_frames(raw: Any, width: int = 0, height: int = 0) -> list[list[list[list[float]]]]:
    """Como `parse`, mas para um LOTE de imagens: o DWPose devolve um quadro por imagem do lote (aqui, uma imagem por pessoa)."""
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    frames = raw if isinstance(raw, list) else [raw]
    return [parse(f, width, height) for f in frames if isinstance(f, dict)]


def combine(isolated: list[list[list[list[float]]]], full: list[list[list[float]]]) -> list[list[list[float]]]:
    """Junta as duas detecções do passo "achar as pessoas":
      isolated  um quadro por pessoa que o SAM 3 separou (o resto da foto some): vale a pessoa mais completa de cada quadro. É o
                que resolve gente colada ou uma na frente da outra, onde o DWPose na foto inteira mistura os corpos.
      full      o DWPose na foto inteira: só entram pessoas que o SAM 3 não pegou (as que não ficam em cima de uma já achada)."""
    own = [max(fr, key=lambda p: len(seen(p))) for fr in isolated if fr]
    people = usable(own, dedupe=False)                       # o SAM 3 já deu uma pessoa por quadro: duas coladas não são a mesma
    boxes = [b for b in (bbox(p) for p in own) if b]
    for p in usable(full):
        bb = bbox(p)
        if bb and not any(_iou(bb, o) > 0.3 or _cover(bb, o) > 0.35 for o in boxes):
            people.append(p)
    people.sort(key=lambda p: (bbox(p) or (0, 0, 0, 0))[0])
    return people


def seen(person: list[list[float]]) -> list[int]:
    return [i for i, p in enumerate(person[:BODY]) if p[2] >= MIN_CONF]


def bbox(person: list[list[float]]) -> tuple[float, float, float, float] | None:
    s = seen(person)
    if not s:
        return None
    xs, ys = [person[i][0] for i in s], [person[i][1] for i in s]
    return min(xs), min(ys), max(xs), max(ys)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _cover(a, b) -> float:
    """Quanto da caixa `a` está dentro da caixa `b` (0 a 1)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / area if area > 0 else 0.0


def usable(people: list[list[list[float]]], min_points: int = 6, min_area: float = 0.01, dedupe: bool = True) -> list[list[list[float]]]:
    """Fica com o que parece uma pessoa: pontos suficientes, tamanho mínimo e sem repetir a mesma pessoa (o DWPose às vezes
    devolve a mesma pessoa duas vezes, ou um esqueleto inventado no meio de outra). Ordem: da esquerda para a direita."""
    cand = []
    for p in people:
        bb = bbox(p)
        if bb is None or len(seen(p)) < min_points:
            continue
        area = (bb[2] - bb[0]) * (bb[3] - bb[1])
        if area < min_area:
            continue
        cand.append((len(seen(p)), area, p, bb))
    cand.sort(key=lambda t: (-t[0], -t[1]))                  # os mais completos primeiro
    kept: list = []
    for n, area, p, bb in cand:
        if dedupe and any(_iou(bb, k[3]) > 0.45 for k in kept):
            continue
        kept.append((n, area, p, bb))
    kept.sort(key=lambda t: t[3][0])
    return [t[2] for t in kept]


def draw(people: list[list[list[float]]], width: int, height: int, stick: int | None = None) -> bytes:
    """Mapa de pose (PNG): fundo preto, ossos e pontos nas cores do OpenPose, do tamanho da foto."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)
    s = stick or max(3, round(min(width, height) / 170))
    for person in people:
        for (a, b), col in zip(LIMBS, COLORS):
            pa, pb = person[a], person[b]
            if pa[2] < MIN_CONF or pb[2] < MIN_CONF:
                continue
            x1, y1, x2, y2 = pa[0] * width, pa[1] * height, pb[0] * width, pb[1] * height
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 1:
                continue
            ang = math.atan2(y2 - y1, x2 - x1)
            cx, cy, hl = (x1 + x2) / 2, (y1 + y2) / 2, length / 2     # elipse alongada (como o cv2.ellipse2Poly do OpenPose)
            poly = []
            for t in range(0, 360, 20):
                ex, ey = hl * math.cos(math.radians(t)), s * math.sin(math.radians(t))
                poly.append((cx + ex * math.cos(ang) - ey * math.sin(ang), cy + ex * math.sin(ang) + ey * math.cos(ang)))
            d.polygon(poly, fill=tuple(int(c * 0.6) for c in col))
        for i, (x, y, c) in enumerate(person[:BODY]):
            if c >= MIN_CONF:
                px, py = x * width, y * height
                d.ellipse([px - s, py - s, px + s, py + s], fill=COLORS[i])
        for start in (HAND_L, HAND_R):
            _draw_hand(d, person[start:start + 21], width, height)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_hand(d: ImageDraw.ImageDraw, hand: list[list[float]], width: int, height: int) -> None:
    """Mão no formato OpenPose: 20 ossos em cores do arco-íris (matiz por osso) e pontos azuis, no tamanho da própria mão (a mão
    fica minúscula numa foto inteira: linha e ponto do tamanho do corpo cobririam os dedos)."""
    if len(hand) < 21 or sum(1 for p in hand if p[2] >= MIN_CONF) < 5:
        return
    pts = [(p[0] * width, p[1] * height, p[2] >= MIN_CONF) for p in hand]
    size = max((math.hypot(a[0] - pts[0][0], a[1] - pts[0][1]) for a in pts if a[2]), default=0)
    line, rad = max(1, round(size * 0.035)), max(1.5, size * 0.035)
    for n, (a, b) in enumerate(HAND_EDGES):
        if pts[a][2] and pts[b][2]:
            r, g, bl = colorsys.hsv_to_rgb(n / len(HAND_EDGES), 1.0, 1.0)
            d.line([pts[a][:2], pts[b][:2]], fill=(int(r * 255), int(g * 255), int(bl * 255)), width=line)
    for x, y, ok in pts:
        if ok:
            d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(0, 0, 255))


def clean(value: Any, max_people: int = 12) -> list[list[list[float]]]:
    """Valida o que vem da tela: lista de pessoas, cada uma com 18 pontos `[x, y, c]` (x e y entre 0 e 1)."""
    if not isinstance(value, list):
        raise ValueError("pose inválida")
    out = []
    for person in value[:max_people]:
        if not isinstance(person, list) or len(person) < BODY:
            raise ValueError("cada pessoa precisa de 18 pontos")
        person = person[:BODY]                                   # as mãos não vêm da tela: o servidor leva a mão do detector atrás do braço
        pts = []
        for p in person:
            x, y, c = (list(p) + [0, 0, 0])[:3] if isinstance(p, (list, tuple)) else (0, 0, 0)
            pts.append([max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y))), 1.0 if float(c) >= MIN_CONF else 0.0])
        out.append(pts)
    return out


def changed(a: list[list[float]], b: list[list[float]], tol: float = 0.004) -> bool:
    """A pessoa mexeu nos pontos? (para dizer, no prompt, quem mudou e quem fica como está)"""
    return any(abs(p[0] - q[0]) > tol or abs(p[1] - q[1]) > tol or (p[2] >= MIN_CONF) != (q[2] >= MIN_CONF) for p, q in zip(a[:BODY], b[:BODY]))


# ---------------------------------------------------------------- mãos
def hand_points(person: list[list[float]], side: str) -> list[list[float]] | None:
    """Os 21 pontos da mão (`side`: "l" = esquerda da pessoa, "r" = direita) se o detector viu a mão bem; senão None."""
    start = HAND_L if side == "l" else HAND_R
    hand = person[start:start + 21]
    return hand if len(hand) == 21 and sum(1 for p in hand if p[2] >= MIN_CONF) >= 14 else None


def _moved_hand(o: list[list[float]], e: list[list[float]], side: str, width: int, height: int) -> list[list[float]] | None:
    """A mão da pessoa `o` levada junto com o braço que foi para a posição `e`: gira com o antebraço (cotovelo -> punho), muda de
    escala com ele e vai para o punho novo. Sem a mão original (o detector não viu), mão aberta padrão se o braço mexeu."""
    wr, el = (4, 3) if side == "r" else (7, 6)
    if e[wr][2] < MIN_CONF:
        return None
    hand = hand_points(o, side)
    arm_ok = min(o[wr][2], o[el][2], e[el][2]) >= MIN_CONF
    moved = _bone_changed(o, e, el, wr, width, height) if arm_ok else (o[wr][2] >= MIN_CONF and abs(o[wr][0] - e[wr][0]) + abs(o[wr][1] - e[wr][1]) > 0.004)
    if e[el][2] >= MIN_CONF:
        ve = ((e[wr][0] - e[el][0]) * width, (e[wr][1] - e[el][1]) * height)
    else:
        ve = None
    if hand is None:
        if not moved or ve is None or math.hypot(*ve) < 4:
            return None
        return _template_hand(e, side, wr, ve, width, height)
    if o[wr][2] < MIN_CONF:
        return None
    if arm_ok and ve is not None:
        vo = ((o[wr][0] - o[el][0]) * width, (o[wr][1] - o[el][1]) * height)
        lo, le = math.hypot(*vo), math.hypot(*ve)
        scale = max(0.5, min(2.0, le / lo)) if lo > 1 else 1.0
        rot = (math.atan2(ve[1], ve[0]) - math.atan2(vo[1], vo[0])) if lo > 1 and le > 1 else 0.0
    else:
        scale, rot = 1.0, 0.0
    cs, sn = math.cos(rot) * scale, math.sin(rot) * scale
    out = []
    for p in hand:
        if p[2] < MIN_CONF:
            out.append([0.0, 0.0, 0.0])
            continue
        dx, dy = (p[0] - o[wr][0]) * width, (p[1] - o[wr][1]) * height
        x, y = e[wr][0] + (dx * cs - dy * sn) / width, e[wr][1] + (dx * sn + dy * cs) / height
        out.append([max(0.0, min(1.0, x)), max(0.0, min(1.0, y)), 1.0])
    return out


def _template_hand(e: list[list[float]], side: str, wr: int, ve: tuple[float, float], width: int, height: int) -> list[list[float]]:
    """Mão aberta no fim do antebraço (de frente: o polegar do lado do pescoço)."""
    L = math.hypot(*ve)
    ux, uy = ve[0] / L, ve[1] / L                                    # adiante (do cotovelo para o punho)
    px, py = -uy, ux                                                 # lado
    if e[1][2] >= MIN_CONF:                                          # polegar para o lado do pescoço
        toward = (e[1][0] - e[wr][0]) * width * px + (e[1][1] - e[wr][1]) * height * py
        sign = 1.0 if toward >= 0 else -1.0
    else:
        sign = 1.0 if side == "l" else -1.0
    hl = L * HAND_FOREARM
    out = []
    for fw, lat in HAND_TEMPLATE:
        x = e[wr][0] * width + (ux * fw + px * lat * sign) * hl
        y = e[wr][1] * height + (uy * fw + py * lat * sign) * hl
        out.append([max(0.0, min(1.0, x / width)), max(0.0, min(1.0, y / height)), 1.0])
    return out


def with_hands(original: list[list[list[float]]], edited: list[list[list[float]]], width: int, height: int) -> list[list[list[float]]]:
    """As pessoas editadas (18 pontos cada) com as mãos acrescentadas (60 pontos): a mão original levada junto com o braço, ou uma mão
    aberta padrão quando o braço mexeu e o detector não tinha visto a mão. Quem não mexeu mantém a mão detectada."""
    out = []
    for o, e in zip(original, edited):
        person = [list(p) for p in e[:BODY]]
        for side in ("l", "r"):
            hand = _moved_hand(o, e, side, width, height)
            person += hand if hand else [[0.0, 0.0, 0.0] for _ in range(21)]
        out.append(person)
    return out


def change_mask(original: list[list[list[float]]], edited: list[list[list[float]]], width: int, height: int) -> Image.Image:
    """Onde a foto PODE mudar (máscara "L", 255 = vem do modelo; 0 = fica a foto original): em volta de cada osso que mexeu, no lugar
    antigo e no novo, a mão ou o pé que o osso leva e a cabeça se ela mexeu. O ControlNet redesenha a foto inteira em ~1 MP e o que não
    devia mudar perde nitidez (mãos de quem não mexeu saíam borradas); fora desta máscara o resultado volta a ser o pixel da original."""
    m = Image.new("L", (width, height), 0)
    d = ImageDraw.Draw(m)

    def px(p):
        return p[0] * width, p[1] * height

    def stroke(a, b, hw):
        d.line([px(a), px(b)], fill=255, width=max(2, round(hw * 2)))
        for c in (a, b):
            x, y = px(c)
            d.ellipse([x - hw, y - hw, x + hw, y + hw], fill=255)

    def bone(pts, a, b):
        if pts[a][2] < MIN_CONF or pts[b][2] < MIN_CONF:
            return
        ln = math.hypot((pts[b][0] - pts[a][0]) * width, (pts[b][1] - pts[a][1]) * height)
        stroke(pts[a], pts[b], max(12.0, 0.32 * ln))

    for o, e in zip(original, edited):
        moved = {i for i in range(BODY) if (o[i][2] >= MIN_CONF) != (e[i][2] >= MIN_CONF)
                 or (o[i][2] >= MIN_CONF and (abs(o[i][0] - e[i][0]) > 0.004 or abs(o[i][1] - e[i][1]) > 0.004))}
        if not moved:
            continue
        for a, b in LIMBS:
            if a in moved or b in moved:
                bone(o, a, b)
                bone(e, a, b)
        for wr, el in ((4, 3), (7, 6)):                                       # a mão vai com o punho: um osso a mais, além do antebraço
            if wr in moved or el in moved:
                for pts in (o, e):
                    if pts[wr][2] >= MIN_CONF and pts[el][2] >= MIN_CONF:
                        tip = [pts[wr][0] + (pts[wr][0] - pts[el][0]) * 0.95, pts[wr][1] + (pts[wr][1] - pts[el][1]) * 0.95, 1.0]
                        ln = math.hypot((pts[wr][0] - pts[el][0]) * width, (pts[wr][1] - pts[el][1]) * height)
                        stroke(pts[wr], tip, max(14.0, 0.5 * ln))
        for wr, kn in ((10, 9), (13, 12)):                                    # o pé
            if wr in moved or kn in moved:
                for pts in (o, e):
                    if pts[wr][2] >= MIN_CONF and pts[kn][2] >= MIN_CONF:
                        tip = [pts[wr][0] + (pts[wr][0] - pts[kn][0]) * 0.4, pts[wr][1] + (pts[wr][1] - pts[kn][1]) * 0.4, 1.0]
                        stroke(pts[wr], tip, max(14.0, 0.3 * math.hypot((pts[wr][0] - pts[kn][0]) * width, (pts[wr][1] - pts[kn][1]) * height)))
        if moved & {0, 14, 15, 16, 17}:                                       # cabeça: um disco em volta do nariz, antigo e novo
            for pts in (o, e):
                if pts[0][2] >= MIN_CONF and pts[1][2] >= MIN_CONF:
                    r = max(16.0, 1.1 * math.hypot((pts[0][0] - pts[1][0]) * width, (pts[0][1] - pts[1][1]) * height))
                    x, y = px(pts[0])
                    d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    feather = max(3, round(max(width, height) * 0.005))               # borda curta: a saída do modelo não coincide pixel a pixel com a original e uma transição larga mostra os dois (fantasma)
    return m.filter(ImageFilter.GaussianBlur(feather))


def hand_boxes(people_hands: list[list[list[float]]], only: set[tuple[int, str]] | None = None, pad: float = 1.0) -> list[dict]:
    """Caixa `[x, y, w, h]` (frações) de cada mão desenhada, com `pad` de folga em volta (1,0 = o tamanho da mão)."""
    out = []
    for k, person in enumerate(people_hands):
        for side, start in (("l", HAND_L), ("r", HAND_R)):
            if only is not None and (k, side) not in only:
                continue
            hand = person[start:start + 21]
            xs = [p[0] for p in hand if p[2] >= MIN_CONF]
            ys = [p[1] for p in hand if p[2] >= MIN_CONF]
            if len(xs) < 8:
                continue
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            out.append({"person": k, "side": side, "box": [x0, y0, x1 - x0, y1 - y0], "pad": pad})
    return out


# ---------------------------------------------------------------- descrever a mudança em palavras
ORD = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth"]
DIRS = ["right", "down and to the right", "down", "down and to the left", "left", "up and to the left", "up", "up and to the right"]
# (nome, osso proximal, osso distal) de cada braço e perna; os lados são os da FOTO (o do lado esquerdo da foto etc.)
LIMB_CHAINS = [("arm", (2, 3, 4)), ("arm", (5, 6, 7)), ("leg", (8, 9, 10)), ("leg", (11, 12, 13))]


def _dir(a: list[float], b: list[float], w: float, h: float) -> str:
    ang = math.degrees(math.atan2((b[1] - a[1]) * h, (b[0] - a[0]) * w)) % 360       # 0 = direita, 90 = baixo (y cresce para baixo)
    return DIRS[int(((ang + 22.5) % 360) // 45)]


def _bone_changed(o: list[list[float]], e: list[list[float]], a: int, b: int, w: float, h: float) -> bool:
    if min(o[a][2], o[b][2], e[a][2], e[b][2]) < MIN_CONF:                 # algum ponto não visto: só conta se a visibilidade mudou
        return (o[a][2] >= MIN_CONF, o[b][2] >= MIN_CONF) != (e[a][2] >= MIN_CONF, e[b][2] >= MIN_CONF)
    vo = ((o[b][0] - o[a][0]) * w, (o[b][1] - o[a][1]) * h)
    ve = ((e[b][0] - e[a][0]) * w, (e[b][1] - e[a][1]) * h)
    lo, le = math.hypot(*vo), math.hypot(*ve)
    if lo < 1 or le < 1:
        return True
    ang = abs((math.degrees(math.atan2(*ve[::-1]) - math.atan2(*vo[::-1])) + 180) % 360 - 180)
    return ang > 12 or abs(le - lo) / lo > 0.2


def _who(k: int, n: int) -> str:
    return "the person" if n == 1 else f"the {ORD[k] if k < len(ORD) else str(k + 1) + 'th'} person from the left"


def moved_limbs(original: list[list[list[float]]], edited: list[list[list[float]]], width: int, height: int) -> list[dict]:
    """Os braços e pernas que mexeram: `{person, kind (arm|leg), side (lado da FOTO), chain (3 pontos), old, new}`. Só entram os
    que o detector viu inteiros na pose nova."""
    out = []
    for k, (o, e) in enumerate(zip(original, edited)):
        if not changed(o, e):
            continue
        for kind, (p, q, r) in LIMB_CHAINS:
            if not (_bone_changed(o, e, p, q, width, height) or _bone_changed(o, e, q, r, width, height)):
                continue
            if min(e[p][2], e[q][2], e[r][2]) < MIN_CONF:
                continue
            side = "left" if e[p][0] < e[1][0] else "right"                    # lado da foto em relação ao pescoço
            out.append({"person": k, "kind": kind, "side": side, "chain": (p, q, r), "old": o, "new": e})
    return out


def _body_bits(o: list[list[float]], e: list[list[float]], width: int, height: int) -> list[str]:
    """O que mudou fora de braços e pernas: cabeça, tronco e o corpo todo (arrastar o pescoço leva a pessoa inteira)."""
    bits: list[str] = []
    ok = lambda *ix: all(o[i][2] >= MIN_CONF and e[i][2] >= MIN_CONF for i in ix)          # noqa: E731

    def ang(p, a, b):
        return math.degrees(math.atan2((p[b][1] - p[a][1]) * height, (p[b][0] - p[a][0]) * width))

    def turn(a: float, b: float) -> float:
        return (b - a + 180) % 360 - 180

    if ok(0, 1):
        t = turn(ang(o, 1, 0), ang(e, 1, 0))                                                # nariz em relação ao pescoço (0° = direita, -90° = em cima)
        if abs(t) > 12:
            bits.append("the head tilted toward the " + ("right" if t > 0 else "left") + " side of the picture")
    if ok(1, 8, 11):
        mid_o = ((o[8][0] + o[11][0]) / 2, (o[8][1] + o[11][1]) / 2, 1.0)
        mid_e = ((e[8][0] + e[11][0]) / 2, (e[8][1] + e[11][1]) / 2, 1.0)
        so, se = [o[1], mid_o], [e[1], mid_e]
        t = turn(ang(so, 0, 1), ang(se, 0, 1))                                              # eixo pescoço -> quadril
        if abs(t) > 10:
            bits.append("the torso leaning toward the " + ("left" if t > 0 else "right") + " side of the picture")
        lo, le = math.hypot((o[1][0] - mid_o[0]) * width, (o[1][1] - mid_o[1]) * height), math.hypot((e[1][0] - mid_e[0]) * width, (e[1][1] - mid_e[1]) * height)
        if lo > 1 and abs(le - lo) / lo > 0.25:
            bits.append("the whole body " + ("bigger and closer" if le > lo else "smaller and farther away"))
    if ok(1):
        dx, dy = (e[1][0] - o[1][0]) * 100, (e[1][1] - o[1][1]) * 100
        if abs(dx) > 3 or abs(dy) > 3:
            where = ("to the right" if dx > 0 else "to the left") if abs(dx) >= abs(dy) else ("lower" if dy > 0 else "higher")
            bits.append(f"the whole body moved {where} in the picture, as the control map shows")
    return bits


def describe(original: list[list[list[float]]], edited: list[list[list[float]]], width: int, height: int) -> str:
    """Frase em inglês com o que mudou na pose de cada pessoa ("the second person from the left: the arm on the left side of the
    picture ... points up"). O ControlNet segue o mapa, mas, com a foto como referência, quase não move ninguém sem um texto
    que diga o que mudou (testado no ComfyUI real). Só entram os membros que mexeram; quem não mudou fica de fora."""
    n = len(edited)
    by_person: dict[int, list[str]] = {}
    for k, (o, e) in enumerate(zip(original, edited)):
        if changed(o, e):
            by_person[k] = _body_bits(o, e, width, height)
    for L in moved_limbs(original, edited, width, height):
        e, (p, q, r), kind = L["new"], L["chain"], L["kind"]
        up, low = _dir(e[p], e[q], width, height), _dir(e[q], e[r], width, height)
        above = kind == "arm" and e[0][2] >= MIN_CONF and e[r][1] < e[0][1] - 0.02
        word, low_word = ("upper arm", "forearm") if kind == "arm" else ("thigh", "shin")
        by_person.setdefault(L["person"], []).append(
            f"the {kind} on the {L['side']} side of the picture: {word} pointing {up}, {low_word} pointing {low}"
            + (", with the hand above the head" if above else ""))
    if not by_person:
        return ""
    # quem mexeu só um pouco (nenhuma regra acima pegou) entra com uma frase geral: o mapa de pose é quem manda
    return ("Pose change: " + ". ".join(f"{_who(k, n)}: " + ("; ".join(bits) if bits else "changes pose slightly, following the control map exactly")
                                       for k, bits in sorted(by_person.items()))
            + ". Everyone else keeps their pose.")


def erase_boxes(original: list[list[list[float]]], edited: list[list[list[float]]], width: int, height: int,
                pad: float = 0.04, hand: float = 0.6) -> list[list[float]]:
    """Um quadrado `[x, y, w, h]` (frações) em volta de onde CADA membro que mexeu estava, mão ou pé incluídos. O PickFlow põe um
    quadrado vermelho aí e manda remover: o ControlNet só desenha o membro na posição nova e o antigo ficaria duplicado."""
    boxes = []
    for L in moved_limbs(original, edited, width, height):
        pts = [L["old"][i] for i in L["chain"] if L["old"][i][2] >= MIN_CONF]
        if len(pts) < 2:
            continue
        q, r = L["old"][L["chain"][1]], L["old"][L["chain"][2]]
        if q[2] >= MIN_CONF and r[2] >= MIN_CONF:                                   # mão ou pé: continua o último osso
            pts.append([r[0] + (r[0] - q[0]) * hand, r[1] + (r[1] - q[1]) * hand, 1.0])
        x0, y0 = max(0.0, min(p[0] for p in pts) - pad), max(0.0, min(p[1] for p in pts) - pad)
        x1, y1 = min(1.0, max(p[0] for p in pts) + pad), min(1.0, max(p[1] for p in pts) + pad)
        if x1 - x0 > 0.01 and y1 - y0 > 0.01:
            boxes.append([round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)])
    return boxes


def erase_text(original: list[list[list[float]]], edited: list[list[list[float]]], width: int, height: int) -> str:
    """O que apagar em cada quadrado (frase em inglês): o membro antigo, por inteiro, sem tocar no resto do corpo."""
    n = len(edited)
    bits = [f"{L['kind']} on the {L['side']} side of the picture of {_who(L['person'], n)}" for L in moved_limbs(original, edited, width, height)]
    if not bits:
        return ""
    return ("the old " + "; the old ".join(bits) + " (the whole limb with its hand or foot, which is no longer there). "
            "Rebuild what was behind it: the person's body and clothes, the other people and the background. "
            "Do not touch the rest of the person's body or anyone else")
