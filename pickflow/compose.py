"""Montagens de imagem feitas pelo PickFlow antes de mandar ao ComfyUI (só Pillow).

  burn_boxes   desenha caixas coloridas (e o número de cada uma) na imagem: é como vários quadrados vermelhos
               ou várias caixas verdes chegam ao modelo, sem precisar de um nó do ComfyUI por caixa
  place_layer  posiciona uma camada com recorte no fundo (tamanho livre, rotação, pode sair da imagem)
  place_layers várias camadas de uma vez, já juntas numa só imagem transparente
  fit_megapixels reduz fotos enormes (celular: 12 MP) antes de irem ao ComfyUI
  pad_gray     aumenta a tela com cinza #808080 (outpaint) e paste_back cola o original de volta no resultado
"""
from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

COLORS = {"red": (255, 0, 0), "green": (0, 210, 0), "blue": (0, 120, 255), "yellow": (255, 220, 0)}
MAX_WORK_SIDE = 6000          # com rotação, camadas gigantes são reduzidas para não estourar a memória


def _font(size: int):
    try:
        return ImageFont.load_default(size=max(10, size))
    except TypeError:                                   # Pillow antigo: fonte fixa pequena
        return ImageFont.load_default()


def fit_megapixels(data: bytes, max_mp: float) -> tuple[bytes, tuple[int, int]]:
    """Reduz a imagem (mantendo a proporção) se passar de `max_mp` megapixels; devolve (bytes, (largura, altura)).
    Imagem que já cabe volta intacta. `max_mp` <= 0 desliga. Uma foto de celular de 12 MP vira ~2 MP: o
    Qwen-Image não ganha nada com ela, e cada megapixel a mais torna o texto-encoder e a difusão muito mais lentos."""
    im = Image.open(io.BytesIO(data))
    im.load()
    W, H = im.size
    if max_mp and max_mp > 0 and W * H > max_mp * 1_000_000:
        k = math.sqrt(max_mp * 1_000_000 / (W * H))
        nw, nh = max(1, int(W * k)), max(1, int(H * k))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
        buf = io.BytesIO()
        im.resize((nw, nh), Image.LANCZOS).save(buf, format="PNG")
        return buf.getvalue(), (nw, nh)
    return data, (W, H)


