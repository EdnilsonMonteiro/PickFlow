"""Mudar o ângulo com 3D (TripoSplat -> escolher na tela -> AnyAngle), biblioteca 3D, Outpaint e casos de uso padrão novos."""
import base64
import io

import pytest
from PIL import Image

from pickflow.clef import Answer
from pickflow.compose import pad_gray, paste_back
from pickflow.store import Store
from test_e2e import api, png, start, upload, wait_event  # noqa: F401  (fixture e helpers)


class Stub:
    mode = "stub"

    def __init__(self, picks=None):
        self.picks = picks or {}

    async def decide(self, state, questions, images=None):
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q, keys[0])
            out[q] = Answer(pick, 0.9, {k: (0.9 if k == pick else 0.1 / max(1, len(keys) - 1)) for k in keys})
        return out


def data_url(size=(64, 96), color=(120, 120, 120)):
    return "data:image/png;base64," + base64.b64encode(png(color, size)).decode()


def answer(c, rid, ev, value):
    r = c.post(f"/api/runs/{rid}/input", json={"input_id": ev["input_id"], "value": value})
    assert r.status_code == 200, r.text


def splat_ev(c, rid):
    return wait_event(c, rid, lambda e: e["type"] == "needs_input" and e.get("kind") == "splat")


def graphs(c):
    return list(c.fake.prompts.values())


def test_room_shows_3d_and_render_is_the_result(api):
    a = upload(api, "quarto.png", png(size=(80, 120)))
    _cid, rid = start(api, "quero ver o quarto de outro ângulo", [a["id"]], route="ambiente_3d")
    ev = splat_ev(api, rid)
    sent = graphs(api)[-1]
    assert sent["88:35"]["inputs"]["switch"] is False                       # ambiente: a foto inteira, sem recortar
    assert any(n["class_type"] == "SaveGLB" for n in sent.values())
    assert not any(n["class_type"] in ("KSampler",) and "88:" not in k for k, n in sent.items())
    assert ev["mode"] == "ambiente" and ev["then_title"] == "Editar imagem"
    assert abs(ev["aspect"] - 80 / 120) < 1e-3 and ev["size"][0] % 16 == 0 and ev["size"][0] < ev["size"][1]
    body = api.get(ev["splat"]["url"])
    assert body.status_code == 200 and body.content.startswith(b"SPZ-FAKE:")
    answer(api, rid, ev, {"action": "render", "image": data_url(), "view": {"yaw": 20, "pitch": 5}})
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    im = done["images"][0]
    assert im["splat"] == ev["splat"]["id"] and im["view"]["yaw"] == 20 and not im["then"]
    assert Image.open(io.BytesIO(api.get(im["url"]).content)).size == (64, 96)
    assert len(graphs(api)) == 1                                            # só o 3D rodou


def test_same_photo_reuses_the_3d_and_library_saves_it(api):
    data = png(size=(90, 60))
    for i in range(2):
        a = upload(api, f"q{i}.png", data)
        _cid, rid = start(api, "mudar o ângulo da sala", [a["id"]], route="ambiente_3d")
        ev = splat_ev(api, rid)
        answer(api, rid, ev, {"action": "render", "image": data_url((96, 64))})
        wait_event(api, rid, lambda e: e["type"] == "done")
    assert len(graphs(api)) == 1                                            # a 2ª vez não roda o TripoSplat
    assert any("uso o mesmo" in (e.get("text") or "") for e in api.pipe.runs[rid].events)
    assert api.get("/api/splats").json() == []                             # nada guardado ainda
    sid = ev["splat"]["id"]
    r = api.put(f"/api/splats/{sid}", json={"name": "Sala", "saved": True}).json()
    assert r["saved"] and r["name"] == "Sala" and r["source"]["url"].startswith("/files/")
    assert [x["id"] for x in api.get("/api/splats").json()] == [sid]
    assert api.delete(f"/api/splats/{sid}").json()["ok"]
    assert api.get(f"/splats/{sid}.spz").status_code == 404
    assert api.put("/api/splats/../../x", json={}).status_code in (404, 405)


