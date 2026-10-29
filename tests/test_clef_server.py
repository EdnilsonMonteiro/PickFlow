"""Servidor do Clef (`clef_server/`) de ponta a ponta, com um modelo de mentira no lugar dos pesos.

O que prova: bf16 e int4 chegam ao `load_release_model` do jeito certo, combinações que não funcionam (int4 na CPU, CUDA
ausente) falham ANTES de baixar o modelo e com o motivo, e o `ClefClient` do PickFlow conversa com o servidor de verdade
(HTTP real). O que NÃO prova: o carregamento dos pesos reais (isso só dá para ver com o clef-flash e uma placa).
"""
import asyncio
import importlib.util
import pathlib
import socket
import sys
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# o que o `joint_schema_model.py` dos pesos oferece (só a parte que o servidor usa)
STUB = '''
CALLS = []


def load_release_model(path, device="cuda", dtype=None, **kwargs):
    CALLS.append({"path": str(path), "device": device, "kwargs": kwargs})
    return object(), object()


def systemone(model, processor, request):
    answers = {}
    for qid, q in request["questions"].items():
        opts = [str(o) for o in q.get("criteria", [])]
        answers[qid] = {"type": "choice", "choice": opts[0], "confidence": 0.9,
                        "probabilities": {o: (0.9 if i == 0 else 0.1 / max(1, len(opts) - 1)) for i, o in enumerate(opts)}}
    return {"model": request["model"], "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 0}}
'''


def carrega(nome, caminho):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def servidor_mod():
    return carrega("clef_server_real", RAIZ / "clef_server" / "server.py")


