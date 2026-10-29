"""Trocar pessoa e trocar roupa (AusBoss) como fluxos padrão, e o editor antigo como reserva só nos casos de exceção."""
import asyncio
import io
import json

import pytest
from PIL import Image

from pickflow import pvars
from pickflow.clef import Answer
from pickflow.router import Ctx, ImageRef, Router, Source, Step
from pickflow.store import Store
from test_e2e import api, png, start, upload, wait_event  # noqa: F401  (fixture e helpers)


def run(coro):
    return asyncio.run(coro)


def img(i):
    return ImageRef(f"i{i}", f"foto{i}.png", b"x", 100, 100)


class Stub:
    """Clef falso: cada pergunta tem a resposta escolhida em `picks` (a primeira opção se faltar) com 0,9 de confiança."""
    mode = "stub"

    def __init__(self, picks=None):
        self.picks, self.calls = picks or {}, []

    async def decide(self, state, questions, images=None):
        self.calls.append(questions)
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q, keys[0])
            out[q] = Answer(pick, 0.9, {k: (0.9 if k == pick else 0.1 / max(1, len(keys) - 1)) for k in keys})
        return out


class Probs(Stub):
    """Clef falso com as probabilidades exatas de uma pergunta."""

    def __init__(self, probs, picks=None):
        super().__init__(picks)
        self.probs = probs

    async def decide(self, state, questions, images=None):
        out = await super().decide(state, questions, images)
        for q, p in self.probs.items():
            if q in questions:
                best = max(p, key=p.get)
                out[q] = Answer(best, p[best], dict(p))
        return out


def plan(project, st, text="troque a pessoa da imagem 1 pela da imagem 2", n=2):
    return run(Router(Store(project), st).plan(Ctx(text, [img(i) for i in range(1, n + 1)])))


def gate_calls(st):
    return [c for c in st.calls if "engine" in c]


# ------------------------------------------------------------------ registro
def test_new_flows_are_registered_with_a_classic_fallback(project):
    s = Store(project)
    for wid, fb_prompt in (("troca_pessoa", "trocar_pessoas"), ("troca_roupa", "trocar_roupa")):
        m = s.manifest(wid)
        assert m["fallback"]["workflow"] == "edit" and m["fallback"]["prompt"] == fb_prompt
        assert fb_prompt in {p["id"] for p in s.prompts("edit")["presets"]}          # a reserva existe de verdade
        assert list(m["modes"]) == ["turbo", "quality"] and not m.get("internal")
    assert {"face_busy", "partial_body", "body_only", "several"} == set(s.manifest("troca_pessoa")["fallback"]["options"])
    assert set(s.manifest("troca_roupa")["fallback"]["options"]) == {"tattoos"}


def test_who_to_replace_and_whole_body_variables_become_the_prompt_and_the_graph_settings(project):
    s = Store(project)
    preset = s.prompts("troca_pessoa")["presets"][0]
    assert [v["key"] for v in preset["vars"]] == ["corpo", "quem"]
    r = pvars.render(preset, {"corpo": "head", "quem": "woman_left"})
    assert r["text"] == "the woman on the left" and r["settings"] == {"whole_body": False}
    assert pvars.render(preset, {"corpo": "both", "quem": "person"})["settings"] == {"whole_body": True}
    outfit = s.prompts("troca_roupa")["presets"][0]
    assert pvars.render(outfit, {"quem": "man_right"})["text"] == "the man on the right"


# ------------------------------------------------------------------ a porta de exceção
def test_the_new_flow_stays_when_the_gate_says_normal(project):
    st = Stub({"route": "troca_pessoa", "then": "none"})
    p = plan(project, st)
    assert [s.workflow for s in p.steps] == ["troca_pessoa"] and p.steps[0].prompt is None
    q = gate_calls(st)[0]["engine"]
    assert list(q["criteria"]) == ["main", "face_busy", "partial_body", "body_only", "several"]       # "main" primeiro
    assert any(d["question"] == "engine" and d["chosen"] == "main" for d in p.decisions)


@pytest.mark.parametrize("reason", ["face_busy", "partial_body", "body_only", "several"])
def test_each_exception_sends_the_person_swap_to_the_classic_editor_with_its_prompt(project, reason):
    st = Stub({"route": "troca_pessoa", "then": "none", "engine": reason})
    p = plan(project, st, "swap her for the person of image 2")
    assert [s.workflow for s in p.steps] == ["edit"] and p.steps[0].prompt == "trocar_pessoas"
    qs = {d["question"] for d in p.decisions}
    assert {"engine", "route_fallback"} <= qs
    assert next(d for d in p.decisions if d["question"] == "route_fallback")["chosen"] == "edit"


