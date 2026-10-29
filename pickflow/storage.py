"""Imagens e conversas gravadas em data/ (nada disso toca nas pastas do ComfyUI)."""
from __future__ import annotations

import re

import io
import json
import time
import uuid
from pathlib import Path

from PIL import Image

from .store import atomic_write

EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "BMP": "bmp"}


class ImageStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "images"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, name: str | None = None) -> dict:
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Arquivo não é uma imagem válida: {name or ''}") from e
        iid = uuid.uuid4().hex[:16]
        ext = EXT.get(im.format or "PNG", "png")
        (self.dir / f"{iid}.{ext}").write_bytes(data)
        return {"id": iid, "name": name or f"{iid}.{ext}", "url": f"/files/{iid}.{ext}",
                "width": im.width, "height": im.height}

    def save_pil(self, im: Image.Image, name: str) -> dict:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return self.save(buf.getvalue(), name)

    def path(self, iid: str) -> Path:
        iid = Path(str(iid)).stem
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", iid):          # nada de curingas nem de sair da pasta
            raise FileNotFoundError(iid)
        for p in self.dir.glob(f"{iid}.*"):
            return p
        raise FileNotFoundError(iid)

    def load(self, iid: str) -> bytes:
        return self.path(iid).read_bytes()

    def info(self, iid: str) -> dict:
        p = self.path(iid)
        with Image.open(p) as im:
            return {"id": p.stem, "name": p.name, "url": f"/files/{p.name}", "width": im.width, "height": im.height}


def to_png(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def flatten_alpha(data: bytes, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """Imagem com transparência vira opaca sobre `color` (branco). O LoadImage do ComfyUI joga o alfa fora e o fundo
    transparente vira PRETO: um personagem de fundo transparente aparecia num fundo preto no resultado."""
    im = Image.open(io.BytesIO(data))
    if "A" not in im.getbands() and not (im.mode == "P" and "transparency" in im.info):
        return data
    im = im.convert("RGBA")
    if im.getchannel("A").getextrema()[0] == 255:
        return data
    base = Image.new("RGB", im.size, color)
    base.paste(im, mask=im.getchannel("A"))
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


def mask_to_alpha_png(source: bytes, mask_png: bytes) -> bytes:
    """Convenção do LoadImage do ComfyUI: a máscara é o INVERSO do alfa.
    Recebe a fonte e uma máscara (branco = região a refazer) e devolve a fonte com a região recortada."""
    src = Image.open(io.BytesIO(source)).convert("RGBA")
    m = Image.open(io.BytesIO(mask_png)).convert("L").resize(src.size)
    alpha = m.point(lambda v: 0 if v > 127 else 255)
    src.putalpha(alpha)
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    return buf.getvalue()


class ConvStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "conversations"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, cid: str) -> Path:
        return self.dir / f"{Path(cid).stem}.json"

    def create(self, title: str = "Nova conversa") -> dict:
        c = {"id": uuid.uuid4().hex[:12], "title": title, "created": time.time(), "updated": time.time(),
             "messages": []}
        self.save(c)
        return c

    def save(self, c: dict):
        c["updated"] = time.time()
        atomic_write(self._p(c["id"]), json.dumps(c, ensure_ascii=False))

    def get(self, cid: str) -> dict:
        p = self._p(cid)
        if not p.exists():
            raise KeyError(cid)
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": c["id"], "title": c["title"], "updated": c["updated"],
                            "count": len(c["messages"])})
            except Exception:  # noqa: BLE001
                continue
        return sorted(out, key=lambda c: -c["updated"])

    def delete(self, cid: str):
        self._p(cid).unlink(missing_ok=True)


def crop_fraction(data: bytes, box: list[float]) -> bytes:
    """Recorta a imagem pela caixa [x, y, w, h] em frações (0-1) e devolve PNG."""
    im = Image.open(io.BytesIO(data))
    im.load()
    W, H = im.size
    x0, y0 = int(round(box[0] * W)), int(round(box[1] * H))
    x1, y1 = int(round((box[0] + box[2]) * W)), int(round((box[1] + box[3]) * H))
    x0, y0 = max(0, min(W - 1, x0)), max(0, min(H - 1, y0))
    x1, y1 = max(x0 + 1, min(W, x1)), max(y0 + 1, min(H, y1))
    buf = io.BytesIO()
    im.crop((x0, y0, x1, y1)).save(buf, format="PNG")
    return buf.getvalue()


class NoTransparency(ValueError):
    pass


def trim_to_alpha(data: bytes, strict: bool = True) -> bytes:
    """Corta as bordas totalmente transparentes e limpa o "pó" quase invisível do recorte.
    `strict`: falha se a imagem não tem transparência ou se não sobrou nada (sem `strict`, devolve a imagem como veio)."""
    im = Image.open(io.BytesIO(data))
    im.load()
    if "A" not in im.getbands() and not (im.mode == "P" and "transparency" in im.info):
        if strict:
            raise NoTransparency("O recorte veio sem transparência (fundo opaco).")
        return to_png(data)
    im = im.convert("RGBA")
    alpha = im.getchannel("A")
    lo, hi = alpha.getextrema()
    if hi == 0:
        if strict:
            raise NoTransparency("O recorte saiu vazio: nada foi reconhecido como pessoa ou objeto.")
        return to_png(data)
    if lo == 255:
        if strict:
            raise NoTransparency("O recorte veio sem transparência (fundo opaco).")
        return to_png(data)
    im.putalpha(alpha.point(lambda v: 0 if v < 10 else v))          # resto de máscara quase invisível some
    box = im.getchannel("A").getbbox()
    buf = io.BytesIO()
    im.crop(box).save(buf, format="PNG")
    return buf.getvalue()


def cut_edges(data: bytes, min_frac: float = 0.03) -> list[str]:
    """Bordas da imagem em que o recorte encosta (`bottom`, `left`, `right`, `top`): a pessoa ou o objeto continua fora
    da foto, ou seja, está cortado (um busto encosta embaixo). Antes de cortar as bordas transparentes."""
    im = Image.open(io.BytesIO(data))
    if "A" not in im.getbands():
        return []
    a = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 128 else 0)
    W, H = a.size
    out = []
    for name in ("top", "bottom", "left", "right"):        # o SAM3 deixa alguns pixels de folga: olha uma faixa, não só a borda
        horiz = name in ("top", "bottom")
        length, depth = (W, H) if horiz else (H, W)
        band = max(6, int(depth * 0.02))
        best = 0
        for k in range(band):
            if horiz:
                line = a.crop((0, k if name == "top" else H - 1 - k, W, (k if name == "top" else H - 1 - k) + 1))
            else:
                line = a.crop((k if name == "left" else W - 1 - k, 0, (k if name == "left" else W - 1 - k) + 1, H))
            best = max(best, line.histogram()[255])
        if best >= min_frac * length:
            out.append(name)
    return out


def cutout_state(data: bytes) -> str:
    """'ok' (tem sujeito e fundo transparente), 'empty' (nada reconhecido) ou 'opaque' (nada foi removido)."""
    im = Image.open(io.BytesIO(data))
    if "A" not in im.getbands():
        return "opaque"
    lo, hi = im.convert("RGBA").getchannel("A").getextrema()
    return "empty" if hi == 0 else "opaque" if lo == 255 else "ok"


from .compose import place_layer  # noqa: E402,F401  (a posição das camadas mora em compose.py)
