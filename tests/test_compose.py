"""Montagens: caixas desenhadas, camadas com rotação e várias camadas."""
import io

import pytest
from PIL import Image

from pickflow.compose import burn_boxes, clamp_frac, place_layer, place_layers


def png(size, color, mode="RGBA"):
    b = io.BytesIO()
    Image.new(mode, size, color).save(b, format="PNG")
    return b.getvalue()


def load(data):
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_burn_boxes_draws_every_box_in_the_color_and_numbers_them():
    out = load(burn_boxes(png((200, 100), (10, 10, 10, 255)), [{"b": [0.1, 0.1, 0.3, 0.5]}, {"b": [0.6, 0.2, 0.3, 0.5]}],
                          "green", thickness=4, numbered=True))
    assert out.getpixel((20, 30))[:3] == (0, 210, 0) and out.getpixel((120, 30))[:3] == (0, 210, 0)     # bordas
    assert out.getpixel((100, 5))[:3] == (10, 10, 10)                                                  # fora das caixas, intacto
    inside = [out.getpixel((x, y))[:3] for x in range(26, 50) for y in range(14, 38)]
    assert any(p == (255, 255, 255) for p in inside)                                                   # o número 1


def test_place_layer_rotation_turns_around_the_center_and_keeps_the_part_inside():
    layer = png((40, 10), (200, 0, 0, 255))                       # uma barra horizontal
    plain, rect0 = place_layer(layer, (100, 100), [0.3, 0.45, 0.4, 0.1])
    assert rect0 == (30, 45, 40, 10)
    out, rect = place_layer(layer, (100, 100), [0.3, 0.45, 0.4, 0.1, 90])
    assert rect == (45, 30, 10, 40)                               # em pé, no mesmo centro
    assert load(out).getpixel((5, 20))[3] == 255
    diag, rd = place_layer(layer, (100, 100), [0.3, 0.45, 0.4, 0.1, 45])
    assert rd[2] > 10 and rd[3] > 10 and abs(rd[2] - rd[3]) <= 1 and load(diag).getpixel((rd[2] // 2, rd[3] // 2))[3] == 255
    assert load(diag).getpixel((0, 0))[3] == 0                    # os cantos da caixa girada ficam transparentes


def test_rotation_can_leave_the_image_and_is_refused_when_almost_outside():
    layer = png((40, 40), (0, 200, 0, 255))
    out, rect = place_layer(layer, (100, 100), [-0.2, 0.3, 0.4, 0.4, 30])
    assert rect[0] == 0 and load(out).size == (rect[2], rect[3])
    with pytest.raises(ValueError):
        place_layer(layer, (100, 100), [1.2, 0.3, 0.2, 0.2, 30])


def test_several_layers_become_one_transparent_image_cropped_to_what_shows():
    a, b = png((20, 20), (255, 0, 0, 255)), png((20, 20), (0, 0, 255, 255))
    out, rect = place_layers([(a, [0.1, 0.1, 0.2, 0.2]), (b, [0.6, 0.5, 0.2, 0.2, 20])], (100, 100))
    im = load(out)
    assert rect[0] == 10 and rect[1] == 10 and im.size == (rect[2], rect[3]) and rect[2] > 60
    assert im.getpixel((5, 5)) == (255, 0, 0, 255) and im.getpixel((im.width // 2, 2))[3] == 0
    assert place_layers([(a, [0.1, 0.1, 0.2, 0.2])], (100, 100))[1] == (10, 10, 20, 20)