def test_the_gate_favors_the_new_flow_and_the_exception_must_win_clearly(project):
    # sem peso, a exceção (0,50) ganharia da padrão (0,40); com o peso 1,5 a padrão vale 0,60 e fica
    close = Probs({"engine": {"main": 0.40, "face_busy": 0.50, "partial_body": 0.05, "body_only": 0.03, "several": 0.02}},
                  {"route": "troca_pessoa", "then": "none"})
    assert [s.workflow for s in plan(project, close).steps] == ["troca_pessoa"]
    clear = Probs({"engine": {"main": 0.10, "face_busy": 0.80, "partial_body": 0.05, "body_only": 0.03, "several": 0.02}},
                  {"route": "troca_pessoa", "then": "none"})
    assert [s.workflow for s in plan(project, clear).steps] == ["edit"]
    # o limite mínimo de confiança da exceção vem dos ajustes
    s = Store(project)
    s.save_settings({"clef": {"engine_min": 0.95}})
    assert [x.workflow for x in run(Router(s, clear).plan(Ctx("x", [img(1), img(2)]))).steps] == ["troca_pessoa"]
    s.save_settings({"clef": {"engine_min": 0.5, "engine_main_weight": 1.0}})                 # sem o peso a favor, a exceção de 0,50 vence
    assert [x.workflow for x in run(Router(s, close).plan(Ctx("x", [img(1), img(2)]))).steps] == ["edit"]


def test_the_gate_is_skipped_for_a_forced_route_a_disabled_fallback_and_other_workflows(project):
    st = Stub()
    p = run(Router(Store(project), st).plan(Ctx("x", [img(1), img(2)]), forced="troca_pessoa"))
    assert [s.workflow for s in p.steps] == ["troca_pessoa"] and not gate_calls(st)       # a escolha do usuário vale
    st = Stub({"route": "edit", "then": "none"})
    plan(project, st, "deixe mais quente")
    assert not gate_calls(st)                                                              # só quem declara `fallback` pergunta
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["edit"]["enabled"] = False
    s.save_clef_config(cfg)
    st = Stub({"route": "troca_pessoa", "then": "none", "engine": "face_busy"})
    p = run(Router(s, st).plan(Ctx("x", [img(1), img(2)])))
    assert [x.workflow for x in p.steps] == ["troca_pessoa"] and not gate_calls(st)       # sem a reserva ligada, não há para onde ir


def test_a_clef_that_fails_on_the_gate_keeps_the_new_flow(project):
    class Broken(Stub):
        async def decide(self, state, questions, images=None):
            if "engine" in questions:
                from pickflow.clef import ClefError
                raise ClefError("fora do ar")
            return await super().decide(state, questions, images)

    p = plan(project, Broken({"route": "troca_pessoa", "then": "none"}))
    assert [s.workflow for s in p.steps] == ["troca_pessoa"]


def test_outfit_swap_goes_to_the_new_flow_and_tattoos_to_the_classic_prompt(project):
    st = Stub({"route": "troca_roupa", "then": "none"})
    p = plan(project, st, "vista a pessoa da imagem 1 com a roupa da imagem 2")
    assert [s.workflow for s in p.steps] == ["troca_roupa"] and list(gate_calls(st)[0]["engine"]["criteria"]) == ["main", "tattoos"]
    st = Stub({"route": "troca_roupa", "then": "none", "engine": "tattoos"})
    p = plan(project, st, "passe as tatuagens da imagem 2 para a pessoa da imagem 1")
    assert [s.workflow for s in p.steps] == ["edit"] and p.steps[0].prompt == "trocar_roupa"


# ------------------------------------------------------------------ o que vai para o ComfyUI
def graphs(c):
    return list(c.fake.prompts.values())


def finish(c, rid):
    ev = wait_event(c, rid, lambda e: e["type"] in ("done", "error"), timeout=20)
    assert ev["type"] == "done", ev
    return ev


