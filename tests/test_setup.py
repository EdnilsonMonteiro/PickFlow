import hashlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from fastapi.testclient import TestClient

from pickflow.app import create_app
from pickflow.setup import catalog
from pickflow.store import Store

A, B, C = b"a" * 300_000, bytes(range(256)) * 2000, b"corrompido" * 1000
SHA_B = hashlib.sha256(B).hexdigest()


@pytest.fixture()
def files_server():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", headers=None, head=False):
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _route(self, head):
            p = self.path
            if p == "/a.safetensors":
                return self._send(200, A, head=head)
            if p == "/hf/b.safetensors":      # como o Hugging Face: 302 com tamanho e sha256 do arquivo
                return self._send(302, b"", {"Location": "/cdn/b.safetensors", "x-linked-size": str(len(B)),
                                             "x-linked-etag": f'"{SHA_B}"'}, head=head)
            if p == "/hf/c.safetensors":      # o sha anunciado não é o do conteúdo
                return self._send(302, b"", {"Location": "/cdn/c.safetensors", "x-linked-size": str(len(C)),
                                             "x-linked-etag": f'"{SHA_B}"'}, head=head)
            if p == "/cdn/b.safetensors":
                return self._send(200, B, head=head)
            if p == "/cdn/c.safetensors":
                return self._send(200, C, head=head)
            if p == "/gated.safetensors":
                return self._send(401, b"", head=head)
            return self._send(404, b"", head=head)

        def do_GET(self):
            self._route(False)

        def do_HEAD(self):
            self._route(True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def entry(i, name, url, **kw):
    return {"id": i, "title": i.upper(), "file": name, "folder": "loras", "url": url, "license": "MIT", **kw}


@pytest.fixture()
def env(project, comfy, files_server, tmp_path):
    root = tmp_path / "ComfyUI"
    (root / "custom_nodes").mkdir(parents=True)
    (root / "models" / "loras").mkdir(parents=True)
    s = Store(project)
    s.save_settings({"comfyui": {"url": comfy.url, "path": str(root)}, "clef": {"mode": "mock"}})
    (project / "custom" / "models.yaml").write_text(yaml.dump({"files": [
        entry("t_a", "t_a.safetensors", files_server + "/a.safetensors"),
        entry("t_b", "t_b.safetensors", files_server + "/hf/b.safetensors"),
        entry("t_c", "t_c.safetensors", files_server + "/hf/c.safetensors"),
        entry("t_gone", "t_gone.safetensors", files_server + "/nada.safetensors"),
        entry("t_gated", "t_gated.safetensors", files_server + "/gated.safetensors"),
    ]}), encoding="utf-8")
    app = create_app(project)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        c.root, c.fake, c.project = root, comfy, project
        yield c


def wait_idle(c, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get("/api/setup/jobs").json()
        if not j["busy"]:
            return {x["id"]: x for x in j["jobs"]}
        time.sleep(0.05)
    raise AssertionError("downloads não terminaram")


def item(c, i):
    return next(f for f in c.get("/api/setup?refresh=true").json()["files"] if f["id"] == i)


# ------------------------------------------------------------------ catálogo
def test_default_catalog_is_valid_sfw_and_covers_every_model_the_workflows_load(project, oi):
    store = Store(project)
    cat = catalog(store)
    assert len(cat) == 22 and {e["group"] for e in cat} == {"lora", "model"}
    assert all(e["url"].startswith("https://huggingface.co/") and e["license"] and e["page"] for e in cat)
    from privacy import pattern
    assert not pattern().search((project / "defaults" / "models.yaml").read_text(encoding="utf-8"))
    from pickflow.setup import used_by
    needed = {f for f in used_by(store) if f.endswith(".safetensors")}
    # tudo o que o PickFlow carrega (fora o codificador de prompt do t2i, que ele não usa) está no catálogo
    # (a LoRA de foto caseira só existe no tensor.art: não tem link direto e o assistente avisa que é manual)
    assert needed - {e["file"].lower() for e in cat} <= {"qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
                                                         "casual_snapshot_realism_qwen21.safetensors"}


def test_catalog_ignores_unsafe_entries(project):
    bad = [entry("x1", "../fora.safetensors", "https://huggingface.co/a"),
           entry("x2", "ok.safetensors", "http://example.com/ok.safetensors"),       # http fora do localhost
           entry("x3", "ok.safetensors", "file:///etc/passwd"),
           {**entry("x4", "ok.safetensors", "https://huggingface.co/a"), "folder": "../../etc"},
           entry("X5", "ok.safetensors", "https://huggingface.co/a"),
           entry("x6", "pasta/ok.safetensors", "https://huggingface.co/a"),
           entry("x7", "ok.ckpt", "https://huggingface.co/a"),
           entry("good", "ok.safetensors", "https://huggingface.co/a")]
    (project / "custom").mkdir(exist_ok=True)
    (project / "custom" / "models.yaml").write_text(yaml.dump({"files": bad}), encoding="utf-8")
    ids = {e["id"] for e in catalog(Store(project))}
    assert "good" in ids and not ids & {"x1", "x2", "x3", "x4", "X5", "x6", "x7"}


# ------------------------------------------------------------- achar o ComfyUI
def test_overview_finds_the_folder_and_marks_missing_files(env):
    o = env.get("/api/setup").json()
    assert o["can_install"] and o["comfy"]["ok"] and o["comfy"]["local"] and o["comfy"]["root_source"] == "ajustes"
    assert item(env, "t_a")["installed"] is False
    (env.root / "models" / "loras" / "t_a.safetensors").write_bytes(b"x")
    assert item(env, "t_a")["installed"] is True


def test_set_path_validates_and_accepts_a_subfolder(env, tmp_path):
    assert env.put("/api/setup/path", json={"path": str(tmp_path / "nada")}).status_code == 422
    assert env.put("/api/setup/path", json={"path": str(env.root / "custom_nodes")}).json()["path"] == str(env.root)
    assert env.get("/api/settings").json()["comfyui"]["path"] == str(env.root)


def test_remote_comfyui_is_never_installed_into(env):
    env.put("/api/settings", json={"comfyui": {"url": "http://comfy.invalid:8188"}})
    o = env.get("/api/setup").json()
    assert o["comfy"]["local"] is False and not o["can_install"]


# ------------------------------------------------------------------ downloads
def test_download_verifies_checksum_and_leaves_no_partial_file(env):
    r = env.post("/api/setup/install", json={"ids": ["t_a", "t_b"], "nodes": False}).json()
    assert r["started"] == ["t_a", "t_b"]
    jobs = wait_idle(env)
    assert jobs["t_a"]["state"] == "pronto" and jobs["t_b"]["state"] == "pronto", jobs
    d = env.root / "models" / "loras"
    assert (d / "t_a.safetensors").read_bytes() == A and (d / "t_b.safetensors").read_bytes() == B
    assert not list(d.glob("*.part")) and jobs["t_b"]["done"] == len(B) == jobs["t_b"]["total"]
    assert item(env, "t_b")["installed"] is True


def test_corrupt_missing_and_gated_downloads_fail_cleanly(env):
    env.post("/api/setup/install", json={"ids": ["t_c", "t_gone", "t_gated"]})
    jobs = wait_idle(env)
    assert jobs["t_c"]["state"] == "erro" and "corrompido" in jobs["t_c"]["error"]
    assert jobs["t_gone"]["state"] == "erro" and "não encontrado" in jobs["t_gone"]["error"]
    assert jobs["t_gated"]["state"] == "erro" and "login" in jobs["t_gated"]["error"]
    d = env.root / "models" / "loras"
    assert not list(d.iterdir())


def test_installed_files_are_not_downloaded_again_and_unknown_ids_are_refused(env):
    (env.root / "models" / "loras" / "t_a.safetensors").write_bytes(b"meu arquivo")
    r = env.post("/api/setup/install", json={"ids": ["t_a"]}).json()
    assert r["started"] == [] and r["skipped"] == ["t_a"]
    assert (env.root / "models" / "loras" / "t_a.safetensors").read_bytes() == b"meu arquivo"
    bad = env.post("/api/setup/install", json={"ids": ["../../x"]})
    assert bad.status_code == 422


def test_client_cannot_choose_url_or_destination(env):
    r = env.post("/api/setup/install", json={"ids": ["t_a"], "url": "http://evil.example/x", "file": "../../x.safetensors"})
    assert r.status_code == 200
    wait_idle(env)
    assert [p.name for p in (env.root / "models" / "loras").iterdir()] == ["t_a.safetensors"]


# ----------------------------------------------------------------------- nós
def test_nodes_are_copied_and_a_restart_is_requested(env):
    for cls in ("DrawBox", "ViggleTurboLora"):
        env.fake.oi.pop(cls, None)           # como um ComfyUI que ainda não carregou os nós
    o = env.get("/api/setup?refresh=true").json()
    assert o["nodes"]["missing"] == 2 and not o["nodes"]["restart_needed"]
    r = env.post("/api/setup/install", json={"ids": [], "nodes": True}).json()
    assert set(r["nodes"].values()) == {"instalado"}
    assert (env.root / "custom_nodes" / "qwen_object_mover" / "__init__.py").is_file()
    assert (env.root / "custom_nodes" / "viggle_turbo.py").is_file()
    o = env.get("/api/setup?refresh=true").json()
    assert o["nodes"]["restart_needed"] and o["nodes"]["missing"] == 0


def test_modified_node_files_are_kept_unless_the_user_says_so(env):
    env.post("/api/setup/install", json={"ids": [], "nodes": True})
    mine = env.root / "custom_nodes" / "viggle_turbo.py"
    mine.write_text("# editado por mim", encoding="utf-8")
    env.post("/api/setup/install", json={"ids": [], "nodes": True})
    assert mine.read_text(encoding="utf-8") == "# editado por mim"
    env.post("/api/setup/install", json={"ids": [], "nodes": True, "overwrite_nodes": True})
    assert mine.read_text(encoding="utf-8") != "# editado por mim"


# ------------------------------------------------------------------- status
def test_status_carries_a_cheap_setup_summary_and_dismiss_sticks(env):
    s = env.get("/api/status").json()["setup"]
    assert s["loras"] >= 0 and s["total"] == s["nodes"] + s["loras"] + s["models"] and s["dismissed"] is False
    env.post("/api/setup/dismiss", json={"value": True})
    assert env.get("/api/status").json()["setup"]["dismissed"] is True
    assert env.get("/api/settings").json()["setup"]["dismissed"] is True


