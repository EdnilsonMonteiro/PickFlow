"""Provedores e modelos do redator, chaves de API no .env, erros claros e o redator na fusão que completa o corpo."""
import asyncio
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pickflow import llm
from test_e2e import api, png  # noqa: F401  (fixture)
from test_verify import _fuse_prompt
from test_splat import Stub


class FakeLLM:
    """Servidor de chat compatível com a OpenAI: confere a chave e guarda o que recebeu."""

    def __init__(self, key="sk-teste", reply="PROMPT DO REDATOR", reject_max_tokens=False):
        self.key, self.reply, self.reject_max_tokens = key, reply, reject_max_tokens
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                if outer.key and self.headers.get("Authorization") != f"Bearer {outer.key}":
                    return self._send(401, {"error": {"message": "Incorrect API key"}})
                if outer.reject_max_tokens and "max_tokens" in body:
                    return self._send(400, {"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
                self._send(200, {"choices": [{"message": {"content": outer.reply}}]})

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture()
def fake():
    s = FakeLLM()
    yield s
    s.stop()


@pytest.fixture(autouse=True)
def _clean_env():
    before = {k for k in os.environ if k.startswith("LLM_")}
    yield
    for k in [k for k in os.environ if k.startswith("LLM_") and k not in before]:
        os.environ.pop(k, None)


def prov(pid, url, models=None, key_env=None, name=None):
    return {"id": pid, "name": name or pid.title(), "url": url, "key_env": key_env or llm.default_key_env(pid),
            "models": models if models is not None else [{"id": "m1", "vision": True}, {"id": "m2", "vision": False}]}


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ configuração
def test_resolve_picks_provider_model_and_reads_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY_A", "ka")
    monkeypatch.setenv("LLM_API_KEY_B", "kb")
    w = {"providers": [prov("a", "http://a/v1"), prov("b", "http://b/v1", [{"id": "x", "vision": False}, {"id": "y", "vision": True}])],
         "active": {"provider": "b", "model": "y"}}
    c = llm.resolve(w)
    assert (c["provider"], c["model"], c["url"], c["api_key"], c["vision"]) == ("b", "y", "http://b/v1", "kb", True)
    c = llm.resolve(w, "a")                                   # outro provedor: o primeiro modelo dele
    assert (c["url"], c["model"], c["api_key"], c["vision"]) == ("http://a/v1", "m1", "ka", True)
    assert llm.resolve(w, "b", "x")["vision"] is False
    assert llm.resolve({"providers": [prov("a", "http://a/v1")], "active": {"provider": "sumiu", "model": "?"}})["provider"] == "a"


# ------------------------------------------------------------------ chamada
def test_the_api_key_goes_as_a_bearer_token_and_a_wrong_one_explains_what_to_do(fake, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY_TESTE", "sk-teste")
    w = {"providers": [prov("teste", fake.url)], "active": {"provider": "teste", "model": "m1"}}
    assert run(llm.chat(llm.resolve(w), [{"role": "user", "content": "oi"}])) == "PROMPT DO REDATOR"
    assert fake.requests[-1]["auth"] == "Bearer sk-teste" and fake.requests[-1]["body"]["model"] == "m1"
    monkeypatch.setenv("LLM_API_KEY_TESTE", "errada")
    with pytest.raises(llm.LLMError) as e:
        run(llm.chat(llm.resolve(w), [{"role": "user", "content": "oi"}]))
    assert "401" in str(e.value) and "LLM_API_KEY_TESTE" in str(e.value) and ".env" in str(e.value)
    monkeypatch.delenv("LLM_API_KEY_TESTE")
    with pytest.raises(llm.LLMError, match="Nenhuma chave de API"):
        run(llm.chat(llm.resolve(w), [{"role": "user", "content": "oi"}]))


def test_models_that_reject_max_tokens_are_retried_with_max_completion_tokens(monkeypatch):
    s = FakeLLM(reject_max_tokens=True)
    try:
        monkeypatch.setenv("LLM_API_KEY_TESTE", "sk-teste")
        c = llm.resolve({"providers": [prov("teste", s.url)], "active": {"provider": "teste", "model": "m1"}})
        assert run(llm.chat(c, [{"role": "user", "content": "oi"}], max_tokens=50)) == "PROMPT DO REDATOR"
        assert "max_tokens" in s.requests[0]["body"] and "max_completion_tokens" in s.requests[1]["body"] and "max_tokens" not in s.requests[1]["body"]
    finally:
        s.stop()


# ------------------------------------------------------------------ chaves no .env
def test_the_key_is_written_to_env_never_to_settings_and_never_sent_back(api, project):
    r = api.put("/api/env", json={"name": "LLM_API_KEY_TESTE", "value": "sk-segredo-123"})
    assert r.status_code == 200 and r.json()["set"] is True
    env = (project / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY_TESTE=sk-segredo-123" in env and os.environ["LLM_API_KEY_TESTE"] == "sk-segredo-123"
    api.put("/api/settings", json={"writer": {"providers": [prov("teste", "http://x/v1")], "active": {"provider": "teste", "model": "m1"}}})
    info = api.get("/api/llm")
    assert info.status_code == 200 and "sk-segredo-123" not in info.text
    assert info.json()["providers"][0]["key_set"] is True and info.json()["active"] == {"provider": "teste", "model": "m1"}
    assert "sk-segredo-123" not in api.get("/api/settings").text
    assert "sk-segredo" not in (project / "custom" / "settings.yaml").read_text(encoding="utf-8")
    api.put("/api/env", json={"name": "LLM_API_KEY_OUTRA", "value": "k2"})                 # as outras linhas ficam
    api.put("/api/env", json={"name": "LLM_API_KEY_TESTE", "value": "novo"})               # troca a linha, sem duplicar
    env = (project / ".env").read_text(encoding="utf-8")
    assert env.count("LLM_API_KEY_TESTE") == 1 and "LLM_API_KEY_OUTRA=k2" in env and "LLM_API_KEY_TESTE=novo" in env
    assert api.put("/api/env", json={"name": "LLM_API_KEY_TESTE", "value": ""}).json()["set"] is False
    assert "LLM_API_KEY_TESTE" not in (project / ".env").read_text(encoding="utf-8") and "LLM_API_KEY_TESTE" not in os.environ


@pytest.mark.parametrize("name", ["PATH", "lower", "LLM_", "LLM_A B", "HF_TOKEN\nX=1", "../x"])
def test_only_hf_token_and_llm_variables_can_be_written(api, name):
    assert api.put("/api/env", json={"name": name, "value": "x"}).status_code == 400


def test_several_providers_and_models_are_saved_and_the_active_one_is_used(api, fake):
    other = FakeLLM(key="", reply="OUTRO")
    try:
        api.put("/api/env", json={"name": "LLM_API_KEY_UM", "value": "sk-teste"})
        api.put("/api/settings", json={"writer": {"enabled": True, "providers": [
            prov("um", fake.url), prov("dois", other.url, [{"id": "z1", "vision": False}])], "active": {"provider": "um", "model": "m2"}}})
        w = api.get("/api/settings").json()["writer"]
        assert [p["id"] for p in w["providers"]] == ["um", "dois"] and [m["id"] for m in w["providers"][0]["models"]] == ["m1", "m2"]
        assert run(api.pipe.llm.ask("oi")) == "PROMPT DO REDATOR" and fake.requests[-1]["body"]["model"] == "m2"     # o LLM dos addons e do agente
        api.put("/api/settings", json={"writer": {"active": {"provider": "dois", "model": "z1"}}})
        assert run(api.pipe.llm.ask("oi")) == "OUTRO" and other.requests[-1]["body"]["model"] == "z1"
    finally:
        other.stop()


def test_the_test_button_reports_success_and_the_reason_for_failure(api, fake):
    api.put("/api/env", json={"name": "LLM_API_KEY_TESTE", "value": "sk-teste"})
    ok = api.post("/api/llm/test", json={"provider": prov("teste", fake.url), "model": "m1"}).json()
    assert ok["ok"] and ok["text"] == "PROMPT DO REDATOR"
    api.put("/api/env", json={"name": "LLM_API_KEY_TESTE", "value": "errada"})
    bad = api.post("/api/llm/test", json={"provider": prov("teste", fake.url), "model": "m1"}).json()
    assert not bad["ok"] and "401" in bad["error"] and "LLM_API_KEY_TESTE" in bad["error"]


# ------------------------------------------------------------------ redator na fusão
def _enable_writer(api, fake, vision=True, key="sk-teste"):
    api.put("/api/env", json={"name": "LLM_API_KEY_TESTE", "value": key})
    api.put("/api/settings", json={"writer": {"enabled": True, "mode": "refine", "providers": [prov("teste", fake.url)],
                                              "active": {"provider": "teste", "model": "m1" if vision else "m2"}}})


def _completar(monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"role_background": "user_1", "role_subject": "user_2", "var_corpo": "completar"}))


def test_the_writer_refines_the_body_completion_prompt_and_sees_the_collage(api, fake, monkeypatch):
    _completar(monkeypatch)
    _enable_writer(api, fake)
    text, rid = _fuse_prompt(api, "completar", "Coloque o coringa da imagem 2 no lugar do cara de vermelho da imagem 1")
    assert text == "PROMPT DO REDATOR"                                         # o texto do redator vai ao workflow
    req = fake.requests[-1]
    assert req["auth"] == "Bearer sk-teste" and req["body"]["model"] == "m1"
    content = req["body"]["messages"][1]["content"]
    assert isinstance(content, list) and content[0]["type"] == "text" and content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert len([c for c in content if c["type"] == "image_url"]) == 1             # só a colagem
    base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
    t = content[0]["text"]
    assert "pysj666 seamless blending" in t and "lugar do cara de vermelho" in t
    assert "ABOUT THIS STEP" in t and "removed completely" in t and "IMAGES OF THIS STEP: 1" in t
    assert any("Redator: Teste / m1" in (e.get("text") or "") for e in api.pipe.runs[rid].events if e["type"] == "status")


def test_a_model_that_does_not_see_images_gets_only_text(api, fake, monkeypatch):
    _completar(monkeypatch)
    _enable_writer(api, fake, vision=False)
    _fuse_prompt(api, "completar")
    content = fake.requests[-1]["body"]["messages"][1]["content"]
    assert isinstance(content, str) and "IMAGES OF THIS STEP: 1" in content and "attached below" not in content


def test_when_the_writer_fails_the_prompt_is_kept_and_the_reason_is_shown(api, fake, monkeypatch):
    _completar(monkeypatch)
    _enable_writer(api, fake, key="chave-errada")                    # o servidor espera outra
    text, rid = _fuse_prompt(api, "completar")
    assert text.startswith("pysj666 seamless blending") and "PROMPT DO REDATOR" not in text
    msgs = [e["text"] for e in api.pipe.runs[rid].events if e["type"] == "status" and "redator" in e["text"].lower()]
    assert any("401" in m and "LLM_API_KEY_TESTE" in m for m in msgs)
