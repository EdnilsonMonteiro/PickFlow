"""Vários modelos de imagem: tags `models`, workflow equivalente (`implements`), lista do chat e padrão intacto."""
import asyncio

import yaml
from fastapi.testclient import TestClient

from pickflow import families
from pickflow.app import create_app
from pickflow.router import Router
from pickflow.store import Store, norm_lora, norm_preset

from test_router import Stub

MOSS = "moss_image"


def write(project, rel, data):
    p = project / "custom" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def with_moss(project):
    """Um workflow do MOSS que faz o papel do `edit`, um prompt só dele, um tagueado para os dois, um caso de uso
    compartilhado e uma LoRA do MOSS. Nada de `defaults/` muda."""
    edit = yaml.safe_load((project / "defaults" / "registry" / "edit.yaml").read_text(encoding="utf-8"))
    write(project, "registry/edit_moss.yaml", {**edit, "id": "edit_moss", "title": "Editar (MOSS)", "models": [MOSS],
                                               "implements": "edit"})
    lib = yaml.safe_load((project / "defaults" / "prompts" / "edit.yaml").read_text(encoding="utf-8"))
    for p in lib["presets"]:
        if p["id"] == "livre":
            p["models"] = ["qwen21", MOSS]                  # este prompt serve aos dois
    write(project, "prompts/edit.yaml", lib)
    write(project, "prompts/edit_moss.yaml", {"default": "so_moss", "presets": [
        {"id": "so_moss", "name": "Só do MOSS", "when": "Anything.", "text": "{request}"}]})
    write(project, "clef_prompt.yaml", {"recipes": [
        {"id": "dois_modelos", "name": "Nos dois", "description": "Works in both.", "models": ["qwen21", MOSS],
         "steps": [{"workflow": "edit", "prompt": "livre"}]}]})
    write(project, "loras.yaml", {"loras": [{"id": "moss_lora", "file": "moss_lora.safetensors", "models": [MOSS]}]})
    return Store(project)


def test_default_model_sees_exactly_what_the_store_sees(project):
    s = Store(project)
    v = s.for_model(None)
    assert v.model == "qwen21"
    assert set(v.enabled_workflows()) == set(s.enabled_workflows())
    assert [r["id"] for r in v.enabled_recipes()] == [r["id"] for r in s.enabled_recipes()]
    assert set(v.loras()) == set(s.loras())
    assert v.prompts("edit") == s.prompts("edit")
    assert v.settings() == s.settings()                     # o resto vem do Store de verdade


def test_other_model_sees_only_its_own_workflows_loras_and_shared_things(project):
    s = with_moss(project)
    v = s.for_model(MOSS)
    assert set(v.enabled_workflows()) == {"edit_moss"}
    assert set(v.loras()) == {"moss_lora"}
    assert [r["id"] for r in v.enabled_recipes()] == ["dois_modelos"]
    q = s.for_model("qwen21")
    assert "edit_moss" not in q.enabled_workflows() and "moss_lora" not in q.loras()
    assert "dois_modelos" in [r["id"] for r in q.enabled_recipes()]


def test_a_workflow_that_implements_another_takes_its_place_in_use_cases(project):
    s = with_moss(project)
    assert s.for_model(MOSS).resolve("edit") == "edit_moss" and s.for_model(MOSS).resolve("t2i") is None
    assert s.for_model("qwen21").resolve("edit") == "edit"
    st = Stub()
    assert [x.workflow for x in Router(s.for_model(MOSS), st).steps_from_route("recipe__dois_modelos")] == ["edit_moss"]
    assert [x.workflow for x in Router(s.for_model("qwen21"), st).steps_from_route("recipe__dois_modelos")] == ["edit"]
    assert Router(s.for_model(MOSS), st).steps_from_route("edit")[0].workflow == "edit_moss"      # forçar `edit` também


def test_prompts_are_filtered_by_tag_and_shared_ones_come_from_the_implemented_workflow(project):
    s = with_moss(project)
    got = s.for_model(MOSS).prompts("edit_moss")
    assert [p["id"] for p in got["presets"]] == ["so_moss", "livre"] and got["default"] == "so_moss"
    qwen = [p["id"] for p in s.for_model("qwen21").prompts("edit")["presets"]]
    assert "livre" in qwen and "so_moss" not in qwen and len(qwen) == len(s.prompts("edit")["presets"])


def test_the_tags_survive_saving(project):
    s = Store(project)
    assert norm_lora({"id": "x", "file": "x.safetensors", "models": [MOSS]})["models"] == [MOSS]
    assert "models" not in norm_lora({"id": "x", "file": "x.safetensors"})
    assert norm_preset({"id": "x", "models": ["qwen21", MOSS]})["models"] == ["qwen21", MOSS]
    rec = s.normalize_recipe({"id": "r", "steps": [{"workflow": "edit"}], "models": [MOSS]})
    assert rec["models"] == [MOSS]
    assert "models" not in s.normalize_recipe({"id": "r", "steps": [{"workflow": "edit"}]})