def test_room_final_fills_the_gray_of_the_render_with_outpaint(api):
    # ambiente: o render do ângulo novo tem cinza onde a foto não mostrava nada; "Completar o cinza" manda ele para o
    # Editar com o prompt de Outpaint (só completar o cinza, sem aumentar a tela) e só ele entra no grafo
    a = upload(api, "quarto.png", png((10, 200, 30), (80, 120)))
    _cid, rid = start(api, "ver o quarto de cima", [a["id"]], route="ambiente_3d")
    ev = splat_ev(api, rid)
    answer(api, rid, ev, {"action": "final", "image": data_url((64, 96), (128, 128, 128))})
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    plans = [e for e in api.pipe.runs[rid].events if e["type"] == "plan"]
    assert [s["workflow"] for s in plans[-1]["steps"]] == ["ambiente_3d", "edit"]
    step = next(e for e in api.pipe.runs[rid].events if e["type"] == "step_start" and e["workflow"] == "edit")
    assert step["preset"] == "expandir" and step["vars"].get("expand") == "gray"
    g = graphs(api)[-1]
    assert g["6"]["inputs"]["value"].startswith("Outpaint the image: replace the solid gray areas")
    # completar o cinza do render TAMBÉM pede mais detalhe (o render do 3D é macio) e redesenha em resolução maior:
    # a instrução da LoRA sozinha ("deixar a imagem como está") congelava a qualidade do render
    assert "redraw the whole image with more detail" in g["6"]["inputs"]["value"]
    assert g["18"]["inputs"]["resolution"] == 1536
    render = api.fake.uploads[g["26"]["inputs"]["image"].split("/")[-1]]
    assert Image.open(io.BytesIO(render)).size == (64, 96)                          # sem cinza a mais em volta
    assert Image.open(io.BytesIO(render)).getpixel((1, 1))[:3] == (128, 128, 128)   # o render do 3D é a image1
    assert "27" not in g                                                            # a foto original não entra
    loras = [n["inputs"]["lora_name"] for n in g.values() if n["class_type"] == "LoraLoader"]
    assert any("outpaint" in x for x in loras) and len(done["images"]) == 1


def test_person_recipe_final_runs_anyangle_once(api):
    a = upload(api, "pessoa.png", png((200, 10, 10), (60, 120)))
    _cid, rid = start(api, "mostre ela de lado", [a["id"]], route="recipe__pessoa_angulo")
    ev = splat_ev(api, rid)
    answer(api, rid, ev, {"action": "final", "image": data_url()})
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert len([e for e in api.pipe.runs[rid].events if e["type"] == "plan"]) == 1           # o plano já tinha a etapa
    assert len(graphs(api)) == 2 and "15" in graphs(api)[-1]


def test_cancelled_3d_choice_cancels_the_run(api):
    a = upload(api, "quarto.png")
    _cid, rid = start(api, "outro ângulo", [a["id"]], route="ambiente_3d")
    ev = splat_ev(api, rid)
    api.post(f"/api/runs/{rid}/cancel")
    assert "cancelada" in wait_event(api, rid, lambda e: e["type"] == "error")["message"]
    assert ev["input_id"]


# --------------------------------------------------------------------- outpaint
def test_pad_gray_and_paste_back_keep_the_original():
    src = png((250, 0, 0), (300, 200))
    padded, box = pad_gray(src, {"left": 0.25, "right": 0.25})
    im = Image.open(io.BytesIO(padded))
    assert im.size[0] % 32 == 0 and im.size[1] % 32 == 0 and im.size[0] >= 450
    assert im.getpixel((2, 100)) == (128, 128, 128) and im.getpixel((box[0] + 150, box[1] + 100)) == (250, 0, 0)
    blue = io.BytesIO()
    Image.new("RGB", (im.size[0] // 2, im.size[1] // 2), (0, 0, 255)).save(blue, "PNG")       # o modelo devolve menor
    out = Image.open(io.BytesIO(paste_back(blue.getvalue(), src, box, im.size)))
    assert out.size == im.size and out.getpixel((2, 100)) == (0, 0, 255)
    assert out.getpixel((box[0] + 150, box[1] + 100)) == (250, 0, 0)


def test_expand_preset_pads_the_photo_and_pastes_it_back(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"preset": "expandir", "var_expand": "sides"}))
    a = upload(api, "praia.png", png((0, 90, 200), (128, 96)))
    _cid, rid = start(api, "aumente a foto para os lados", [a["id"]], route="edit@turbo")
    ev = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e.get("kind") == "outpaint")
    assert ev["size"] == [128, 96] and ev["expand"] == {"left": 0.25, "right": 0.25}      # a sugestão do Clef vem pré-preenchida
    answer(api, rid, ev, {"left": 0.25, "right": 0.25})                                    # a pessoa confirma
    done = wait_event(api, rid, lambda e: e["type"] in ("done", "error"))
    assert done["type"] == "done", done
    g = graphs(api)[-1]
    sent = Image.open(io.BytesIO(api.fake.uploads[g["26"]["inputs"]["image"].split("/")[-1]]))
    assert sent.size == (192, 96) and sent.getpixel((2, 48))[:3] == (128, 128, 128)
    assert g["18"]["inputs"]["resolution"] == 0
    loras = [n["inputs"]["lora_name"] for n in g.values() if n["class_type"] == "LoraLoader"]
    assert "qwen-image-2.1-outpaint-v2.safetensors" in loras
    assert any(x.get("prompt", "").startswith("Outpaint the image: replace the solid gray areas")
               for x in api.pipe.runs[rid].events if x["type"] == "step_start")
    out = Image.open(io.BytesIO(api.get(done["images"][0]["url"]).content))
    assert out.size == (192, 96) and out.getpixel((96, 48))[:3] == (0, 90, 200)               # o original volta igual


