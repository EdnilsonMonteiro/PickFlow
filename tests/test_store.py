import json
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from pickflow import importer
from pickflow.app import create_app
from pickflow.store import Store, StoreError


def test_defaults_only_install_works_and_custom_starts_empty(project):
    s = Store(project)
    assert len(s.manifests()) == 17 and all(m["source"] == "default" for m in s.manifests().values())
    assert s.settings()["comfyui"]["url"].startswith("http://")
    assert not (project / "custom").exists() or not any((project / "custom").rglob("*.yaml"))


def test_custom_overrides_default_by_id_and_wins_for_files(project):
    s = Store(project)
    s.ensure_custom_dirs()
    m = yaml.safe_load((project / "defaults" / "registry" / "background_removal.yaml").read_text(encoding="utf-8"))
    m["title"] = "Meu BG"
    m["file"] = "meu.json"
    (project / "custom" / "registry" / "background_removal.yaml").write_text(yaml.dump(m), encoding="utf-8")
    (project / "custom" / "workflows" / "meu.json").write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    mf = s.manifest("background_removal")
    assert mf["title"] == "Meu BG" and mf["source"] == "custom"
    assert s.workflow_path("background_removal").name == "meu.json"
    assert s.workflow_path("replace_redbox").parent.name == "workflows" and s.workflow_path("replace_redbox").parent.parent.name == "defaults"


def test_modes_pick_their_own_file_and_outputs(project):
    s = Store(project)
    m = s.manifest("edit")
    assert m["mode"] == "turbo" and m["file"].endswith("Turbo.json") and [v[0] for v in s.variants("edit")] == ["turbo", "quality"]
    q = s.manifest("edit", "quality")
    assert q["file"].endswith("Qualidade.json") and "cfg" in q["knobs"] and "cfg" not in m["knobs"]
    assert s.workflow_path("edit", "quality").name == "PickFlow Editar - Qualidade.json"
    with pytest.raises(StoreError):
        s.manifest("edit", "nao_existe")
    # o modo padrão não vaza para os outros (o t2i do modo quality não tem a cadeia de LoRAs do turbo)
    assert s.manifest("t2i")["lora_chain"] and not s.manifest("t2i", "quality").get("lora_chain")
    # um registro seu pode trocar o arquivo de um só modo
    s.ensure_custom_dirs()
    raw = yaml.safe_load((project / "defaults" / "registry" / "edit.yaml").read_text(encoding="utf-8"))
    raw["modes"]["turbo"]["file"] = "meu.json"
    (project / "custom" / "registry" / "edit.yaml").write_text(yaml.dump(raw), encoding="utf-8")
    (project / "custom" / "workflows" / "meu.json").write_text("{}", encoding="utf-8")
    assert s.workflow_path("edit", "turbo").name == "meu.json" and s.workflow_path("edit", "quality").parent.parent.name == "defaults"


def test_workflow_file_cannot_escape_the_workflow_folders(project):
    s = Store(project)
    s.ensure_custom_dirs()
    (project / "secret.json").write_text("{}", encoding="utf-8")
    m = yaml.safe_load((project / "defaults" / "registry" / "background_removal.yaml").read_text(encoding="utf-8"))
    m.update(id="fuga", file="../../secret.json")
    (project / "custom" / "registry" / "fuga.yaml").write_text(yaml.dump(m), encoding="utf-8")
    with pytest.raises(StoreError):
        s.workflow_path("fuga")


def test_saving_identical_to_default_leaves_no_custom_file(project):
    s = Store(project)
    lib = s.prompts("edit")
    s.save_prompts("edit", lib)
    assert not s.prompts_customized("edit")
    cfg = s.clef_config()
    s.save_clef_config(cfg)
    assert not (project / "custom" / "clef_prompt.yaml").exists()
    cfg["workflows"]["edit"]["description"] = "outra"
    s.save_clef_config(cfg)
    mine = yaml.safe_load((project / "custom" / "clef_prompt.yaml").read_text(encoding="utf-8"))
    assert list(mine["workflows"]) == ["edit"] and "instructions" not in mine