# --------------------------------------------------------------- a lista do chat
OI = {"UNETLoader": {"input": {"required": {"unet_name": [["qwen_image_2.1_fp8.safetensors", "sub/MOSS-Image_v1.safetensors"]]}}}}


def test_model_files_reads_the_comfyui_loaders_and_the_folder(tmp_path):
    assert families.model_files(OI, None) == {"qwen_image_2.1_fp8.safetensors", "moss-image_v1.safetensors"}
    (tmp_path / "models" / "checkpoints").mkdir(parents=True)
    (tmp_path / "models" / "checkpoints" / "Outro.safetensors").write_bytes(b"x")
    assert "outro.safetensors" in families.model_files(None, tmp_path)
    assert families.model_files(None, None) is None and families.model_files({}, tmp_path / "vazia") is None


def test_only_models_with_a_workflow_and_a_file_are_offered(project):
    plain = Store(project)
    files = families.model_files(OI, None)
    assert [m["id"] for m in families.available(plain, files)] == ["qwen21"]          # tem o arquivo, não tem workflow
    s = with_moss(project)
    got = families.available(s, files)
    assert [m["id"] for m in got] == ["qwen21", MOSS] and got[1]["workflows"] == 1 and got[0]["default"]
    assert [m["id"] for m in families.available(s, {"qwen_image_2.1_fp8.safetensors"})] == ["qwen21"]   # tem workflow, não tem o arquivo
    assert [m["id"] for m in families.available(s, None)] == ["qwen21"]               # não sabe o que está instalado


# ------------------------------------------------------------------------ a API
def test_api_lists_models_and_passes_the_choice_to_the_run(project, comfy, monkeypatch):
    Store(project).save_settings({"comfyui": {"url": comfy.url}, "clef": {"mode": "mock"}})
    with_moss(project)
    monkeypatch.setattr(families, "model_files", lambda oi, root: {"moss-image.safetensors"})
    app = create_app(project)
    sent = []
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        monkeypatch.setattr(c.app.state.pipe, "start", lambda *a: sent.append(a) or type("R", (), {"id": "r1"})())
        got = c.get("/api/models").json()
        assert got["default"] == "qwen21" and [m["id"] for m in got["models"]] == ["qwen21", MOSS]
        cid = c.post("/api/conversations").json()["id"]

        def send(**extra):
            return c.post(f"/api/conversations/{cid}/messages", json={"text": "oi", **extra})

        assert send(model=MOSS).status_code == 200 and sent[-1][5] == MOSS
        assert send().status_code == 200 and sent[-1][5] == "qwen21"                        # sem escolha: o padrão
        assert send(model="nao_existe").status_code == 200 and sent[-1][5] == "qwen21"      # desconhecido: o padrão
        msgs = c.get(f"/api/conversations/{cid}").json()["messages"]
        assert [m.get("model") for m in msgs if m["role"] == "user"] == [MOSS, None, None]


def test_api_without_the_model_file_offers_only_the_default_and_refuses_the_other(project, comfy, monkeypatch):
    Store(project).save_settings({"comfyui": {"url": comfy.url}, "clef": {"mode": "mock"}})
    with_moss(project)
    monkeypatch.setattr(families, "model_files", lambda oi, root: {"qwen_image_2.1_fp8.safetensors"})
    sent = []
    with TestClient(create_app(project), base_url="http://127.0.0.1:7860") as c:
        monkeypatch.setattr(c.app.state.pipe, "start", lambda *a: sent.append(a) or type("R", (), {"id": "r1"})())
        assert [m["id"] for m in c.get("/api/models").json()["models"]] == ["qwen21"]
        cid = c.post("/api/conversations").json()["id"]
        c.post(f"/api/conversations/{cid}/messages", json={"text": "oi", "model": MOSS})
        assert sent[-1][5] == "qwen21"


def test_a_turn_for_another_model_routes_only_through_its_workflows(project):
    s = with_moss(project)
    st = Stub()
    from test_router import Ctx, img
    plan = asyncio.run(Router(s.for_model(MOSS), st).plan(Ctx("x", [img(1)])))
    assert [x.workflow for x in plan.steps] == ["edit_moss"]
    crit = next(c for c in st.calls if "route" in c[1])[1]["route"]["criteria"]
    assert set(crit) == {"edit_moss", "recipe__dois_modelos"}                   # o Clef nem vê os fluxos do Qwen