@pytest.fixture()
def pesos(tmp_path, monkeypatch):
    """Uma pasta de "pesos" só com o `joint_schema_model.py` de mentira; devolve (pasta, lista de chamadas)."""
    (tmp_path / "joint_schema_model.py").write_text(STUB, encoding="utf-8")
    monkeypatch.delitem(sys.modules, "joint_schema_model", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield tmp_path
    sys.modules.pop("joint_schema_model", None)


def chamadas():
    return sys.modules["joint_schema_model"].CALLS


def pedido():
    return {"model": "clef-flash", "state": "x", "questions": {"route": {"type": "choice", "criteria": {"a": "A", "b": "B"}}}}


def test_quant_names_and_what_cannot_work(servidor_mod, monkeypatch):
    s = servidor_mod
    assert [s.normalize_quant(v) for v in (None, "", "none", "bf16", "int4", "4bit")] == ["bf16"] * 4 + ["int4"] * 2
    with pytest.raises(ValueError):
        s.normalize_quant("int8")
    assert "NVIDIA" in s.check_setup("cpu", "int4")                         # int4 precisa de CUDA
    assert s.check_setup("cpu", "bf16") == ""                               # bf16 roda na CPU
    assert "device" in s.check_setup("tpu", "bf16")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert "CUDA" in s.check_setup("cuda", "bf16")                          # sem placa: diz o que fazer
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert s.check_setup("cuda", "bf16") == ""


def test_bf16_loads_the_plain_model(servidor_mod, pesos):
    h = servidor_mod.Holder(str(pesos), "cpu", 0, "bf16")
    out = h.run(pedido())
    assert out["answers"]["route"]["choice"] == "a"
    assert chamadas() == [{"path": str(pesos), "device": "cpu", "kwargs": {}}]       # sem quantização
    assert (h.quant, h.vram_expected_gb(), h.vram_work_gb()) == ("bf16", 17.8, 26.3)


def test_int4_passes_the_nf4_config(servidor_mod, pesos, monkeypatch):
    monkeypatch.setattr(servidor_mod, "check_setup", lambda device, quant: "")        # como se houvesse placa e bitsandbytes
    h = servidor_mod.Holder(str(pesos), "cuda", 0, "int4")
    h.load()
    cfg = chamadas()[0]["kwargs"]["quantization_config"]
    assert (cfg.load_in_4bit, cfg.bnb_4bit_quant_type, cfg.bnb_4bit_compute_dtype) == (True, "nf4", torch.bfloat16)
    assert (h.vram_expected_gb(), h.vram_work_gb()) == (7.9, 16.4)                    # os números medidos no clef-flash


def test_an_impossible_setup_fails_before_loading_anything(servidor_mod, pesos):
    h = servidor_mod.Holder(str(pesos), "cpu", 0, "int4")
    with pytest.raises(RuntimeError, match="NVIDIA"):
        h.load()
    assert "joint_schema_model" not in sys.modules                          # nem chegou a importar/baixar o modelo


def test_the_pickflow_client_talks_to_the_server_over_http(servidor_mod, pesos):
    import uvicorn

    from pickflow.clef import ClefClient, choice_question

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    holder = servidor_mod.Holder(str(pesos), "cpu", 0, "bf16")
    srv = uvicorn.Server(uvicorn.Config(servidor_mod.create_app(holder), host="127.0.0.1", port=porta, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    try:
        client = ClefClient(f"http://127.0.0.1:{porta}", timeout_s=30)

        async def fluxo():
            antes = await client.health()
            ans = (await client.decide("pedido", {"route": choice_question("Which?", {"a": "A", "b": "B"})}))["route"]
            depois = await client.health()
            return antes, ans, depois, await client.unload(), await client.health()

        antes, ans, depois, descarregou, fim = asyncio.run(fluxo())
        assert (antes["loaded"], antes["quant"], antes["vram_expected_gb"]) == (False, "", 17.8)      # /health não carrega o modelo
        assert ans.choice == "a" and ans.confidence == pytest.approx(0.9)
        assert depois["loaded"] is True and descarregou and fim["loaded"] is False
    finally:
        srv.should_exit = True
        t.join(timeout=10)


# --------------------------------------------------------------------------- o script que liga (start_clef.py)
@pytest.fixture()
def launcher(tmp_path, monkeypatch):
    mod = carrega("start_clef_teste", RAIZ / "clef_server" / "start_clef.py")
    monkeypatch.setattr(mod, "LAST", tmp_path / ".last_quant")
    return mod


def test_launcher_reads_the_gpu_and_recommends_a_quantization(launcher):
    assert launcher.parse_gpu("NVIDIA GeForce RTX 3090, 24576\n") == ("NVIDIA GeForce RTX 3090", 24.0)
    assert launcher.parse_gpu("") is None and launcher.parse_gpu("lixo") is None
    assert [launcher.recommend(g) for g in (None, ("x", 24.0), ("x", 48.0))] == ["bf16", "int4", "bf16"]


def test_launcher_asks_for_the_quantization_and_remembers_it(launcher, monkeypatch, capsys):
    gpu = ("RTX", 24.0)
    respostas = iter(["", "7", "2"])                          # Enter, lixo e depois 2
    monkeypatch.setattr("builtins.input", lambda _p="": next(respostas))
    assert launcher.choose_quant(None, gpu, interactive=True) == "int4"            # Enter = a recomendada
    assert launcher.choose_quant(None, gpu, interactive=True) == "int4"            # "7" não vale; pergunta de novo e aceita 2
    out = capsys.readouterr().out
    assert "bf16" in out and "int4" in out and "Type 1, 2" in out
    launcher.LAST.write_text("bf16\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    assert launcher.choose_quant(None, gpu, interactive=True) == "bf16"            # Enter = a última escolha


def test_launcher_does_not_ask_when_there_is_nothing_to_choose(launcher, monkeypatch):
    def nao_pergunta(_p=""):
        raise AssertionError("não devia perguntar")

    monkeypatch.setattr("builtins.input", nao_pergunta)
    assert launcher.choose_quant("int4", None, True) == "int4"                     # --quant manda
    assert launcher.choose_quant(None, ("x", 24.0), True, device="cpu") == "bf16"  # na CPU só o bf16 funciona
    assert launcher.choose_quant(None, ("x", 24.0), interactive=False) == "int4"   # sem terminal: a recomendada


def test_launcher_builds_the_server_command_and_refuses_int4_on_the_cpu(launcher, monkeypatch):
    cmd = launcher.server_command("py", "int4", "cuda", ["--port", "9000"])
    assert cmd[2:] == ["--model", "Cloudflare/clef-flash", "--port", "8321", "--idle-unload", "120", "--quant", "int4",
                       "--device", "cuda", "--port", "9000"]                       # o que o usuário passa vem por último
    monkeypatch.setattr(launcher, "ensure_env", lambda gpu: True)
    monkeypatch.setattr(launcher, "gpu_info", lambda: None)
    monkeypatch.setattr(launcher.subprocess, "call", lambda *_a, **_k: pytest.fail("não devia subir o servidor"))
    assert launcher.main(["--quant", "int4"]) == 2
