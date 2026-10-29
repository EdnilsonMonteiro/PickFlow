"""Peças soltas da biblioteca de LoRAs: pilha efetiva, ajustes, prompt, religação do grafo, API."""
import pytest
from fastapi.testclient import TestClient

from pickflow import loras as L
from pickflow.app import create_app
from pickflow.converter import Converter
from pickflow.router import Router
from pickflow.store import Store, StoreError

LIB = {
    "real": {"id": "real", "title": "Realismo", "file": "real.safetensors", "strength": 0.6, "prompt": ""},
    "swap": {"id": "swap", "title": "Troca", "file": "swap.safetensors", "strength": 1.0, "adjust": {"real": 0},
             "settings": {"resolution": 1024, "scene_mp": "off"}, "prompt": {"turbo": "swap t", "quality": "swap q"}},
    "face": {"id": "face", "title": "Rosto", "file": "face.safetensors", "strength": 0.8, "prompt": "face!"},
}
KNOBS = {"resolution": {"node": 5, "widget": "value", "default": 1536},
         "scene_mp": {"nodes": [40, 41], "widget": "megapixels", "bypass": True, "default": 1}}


def ids(stack):
    return [(i["id"], i["strength"], i["origin"]) for i in stack]


def test_stack_order_adjust_and_zero_strength_drops():
    st = L.effective_loras(LIB, ["real"], ["swap"], ["face"], "turbo")
    assert ids(st) == [("swap", 1.0, "preset"), ("face", 0.8, "clef")]          # swap zerou o realismo → sai
    assert ids(L.effective_loras(LIB, ["real"], [], [], "turbo")) == [("real", 0.6, "base")]


def test_explicit_strength_beats_adjust_and_mode_filter():
    st = L.effective_loras(LIB, ["real"], [{"id": "real", "strength": 0.3}, "swap"], [], "turbo")
    assert ("real", 0.0) not in [(i["id"], i["strength"]) for i in st]           # swap ainda ajusta depois
    st = L.effective_loras(LIB, ["real"], [{"id": "swap", "mode": "quality"}], [], "turbo")
    assert ids(st) == [("real", 0.6, "base")]
    assert L.effective_loras(LIB, [], ["fantasma"], [], None) == []              # id fora da biblioteca é ignorado


def test_settings_merge_and_knob_plan():
    st = L.effective_loras(LIB, ["real"], ["swap"], [], None)
    s = L.merged_settings(LIB, st, {"resolution": 2048}, {"resolution": 1536})
    assert s == {"resolution": 2048, "scene_mp": "off"}                          # prompt > LoRA > padrão
    over, byp = L.knob_plan(KNOBS, s)
    assert over == {(5, "value"): 2048} and byp == {"40", "41"}
    over, byp = L.knob_plan(KNOBS, {"scene_mp": 2})
    assert over == {(40, "megapixels"): 2, (41, "megapixels"): 2} and not byp


def test_compose_with_and_without_placeholder():
    st = L.effective_loras(LIB, ["real"], ["swap"], ["face"], "quality")
    assert L.compose("Base. {request}", "pedido", st) == "face!\nBase. pedido"   # só a LoRA do Clef vai antes
    assert L.compose("{loras}\n{request}", "pedido", st) == "swap q\nface!\npedido"
    assert L.compose("Troque.\nExtra instructions: {request}", "", st[:1]).strip() == "Troque."
    assert L.pick_prompt({"turbo": "a", "default": "b"}, "quality") == "b"
    d = L.describe_stack(st, {**LIB, "swap": {**LIB["swap"], "description": "troca corpo"}})
    assert "- Troca: troca corpo" in d and "Realismo" not in d


def _tiny_graph():
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
        "2": {"class_type": "LoraLoader", "inputs": {"lora_name": "old.safetensors", "strength_model": 1, "strength_clip": 1,
                                                      "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "a", "clip": ["2", 1]}},
        "4": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0]}},
    }