def test_person_swap_sends_who_whole_body_and_both_pictures(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"var_corpo": "head", "var_quem": "woman_left"}))
    a = upload(api, "cena.png", png((10, 200, 30), (80, 120)))
    b = upload(api, "ref.png", png((200, 10, 10), (40, 40)))
    _cid, rid = start(api, "troque a mulher da esquerda pela da imagem 2, só a cabeça", [a["id"], b["id"]], route="troca_pessoa@turbo")
    done = finish(api, rid)
    g = graphs(api)[-1]
    assert g["4"]["inputs"]["text"] == "the woman on the left" and g["5"]["inputs"]["value"] is False
    assert g["53"]["inputs"]["value"] == 6
    up = lambda n: Image.open(io.BytesIO(api.fake.uploads[g[n]["inputs"]["image"].split("/")[-1]])).getpixel((1, 1))[:3]  # noqa: E731
    assert up("2") == (10, 200, 30) and up("3") == (200, 10, 10)                     # cena = imagem 1, personagem = imagem 2
    rows = json.loads(g["10"]["inputs"]["loras"])
    assert [r["name"] for r in rows if r["enabled"]] == ["Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors",
                                                         "bfs_body_swap_v1.0_qwen_2.1.safetensors"]
    assert len(done["images"]) == 1 and not [n for n in g.values() if n["class_type"] in ("AUSBOSS_NODES_Compare", "AUSBOSS_NODES_ShowText")]


def test_person_swap_quality_turns_the_turbo_lora_off_and_uses_25_steps(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub())
    a, b = upload(api, "cena.png"), upload(api, "ref.png")
    _cid, rid = start(api, "troque a pessoa, com máxima qualidade", [a["id"], b["id"]], route="troca_pessoa@quality")
    finish(api, rid)
    g = graphs(api)[-1]
    assert g["53"]["inputs"]["value"] == 25 and g["5"]["inputs"]["value"] is True
    for nid, lora in (("10", "bfs_body_swap_v1.0_qwen_2.1.safetensors"), ("11", "bfs_head_v1.1_qwen_2.1.safetensors")):
        rows = json.loads(g[nid]["inputs"]["loras"])
        assert [r["name"] for r in rows if r["enabled"]] == [lora]


def test_outfit_swap_sends_the_person_the_outfit_and_the_mode_settings(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"var_quem": "man"}))
    a = upload(api, "pessoa.png", png((10, 200, 30), (80, 120)))
    b = upload(api, "roupa.png", png((200, 10, 10), (40, 40)))
    _cid, rid = start(api, "vista o homem com a roupa da imagem 2", [a["id"], b["id"]], route="troca_roupa@turbo")
    finish(api, rid)
    g = graphs(api)[-1]
    up = lambda n: Image.open(io.BytesIO(api.fake.uploads[g[n]["inputs"]["image"].split("/")[-1]])).getpixel((1, 1))[:3]  # noqa: E731
    assert g["4"]["inputs"]["text"] == "the man" and up("2") == (10, 200, 30) and up("3") == (200, 10, 10)
    assert g["71"]["inputs"]["value"] == 8 and g["3"]["inputs"]["crop_width"] == 0          # foto inteira, sem recorte
    assert [r["name"] for r in json.loads(g["70"]["inputs"]["loras"]) if r["enabled"]][0] == "qwen-image-2.1-outfit-swap.safetensors"
    _cid, rid = start(api, "vista a pessoa, com máxima qualidade", [a["id"], b["id"]], route="troca_roupa@quality")
    finish(api, rid)
    q = graphs(api)[-1]
    assert q["71"]["inputs"]["value"] == 25 and [r["name"] for r in json.loads(q["70"]["inputs"]["loras"]) if r["enabled"]] == [
        "qwen-image-2.1-outfit-swap.safetensors"]


def test_gate_exception_runs_the_classic_prompt_through_the_whole_pipeline(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"route": "troca_pessoa", "then": "none", "engine": "face_busy",
                                                                       "var_scope": "head"}))
    a, b = upload(api, "cena.png"), upload(api, "ref.png")
    _cid, rid = start(api, "troque o rosto de quem bebe a lata pelo da imagem 2", [a["id"], b["id"]])
    done = finish(api, rid)
    evs = api.pipe.runs[rid].events
    assert [s["workflow"] for s in next(e for e in evs if e["type"] == "plan")["steps"]] == ["edit"]
    assert next(e for e in evs if e["type"] == "step_start")["preset"] == "trocar_pessoas"
    assert any(e["type"] == "decision" and e["question"] == "route_fallback" for e in evs) and done["images"]