def test_angle_then_face_swap_with_reference(api, monkeypatch):
    stub = Stub({"role_person": "user_1", "role_reference": "user_2", "var_corpo": "head"})
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: stub)
    a = upload(api, "pessoa.png", png((200, 10, 10), (60, 120)))
    b = upload(api, "rosto.png", png((10, 10, 200), (40, 40)))
    _cid, rid = start(api, "mostre de lado e use o rosto da imagem 2", [a["id"], b["id"]], route="recipe__pessoa_angulo_troca")
    ev = splat_ev(api, rid)
    assert graphs(api)[-1]["88:35"]["inputs"]["switch"] is True
    answer(api, rid, ev, {"action": "final", "image": data_url((64, 128), (128, 128, 128))})
    done = wait_event(api, rid, lambda e: e["type"] in ("done", "error"), timeout=20)
    assert done["type"] == "done", done
    g = graphs(api)
    assert len(g) == 3 and "15" in g[1]                                     # 3D, AnyAngle e a troca
    orig = api.fake.uploads[g[1]["6"]["inputs"]["image"].split("/")[-1]]
    assert Image.open(io.BytesIO(orig)).getpixel((1, 1))[:3] == (200, 10, 10)       # AnyAngle recebe a foto da pessoa
    swap = g[2]                                                                     # o fluxo de trocar pessoa (AusBoss)
    ref = api.fake.uploads[swap["3"]["inputs"]["image"].split("/")[-1]]
    assert Image.open(io.BytesIO(ref)).getpixel((1, 1))[:3] == (10, 10, 200)        # o personagem é a referência
    assert swap["5"]["inputs"]["value"] is False and swap["4"]["inputs"]["text"] == "the person"     # só a cabeça


def test_cutout_mode_switch_keeps_the_sam3_text_consistent(api):
    from pickflow.router import Binding, Step
    pipe = api.pipe
    b = Binding("padrao", {"image_1": "user_1"}, mode="objeto", vars={"alvo": "chair", "cor": "blue"})
    st = Step("colar_recortar")
    assert pipe._vars_for_mode(st, b, "pessoa")["alvo"] == "person"
    assert pipe._vars_for_mode(st, b, "objeto")["alvo"] == "chair"              # já era um objeto: mantém
    assert pipe._vars_for_mode(st, b, "fundo")["alvo"] == "alone"
    p = Binding("padrao", {"image_1": "user_1"}, mode="pessoa", vars={"alvo": "person", "cor": "none"})
    assert pipe._vars_for_mode(st, p, "objeto")["alvo"] == "other"


def test_gray_fraction_measures_the_leftover_outpaint_gray():
    from pickflow.compose import gray_fraction
    half = Image.new("RGB", (64, 64), (128, 128, 128))
    half.paste(Image.new("RGB", (64, 32), (10, 200, 30)), (0, 0))
    buf = io.BytesIO(); half.save(buf, "PNG")
    assert abs(gray_fraction(buf.getvalue()) - 0.5) < 0.02
    buf2 = io.BytesIO(); Image.new("RGB", (64, 64), (10, 200, 30)).save(buf2, "PNG")
    assert gray_fraction(buf2.getvalue()) == 0


def test_outpaint_screen_lets_you_pick_the_gray_yourself(api, monkeypatch):
    # opção "Escolher na tela": sem sugestão do Clef, a pessoa acrescenta o cinza (aqui só em cima e embaixo)
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"preset": "expandir", "var_expand": "choose"}))
    a = upload(api, "praia.png", png((0, 90, 200), (128, 96)))
    _cid, rid = start(api, "só outpaint", [a["id"]], route="edit@turbo")
    ev = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e.get("kind") == "outpaint")
    assert ev["expand"] == {}
    answer(api, rid, ev, {"top": 0.5, "bottom": 0.5, "left": 0})
    assert wait_event(api, rid, lambda e: e["type"] in ("done", "error"))["type"] == "done"
    g = graphs(api)[-1]
    sent = Image.open(io.BytesIO(api.fake.uploads[g["26"]["inputs"]["image"].split("/")[-1]]))
    assert sent.size == (128, 192) and sent.getpixel((64, 4))[:3] == (128, 128, 128) and sent.getpixel((64, 96))[:3] == (0, 90, 200)


