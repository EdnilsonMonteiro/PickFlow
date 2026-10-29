import base64
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pickflow.app import create_app
from pickflow.clef import Answer


def png(color=(10, 120, 200), size=(80, 60), alpha=False):
    buf = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", size, color + ((255,) if alpha else ())).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def api(project, comfy):
    from pickflow.store import Store
    Store(project).save_settings({"comfyui": {"url": comfy.url}, "clef": {"mode": "mock"}})
    app = create_app(project)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        c.fake, c.pipe = comfy, app.state.pipe
        yield c


def upload(c, name="a.png", data=None):
    r = c.post("/api/upload", files={"file": (name, data or png(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()


def start(c, text, ids, route=None):
    cid = c.post("/api/conversations").json()["id"]
    rid = c.post(f"/api/conversations/{cid}/messages", json={"text": text, "image_ids": ids, "route": route}).json()["run_id"]
    return cid, rid


def wait_event(c, rid, pred, timeout=10):
    run = c.pipe.runs[rid]
    t0 = time.time()
    while time.time() - t0 < timeout:
        for e in list(run.events):
            if pred(e):
                return e
        if run.finished and not any(pred(e) for e in run.events):
            break
        time.sleep(0.05)
    raise AssertionError("evento não apareceu: " + json.dumps(run.events[-3:], ensure_ascii=False)[:600])


def types(c, rid):
    return [e["type"] for e in c.pipe.runs[rid].events]


def test_simple_edit_end_to_end(api):
    a = upload(api)
    cid, rid = start(api, "troque a cor da camisa para vermelho", [a["id"]], route="edit")
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    assert done["images"][0]["url"].startswith("/files/")
    assert api.get(done["images"][0]["url"]).status_code == 200
    # o que foi para o ComfyUI
    (prompt,) = api.fake.prompts.values()
    assert prompt["6"]["inputs"]["value"] == "troque a cor da camisa para vermelho"
    assert prompt["26"]["inputs"]["image"].startswith("pickflow/up_")
    assert not {"27", "50", "52"} & set(prompt) and "25" in prompt
    assert prompt["20"]["inputs"]["noise_seed"] != 1055939264985049        # seed randomizada
    # edição simples: sem escaladores (ajustes em "off") e a cadeia de realismo do modo turbo
    assert not {"40", "41", "51", "53"} & set(prompt) and prompt["18"]["inputs"]["images.image_1"] == ["26", 0]
    assert prompt["18"]["inputs"]["resolution"] == 1024
    assert _chain(prompt) == [("Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors", 0.8, 0.8),
                              ("qwen-image-2.1-consistency.safetensors", 0.82, 0.8)]
    assert prompt["3"]["inputs"]["model"][0] == _last_lora(prompt)          # o Viggle parte da última LoRA
    conv = api.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] and conv["title"].startswith("troque a cor")
    assert conv["messages"][1]["images"] and "progress" not in [e["type"] for e in conv["messages"][1]["events"]]


def _lora_nodes(prompt):
    return [n for n in prompt.values() if n["class_type"] == "LoraLoader"]


def _chain(prompt):
    return [(n["inputs"]["lora_name"], n["inputs"]["strength_model"], n["inputs"]["strength_clip"]) for n in _lora_nodes(prompt)]


def _last_lora(prompt):
    ids = [k for k, n in prompt.items() if n["class_type"] == "LoraLoader"]
    return ids[-1]


def test_sse_replays_finished_run(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    time.sleep(0.1)
    with api.stream("GET", f"/api/runs/{rid}/events") as r:
        body = "".join(r.iter_text())
    assert '"type": "plan"' in body and '"type": "done"' in body and "event: end" in body


def test_text_to_image_when_no_images(api):
    cid, rid = start(api, "desenhe uma praia ao pôr do sol", [], route="t2i@turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert prompt["30"]["inputs"]["value"] is True and not {"26", "27", "50", "52", "40"} & set(prompt)
    assert prompt["6"]["inputs"]["value"] == "desenhe uma praia ao pôr do sol"


def test_follow_up_without_attachment_edits_last_result(api):
    a = upload(api)
    cid, rid = start(api, "primeiro", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    time.sleep(0.1)
    r = api.post(f"/api/conversations/{cid}/messages", json={"text": "agora deixe mais escuro", "image_ids": [], "route": "edit"})
    rid2 = r.json()["run_id"]
    ev = wait_event(api, rid2, lambda e: e["type"] in ("done", "error"))
    assert ev["type"] == "done"
    assert any(e["type"] == "status" and "último resultado" in e["text"] for e in api.pipe.runs[rid2].events)
    assert len(api.fake.prompts) == 2


def test_two_image_workflow_uploads_both_and_keeps_order(api):
    a, b = upload(api, "cena.png", png((1, 1, 1))), upload(api, "pessoa.png", png((9, 9, 9)))
    _, rid = start(api, "swap the body of the person in the scene for the reference person", [a["id"], b["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    names = {k: prompt[k]["inputs"]["image"] for k in ("26", "27")}
    assert names["26"] != names["27"]
    assert "50" not in prompt and "52" not in prompt       # LoadImage não conectado foi descartado
    assert "body_swap: start with <image1>" in prompt["6"]["inputs"]["value"]
    # troca de corpo: escaladores do autor ligados, resolution 0 e a pilha de LoRAs do BFS Body Swap (sem flymy)
    assert prompt["40"]["inputs"]["megapixels"] == 2.0 and prompt["41"]["inputs"]["megapixels"] == 0.59
    assert prompt["18"]["inputs"]["resolution"] == 0 and "51" not in prompt and "53" not in prompt
    assert _chain(prompt) == [("Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors", 0.6, 0.6),
                              ("qwen-image-2.1-consistency.safetensors", 0.5, 0.5),
                              ("bfs_body_swap_v1.0_qwen_2.1.safetensors", 1.0, 1.0),
                              ("bfs_head_v1.1_qwen_2.1.safetensors", 1.0, 1.0)]
    txt = prompt["6"]["inputs"]["value"]                  # uma pessoa, corpo e cabeça: só os prompts das duas LoRAs
    assert "head_swap: start with <image1>" in txt and "Replicate the exact pose" not in txt
    step = next(e for e in api.pipe.runs[rid].events if e["type"] == "step_start")
    assert step["mode"] == "turbo" and [x["title"] for x in step["loras"]] == ["BFS Body Swap", "BFS Head Swap"]


def test_quality_mode_uses_its_own_graph_and_sampler_settings(api):
    a, b = upload(api), upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit@quality")
    wait_event(api, rid, lambda e: e["type"] == "done")
    p = list(api.fake.prompts.values())[0]
    ks = next(n for n in p.values() if n["class_type"] == "KSampler")["inputs"]
    assert (ks["steps"], ks["cfg"], ks["sampler_name"], ks["scheduler"]) == (25, 4, "er_sde", "beta")
    assert "3" not in p and "23" not in p                      # nada do ramo Turbo (Viggle, SamplerCustomAdvanced)
    assert _chain(p) == [("Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors", 0.6, 0.6),
                         ("flymy_realism.safetensors", 0.5, 0.5), ("qwen-image-2.1-consistency.safetensors", 0.5, 0.5)]
    _, rid = start(api, "swap the body of the person in the scene for the reference person", [a["id"], b["id"]], route="edit@quality")
    wait_event(api, rid, lambda e: e["type"] == "done")
    q = list(api.fake.prompts.values())[1]
    ks = next(n for n in q.values() if n["class_type"] == "KSampler")["inputs"]
    assert (ks["cfg"], ks["sampler_name"], ks["scheduler"]) == (1, "euler", "simple")      # ajustes da LoRA de troca
    assert "flymy_realism.safetensors" not in [c[0] for c in _chain(q)]


def test_clef_added_lora_brings_its_prompt_and_settings(api, monkeypatch):
    """Prompt 'livre' + LoRA que o Clef ligou por conta própria: as instruções da LoRA vão antes do pedido."""
    from pickflow import runner
    from pickflow.clef import Answer, MockClef

    class WantsHead(MockClef):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            if "preset" in questions:
                out["preset"] = Answer("livre", 0.9, {"livre": 0.9})
            out["lora_bfs_head"] = Answer("usar", 0.9, {"usar": 0.9, "none": 0.1})
            out["lora_bfs_body"] = Answer("none", 0.9, {"usar": 0.1, "none": 0.9})
            return out
    monkeypatch.setattr(runner, "make_clef", lambda settings: WantsHead())
    a, b = upload(api), upload(api)
    _, rid = start(api, "coloque o rosto da referência, com cabelo curto", [a["id"], b["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (p,) = api.fake.prompts.values()
    text = p["6"]["inputs"]["value"]
    assert text.startswith("head_swap: start with <image1>") and text.endswith("coloque o rosto da referência, com cabelo curto")
    assert ("bfs_head_v1.1_qwen_2.1.safetensors", 1.0, 1.0) in _chain(p)
    assert "qwen-image-2.1-consistency.safetensors" not in [c[0] for c in _chain(p)]      # a LoRA pediu para desligar
    assert not {"40", "41"} & set(p) and p["18"]["inputs"]["resolution"] == 1024


def test_missing_lora_files(api, project):
    """Faltou a LoRA do prompt: erro claro. Faltou uma de realismo: segue sem ela e avisa."""
    import copy
    api.fake.oi = copy.deepcopy(api.fake.oi)
    oi = api.fake.oi
    opts = oi["LoraLoader"]["input"]["required"]["lora_name"][0]
    opts.remove("flymy_realism.safetensors")
    opts.remove("bfs_body_swap_v1.0_qwen_2.1.safetensors")
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit@quality")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert any("FlyMy Realism" in e.get("text", "") for e in api.pipe.runs[rid].events if e["type"] == "status")
    assert "flymy_realism.safetensors" not in [c[0] for c in _chain(list(api.fake.prompts.values())[0])]
    b = upload(api)
    cid = api.post("/api/conversations").json()["id"]
    rid = api.post(f"/api/conversations/{cid}/messages", json={"text": "swap the body of the person in the scene for the reference person", "image_ids": [a["id"], b["id"]], "route": "edit"}).json()["run_id"]
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "BFS Body Swap" in err["message"] and "não está instalada" in err["message"]


def test_llm_writer_refines_the_assembled_prompt(api, project, monkeypatch):
    from pickflow import runner
    from pickflow.store import Store
    seen = {}

    async def fake_refine(draft, request, loras, cfg, images=None, note="", notify=None):
        seen.update(draft=draft, request=request, loras=loras, images=images)
        return "REFINED: " + draft.splitlines()[0][:20]
    monkeypatch.setattr(runner.writer, "refine", fake_refine)
    Store(project).save_settings({"writer": {"enabled": True, "url": "http://127.0.0.1:9/v1"}})
    store = api.pipe.store
    lib = store.prompts("edit")
    for pr in lib["presets"]:
        pr["rewrite"] = True
    store.save_prompts("edit", lib)
    a, b = upload(api), upload(api)
    _, rid = start(api, "swap the body of the person in the scene for the reference person, I want red clothes", [a["id"], b["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (p,) = api.fake.prompts.values()
    assert p["6"]["inputs"]["value"].startswith("REFINED: ")
    assert seen["request"] == "swap the body of the person in the scene for the reference person, I want red clothes" and "body_swap:" in seen["draft"]
    assert "BFS Body Swap" in seen["loras"] and len(seen["images"]) == 2          # as imagens do passo, para modelos com visão


def test_box_workflow_pauses_for_drawing_and_applies_boxes(api):
    a = upload(api)
    _, rid = start(api, "mova o objeto", [a["id"]], route="object_move@turbo")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["kind"] == "draw" and [t["key"] for t in need["tasks"]] == ["red", "green"]
    assert need["tasks"][0]["image_url"].startswith("/files/")
    assert not api.fake.prompts                                # nada foi para o ComfyUI ainda
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"],
                                             "value": {"red": [0.1, 0.2, 0.3, 0.2], "green": [0.6, 0.5, 0.2, 0.2]}})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    mb = prompt["6"]["inputs"]
    assert (mb["src_x"], mb["src_y"], mb["src_w"], mb["src_h"]) == (0.1, 0.2, 0.3, 0.2)
    assert (mb["tgt_x"], mb["tgt_y"], mb["tgt_w"], mb["tgt_h"]) == (0.6, 0.5, 0.2, 0.2)
    assert "red box" in prompt["8"]["inputs"]["prompt"] or "red box" in prompt["8"]["inputs"]["prompt"].lower()


def test_subgraph_workflows_run_end_to_end(api):
    """t2i (texto -> imagem) e remoção de fundo: workflows oficiais com subgraph, achatados na hora de rodar."""
    _, rid = start(api, "um gato astronauta", [], route="t2i@quality")
    wait_event(api, rid, lambda e: e["type"] == "done")
    p1 = list(api.fake.prompts.values())[0]
    assert p1["459:474"]["inputs"]["on_false"] == "um gato astronauta" and "459:471" not in p1
    seed1 = p1["459:458"]["inputs"]["seed"]
    a = upload(api)
    _, rid = start(api, "remove the background, transparent", [a["id"]], route="background_removal")
    wait_event(api, rid, lambda e: e["type"] == "done")
    p2 = list(api.fake.prompts.values())[1]
    assert p2["459:474"]["inputs"]["prompt"] == "Remove the background, and output a PNG image"
    assert p2["470"]["inputs"]["image"].startswith("pickflow/up_") and "472" not in p2
    assert p2["459:458"]["inputs"]["seed"] != 992618384254740 and seed1 != 0           # semente sorteada (registro: set random)


def test_recipe_chains_two_workflows_and_feeds_previous_result(api):
    ids = [upload(api, f"i{i}.png", png((i * 30, 0, 0))) ["id"] for i in (1, 2, 3)]
    _, rid = start(api, "troque a pessoa e o fundo", ids, route="recipe__trocar_pessoa_e_fundo")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")        # passo 2 pede as caixas
    assert [t["key"] for t in need["tasks"]] == ["green", "red"]
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": {
        "green": [0.4, 0.4, 0.3, 0.5], "red": [0.2, 0.1, 0.5, 0.8]}})
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert [e["index"] for e in api.pipe.runs[rid].events if e["type"] == "step_done"] == [0, 1]
    assert len(api.fake.prompts) == 2 and len(api.fake.uploads) == 4
    second = list(api.fake.prompts.values())[1]
    assert second["5"]["inputs"]["image"] != second["6"]["inputs"]["image"]
    assert second["7"]["inputs"]["box_x"] == 0.4 and second["8"]["inputs"]["box_w"] == 0.5


def test_ambiguous_route_asks_user(api, project):
    from pickflow.store import Store
    Store(project).save_settings({"clef": {"ask_below": 0.999}})
    a, b = upload(api), upload(api)
    _, rid = start(api, "algo vago", [a["id"], b["id"]])
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["kind"] == "choose" and len(need["options"]) == 3
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": "edit"})
    wait_event(api, rid, lambda e: e["type"] == "plan")
    plan = next(e for e in api.pipe.runs[rid].events if e["type"] == "plan")
    assert plan["steps"][0]["workflow"] == "edit"


def test_comfy_execution_error_is_reported(api):
    api.fake.fail_node = "42"
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit@quality")
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "CUDA out of memory" in err["message"] and "nó 42" in err["message"]


def test_comfy_validation_error_is_readable(api):
    a = upload(api)
    # força um grafo inválido: remove o nó de saída da lista de outputs do manifesto
    import yaml
    store = api.pipe.store
    m = yaml.safe_load((store.defaults_dir / "registry" / "edit.yaml").read_text(encoding="utf-8"))
    m["modes"]["turbo"]["outputs"] = [999]
    (store.custom_dir / "registry" / "edit.yaml").write_text(yaml.dump(m), encoding="utf-8")   # o seu vale mais
    _, rid = start(api, "x", [a["id"]], route="edit@turbo")
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "999" in err["message"]


def test_clef_down_gives_actionable_error(api, project):
    from pickflow.store import Store
    Store(project).save_settings({"clef": {"mode": "systemone", "url": "http://127.0.0.1:9"}})
    a, b = upload(api), upload(api)
    _, rid = start(api, "x", [a["id"], b["id"]])
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "Clef" in err["message"] and "127.0.0.1:9" in err["message"]


def test_cancel_unblocks_waiting_run(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="replace_redbox")
    wait_event(api, rid, lambda e: e["type"] == "needs_input")
    api.post(f"/api/runs/{rid}/cancel")
    wait_event(api, rid, lambda e: e["type"] == "error")
    t0 = time.time()
    while not api.pipe.runs[rid].finished and time.time() - t0 < 5:
        time.sleep(0.05)
    assert api.pipe.runs[rid].finished
    # o lock global foi liberado: uma nova execução termina normalmente
    _, rid2 = start(api, "x", [a["id"]], route="edit")
    wait_event(api, rid2, lambda e: e["type"] == "done")


def test_editing_prompts_goes_to_custom_and_never_touches_defaults(api):
    store = api.pipe.store
    before = {p: p.read_bytes() for p in store.defaults_dir.rglob("*") if p.is_file()}
    w = next(x for x in api.get("/api/workflows").json() if x["id"] == "edit")
    n0 = len(w["presets"])
    assert w["source"] == "default" and w["customized"] is False
    w["presets"][0]["text"] = "<orbit> novo texto {request}"
    w["presets"].append({"id": "custom", "name": "Meu prompt", "when": "sempre", "text": "meu {request}", "rewrite": False})
    r = api.put("/api/workflows/edit", json={"enabled": True, "description": "nova descrição",
                                                       "active": ["custom"], "default": "custom", "presets": w["presets"]})
    assert r.status_code == 200, r.text
    again = next(x for x in api.get("/api/workflows").json() if x["id"] == "edit")
    assert again["description"] == "nova descrição" and again["active"] == ["custom"] and len(again["presets"]) == n0 + 1
    assert again["customized"] is True and again["source"] == "default"
    clef_text = (store.custom_dir / "clef_prompt.yaml").read_text(encoding="utf-8")
    assert "nova descrição" in clef_text and "background_removal" not in clef_text          # só o que mudou
    assert "meu {request}" in (store.custom_dir / "prompts" / "edit.yaml").read_text(encoding="utf-8")
    assert {p: p.read_bytes() for p in store.defaults_dir.rglob("*") if p.is_file()} == before
    bad = api.put("/api/workflows/edit", json={"enabled": True, "description": "", "presets": [{"id": "Inválido!", "text": "x"}]})
    assert bad.status_code == 422
    # restaurar o padrão apaga só as suas edições
    assert api.delete("/api/workflows/edit").json() == {"result": "reset"}
    back = next(x for x in api.get("/api/workflows").json() if x["id"] == "edit")
    assert back["customized"] is False and back["description"] != "nova descrição" and len(back["presets"]) == n0
    assert not (store.custom_dir / "prompts" / "edit.yaml").exists()
    assert not (store.custom_dir / "clef_prompt.yaml").exists()


def test_recipes_roundtrip_and_validation(api):
    cfg = api.get("/api/clef-config").json()
    cfg["recipes"].append({"id": "so_cabeca", "name": "Só cabeça", "description": "x", "enabled": True,
                           "steps": [{"workflow": "edit", "prompt": "trocar_cabeca", "mode": "quality", "loras": ["bfs_head"],
                                      "inputs": {"image_1": "user:1"}}]})
    assert api.put("/api/clef-config", json={"recipes": cfg["recipes"], "instructions": "novas instruções"}).status_code == 200
    back = api.get("/api/clef-config").json()["recipes"]
    assert len(back) == 11 and back[-1]["steps"][0]["mode"] == "quality" and back[-1]["steps"][0]["loras"] == ["bfs_head"]
    bad = api.put("/api/clef-config", json={"recipes": [{"id": "x", "steps": [{"workflow": "nao_existe"}]}]})
    assert bad.status_code == 422


def test_doctor_endpoint_reports_all_ok(api):
    r = api.post("/api/doctor").json()
    assert len(r) == 26 and all(x["ok"] for x in r)


def test_upload_rejects_non_image(api):
    r = api.post("/api/upload", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


GB = 2 ** 30


def _spy_clef(monkeypatch, health):
    from pickflow import runner
    from pickflow.clef import MockClef
    calls = []

    class Spy(MockClef):
        mode = "systemone"

        async def health(self):
            return health

        async def decide(self, state, questions, images=None):
            calls.append("decide")
            # resposta DECIDIDA (0,9 na primeira opção): estes testes são da política de memória, e um Clef em
            # dúvida abriria a pergunta na tela (`needs_input`) em vez de chegar ao `done`
            out = {}
            for qid, spec in questions.items():
                crit = spec.get("criteria")
                keys = list(crit) if isinstance(crit, dict) and crit else ["yes", "no"]
                probs = {k: (0.9 if k == keys[0] else 0.1 / max(1, len(keys) - 1)) for k in keys}
                out[qid] = Answer(keys[0], probs[keys[0]], probs)
            return out

        async def unload(self):
            calls.append("unload")
            return True

    monkeypatch.setattr(runner, "make_clef", lambda settings: Spy())
    return calls


def _stats(vram_total=24, vram_free=24):
    return {"system": {"ram_total": 32 * GB, "ram_free": 20 * GB},
            "devices": [{"vram_total": vram_total * GB, "vram_free": vram_free * GB}]}


def test_clef_unloaded_when_it_does_not_fit(api, monkeypatch):
    calls = _spy_clef(monkeypatch, {"loaded": True, "device": "cuda", "vram": {"allocated_gb": 19.0}})
    api.fake.stats = _stats(24, 3)
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert calls.count("unload") == 1 and calls.index("unload") > max(i for i, c in enumerate(calls) if c == "decide")
    mem = [e for e in api.pipe.runs[rid].events if e["type"] == "memory"]
    assert mem and "Descarreguei o Clef" in mem[0]["text"]


def test_comfy_freed_when_clef_cannot_load(api, monkeypatch):
    _spy_clef(monkeypatch, {"loaded": False, "device": "cuda"})
    api.fake.stats = _stats(24, 2)        # ComfyUI ocupando a GPU
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    # duas vezes: no começo do turno e de novo antes do bind do passo — o Clef pode ter de carregar no meio do turno,
    # com o Qwen ainda na placa; o segundo POST /free é inofensivo quando não há modelo carregado
    assert api.fake.freed == 2
    assert any("Liberei os modelos do ComfyUI" in e["text"] for e in api.pipe.runs[rid].events if e["type"] == "memory")


# ------------------------------------------------------------ colar e fundir
def _cutout_need(api, rid, n=1, timeout=10):
    """O n-ésimo pedido de revisão do recorte."""
    run = api.pipe.runs[rid]
    t0 = time.time()
    while time.time() - t0 < timeout:
        evs = [e for e in list(run.events) if e["type"] == "needs_input" and e["kind"] == "cutout"]
        if len(evs) >= n:
            return evs[n - 1]
        if run.finished:
            break
        time.sleep(0.05)
    raise AssertionError("revisão do recorte não apareceu: " + json.dumps(run.events[-3:], ensure_ascii=False)[:500])


def _answer(api, rid, need, value):
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": value})


def _paste_run(api, route="recipe__colar_e_fundir"):
    api.fake.view_rgba = True
    bg = upload(api, "fundo.png", png((20, 30, 40), (200, 100)))
    person = upload(api, "pessoa.png", png((9, 9, 9), (100, 80)))
    _, rid = start(api, "cole a pessoa neste cenário", [bg["id"], person["id"]], route=route)
    return rid


def _uploaded_size(api, prompt, node):
    return Image.open(io.BytesIO(api.fake.uploads[prompt[node]["inputs"]["image"].split("/")[-1]])).size


def _layer(prompt):
    return next(n for n in prompt.values() if n["class_type"] == "AddLayer" and n["inputs"].get("name") == "Pessoa")


def test_cutout_is_automatic_then_reviewed_and_can_be_redone_with_a_box(api):
    rid = _paste_run(api)
    need = _cutout_need(api, rid, 1)                          # nada de caixa antes: o recorte já rodou sozinho
    assert need["original_url"] != need["result_url"] and need["mode"] == "pessoa"
    assert [m["id"] for m in need["modes"]] == ["pessoa", "objeto", "fundo"] and not need["warning"]
    first = next(iter(api.fake.prompts.values()))
    assert first["4"]["class_type"] == "SAM3Segment" and first["4"]["inputs"]["prompt"] == "person"
    assert _uploaded_size(api, first, "3") == (100, 80)       # imagem inteira
    # o usuário não gostou: desenha uma caixa no original e troca para "tirar o fundo" (RMBG)
    _answer(api, rid, need, {"action": "redo", "box": [0.25, 0.25, 0.5, 0.5], "mode": "fundo"})
    need2 = _cutout_need(api, rid, 2)
    second = list(api.fake.prompts.values())[1]
    assert second["4"]["class_type"] == "RMBG" and _uploaded_size(api, second, "3") == (50, 40)
    assert need2["mode"] == "fundo" and need2["box"] == [0.25, 0.25, 0.5, 0.5]
    _answer(api, rid, need2, {"action": "accept"})
    place = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e["kind"] == "draw")
    t = place["tasks"][0]
    assert t["kind"] == "place" and t["overlay_size"] == [48, 32] and t["image_size"] == [200, 100]   # já sem a borda transparente


def test_paste_recipe_places_in_chat_and_feeds_the_blend_step(api):
    rid = _paste_run(api)
    _answer(api, rid, _cutout_need(api, rid), {"action": "accept"})
    place = wait_event(api, rid, lambda e: e["type"] == "needs_input" and e["kind"] == "draw")
    # largura 0.6 do fundo e altura 0.9: proporção livre, e a camada passa da borda de baixo (cortada em 100)
    _answer(api, rid, place, {"lugar": [0.1, 0.2, 0.6, 0.9]})
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert types(api, rid).count("step_start") == 3
    prompts = list(api.fake.prompts.values())
    layer = _layer(prompts[1])
    assert (layer["inputs"]["x"], layer["inputs"]["y"], layer["inputs"]["width"], layer["inputs"]["height"]) == (20, 20, 120, 80)
    assert _uploaded_size(api, prompts[1], "3") == (120, 80)
    assert "mask" in layer["inputs"]                           # a transparência do recorte vai pela máscara
    comp = next(n for n in prompts[1].values() if n["class_type"] == "ImageCompositor")
    assert comp["inputs"]["compositor"] == {}                  # o JSON guardado no seu workflow não pode mandar na posição
    assert any(n["class_type"] == "TextEncodeQwenImage21" and n["inputs"]["prompt"].startswith("pysj666") for n in prompts[2].values())


def test_paste_stops_with_a_clear_message_when_the_cutout_has_no_transparency(api):
    api.fake.view_rgba = False                                # recorte configurado sem Alpha
    bg, person = upload(api, "f.png"), upload(api, "p.png")
    _, rid = start(api, "cole a pessoa neste cenário", [bg["id"], person["id"]], route="recipe__colar_sem_fundir")
    need = _cutout_need(api, rid)
    assert need["warning"] and "fundo" in need["warning"]       # a tela de revisão já avisa
    _answer(api, rid, need, {"action": "accept"})              # aceitar um recorte sem transparência não passa
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "Alpha" in err["message"] or "transparên" in err["text"]


def test_person_cutout_adds_body_parts_that_only_the_rmbg_branch_found(api):
    """SAM3 (nó 14) deixou a perna de fora; o RMBG (nó 15) a achou: o recorte entregue tem a perna, com as cores da foto."""
    def cut(parts):
        im = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
        for box in parts:
            im.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (1, 2, 3, 255)), box[:2])
        return _png_bytes(im)
    torso, leg = (30, 5, 60, 45), (35, 45, 55, 78)
    api.fake.views = {"out_14.png": cut([torso]), "out_15.png": cut([torso, leg])}
    rid = _paste_run(api)
    need = _cutout_need(api, rid, 1)
    assert not need["warning"]
    res = Image.open(io.BytesIO(api.get(need["result_url"]).content)).convert("RGBA")
    assert res.size == (30, 73) and res.getpixel((15, 60))[3] == 255            # torso + perna (borda transparente cortada)
    assert res.getpixel((15, 60))[:3] == (9, 9, 9)                              # cor da foto original, não a do nó


def _png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
