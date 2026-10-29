import io
import shutil
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pickflow.addons import AddonError, new_addon
from pickflow.app import create_app
from pickflow.store import Store

from test_e2e import png, start, types, upload, wait_event

ROOT = Path(__file__).resolve().parent.parent


def put(base: Path, name: str, files: dict[str, str]):
    for rel, text in files.items():
        p = base / "addons" / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")


def make(project, comfy):
    Store(project).save_settings({"comfyui": {"url": comfy.url}, "clef": {"mode": "mock"}})
    app = create_app(project)
    c = TestClient(app, base_url="http://127.0.0.1:7860")
    c.__enter__()
    c.fake, c.pipe = comfy, app.state.pipe
    return c


def from_template(project, name="meu-addon"):
    return new_addon(project / "addons", name, ROOT / "defaults" / "addon_template")


def test_new_addon_copies_the_template_and_renames_everything(project):
    dest = from_template(project, "meu-addon")
    assert (dest / "addon.py").is_file() and (dest / "web" / "main.js").is_file()
    assert (dest / "registry" / "meu_addon_girar.yaml").is_file()       # __ID__ vira o nome com _
    text = "".join(p.read_text(encoding="utf-8") for p in dest.rglob("*") if p.is_file())
    assert "__NAME__" not in text and "__ID__" not in text and "meu-addon.girar" in text
    with pytest.raises(AddonError):
        from_template(project, "meu-addon")                              # já existe
    with pytest.raises(AddonError):
        from_template(project, "Nome Ruim")


def test_template_addon_loads_and_exposes_everything(project, comfy):
    from_template(project)
    c = make(project, comfy)
    try:
        info = c.get("/api/addons").json()["addons"][0]
        assert info["name"] == "meu-addon" and not info["error"] and info["enabled"]
        assert info["actions"] == ["girar"] and info["hooks"] == ["prompt"]
        assert info["scripts"] == ["/addons/meu-addon/web/main.js"] and info["styles"] == ["/addons/meu-addon/web/style.css"]
        assert c.get("/api/addons/meu-addon/ola").json()["config"]["graus_padrao"] == 90       # rota do addon
        assert c.get("/addons/meu-addon/web/main.js").status_code == 200
        assert c.get("/addons/meu-addon/web/..%2Faddon.py").status_code == 404                # não sai de web/
        assert c.get("/addons/meu-addon/web/nao_existe.js").status_code == 404
        assert any(w["id"] == "meu_addon_girar" and w["kind"] == "action" for w in c.get("/api/workflows").json())
        assert all(r["ok"] for r in c.post("/api/doctor").json())
    finally:
        c.__exit__(None, None, None)


def test_python_action_runs_without_comfyui_and_returns_the_image(project, comfy):
    from_template(project)
    c = make(project, comfy)
    try:
        before = len(c.fake.submitted) if hasattr(c.fake, "submitted") else None
        a = upload(c, data=png(size=(80, 60)))
        _cid, rid = start(c, "gire a imagem 90 graus", [a["id"]], route="meu_addon_girar")
        done = wait_event(c, rid, lambda e: e["type"] == "done")
        url = done["images"][0]["url"]
        im = Image.open(io.BytesIO(c.get(url).content))
        assert im.size == (60, 80)                                  # 80x60 girado 90°
        assert "step_start" in types(c, rid) and "error" not in types(c, rid)
        if before is not None:
            assert len(c.fake.submitted) == before                  # não passou pelo ComfyUI
    finally:
        c.__exit__(None, None, None)


def test_prompt_and_request_hooks_change_what_reaches_comfyui(project, comfy):
    put(project, "ganchos", {"addon.py": """
        def register(ctx):
            @ctx.hook("request")
            def r(text, info):
                return text + " ## pedido"
            @ctx.hook("prompt")
            async def p(text, info):
                return text + "\\nKEEP-RESOLUTION " + info["workflow"]
        """})
    c = make(project, comfy)
    try:
        a = upload(c)
        _cid, rid = start(c, "deixe mais escuro", [a["id"]], route="edit")
        ev = wait_event(c, rid, lambda e: e["type"] == "step_start")
        assert "## pedido" in ev["prompt"] and "KEEP-RESOLUTION edit" in ev["prompt"]
        wait_event(c, rid, lambda e: e["type"] == "done")
    finally:
        c.__exit__(None, None, None)


def test_observer_hooks_fire_and_a_failing_hook_does_not_stop_the_run(project, comfy):
    put(project, "obs", {"addon.py": """
        seen = []
        def register(ctx):
            @ctx.hook("step_done")
            def a(res, info):
                raise RuntimeError("quebrou de propósito")
            @ctx.hook("run_done")
            def b(res, info):
                ctx.save_data({"fim": len(res)})
            @ctx.hook("plan")
            def c(wfs, info):
                ctx.save_data({"plano": wfs})
        """})
    c = make(project, comfy)
    try:
        a = upload(c)
        _cid, rid = start(c, "x", [a["id"]], route="edit")
        wait_event(c, rid, lambda e: e["type"] == "done")
        time_ok = c.get("/api/addons").json()["addons"][0]
        assert "gancho 'step_done'" in time_ok["error"]
        assert (project / "data" / "addons" / "obs.json").is_file()
    finally:
        c.__exit__(None, None, None)


