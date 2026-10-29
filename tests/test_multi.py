"""Vários quadrados vermelhos, várias caixas verdes com a imagem de cada uma, vários recortes e camadas girando."""
import io

from PIL import Image

from test_e2e import (_answer, _cutout_need, _layer, _paste_run, _uploaded_size, api, png,  # noqa: F401
                      start, types, upload, wait_event)


def px(api, prompt, node, xy):
    data = api.fake.uploads[prompt[node]["inputs"]["image"].split("/")[-1]]
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel(xy)


def test_several_red_boxes_all_reach_the_model_and_the_prompt_says_they_are_the_same_kind(api):
    a = upload(api, "a.png", png((200, 200, 200), (200, 100)))
    _, rid = start(api, "tire os copos", [a["id"]], route="replace_redbox")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    t = need["tasks"][0]
    assert t["multiple"] and "iguais" in t["hint"].lower()
    _answer(api, rid, need, {"red": [{"b": [0.1, 0.1, 0.2, 0.3]}, {"b": [0.6, 0.5, 0.2, 0.3]}, {"b": [0.4, 0.05, 0.1, 0.2]}]})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    img_node = next(k for k, n in prompt.items() if n["class_type"] == "LoadImage")
    # as caixas extras (2ª e 3ª) vão queimadas na imagem; a 1ª o nó DrawBox desenha
    assert px(api, prompt, img_node, (130, 65)) == (255, 0, 0) or px(api, prompt, img_node, (121, 52)) == (255, 0, 0)
    box = prompt["6"]["inputs"]
    assert (box["box_x"], box["box_y"], box["box_w"], box["box_h"]) == (0.1, 0.1, 0.2, 0.3)
    text = next(n["inputs"]["prompt"] for n in prompt.values() if n["class_type"] == "TextEncodeQwenImage21")
    assert "There are 3 red squares" in text                      # o prompt sabe que são 3 quadrados


def test_several_objects_go_into_green_boxes_each_with_the_image_it_came_from(api):
    dest = upload(api, "cena.png", png((90, 90, 90), (200, 100)))
    o1, o2, o3 = (upload(api, f"o{i}.png", png((10 * i, 50, 50), (60, 60))) for i in (1, 2, 3))
    _, rid = start(api, "coloque os objetos das imagens 2, 3 e 4 na cena", [dest["id"], o1["id"], o2["id"], o3["id"]], route="transfer_multi")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    keys = [t["key"] for t in need["tasks"]]
    assert keys == ["green", "red_2", "red_3", "red_4"]
    green = need["tasks"][0]
    assert green["multiple"] and green["numbered"] and [r["n"] for r in green["refs"]] == ["2", "3", "4"]
    assert all(t["label"].startswith("Marque o objeto na imagem") for t in need["tasks"][1:]) and need["tasks"][1]["label"].endswith("2 (caixa vermelha)")
    refs = [r["key"] for r in green["refs"]]
    _answer(api, rid, need, {"green": [{"b": [0.05, 0.1, 0.25, 0.5], "ref": refs[0]}, {"b": [0.40, 0.1, 0.25, 0.5], "ref": refs[2]},
                                       {"b": [0.70, 0.1, 0.25, 0.5], "ref": refs[0]}],
                             "red_2": [0.2, 0.2, 0.5, 0.5], "red_3": "skip", "red_4": "skip"})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    text = next(n["inputs"]["prompt"] for n in prompt.values() if n["class_type"] == "TextEncodeQwenImage21")
    assert "Green box 1: put the object from <image2>" in text and "Green box 2: put the object from <image4>" in text
    assert "Green box 3: put the object from <image2>" in text and "inside its red box" in text.split("Green box 2")[0]
    assert "inside its red box" not in text.split("Green box 2")[1].split("\n")[0]       # só a imagem 2 tem caixa vermelha
    lo = {k: n for k, n in prompt.items() if n["class_type"] == "LoadImage"}
    assert len(lo) == 4
    # a imagem de destino (200x100) saiu com as 3 caixas verdes queimadas; as de objeto (60x60) não têm verde
    sizes = {k: _uploaded_size(api, prompt, k) for k in lo}
    (dk,) = [k for k, sz in sizes.items() if sz == (200, 100)]
    for x in (0.05, 0.40, 0.70):
        assert px(api, prompt, dk, (int(200 * x) + 1, 30)) == (0, 210, 0)           # borda esquerda de cada caixa
    assert px(api, prompt, dk, (int(200 * 0.30) + 3, 30)) != (0, 210, 0)             # entre as caixas, nada
    (ok2,) = [k for k, sz in sizes.items() if sz == (60, 60) and px(api, prompt, k, (12, 12)) == (255, 0, 0)]  # a caixa vermelha da imagem 2
    assert ok2


