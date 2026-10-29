"""Testes da medição de foto (`pickflow.quality`): só imagens sintéticas, feitas na hora (não usam `data/images/`)."""
from __future__ import annotations

import io
import random
import time

import pytest
from PIL import Image, ImageFilter

from pickflow.quality import BIG_MP, CRISP, EMPTY, SMALL_MP, SOFT, Pic, describe, measure


def ruido(w: int, h: int | None = None, seed: int = 7) -> Image.Image:
    """Foto sintética "nítida": ruído determinístico (detalhe de 1 pixel em todo lugar)."""
    h = w if h is None else h
    random.seed(seed)
    return Image.frombytes("L", (w, h), random.randbytes(w * h))


def png(im: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def jpeg(im: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def test_foto_grande_e_nitida():
    pic = measure(png(ruido(2000), compress_level=1))
    assert (pic.width, pic.height) == (2000, 2000)
    assert pic.megapixels == pytest.approx(4.0)
    assert pic.big and not pic.small
    assert pic.crisp and not pic.soft
    frase = describe(pic)
    assert "MP" in frase
    assert frase.startswith("2000x2000, 4.0 MP")
    assert frase.endswith("sharp detail")


def test_borrada_e_menos_nitida_e_fica_soft():
    nitida = ruido(2000)
    a = measure(png(nitida, compress_level=1))
    b = measure(png(nitida.filter(ImageFilter.GaussianBlur(6)), compress_level=1))
    assert b.sharpness < a.sharpness
    assert b.sharpness < a.sharpness * 0.5        # a diferença é clara, não uma vírgula
    assert b.soft and not b.crisp
    assert not a.soft and a.crisp
    assert describe(b).endswith("soft focus")


def test_lixo_nao_levanta_e_vira_pic_vazio():
    for dado in (b"nao e uma imagem", b"", b"\x89PNG\r\n\x1a\n" + b"lixo" * 50):
        assert measure(dado) == EMPTY
        assert describe(measure(dado)) == ""
    assert EMPTY == Pic(0, 0, 0.0, 0.0)
    assert describe(Pic(0, 0, 0.0, 0.0)) == ""


def test_png_com_alfa_jpeg_e_webp():
    im = ruido(600, 400).convert("RGBA")
    im.putalpha(Image.linear_gradient("L").resize((600, 400)))
    a = measure(png(im))
    assert (a.width, a.height) == (600, 400) and a.sharpness > 0

    # PNG todo transparente: não pode quebrar (a transparência é ignorada, só o RGB conta)
    vazio = Image.new("RGBA", (400, 300), (255, 0, 0, 0))
    assert measure(png(vazio)).sharpness > 0

    b = measure(jpeg(im.convert("RGB")))
    assert (b.width, b.height) == (600, 400) and b.sharpness > 0

    buf = io.BytesIO()
    im.save(buf, "WEBP")
    c = measure(buf.getvalue())
    assert (c.width, c.height) == (600, 400) and c.sharpness > 0


def test_describe_frases_e_limiares():
    assert describe(Pic(1600, 1200, 1.92, 1500.0)) == "1600x1200, 1.9 MP, sharp detail"
    assert describe(Pic(640, 480, 0.3072, 100.0)) == "640x480, 0.3 MP, soft focus"
    assert describe(Pic(2400, 2400, 5.76, 800.0)) == "2400x2400, 5.8 MP, normal detail"
    # `big` pede 2 MP: 1600x1200 (1,9 MP) ainda não é grande
    assert Pic(1600, 1200, 1.92, 1500.0).big is False
    assert Pic(1600, 1200, 2.0, 1500.0).big is True
    # nas bordas: SOFT já não é "soft", CRISP já é "crisp"
    assert Pic(1000, 1000, 1.0, SOFT).soft is False
    assert Pic(1000, 1000, 1.0, SOFT - 1).soft is True
    assert Pic(1000, 1000, 1.0, CRISP).crisp is True
    assert Pic(1000, 1000, 1.0, CRISP - 1).crisp is False


