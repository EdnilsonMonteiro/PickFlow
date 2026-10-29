"""Recorte de pessoa completo: junta a máscara do SAM3 com a do RMBG.

O SAM3 procura o conceito "person" e às vezes deixa de fora partes do corpo (pele à mostra, mãos, pés, um braço
em pose incomum, cabelo fino). O RMBG-2.0 olha "o que é o assunto da foto" e costuma pegar essas partes, mas
também pega o que estiver encostado na pessoa (cadeira, sofá, objeto). Por isso as duas máscaras são somadas
com cuidado, só com Pillow:

  1. o SAM3 manda: o que ele marcou fica sempre;
  2. cada pedaço contínuo do RMBG que toca a pessoa do SAM3 entra inteiro quando só acrescenta uma parte
     razoável da pessoa (um braço, uma perna, o cabelo); se acrescentaria muito mais do que isso (provavelmente
     um móvel ou outro objeto), só entra a faixa junto da borda da pessoa;
  3. buracos pequenos no meio da pessoa (pele que ninguém marcou) são preenchidos; vãos grandes (entre o braço
     e o corpo) continuam transparentes;
  4. as cores vêm sempre da imagem original, sem depender de como cada node grava o RGB.
"""
from __future__ import annotations

import io
from collections import deque

from PIL import Image, ImageChops, ImageFilter

WORK = 384                 # lado maior da análise de regiões (a máscara final sai no tamanho original)
ADD_LIMIT = 0.70           # um pedaço do RMBG pode acrescentar até 70% da área da pessoa do SAM3
BAND = 0.12                # faixa junto da borda (fração do tamanho da pessoa) quando o pedaço acrescentaria demais
HOLE = 0.01                # buracos internos de até 1% da pessoa são preenchidos


def _alpha(data: bytes, size: tuple[int, int] | None = None) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    a = im.convert("RGBA").getchannel("A")
    return a.resize(size, Image.BILINEAR) if size and a.size != size else a


def _binary(a: Image.Image, cut: int) -> Image.Image:
    return a.point(lambda v: 255 if v >= cut else 0)


def _dilate(m: Image.Image, steps: int) -> Image.Image:
    for _ in range(max(0, steps)):
        m = m.filter(ImageFilter.MaxFilter(3))
    return m


def _components(m: Image.Image) -> tuple[list[int], int, int, int]:
    """Rótulos (0 = fundo) dos pedaços contínuos (vizinhança de 4) de uma máscara binária pequena."""
    w, h = m.size
    px = m.tobytes()
    lab = [0] * (w * h)
    n = 0
    for i0 in range(w * h):
        if px[i0] < 128 or lab[i0]:
            continue
        n += 1
        lab[i0] = n
        q = deque([i0])
        while q:
            i = q.popleft()
            x = i % w
            for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, i >= w), (i + w, i < w * (h - 1))):
                if ok and px[j] >= 128 and not lab[j]:
                    lab[j] = n
                    q.append(j)
    return lab, n, w, h


def _from_labels(lab: list[int], keep: set[int], size: tuple[int, int]) -> Image.Image:
    out = Image.new("L", size, 0)
    out.frombytes(bytes(255 if v in keep else 0 for v in lab))
    return out


def merge_masks(sam: Image.Image, rmbg: Image.Image) -> Image.Image:
    """Alfa final (tamanho original) a partir dos alfas do SAM3 e do RMBG."""
    W, H = sam.size
    s_ext, r_ext = sam.getextrema(), rmbg.getextrema()
    if s_ext[1] == 0:
        return rmbg                                   # o SAM3 não achou ninguém: o RMBG é o melhor palpite
    if r_ext[1] == 0:
        return sam
    k = min(1.0, WORK / max(W, H))
    sw, sh = max(2, round(W * k)), max(2, round(H * k))
    s_small = _binary(sam.resize((sw, sh), Image.BILINEAR), 96)
    r_small = _binary(rmbg.resize((sw, sh), Image.BILINEAR), 128)
    bbox = s_small.getbbox() or (0, 0, sw, sh)
    size = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    s_area = max(1, sum(1 for v in s_small.tobytes() if v))
    band = _dilate(s_small, max(2, round(BAND * size)))

    lab, n, _w, _h = _components(r_small)
    sp, bp = s_small.tobytes(), band.tobytes()
    area = [0] * (n + 1)
    extra = [0] * (n + 1)          # pixels do pedaço que o SAM3 não marcou
    touch = [False] * (n + 1)
    near = [0] * (n + 1)           # pixels do pedaço dentro da faixa junto da pessoa
    for i, c in enumerate(lab):
        if not c:
            continue
        area[c] += 1
        if sp[i]:
            touch[c] = True
        else:
            extra[c] += 1
            if bp[i]:
                near[c] += 1
    whole = {c for c in range(1, n + 1) if touch[c] and extra[c] <= ADD_LIMIT * s_area}
    partial = {c for c in range(1, n + 1) if touch[c] and c not in whole and near[c]}
    take = _from_labels(lab, whole, (sw, sh))
    if partial:
        take = ImageChops.lighter(take, ImageChops.multiply(_from_labels(lab, partial, (sw, sh)), band))
    take = ImageChops.lighter(take, s_small)

    # buracos pequenos dentro da pessoa: fundo que não encosta na borda e é minúsculo
    inv = take.point(lambda v: 0 if v >= 128 else 255)
    hl, hn, _a, _b = _components(inv)
    edge = {hl[i] for i in range(sw)} | {hl[(sh - 1) * sw + i] for i in range(sw)} | \
           {hl[j * sw] for j in range(sh)} | {hl[j * sw + sw - 1] for j in range(sh)}
    cnt = [0] * (hn + 1)
    for c in hl:
        cnt[c] += 1
    total = max(1, sum(1 for v in take.tobytes() if v))
    holes = {c for c in range(1, hn + 1) if c not in edge and cnt[c] <= HOLE * total}
    if holes:
        take = ImageChops.lighter(take, _from_labels(hl, holes, (sw, sh)))

    gate = _dilate(take, 2).resize((W, H), Image.BILINEAR)           # margem para não cortar a borda macia do RMBG
    gate = gate.point(lambda v: 255 if v > 24 else 0)
    r_part = ImageChops.multiply(rmbg, gate)
    out = ImageChops.lighter(sam, r_part)
    if holes:                                                         # preenche os buracos no tamanho original
        hole_mask = _from_labels(hl, holes, (sw, sh)).resize((W, H), Image.BILINEAR).point(lambda v: 255 if v > 127 else 0)
        out = ImageChops.lighter(out, hole_mask)
    return out


def merge_person_cutouts(original: bytes, sam_png: bytes, rmbg_png: bytes) -> bytes:
    """PNG final: cores da imagem original, alfa = SAM3 + o que o RMBG acrescenta de pessoa."""
    src = Image.open(io.BytesIO(original))
    src.load()
    src = src.convert("RGB")
    sam = _alpha(sam_png, src.size)
    rmbg = _alpha(rmbg_png, src.size)
    out = src.convert("RGBA")
    out.putalpha(merge_masks(sam, rmbg))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
