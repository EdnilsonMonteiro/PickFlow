"""Recorte de pessoa: SAM3 + RMBG (pickflow/cutout.py), com máscaras sintéticas."""
import io

from PIL import Image, ImageDraw

from pickflow.cutout import merge_masks, merge_person_cutouts

W, H = 400, 600


def mask(*shapes, size=(W, H)):
    im = Image.new("L", size, 0)
    d = ImageDraw.Draw(im)
    for kind, box in shapes:
        getattr(d, kind)(box, fill=255)
    return im


def png(im):
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


TORSO = ("rectangle", (150, 150, 250, 400))
HEAD = ("ellipse", (160, 60, 240, 140))
LEG = ("rectangle", (160, 400, 240, 580))
ARM = ("rectangle", (250, 160, 330, 200))             # um braço que o SAM3 deixou de fora


def px(m, x, y):
    return m.getpixel((x, y))


def test_body_parts_missed_by_sam_are_recovered_from_rmbg():
    sam = mask(TORSO, HEAD)                              # sem perna e sem braço
    rmbg = mask(TORSO, HEAD, LEG, ARM)
    out = merge_masks(sam, rmbg)
    assert px(out, 200, 500) == 255 and px(out, 300, 180) == 255        # perna e braço voltaram
    assert px(out, 200, 250) == 255 and px(out, 30, 30) == 0


def test_a_big_object_touching_the_person_is_not_pasted_whole():
    chair = ("rectangle", (60, 380, 340, 590))           # bem maior que o que falta da pessoa
    sam = mask(TORSO, HEAD, LEG)
    rmbg = mask(TORSO, HEAD, LEG, chair)
    out = merge_masks(sam, rmbg)
    assert px(out, 70, 585) == 0 and px(out, 330, 585) == 0             # o móvel fica de fora
    assert px(out, 200, 250) == 255 and px(out, 200, 500) == 255        # a pessoa fica


def test_small_holes_inside_the_person_are_filled_but_big_gaps_stay():
    sam = mask(TORSO, HEAD, LEG)
    d = ImageDraw.Draw(sam)
    d.rectangle((190, 250, 205, 265), fill=0)                           # pele que ninguém marcou
    d.rectangle((255, 0, 270, 0), fill=0)
    rmbg = mask(TORSO, HEAD, LEG)
    ImageDraw.Draw(rmbg).rectangle((190, 250, 205, 265), fill=0)
    out = merge_masks(sam, rmbg)
    assert px(out, 197, 257) == 255
    gap = mask(("rectangle", (150, 150, 180, 400)), ("rectangle", (230, 150, 260, 400)), HEAD)   # vão grande no meio
    gap_out = merge_masks(gap, gap)
    assert px(gap_out, 205, 300) == 0


def test_sam_empty_falls_back_to_rmbg_and_rmbg_empty_keeps_sam():
    full = mask(TORSO, HEAD)
    empty = mask()
    assert px(merge_masks(empty, full), 200, 250) == 255
    assert px(merge_masks(full, empty), 200, 250) == 255


def test_merged_png_takes_colors_from_the_original_and_alpha_from_the_masks():
    orig = Image.new("RGB", (W, H), (200, 120, 90))
    sam = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sam.putalpha(mask(TORSO, HEAD))
    rm = Image.new("RGBA", (W, H), (9, 9, 9, 0))
    rm.putalpha(mask(TORSO, HEAD, LEG))
    out = Image.open(io.BytesIO(merge_person_cutouts(png(orig), png(sam), png(rm)))).convert("RGBA")
    assert out.getpixel((200, 500)) == (200, 120, 90, 255)               # a perna tem a cor da foto, não a do RMBG
    assert out.getpixel((10, 10))[3] == 0