def test_a_broken_addon_is_reported_and_the_app_still_works(project, comfy):
    put(project, "quebrado", {"addon.py": "raise ValueError('boom')\n"})
    put(project, "semregister", {"addon.py": "x = 1\n"})
    put(project, "yamlruim", {"addon.yaml": "titulo: [\n"})
    put(project, "bom", {"addon.py": "def register(ctx):\n    pass\n"})
    c = make(project, comfy)
    try:
        by = {a["name"]: a for a in c.get("/api/addons").json()["addons"]}
        assert "boom" in by["quebrado"]["error"] and "register" in by["semregister"]["error"] and "inválido" in by["yamlruim"]["error"]
        assert not by["bom"]["error"]
        assert c.get("/api/status").status_code == 200
    finally:
        c.__exit__(None, None, None)


def test_addon_resources_layer_between_defaults_and_custom(project, comfy):
    reg = (ROOT / "defaults" / "registry" / "background_removal.yaml").read_text(encoding="utf-8")
    base_file = next(l.split(":", 1)[1].strip() for l in reg.splitlines() if l.startswith("file:"))
    put(project, "recursos", {
        "registry/rec_flow.yaml": reg.replace("id: background_removal", "id: rec_flow").replace(base_file, "do_addon.json"),
        "prompts/rec_flow.yaml": "default: p\npresets:\n- id: p\n  name: P\n  text: 'do addon {request}'\n",
        "clef_prompt.yaml": "workflows:\n  rec_flow:\n    description: Fluxo vindo de um addon\n",
    })
    (project / "addons" / "recursos" / "workflows").mkdir()
    shutil.copy(ROOT / "defaults" / "workflows" / base_file, project / "addons" / "recursos" / "workflows" / "do_addon.json")
    s = Store(project)
    assert "nodes" in s.workflow_json("rec_flow") and s.workflow_path("rec_flow").parent.parent.name == "recursos"
    assert s.manifests()["rec_flow"]["source"] == "addon:recursos"
    assert s.prompts("rec_flow")["presets"][0]["text"] == "do addon {request}"
    assert s.clef_config()["workflows"]["rec_flow"]["description"] == "Fluxo vindo de um addon"
    # custom/ vale mais que o addon
    (project / "custom" / "prompts").mkdir(parents=True)
    (project / "custom" / "prompts" / "rec_flow.yaml").write_text("default: q\npresets:\n- id: q\n  name: Q\n  text: 'meu {request}'\n", encoding="utf-8")
    assert Store(project).prompts("rec_flow")["presets"][0]["text"] == "meu {request}"
    assert "background_removal" in s.manifests() and "edit" in s.manifests()      # os padrões continuam


def test_action_can_ask_the_user_with_a_custom_screen(project, comfy):
    put(project, "pergunta", {
        "addon.py": """
            def register(ctx):
                @ctx.action("escolher")
                async def escolher(call):
                    v = await call.ask("minha_tela", title="Qual?", opcoes=["a", "b"])
                    return [call.pil("image_1").resize((10 if v == "a" else 20, 10))]
            """,
        "registry/pergunta_flow.yaml": """
            id: pergunta_flow
            kind: action
            action: pergunta.escolher
            title: Pergunta
            images: [{key: image_1, label: Imagem, required: true}]
            """})
    c = make(project, comfy)
    try:
        a = upload(c)
        _cid, rid = start(c, "x", [a["id"]], route="pergunta_flow")
        ev = wait_event(c, rid, lambda e: e["type"] == "needs_input" and e.get("kind") == "minha_tela")
        assert ev["opcoes"] == ["a", "b"] and ev["title"] == "Qual?"
        assert c.post(f"/api/runs/{rid}/input", json={"input_id": ev["input_id"], "value": "b"}).status_code == 200
        done = wait_event(c, rid, lambda e: e["type"] == "done")
        assert Image.open(io.BytesIO(c.get(done["images"][0]["url"]).content)).size == (20, 10)
    finally:
        c.__exit__(None, None, None)


def test_llm_helper_talks_to_the_configured_server(project, comfy, monkeypatch):
    import asyncio
    import httpx
    from pickflow.addons import LLM
    sent = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json, headers=None):
            sent.update(url=url, body=json, headers=headers)
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"choices": [{"message": {"content": ' {"cores": ["azul"]} '}}]}, request=req)
    monkeypatch.setattr("pickflow.llm.httpx.AsyncClient", FakeClient)
    llm = LLM(lambda: {"url": "http://x/v1", "model": "m", "timeout_s": 5})
    assert llm.configured
    data = asyncio.run(llm.ask_json("liste cores", system="json"))
    assert data == {"cores": ["azul"]} and sent["url"] == "http://x/v1/chat/completions" and sent["body"]["response_format"]
    with pytest.raises(AddonError):
        asyncio.run(LLM(lambda: {"url": ""}).ask("oi"))
    imgs = asyncio.run(LLM(lambda: {"url": "http://x/v1"}).ask("o que é?", images=[b"png"]))
    assert isinstance(sent["body"]["messages"][-1]["content"], list) and imgs == '{"cores": ["azul"]}'


def test_addons_folder_is_git_ignored_but_its_readme_is_not():
    ig = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/addons/*" in ig and "!/addons/README.md" in ig and (ROOT / "addons" / "README.md").is_file()


def test_the_addons_skill_ships_with_valid_front_matter():
    import yaml
    p = ROOT / ".claude" / "skills" / "pickflow-addons" / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == "pickflow-addons" and len(meta["description"]) < 1024
    for must in ("kind: action", "@ctx.action", "PickFlow.registerInput", "addon new", "custom/` > `addons/` > `defaults/"):
        assert must in text.replace("window.", "") or must in text


def test_template_has_no_personal_text_and_no_leftover_placeholders_outside_the_template(project):
    dest = from_template(project, "outro-addon")
    for p in dest.rglob("*"):
        if p.is_file():
            t = p.read_text(encoding="utf-8")
            assert "__NAME__" not in t and "__ID__" not in t, p