def test_the_destination_can_be_any_uploaded_image(api):
    o1 = upload(api, "o1.png", png((5, 50, 50), (60, 60)))
    o2 = upload(api, "o2.png", png((15, 50, 50), (60, 60)))
    dest = upload(api, "cena.png", png((90, 90, 90), (200, 100)))
    _, rid = start(api, "coloque os objetos na imagem 3", [o1["id"], o2["id"], dest["id"]], route="transfer_multi")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    green = need["tasks"][0]
    assert green["image_url"].endswith(dest["url"].split("/")[-1])         # as caixas verdes vão na cena, mesmo sendo a 3ª imagem
    assert [r["n"] for r in green["refs"]] == ["1", "2"]                   # o número é o da ordem de envio
    assert green["label"]


def test_one_cutout_per_box_then_each_layer_is_placed_and_rotated(api):
    api.fake.view_rgba = True
    rid = _paste_run(api)
    need = _cutout_need(api, rid, 1)
    _answer(api, rid, need, {"action": "redo", "mode": "pessoa", "boxes": [{"b": [0.0, 0.0, 0.5, 0.5], "mode": "pessoa"},
                                                                          {"b": [0.5, 0.5, 0.5, 0.5], "mode": "objeto"}]})
    need2 = _cutout_need(api, rid, 2)
    assert len(need2["results"]) == 2 and [b["mode"] for b in need2["boxes"]] == ["pessoa", "objeto"]
    assert api.fake.prompts and len(api.fake.prompts) == 3              # a execução inicial + um recorte por caixa
    _answer(api, rid, need2, {"action": "accept"})
    place = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e["kind"] == "draw")
    t = place["tasks"][0]
    assert t["kind"] == "place" and len(t["overlays"]) == 2
    _answer(api, rid, place, {t["key"]: [[0.05, 0.1, 0.3, 0.5, 20], [0.55, 0.2, 0.3, 0.5, -35]]})
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert "error" not in types(api, rid)
    p = list(api.fake.prompts.values())[3]                               # o 1º passo depois dos recortes: a colagem
    w, h = _uploaded_size(api, p, "3")
    print("camadas juntas:", (w, h))
    assert 60 < w <= 200 and 20 < h <= 100                               # as duas camadas viraram uma imagem só, dentro do fundo
    lay = _layer(p)["inputs"]
    assert lay["width"] == w and lay["height"] == h


def _text_encode(prompt):
    return next(n for n in prompt.values() if n["class_type"] == "TextEncodeQwenImage21")["inputs"]


def test_big_phone_photos_are_shrunk_before_anything_else_and_the_user_is_told(api):
    big = upload(api, "celular.png", png((90, 90, 90), (3000, 2000)))          # 6 MP
    small = upload(api, "o.png", png((9, 90, 90), (60, 60)))
    _, rid = start(api, "x", [big["id"], small["id"]], route="transfer_object")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    _answer(api, rid, need, {"green": [0.1, 0.1, 0.3, 0.3], "red": [0.1, 0.1, 0.5, 0.5]})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    sizes = sorted(_uploaded_size(api, prompt, k) for k, n in prompt.items() if n["class_type"] == "LoadImage")
    assert sizes[0] == (60, 60) and sizes[1][0] * sizes[1][1] <= 2_000_000 and abs(sizes[1][0] / sizes[1][1] - 1.5) < 0.01
    assert any("Reduzi imagem grande" in e.get("text", "") for e in api.pipe.runs[rid].events if e["type"] == "status")


