"""Conferência do "mover objeto" (o objeto some do lugar de origem), prompts sem LoRA do Clef e completar o corpo na fusão."""
import io

from PIL import Image

from pickflow.router import Ctx, ImageRef, Router, Source, Step
from pickflow.store import Store
from test_e2e import api, png, start, upload, wait_event  # noqa: F401  (fixture e helpers)
from test_splat import Stub, answer


def graphs(c):
    return list(c.fake.prompts.values())


def draw_ev(c, rid):
    return wait_event(c, rid, lambda e: e["type"] == "needs_input" and e.get("kind") == "draw")


def move(api, picks_png, boxes=None):
    a = upload(api, "foto.png", picks_png)
    _cid, rid = start(api, "mova a criança em verde de lugar", [a["id"]], route="object_move@turbo")
    ev = draw_ev(api, rid)
    answer(api, rid, ev, boxes or {"red": [0.1, 0.1, 0.3, 0.4], "green": [0.6, 0.5, 0.3, 0.4]})
    return rid


def test_moved_object_left_behind_gets_a_second_pass_that_removes_it(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"preset": "mover"}))
    src = png((40, 160, 60), (100, 80))
    api.fake.views["out_38.png"] = src                      # o modelo devolveu a imagem igual: o objeto ficou no lugar
    rid = move(api, src)
    done = wait_event(api, rid, lambda e: e["type"] in ("done", "error"), timeout=20)
    assert done["type"] == "done", done
    g = graphs(api)
    assert len(g) == 2                                        # mover + apagar
    assert any("segunda passada" in (e.get("text") or "") for e in api.pipe.runs[rid].events if e["type"] == "status")
    second = g[1]
    assert any(n["class_type"] == "DrawBox" and abs(n["inputs"]["box_x"] - 0.1) < 1e-3 for n in second.values())
    texts = [n["inputs"].get("prompt") or "" for n in second.values()]
    assert any("Remove the following from the image, inside the red square" in t and "leftover duplicate" in t for t in texts)
    up = api.fake.uploads[next(n["inputs"]["image"] for n in second.values() if n["class_type"] == "LoadImage").split("/")[-1]]
    assert Image.open(io.BytesIO(up)).size == (100, 80)       # a 2ª passada parte do resultado da 1ª


def test_a_move_that_really_cleared_the_origin_runs_once(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"preset": "mover"}))
    rid = move(api, png((40, 160, 60), (100, 80)))            # o /view de mentira devolve outra imagem: a caixa mudou
    assert wait_event(api, rid, lambda e: e["type"] in ("done", "error"), timeout=20)["type"] == "done"
    assert len(graphs(api)) == 1


# ------------------------------------------------------------------- LoRAs do Clef
def img(i):
    return ImageRef(f"i{i}", f"i{i}.png", b"x", 10, 10)


def test_style_prompt_never_gets_a_body_swap_lora_from_the_clef(project):
    srcs = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    ctx = Ctx("Faça o coringa da imagem 1 no mesmo estilo do batman da imagem 2", [img(1), img(2)])
    stub = Stub({"preset": "estilo", "lora_bfs_body": "usar", "lora_bfs_head": "usar", "mode": "turbo"})
    b = __import__("asyncio").run(Router(Store(project), stub).bind(ctx, Step("edit", mode="turbo"), srcs))
    assert b.preset_id == "estilo" and b.loras == [] and not any(d["question"].startswith("lora_") for d in b.decisions)
    free = Stub({"preset": "livre", "lora_bfs_body": "usar", "mode": "turbo"})
    b2 = __import__("asyncio").run(Router(Store(project), free).bind(ctx, Step("edit", mode="turbo"), srcs))
    assert b2.preset_id == "livre" and b2.loras == ["bfs_body"]        # o prompt livre continua aceitando


# ------------------------------------------------------------------- fusão: completar o corpo
def _fuse_prompt(api, corpo, request="Make the joker from image 2 sitting at the chair of image 1. He holds a beer in his hands."):
    import test_e2e
    api.fake.view_rgba = True
    bg = upload(api, "fundo.png", png((20, 30, 40), (200, 100)))
    person = upload(api, "pessoa.png", png((9, 9, 9), (100, 80)))
    _, rid = start(api, request, [bg["id"], person["id"]], route="recipe__colar_e_fundir")
    test_e2e._answer(api, rid, test_e2e._cutout_need(api, rid), {"action": "accept"})
    place = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e["kind"] == "draw")
    test_e2e._answer(api, rid, place, {"lugar": [0.1, 0.2, 0.5, 0.6]})
    wait_event(api, rid, lambda e: e["type"] == "done")
    fuse = list(api.fake.prompts.values())[2]
    return next(n["inputs"]["prompt"] for n in fuse.values() if n["class_type"] == "TextEncodeQwenImage21"), rid


