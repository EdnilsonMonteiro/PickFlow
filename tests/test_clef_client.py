"""Contrato HTTP do ClefClient contra um servidor System One falso."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pickflow.clef import ClefClient, ClefError, choice_question
from PIL import Image
import io


def png(color=(200, 30, 30)):
    b = io.BytesIO()
    Image.new("RGB", (900, 600), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def server():
    seen = {}
    mode = {"status": 200, "answers": None}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok": true, "loaded": true}')

        def do_POST(self):
            if self.path == "/v1/unload":
                seen["unloaded"] = seen.get("unloaded", 0) + 1
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}"); return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["path"], seen["body"] = self.path, body
            seen.setdefault("bodies", []).append(body)  # uma entrada por requisição
            if mode["status"] != 200:
                self.send_response(mode["status"]); self.end_headers(); self.wfile.write(b"boom"); return
            answers = mode["answers"] if mode["answers"] is not None else {
                q: {"choice": list(v["criteria"])[1], "confidence": 0.8,
                    "probabilities": {k: (0.8 if i == 1 else 0.2 / (len(v["criteria"]) - 1))
                                      for i, k in enumerate(v["criteria"])}}
                for q, v in body["questions"].items()}
            out = json.dumps({"answers": answers}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen, mode
    srv.shutdown()


def test_request_shape_and_answer_parsing(server):
    url, seen, _ = server
    c = ClefClient(url, max_images=2)
    qs = {"route": choice_question("qual?", {"none": "nada", "edit": "editar", "orbit": "girar"})}
    ans = asyncio.run(c.decide({"request": "gire"}, qs, [png(), png(), png()]))
    assert seen["path"] == "/v1/systemone"
    assert seen["body"]["model"] == "clef"
    assert seen["body"]["state"] == {"request": "gire"}
    assert seen["body"]["questions"]["route"]["type"] == "choice"
    # 3 imagens com limite 2 viram duas janelas em vez de descartar a terceira;
    # nenhuma requisição passa do limite (a primeira leva 2, a segunda leva 1)
    assert [len(b["images"]) for b in seen["bodies"]] == [2, 1]
    assert seen["body"]["images"][0].startswith("data:image/png;base64,")
    a = ans["route"]
    assert a.choice == "edit" and a.confidence == pytest.approx(0.8)
    assert a.top(1)[0][0] == "edit"


def test_errors_are_explained(server):
    url, _, mode = server
    qs = {"q": choice_question("x", {"a": "a", "b": "b"})}
    mode["status"] = 500
    with pytest.raises(ClefError, match="500"):
        asyncio.run(ClefClient(url).decide({}, qs))
    mode["status"], mode["answers"] = 200, {}
    with pytest.raises(ClefError, match="não respondeu"):
        asyncio.run(ClefClient(url).decide({}, qs))
    with pytest.raises(ClefError) as e:
        asyncio.run(ClefClient("http://127.0.0.1:1", timeout_s=2).decide({}, qs))
    # porta morta: o nome do endereço tem que aparecer e a mensagem tem que dizer algo (no Windows a recusa pode
    # chegar como ConnectTimeout)
    assert "127.0.0.1:1" in str(e.value) and ("Não consegui falar" in str(e.value)
                                              or "não respondeu em 2 s" in str(e.value))


def test_a_timeout_says_what_happened_and_what_to_tune(monkeypatch):
    """O timeout do httpx chega sem mensagem: antes o erro saía "…Clef em http://x: " (vazio). Agora explica e diz o
    que ajustar — foi assim que um pedido de 4 passos estourou o tempo com o Clef e o Qwen dividindo a placa."""
    import httpx

    async def estoura(self, *a, **k):
        raise httpx.ReadTimeout("", request=httpx.Request("POST", "http://x/v1/systemone"))

    monkeypatch.setattr(httpx.AsyncClient, "post", estoura)
    qs = {"q": choice_question("x", {"a": "a", "b": "b"})}
    with pytest.raises(ClefError) as e:
        asyncio.run(ClefClient("http://127.0.0.1:8765", timeout_s=120).decide({}, qs))
    msg = str(e.value)
    assert "não respondeu em 120 s" in msg and "Ajustes > Clef" in msg and "int4" in msg


def test_choice_limits():
    with pytest.raises(ClefError):
        choice_question("x", {"a": "a"})
    with pytest.raises(ClefError):
        choice_question("x", {chr(97 + i) + str(i): "d" for i in range(27)})


def test_unload_hits_server_and_tolerates_absence(server):
    url, seen, _ = server
    assert asyncio.run(ClefClient(url).unload()) is True and seen["unloaded"] == 1
    assert asyncio.run(ClefClient("http://127.0.0.1:1").unload()) is False  # sem servidor: não é erro