def test_rewire_builds_new_chain_in_order_and_handles_empty():
    c = Converter({}, randomize_seeds=False)
    api = _tiny_graph()
    c.rewire_loras(api, [2], [{"file": "a.safetensors", "strength": 0.5, "title": "A"},
                              {"file": "b.safetensors", "strength": 1, "strength_clip": 0.7, "title": "B"}])
    assert "2" not in api and api["lora_1"]["inputs"]["model"] == ["1", 0] and api["lora_2"]["inputs"]["model"] == ["lora_1", 0]
    assert api["lora_2"]["inputs"]["strength_clip"] == 0.7
    assert api["4"]["inputs"]["model"] == ["lora_2", 0] and api["3"]["inputs"]["clip"] == ["lora_2", 1]
    api = _tiny_graph()
    api["9"] = {"class_type": "CLIPLoader", "inputs": {}}
    c.rewire_loras(api, {"nodes": [2], "model": 1, "clip": 9}, [])
    assert api["4"]["inputs"]["model"] == ["1", 0] and api["3"]["inputs"]["clip"] == ["9", 0]


def test_bypass_passes_input_through(oi):
    from conftest import ROOT
    import json
    wf = json.loads((ROOT / "defaults" / "workflows" / "PickFlow Editar - Turbo.json").read_text(encoding="utf-8"))
    base = Converter(oi, randomize_seeds=False).convert(wf)
    byp = Converter(oi, randomize_seeds=False).convert(wf, bypass={"40", "41"})
    assert "40" in base and "40" not in byp and "41" not in byp


# ------------------------------------------------------------------------ biblioteca e API
def test_store_library_overlay_save_reset_hide(project):
    s = Store(project)
    base = s.loras()["bfs_body"]
    assert base["source"] == "default" and "troca" == base["group"]
    s.save_lora({**base, "description": "minha descrição", "strength": 0.9})
    assert s.loras()["bfs_body"]["description"] == "minha descrição" and s.loras()["bfs_body"]["source"] == "custom"
    assert s.delete_lora("bfs_body") == "reset" and s.loras()["bfs_body"]["description"] == base["description"]
    assert s.delete_lora("bfs_body") == "hidden" and "bfs_body" not in s.loras()
    s.save_lora({"id": "minha", "file": "x.safetensors", "prompt": {"turbo": "t"}})
    assert s.loras()["minha"]["prompt"] == {"turbo": "t"} and s.delete_lora("minha") == "removed"
    with pytest.raises(StoreError):
        s.save_lora({"id": "Ruim Id", "file": "x"})
    with pytest.raises(StoreError):
        s.save_lora({"id": "sem_arquivo"})


def test_api_loras_roundtrip(project):
    with TestClient(create_app(project), base_url="http://127.0.0.1:7860") as c:
        d = c.get("/api/loras").json()
        assert any(x["id"] == "bfs_body" for x in d["loras"]) and "edit" in d["workflows"] and "natexp" in d["base"]
        r = c.put("/api/loras/zzz", json={"file": "z.safetensors", "description": "teste", "auto": True})
        assert r.status_code == 200 and r.json()["source"] == "custom"
        assert c.put("/api/loras/Bad%20Id", json={"file": "z"}).status_code >= 400
        assert c.delete("/api/loras/zzz").json() == {"result": "removed"}


def test_api_hidden_and_restore(project):
    with TestClient(create_app(project), base_url="http://127.0.0.1:7860") as c:
        assert c.delete("/api/loras/natexp").json() == {"result": "hidden"}
        d = c.get("/api/loras").json()
        assert [x["id"] for x in d["hidden"]] == ["natexp"] and all(x["id"] != "natexp" for x in d["loras"])
        assert c.post("/api/loras/natexp/restore").json() == {"result": "restored"}
        assert c.get("/api/loras").json()["hidden"] == []