def test_incomplete_person_is_completed_in_the_fusion_with_the_request(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"role_background": "user_1", "role_subject": "user_2", "var_corpo": "completar"}))
    text, rid = _fuse_prompt(api, "completar")
    assert text.startswith("pysj666") and "complete the whole person" in text
    assert text.endswith("sitting at the chair of image 1. He holds a beer in his hands.")      # o pedido diz a pose e a ação
    fuse = list(api.fake.prompts.values())[2]
    assert "13" not in fuse                                                               # Consistency desligada: deixa trocar quem estava lá
    lora = next(n for n in fuse.values() if n["class_type"] == "LoraLoader" and "pysj" not in n["inputs"]["lora_name"].lower()
                and n["inputs"]["strength_model"] == 0.5)
    assert lora["inputs"]["strength_clip"] == 0.5                                         # pysj666 em 0,5
    assert any(d.get("question") == "var_corpo" and d["chosen"] == "completar" for e in api.pipe.runs[rid].events
               if e["type"] == "decision" for d in [e])


def test_the_clef_is_asked_about_the_body_looking_at_the_collage(api, monkeypatch):
    seen = {}

    class Spy(Stub):
        async def decide(self, state, questions, images=None):
            if "var_corpo" in questions:
                seen["q"], seen["n"], seen["labels"] = questions["var_corpo"], len(images or []), [s["label"] for s in state.get("sources", [])]
            return await super().decide(state, questions, images)

    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Spy({"role_background": "user_1", "role_subject": "user_2"}))
    _fuse_prompt(api, "inteiro")
    assert seen["n"] == 3 and seen["labels"][-1] == "Resultado do passo anterior"          # as 2 enviadas + a colagem
    assert "LAST image" in seen["q"]["instructions"] and set(seen["q"]["criteria"]) == {"inteiro", "completar"}


# ------------------------------------------------------------------- recorte cortado pela foto
def _rgba(box, size=(100, 80)):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (box[2], box[3]), (200, 50, 50, 255)), (box[0], box[1]))
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()


def test_cut_edges_sees_a_bust_but_not_a_whole_object():
    from pickflow.storage import cut_edges
    assert cut_edges(_rgba((20, 30, 60, 47))) == ["bottom"]                # o SAM3 deixa 3 px de folga: ainda conta
    assert cut_edges(_rgba((20, 10, 60, 50))) == []                        # inteiro, com margem
    assert cut_edges(_rgba((0, 10, 30, 70))) == ["bottom", "left"]
    assert cut_edges(_rgba((45, 40, 2, 40))) == []                         # perninha fina na borda não basta


def test_a_cut_off_person_makes_the_fusion_complete_the_body_even_if_the_clef_says_whole(api, monkeypatch):
    import test_e2e
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"role_background": "user_1", "role_subject": "user_2", "var_corpo": "inteiro"}))
    api.fake.view_rgba = True
    bust = _rgba((20, 20, 60, 60))                                         # encosta embaixo: um busto
    api.fake.views["out_14.png"] = bust
    api.fake.views["out_15.png"] = bust
    bg = upload(api, "fundo.png", png((20, 30, 40), (200, 100)))
    person = upload(api, "pessoa.png", png((9, 9, 9), (100, 80)))
    _, rid = start(api, "coloque ele sentado na cadeira", [bg["id"], person["id"]], route="recipe__colar_e_fundir")
    test_e2e._answer(api, rid, test_e2e._cutout_need(api, rid), {"action": "accept"})
    place = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e["kind"] == "draw")
    test_e2e._answer(api, rid, place, {"lugar": [0.1, 0.2, 0.4, 0.6]})
    wait_event(api, rid, lambda e: e["type"] == "done")
    fuse = list(api.fake.prompts.values())[2]
    text = next(n["inputs"]["prompt"] for n in fuse.values() if n["class_type"] == "TextEncodeQwenImage21")
    assert "complete the whole person" in text and "coloque ele sentado na cadeira" in text
    assert any("borda inferior" in (e.get("text") or "") for e in api.pipe.runs[rid].events if e["type"] == "status")


def test_transparent_inputs_reach_comfyui_on_white_not_black(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"preset": "livre"}))
    a = upload(api, "personagem.png", _rgba((20, 10, 40, 40)))           # fundo transparente
    _, rid = start(api, "deixe mais quente", [a["id"]], route="edit@turbo")
    assert wait_event(api, rid, lambda e: e["type"] in ("done", "error"))["type"] == "done"
    g = graphs(api)[-1]
    up = Image.open(io.BytesIO(api.fake.uploads[g["26"]["inputs"]["image"].split("/")[-1]]))
    assert up.mode == "RGB" and up.getpixel((2, 2)) == (255, 255, 255) and up.getpixel((30, 20)) == (200, 50, 50)