def test_multi_transfer_uses_about_one_megapixel_per_reference_not_the_original_size(api):
    dest = upload(api, "cena.png", png((90, 90, 90), (200, 100)))
    o1, o2 = (upload(api, f"o{i}.png", png((10 * i, 50, 50), (60, 60))) for i in (1, 2))
    _, rid = start(api, "coloque os objetos na cena", [dest["id"], o1["id"], o2["id"]], route="transfer_multi")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    refs = [r["key"] for r in need["tasks"][0]["refs"]]
    _answer(api, rid, need, {"green": [{"b": [0.1, 0.1, 0.3, 0.5], "ref": refs[0]}, {"b": [0.5, 0.1, 0.3, 0.5], "ref": refs[1]}],
                             "red_2": "skip", "red_3": "skip"})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert _text_encode(prompt)["resolution"] == 1024


def test_an_object_image_without_a_green_box_is_not_sent_when_it_is_the_last_one(api):
    dest = upload(api, "cena.png", png((90, 90, 90), (200, 100)))
    objs = [upload(api, f"o{i}.png", png((10 * i, 50, 50), (60, 60))) for i in (1, 2, 3)]
    _, rid = start(api, "x", [dest["id"]] + [o["id"] for o in objs], route="transfer_multi")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    refs = [r["key"] for r in need["tasks"][0]["refs"]]
    # só as imagens 2 e 3 recebem caixa; a 4 (a última) fica de fora
    _answer(api, rid, need, {"green": [{"b": [0.1, 0.1, 0.3, 0.5], "ref": refs[0]}, {"b": [0.5, 0.1, 0.3, 0.5], "ref": refs[1]}],
                             "red_2": "skip", "red_3": "skip", "red_4": "skip"})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert sum(1 for n in prompt.values() if n["class_type"] == "LoadImage") == 3
    assert "images.image_4" not in _text_encode(prompt) and "images.image_3" in _text_encode(prompt)
    assert any("não vai ao ComfyUI" in e.get("text", "") for e in api.pipe.runs[rid].events if e["type"] == "status")


def test_a_middle_object_image_without_a_box_stays_so_the_numbering_has_no_hole(api):
    dest = upload(api, "cena.png", png((90, 90, 90), (200, 100)))
    objs = [upload(api, f"o{i}.png", png((10 * i, 50, 50), (60, 60))) for i in (1, 2, 3)]
    _, rid = start(api, "x", [dest["id"]] + [o["id"] for o in objs], route="transfer_multi")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    refs = [r["key"] for r in need["tasks"][0]["refs"]]
    _answer(api, rid, need, {"green": [{"b": [0.1, 0.1, 0.3, 0.5], "ref": refs[0]}, {"b": [0.5, 0.1, 0.3, 0.5], "ref": refs[2]}],
                             "red_2": "skip", "red_3": "skip", "red_4": "skip"})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert sum(1 for n in prompt.values() if n["class_type"] == "LoadImage") == 4


def test_fit_megapixels_keeps_small_images_untouched_and_the_aspect_of_big_ones():
    from pickflow.compose import fit_megapixels
    small = png((1, 2, 3), (100, 50))
    assert fit_megapixels(small, 2.0) == (small, (100, 50))
    data, (w, h) = fit_megapixels(png((1, 2, 3), (4032, 3024)), 2.0)
    assert Image.open(io.BytesIO(data)).size == (w, h) and w * h <= 2_000_000 and abs(w / h - 4 / 3) < 0.01
    big = png((1, 2, 3), (3000, 2000))
    assert fit_megapixels(big, 0) == (big, (3000, 2000))