def test_new_defaults_reach_users_who_only_edited_other_workflows(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["edit"]["description"] = "minha"
    s.save_clef_config(cfg)
    base = yaml.safe_load((project / "defaults" / "clef_prompt.yaml").read_text(encoding="utf-8"))
    base["workflows"]["t2i"]["description"] = "descrição nova do repositório"
    (project / "defaults" / "clef_prompt.yaml").write_text(yaml.dump(base, allow_unicode=True), encoding="utf-8")
    now = s.clef_config()["workflows"]
    assert now["t2i"]["description"] == "descrição nova do repositório" and now["edit"]["description"] == "minha"


def test_recipe_with_disabled_workflow_is_hidden(project):
    s = Store(project)
    assert [r["id"] for r in s.enabled_recipes()] == ["trocar_pessoa_e_fundo", "vestir_e_levar", "vestir_e_trocar",
                                                      "trocar_pessoa_roupa_e_fundo", "colar_e_fundir", "colar_sem_fundir", "pessoa_angulo",
                                                      "pessoa_angulo_troca", "mudar_pose", "expandir_imagem"]
    cfg = s.clef_config()
    cfg["workflows"]["transfer_object"]["enabled"] = False
    s.save_clef_config(cfg)
    assert "trocar_pessoa_e_fundo" not in [r["id"] for r in s.enabled_recipes()]
    # o caso novo depende de troca_roupa: desligando o fluxo, ele sai junto
    cfg = s.clef_config()
    cfg["workflows"]["troca_roupa"]["enabled"] = False
    s.save_clef_config(cfg)
    assert "vestir_e_levar" not in [r["id"] for r in s.enabled_recipes()]


# -------------------------------------------------------------------- importer
@pytest.fixture()
def api(project):
    app = create_app(project)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        yield c


def _copy_default_to_custom(project, name, dest=None):
    dst = project / "custom" / "workflows" / (dest or name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(project / "defaults" / "workflows" / name, dst)


def test_importer_finds_unregistered_files_and_builds_a_working_draft(api, project):
    _copy_default_to_custom(project, "PickFlow Editar - Qualidade.json", "Meu Edit.json")
    _copy_default_to_custom(project, "PickFlow Editar - Qualidade.json", "NSFW/Outro.json")
    listing = api.get("/api/importable").json()
    assert {x["file"] for x in listing} == {"Meu Edit.json", "NSFW/Outro.json"}
    assert [x["nsfw"] for x in listing if x["file"].startswith("NSFW")] == [True]
    d = api.get("/api/importable/draft", params={"file": "Meu Edit.json"}).json()
    assert d["id"] == "meu_edit" and [i["node"] for i in d["images"]][:2] == [26, 27]
    assert d["prompts"][0]["node"] == 6 and [o["node"] for o in d["outputs"]] == [25]
    r = api.post("/api/import", json={"file": "Meu Edit.json", "id": "meu_edit", "title": "Meu edit",
                                      "images": [{"node": 26, "label": "Foto"}], "prompt": {"node": 6, "widget": "value"},
                                      "outputs": [25]})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["source"] == "custom" and view["id"] == "meu_edit" and view["presets"][0]["text"] == "{request}"
    assert "Meu Edit.json" not in {x["file"] for x in api.get("/api/importable").json()}
    # entra na validação do doctor e some com o "remover"
    assert api.delete("/api/workflows/meu_edit").json() == {"result": "removed"}
    assert not any(w["id"] == "meu_edit" for w in api.get("/api/workflows").json())
    assert (project / "custom" / "workflows" / "Meu Edit.json").exists()          # o arquivo é seu: não apagamos


def test_import_rejects_bad_input(api, project):
    _copy_default_to_custom(project, "PickFlow Editar - Qualidade.json", "x.json")
    ok = {"file": "x.json", "id": "x1", "outputs": [25]}
    assert api.post("/api/import", json={**ok, "id": "Inválido"}).status_code == 422
    assert api.post("/api/import", json={**ok, "id": "edit"}).status_code == 422       # já existe
    assert api.post("/api/import", json={**ok, "file": "../defaults/clef_prompt.yaml"}).status_code == 422
    assert api.post("/api/import", json={**ok, "outputs": []}).status_code == 422
    assert api.get("/api/importable/draft", params={"file": "nao_existe.json"}).status_code == 422


def test_other_sites_and_odd_hosts_are_refused(api):
    assert api.get("/api/status").status_code == 200
    r = api.post("/api/conversations", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = api.get("/api/workflows", headers={"Host": "evil.example"})
    assert r.status_code == 403
    ok = api.post("/api/conversations", headers={"Origin": "http://127.0.0.1:7860"})
    assert ok.status_code == 200


def test_files_endpoint_rejects_wildcards_and_traversal(api):
    for bad in ("*", "..%2f..%2fsecret", "a*", "%5Bab%5D"):
        assert api.get(f"/files/{bad}").status_code == 404