def burn_boxes(data: bytes, boxes: list[dict], color: str = "red", thickness: int = 8, filled: bool = False,
               numbered: bool = False) -> bytes:
    """Desenha cada caixa `{"b": [x, y, w, h]}` (frações 0-1) com a cor pedida. `numbered` escreve 1, 2, 3… dentro delas."""
    im = Image.open(io.BytesIO(data))
    im.load()
    im = im.convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    c = COLORS.get(color, COLORS["red"])
    for i, bx in enumerate(boxes, 1):
        x, y, w, h = bx["b"]
        r = [x * W, y * H, (x + w) * W, (y + h) * H]
        if filled:
            d.rectangle(r, fill=c)
        else:
            d.rectangle(r, outline=c, width=max(1, int(thickness)))
        if numbered:
            side = max(28, min(r[2] - r[0], r[3] - r[1]) * 0.28)
            f = _font(int(side * 0.8))
            pad = int(thickness)
            bb = (r[0] + pad, r[1] + pad, r[0] + pad + side, r[1] + pad + side)
            d.rectangle(bb, fill=c)
            d.text(((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), str(i), fill=(255, 255, 255), font=f, anchor="mm")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def place_layer(overlay: bytes, bg_size: tuple[int, int], frac: list[float]) -> tuple[bytes, tuple[int, int, int, int]]:
    """Posiciona a camada no fundo. `frac` = [x, y, largura, altura] em frações do fundo (x e y podem ser negativos
    e a camada pode passar das bordas: a parte de fora é cortada) e, opcionalmente, um 5º valor: rotação em graus,
    no sentido horário, em torno do centro. Devolve o PNG já no tamanho final e a parte visível em pixels do fundo
    (x, y, largura, altura)."""
    W, H = bg_size
    fx, fy, fw, fh = [float(v) for v in frac[:4]]
    rot = float(frac[4]) if len(frac) > 4 else 0.0
    rot = ((rot + 180.0) % 360.0) - 180.0
    pw, ph = max(1, round(fw * W)), max(1, round(fh * H))
    px, py = round(fx * W), round(fy * H)
    a = math.radians(rot)
    rw = abs(pw * math.cos(a)) + abs(ph * math.sin(a))               # caixa que envolve a camada girada
    rh = abs(pw * math.sin(a)) + abs(ph * math.cos(a))
    cx, cy = px + pw / 2, py + ph / 2
    ix0, iy0 = max(0, math.floor(cx - rw / 2)), max(0, math.floor(cy - rh / 2))
    ix1, iy1 = min(W, math.ceil(cx + rw / 2)), min(H, math.ceil(cy + rh / 2))
    vis = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if ix1 - ix0 < 2 or iy1 - iy0 < 2 or vis < 0.05 * rw * rh:
        raise ValueError("A pessoa ou o objeto ficou fora da imagem de fundo (ou quase): traga mais para dentro.")
    im = Image.open(io.BytesIO(overlay))
    im.load()
    im = im.convert("RGBA")
    ow, oh = im.size
    if abs(rot) < 0.05:                                               # sem giro: recorta primeiro, no tamanho original
        sx, sy = ow / pw, oh / ph
        src = (round((ix0 - px) * sx), round((iy0 - py) * sy), round((ix1 - px) * sx), round((iy1 - py) * sy))
        src = (max(0, src[0]), max(0, src[1]), max(src[0] + 1, min(ow, src[2])), max(src[1] + 1, min(oh, src[3])))
        part = im.crop(src).resize((ix1 - ix0, iy1 - iy0), Image.LANCZOS)
    else:
        k = min(1.0, MAX_WORK_SIDE / max(rw, rh))
        sw, sh = max(1, round(pw * k)), max(1, round(ph * k))
        big = im.resize((sw, sh), Image.LANCZOS).rotate(-rot, resample=Image.BICUBIC, expand=True)
        bw_, bh_ = big.size
        left, top = cx * k - bw_ / 2, cy * k - bh_ / 2                # onde a camada girada começa, na escala k
        box = (round(ix0 * k - left), round(iy0 * k - top), round(ix1 * k - left), round(iy1 * k - top))
        part = big.crop(box).resize((ix1 - ix0, iy1 - iy0), Image.LANCZOS)      # crop fora da camada vira transparente
    buf = io.BytesIO()
    part.save(buf, format="PNG")
    return buf.getvalue(), (ix0, iy0, ix1 - ix0, iy1 - iy0)


def place_layers(layers: list[tuple[bytes, list[float]]], bg_size: tuple[int, int]) -> tuple[bytes, tuple[int, int, int, int]]:
    """Várias camadas (na ordem: a última fica por cima) numa imagem transparente só, cortada ao que aparece."""
    if len(layers) == 1:
        return place_layer(layers[0][0], bg_size, layers[0][1])
    W, H = bg_size
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for data, frac in layers:
        png, (x, y, _w, _h) = place_layer(data, bg_size, frac)
        piece = Image.open(io.BytesIO(png)).convert("RGBA")
        canvas.alpha_composite(piece, (x, y))
    box = canvas.getchannel("A").getbbox()
    if not box:
        raise ValueError("As camadas ficaram fora da imagem de fundo.")
    buf = io.BytesIO()
    canvas.crop(box).save(buf, format="PNG")
    return buf.getvalue(), (box[0], box[1], box[2] - box[0], box[3] - box[1])


def clamp_frac(v: Any) -> list[float]:
    """Valida [x, y, w, h(, rot)] vindo da tela e põe nos limites aceitos."""
    nums = [float(n) for n in v]
    if len(nums) not in (4, 5) or not all(map(math.isfinite, nums)):
        raise ValueError("Posição inválida para a pessoa ou o objeto.")
    x, y, w, h = nums[:4]
    if w < 0.01 or h < 0.01:
        raise ValueError("A pessoa ou o objeto ficou pequeno demais na imagem de fundo.")
    out = [round(min(max(-4.0, x), 5.0), 5), round(min(max(-4.0, y), 5.0), 5), round(min(w, 4.0), 5), round(min(h, 4.0), 5)]
    if len(nums) == 5:
        out.append(round(((nums[4] + 180.0) % 360.0) - 180.0, 2))
    return out


OUTPAINT_GRAY = (128, 128, 128)         # o cinza que a LoRA de Outpaint completa (#808080)
SIDES = ("left", "right", "top", "bottom")


def expand_spec(v: Any) -> dict[str, float]:
    """`{left, right, top, bottom}` (ou `all`) em frações do lado da foto, cada uma entre 0 e 1."""
    if not isinstance(v, dict):
        return {}
    base = float(v.get("all") or 0)
    out = {}
    for k in SIDES:
        try:
            f = float(v.get(k, base) or 0)
        except (TypeError, ValueError):
            f = 0.0
        if f > 0:
            out[k] = min(1.0, f)
    return out


def pad_gray(data: bytes, spec: dict, multiple: int = 32) -> tuple[bytes, tuple[int, int, int, int]]:
    """Põe a foto numa tela maior, cinza #808080 em volta (o que a LoRA de Outpaint completa). A tela fica com lados
    múltiplos de `multiple`. Devolve (PNG, (x, y, largura, altura) da foto dentro da tela)."""
    sp = expand_spec(spec)
    im = Image.open(io.BytesIO(data)).convert("RGB")
    W, H = im.size
    left, right = int(round(W * sp.get("left", 0))), int(round(W * sp.get("right", 0)))
    top, bottom = int(round(H * sp.get("top", 0))), int(round(H * sp.get("bottom", 0)))
    cw, ch = W + left + right, H + top + bottom
    cw2, ch2 = -(-cw // multiple) * multiple, -(-ch // multiple) * multiple       # arredonda para cima
    left += (cw2 - cw) // 2 if (left or right) else 0
    top += (ch2 - ch) // 2 if (top or bottom) else 0
    if not (sp.get("left") or sp.get("right")):
        left = (cw2 - W) // 2
    if not (sp.get("top") or sp.get("bottom")):
        top = (ch2 - H) // 2
    canvas = Image.new("RGB", (cw2, ch2), OUTPAINT_GRAY)
    canvas.paste(im, (left, top))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue(), (left, top, W, H)


def paste_back(result: bytes, original: bytes, box: tuple[int, int, int, int], canvas: tuple[int, int],
               feather: int = 32) -> bytes:
    """Cola a foto original de volta no resultado do outpaint (com a borda suavizada em `feather` px): o que já
    existia fica idêntico e só a parte nova vem do modelo."""
    out = Image.open(io.BytesIO(result)).convert("RGB")
    if out.size != tuple(canvas):
        out = out.resize(tuple(canvas), Image.LANCZOS)
    orig = Image.open(io.BytesIO(original)).convert("RGB")
    x, y, w, h = box
    if orig.size != (w, h):
        orig = orig.resize((w, h), Image.LANCZOS)
    f = max(0, min(feather, w // 4, h // 4))
    m = 2 * f                                            # margem: o desfoque não "come" as bordas que tocam a tela
    flush = (x <= 0, y <= 0, x + w >= canvas[0], y + h >= canvas[1])
    inset = [0 if fl else f for fl in flush]
    big = Image.new("L", (w + 2 * m, h + 2 * m), 0)
    ImageDraw.Draw(big).rectangle([
        0 if flush[0] else m + inset[0], 0 if flush[1] else m + inset[1],
        w + 2 * m if flush[2] else m + w - 1 - inset[2], h + 2 * m if flush[3] else m + h - 1 - inset[3]], fill=255)
    if f:
        big = big.filter(ImageFilter.GaussianBlur(f / 2))
    mask = big.crop((m, m, m + w, m + h))
    region = out.crop((x, y, x + w, y + h))
    region.paste(orig, (0, 0), mask)
    out.paste(region, (x, y))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def blend_masked(original: bytes, result: bytes, mask: Image.Image) -> bytes:
    """`result` onde a máscara é branca, a `original` onde é preta (com a transição suave da própria máscara). O resultado é levado ao
    tamanho da original se o modelo devolveu outro. Usado em "Mudar a pose": só o que mexeu vem do modelo, o resto é a foto de verdade."""
    base = Image.open(io.BytesIO(original)).convert("RGB")
    res = Image.open(io.BytesIO(result)).convert("RGB")
    if res.size != base.size:
        res = res.resize(base.size, Image.LANCZOS)
    m = mask if mask.size == base.size else mask.resize(base.size, Image.BILINEAR)
    out = Image.composite(res, base, m)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def gray_fraction(data: bytes, tol: int = 3) -> float:
    """Fração dos pixels que ainda são o cinza #808080 do outpaint (o fundo de um render 3D ou a borda aumentada).
    Um render de quarto em ângulo novo tinha 22-41% de cinza; depois de um bom outpaint sobra menos de 1%."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((512, 512))
    r, g, b = (ch.point(lambda v: 255 if abs(v - 128) <= tol else 0) for ch in im.split())
    near = ImageChops.multiply(ImageChops.multiply(r, g), b)
    return near.histogram()[255] / (im.size[0] * im.size[1])


def region_change(original: bytes, result: bytes, box: list[float], ignore: list[list[float]] | None = None,
                  thr: int = 28) -> float | None:
    """Fração dos pixels de `box` ([x, y, w, h] em frações) que mudaram de verdade entre a imagem original e o
    resultado (o resultado pode ter outro tamanho). `ignore`: outras caixas a descontar (o destino, num "mover").
    Serve para conferir se algo foi mesmo tirado de um lugar: removido dá ~0,5; intacto, ~0 (o ruído do VAE fica
    abaixo do limite). Devolve None quando a região é pequena demais ou quase toda coberta por `ignore`."""
    a = Image.open(io.BytesIO(original)).convert("RGB")
    b = Image.open(io.BytesIO(result)).convert("RGB")
    if b.size != a.size:
        b = b.resize(a.size, Image.LANCZOS)
    W, H = a.size

    def px(bx):
        x, y, w, h = [float(n) for n in bx]
        return max(0, int(x * W)), max(0, int(y * H)), min(W, int((x + w) * W)), min(H, int((y + h) * H))

    x0, y0, x1, y1 = px(box)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    blur = ImageFilter.GaussianBlur(2)
    A, B = a.crop((x0, y0, x1, y1)).filter(blur), b.crop((x0, y0, x1, y1)).filter(blur)
    diff = ImageChops.difference(A, B).convert("L").point(lambda v: 255 if v > thr else 0)
    keep = Image.new("L", A.size, 255)
    d = ImageDraw.Draw(keep)
    for ig in ignore or []:
        ix0, iy0, ix1, iy1 = px(ig)
        d.rectangle([ix0 - x0, iy0 - y0, ix1 - x0, iy1 - y0], fill=0)
    total = keep.histogram()[255]
    if total < 0.25 * A.size[0] * A.size[1]:
        return None
    return ImageChops.multiply(diff, keep).histogram()[255] / total
