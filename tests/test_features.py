"""Variáveis de prompt (n pessoas, corpo/cabeça, ângulo), conflitos entre LoRAs, link do Hugging Face,
teste do ComfyUI e nomes dos modos."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from pickflow import hf, pvars
from pickflow import loras as L
from pickflow.app import create_app
from pickflow.clef import Answer
from pickflow.router import Ctx, ImageRef, Router, Source, Step
from pickflow.store import Store
from test_e2e import png, start, upload, wait_event


def run(coro):
    return asyncio.run(coro)


def img(i):
    return ImageRef(f"i{i}", f"img{i}.png", b"x", 10, 10)


def src(n):
    return [Source(f"user_{i}", f"Imagem {i}", b"x") for i in range(1, n + 1)]


class Stub:
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


@pytest.fixture()
def swap(project):
    return next(p for p in Store(project).prompts("edit")["presets"] if p["id"] == "trocar_pessoas")


# ---------------------------------------------------------------- um prompt só para n pessoas
def three_text(swap):
    return pvars.render(swap, {"people": "3", "scope": "both", "place": "none"})["text"]


def test_one_person_uses_only_the_lora_prompts_and_head_strength_is_one(swap):
    ids = lambda p: [(x if isinstance(x, str) else x["id"], None if isinstance(x, str) else x.get("strength"))   # noqa: E731
                     for x in p["loras"]]
    assert ids(pvars.render(swap, {"people": "1", "scope": "both"})) == [("bfs_body", None), ("bfs_head", 1)]
    two = pvars.render(swap, {"people": "2", "scope": "both"})
    assert two["text"].startswith("{loras}") and "Replicate the exact pose" in two["text"]
    assert "{people_many}" not in two["text"] and "{people_many}" not in pvars.render(swap, {})["text"]


def test_one_prompt_swaps_n_people_from_left_to_right(swap):
    one = pvars.render(swap, {"people": "1", "scope": "both", "place": "none"})
    assert one["needs"] == 2
    assert "Replace the" not in one["text"] and "Replicate the exact pose" not in one["text"]       # 1 pessoa: só as LoRAs
    assert "Replicate the exact pose" in three_text(swap)
    three = pvars.render(swap, {"people": "3", "scope": "both", "place": "none"})
    t = three["text"]
    assert "first 3 people of <image1>, counting from LEFT to RIGHT" in t
    assert "1st person from the left becomes the person in <image2>" in t
    assert "3rd person from the left becomes the person in <image4>" in t and "<image5>" not in t
    assert three["needs"] == 4                                   # a cena + 3 referências
    assert pvars.render(swap, {"people": "7"})["needs"] == 8


def test_scope_states_the_user_decision_in_the_final_prompt(swap):
    both = pvars.render(swap, {"people": "1", "scope": "both"})
    body = pvars.render(swap, {"people": "2", "scope": "body"})
    head = pvars.render(swap, {"people": "2", "scope": "head"})
    both2 = pvars.render(swap, {"people": "2", "scope": "both"})
    assert "swap BOTH body and head" in both2["text"] and "swap ONLY the body, NOT the head" in body["text"]
    assert "swap ONLY the head, NOT the body" in head["text"]
    assert "swap" not in both["text"].replace("{loras}", "")                              # 1 pessoa: sem texto extra
    ids = lambda p: [x if isinstance(x, str) else x["id"] for x in p["loras"]]          # noqa: E731
    assert ids(both) == ["bfs_body", "bfs_head"] and ids(body) == ["bfs_body"] and ids(head) == ["bfs_head"]
    assert pvars.render(swap, {"people": "2"})["text"].count("swap BOTH") == 1             # padrão: corpo e cabeça


def test_clef_is_asked_how_many_people_and_which_scope(project):
    st = Stub({"var_people": "3", "var_scope": "head", "preset": "trocar_pessoas"})
    b = run(Router(Store(project), st).bind(Ctx("troque as três pessoas", [img(i) for i in range(1, 5)]), Step("edit"), src(4)))
    assert b.vars["people"] == "3" and b.vars["scope"] == "head"
    asked = {q for c in st.calls for q in c}
    assert {"var_people", "var_scope", "var_place"} <= asked
    assert list(next(c for c in st.calls if "var_people" in c)["var_people"]["criteria"]) == ["1", "2", "3"]      # 4 imagens: até 3 pessoas
    # as imagens entram na ordem de envio, sem o Clef decidir entrada por entrada
    assert [b.slots[f"image_{i}"] for i in range(1, 6)] == ["user_1", "user_2", "user_3", "user_4", None]
    assert not any(q.startswith("slot_") for c in st.calls for q in c)
    assert b.loras == []                                          # bfs_head vem do prompt, não do Clef


def test_recipe_can_pin_a_variable(project):
    st = Stub()
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2), img(3)]),
                                            Step("edit", "trocar_pessoas", mode="turbo", loras=[], vars={"scope": "body"}), src(3)))
    assert b.vars["scope"] == "body" and "var_scope" not in {q for c in st.calls for q in c}


# ---------------------------------------------------------------- ângulo da órbita
@pytest.mark.parametrize("text,az,elev,complete", [
    ("gire 90 graus para a direita", 90, "eye", True),
    ("girar 45 graus para a esquerda", -45, "eye", True),
    ("mostre de costas", 180, "eye", True),
    ("mostre a parte de trás do objeto", 180, "eye", True),
    ("gire 90 graus", 90, "eye", False),                    # falta o lado
    ("mostre de lado", 90, "eye", False),
    ("veja de cima, 135 graus para a direita", 135, "high", True),
])
def test_angle_is_read_from_the_request(text, az, elev, complete):
    a = pvars.parse_angle(text)
    assert (a["azimuth"], a["elevation"], a["complete"]) == (az, elev, complete)


def test_angle_phrase_snaps_to_the_lora_steps():
    assert pvars.angle_phrase({"azimuth": 100, "elevation": "eye"}) == "<orbit> rotate the camera 90 degrees to the right, eye level."
    assert pvars.angle_phrase({"azimuth": -45, "elevation": "low"}) == "<orbit> rotate the camera 45 degrees to the left, low angle."
    assert pvars.angle_phrase({"azimuth": 180, "elevation": "high"}) == "<orbit> rotate the camera 180 degrees, elevated."


def test_orbit_opens_the_angle_screen_only_when_the_request_has_no_angle(api):
    a = upload(api)
    _, rid = start(api, "girar o ponto de vista do objeto", [a["id"]], route="edit")
    need = wait_event(api, rid, lambda e: e["type"] in ("needs_input", "done", "error"))
    assert need["type"] == "needs_input" and need["kind"] == "angle" and need["steps"] == [45, 90, 135, 180]
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": {"azimuth": -90, "elevation": "low"}})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert "rotate the camera 90 degrees to the left, low angle" in prompt["6"]["inputs"]["value"]
    names = [n["inputs"]["lora_name"] for n in prompt.values() if n["class_type"] == "LoraLoader"]
    assert "Qwen2.1_viewpoint_orbit.safetensors" in names and "qwen-image-2.1-consistency.safetensors" not in names


# ---------------------------------------------------------------- conflitos entre LoRAs
LIB = {
    "cons": {"id": "cons", "title": "Consistência", "file": "c.safetensors", "strength": 0.5, "conflicts": ["roupa"]},
    "roupa": {"id": "roupa", "title": "Roupa", "file": "r.safetensors", "strength": 1.0},
    "extra": {"id": "extra", "title": "Extra", "file": "e.safetensors", "strength": 0.7},
}


def names(stack):
    return [i["id"] for i in stack]


def test_conflict_is_symmetric():
    assert L.clash(LIB, "cons", "roupa") and L.clash(LIB, "roupa", "cons") and not L.clash(LIB, "cons", "extra")


def test_entering_lora_turns_off_the_base_lora_it_conflicts_with():
    notes = []
    st = L.effective_loras(LIB, ["cons", "extra"], ["roupa"], [], "turbo", notes)
    assert names(st) == ["extra", "roupa"] and "Consistência" in notes[0] and "foi desligada" in notes[0]


def test_clef_added_lora_never_knocks_out_a_preset_lora():
    notes = []
    st = L.effective_loras(LIB, [], ["cons"], ["roupa"], "turbo", notes)
    assert names(st) == ["cons"] and "ficou de fora" in notes[0]


def test_clef_added_lora_replaces_a_base_lora():
    st = L.effective_loras(LIB, ["cons"], [], ["roupa"], "turbo")
    assert names(st) == ["roupa"]


def test_router_skips_a_clef_lora_that_clashes_with_the_prompt_lora(project):
    class Both(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            if "lora_outfit_swap" in questions:
                out["lora_outfit_swap"] = Answer("usar", 0.95, {"usar": 0.95, "none": 0.05})
            return out
    # "livre" traz a consistência no turbo: a de roupa, que não funciona com ela, não é acrescentada pelo Clef
    b = run(Router(Store(project), Both({"preset": "livre", "mode": "turbo"})).bind(Ctx("x", [img(1), img(2)]), Step("edit"), src(2)))
    assert "outfit_swap" not in b.loras


# ---------------------------------------------------------------- Hugging Face
def test_hf_links_are_understood():
    assert hf.parse("https://huggingface.co/dono/modelo") == {"repo": "dono/modelo", "rev": "main", "path": None}
    r = hf.parse("https://huggingface.co/dono/modelo/blob/main/pasta/arq.safetensors")
    assert (r["repo"], r["rev"], r["path"]) == ("dono/modelo", "main", "pasta/arq.safetensors")
    assert hf.parse("https://huggingface.co/dono/modelo/resolve/v2/arq.safetensors?download=true")["rev"] == "v2"
    for bad in ("http://huggingface.co/a/b", "https://evil.example/a/b", "https://huggingface.co/spaces/a/b", "https://huggingface.co/a"):
        with pytest.raises(hf.HFError):
            hf.parse(bad)


def test_env_file_is_loaded_without_overriding_and_token_goes_only_to_hf(tmp_path, monkeypatch):
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "OUTRA"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text('# comentário\nHF_TOKEN="hf_abc"\nexport OUTRA=1\nlixo sem igual\n', encoding="utf-8")
    hf.load_env(tmp_path)
    assert hf.token() == "hf_abc" and hf.auth_headers("https://huggingface.co/x") == {"Authorization": "Bearer hf_abc"}
    assert hf.auth_headers("https://example.com/x") == {} and hf.auth_headers("http://127.0.0.1:1/x") == {}
    monkeypatch.setenv("HF_TOKEN", "do_ambiente")
    hf.load_env(tmp_path)
    assert hf.token() == "do_ambiente"


def test_env_is_ignored_by_git_and_has_a_template(project):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert ".env" in (root / ".gitignore").read_text().split() and "HF_TOKEN" in (root / ".env.example").read_text()


@pytest.fixture()
def api(project, comfy):
    Store(project).save_settings({"comfyui": {"url": comfy.url}, "clef": {"mode": "mock"}})
    app = create_app(project)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        c.fake, c.pipe, c.project = comfy, app.state.pipe, project
        yield c


def test_hf_inspect_endpoint(api, monkeypatch):
    async def fake_list(repo, rev="main", base_dir=""):
        return [{"path": "a.safetensors", "name": "a.safetensors", "size": 10, "url": hf.file_url(repo, rev, "a.safetensors")}]
    monkeypatch.setattr(hf, "list_safetensors", fake_list)
    r = api.post("/api/loras/hf/inspect", json={"url": "https://huggingface.co/dono/modelo"}).json()
    assert r["repo"] == "dono/modelo" and r["files"][0]["url"].endswith("/dono/modelo/resolve/main/a.safetensors")
    one = api.post("/api/loras/hf/inspect", json={"url": "https://huggingface.co/dono/modelo/blob/main/x.safetensors"}).json()
    assert one["files"][0]["name"] == "x.safetensors"
    assert api.post("/api/loras/hf/inspect", json={"url": "https://exemplo.com/a/b"}).status_code == 400
    assert api.post("/api/loras/hf/inspect", json={"url": "https://huggingface.co/a/b/blob/main/x.ckpt"}).status_code == 400


def test_hf_download_registers_the_lora(api, monkeypatch):
    got = {}

    async def fake_download(entry):
        got.update(entry)
        return {"started": True, "installed": False, "file": entry["file"]}
    monkeypatch.setattr(api.app.state.setup, "download_lora", fake_download)
    r = api.post("/api/loras/hf/download", json={"url": "https://huggingface.co/dono/modelo/blob/main/Meu Estilo.safetensors",
                                                  "title": "Meu estilo", "description": "faz X"})
    assert r.status_code == 200, r.text
    assert got["url"] == "https://huggingface.co/dono/modelo/resolve/main/Meu Estilo.safetensors" and got["file"] == "Meu Estilo.safetensors"
    lo = r.json()["lora"]
    assert lo["id"] == "meu_estilo" and lo["file"] == "Meu Estilo.safetensors" and lo["description"] == "faz X"
    assert "meu_estilo" in {x["id"] for x in api.get("/api/loras").json()["loras"]}
    again = api.post("/api/loras/hf/download", json={"url": "https://huggingface.co/dono/modelo/blob/main/Meu Estilo.safetensors"})
    assert again.status_code == 400 and "Já existe" in again.json()["detail"]
    assert api.post("/api/loras/hf/download", json={"url": "https://huggingface.co/dono/modelo"}).status_code == 400


# ---------------------------------------------------------------- botão de testar o ComfyUI e nomes do seletor
def test_comfy_test_button_answers_fast_with_success_or_failure(api):
    ok = api.post("/api/comfy/test", json={"url": api.fake.url}).json()
    assert ok["ok"] is True
    bad = api.post("/api/comfy/test", json={"url": "http://127.0.0.1:9"}).json()
    assert bad["ok"] is False and bad["error"]
    assert api.post("/api/comfy/test", json={"url": "ftp://x"}).json()["ok"] is False


def test_three_people_swap_runs_with_four_images_and_the_rest_bypassed(api):
    ids = [upload(api, f"i{i}.png", png((i * 20, 9, 9)))["id"] for i in range(1, 5)]
    _, rid = start(api, "trocar as 3 pessoas da cena pelas pessoas de referência, só o corpo", ids, route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    loaders = sorted(k for k, n in prompt.items() if n["class_type"] == "LoadImage")
    assert len(loaders) == 4                                   # as outras quatro entradas saíram do grafo
    text = prompt["6"]["inputs"]["value"]
    step = next(e for e in api.pipe.runs[rid].events if e["type"] == "step_start")
    assert step["vars"]["people"] == "3" and "first 3 people" in text and "<image4>" in text
