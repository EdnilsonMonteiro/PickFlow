"""Servidor de referência do Clef: `/health` e troca de device, sem torch e sem pesos.

O `clef_server/` não é um pacote instalado, então o módulo é carregado por caminho.
"""
import importlib.util
import pathlib

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402 - depois do importorskip, de propósito

RAIZ = pathlib.Path(__file__).resolve().parents[1]
ARQUIVO = RAIZ / "clef_server" / "server.py"


def carrega_servidor():
    """Importa `clef_server/server.py` pelo caminho (o pacote não está no sys.path)."""
    spec = importlib.util.spec_from_file_location("clef_server_de_teste", ARQUIVO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HolderFalso:
    """Faz o papel do `Holder` de verdade: tem `model`, `device`, `idle`, `quant`, `unload()` e `run()`."""

    def __init__(self, device="cuda", loaded=True, quant="none"):
        self.model_id = "Cloudflare/clef-flash"
        self.device = device
        self.idle = 0                      # desliga a faxina por inatividade
        self.model = object() if loaded else None
        self.quant = quant
        self.unloads = 0

    def vram_expected_gb(self):
        return {"none": 17.8, "int8": 9.6, "int4": 6.0}.get(str(self.quant or "none"), 0.0)

    def vram_work_gb(self):
        return round(self.vram_expected_gb() + 8.5, 2)      # pesos + ativações de uma decisão

    def unload(self):
        self.unloads += 1
        self.model = None

    def run(self, request):            # não é o alvo destes testes
        return {"answers": []}


@pytest.fixture()
def servidor():
    mod = carrega_servidor()
    holder = HolderFalso()
    with TestClient(mod.create_app(holder)) as cliente:
        yield cliente, holder


def test_health_reporta_ram_device_e_dtype(servidor):
    cliente, holder = servidor
    r = cliente.get("/health")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["loaded"] is True
    assert corpo["device"] == "cuda"
    assert isinstance(corpo["ram_gb"], (int, float)) and corpo["ram_gb"] > 0
    assert "dtype" in corpo
    assert "vram" not in corpo            # sem torch/GPU não se inventa VRAM


def test_health_reporta_a_quantizacao_e_quanto_ele_ocupa():
    """É com `quant`, `vram_expected_gb` e `vram_work_gb` que o PickFlow decide se o Clef cabe junto do ComfyUI —
    os números valem mesmo com o modelo descarregado."""
    mod = carrega_servidor()
    with TestClient(mod.create_app(HolderFalso(quant="int4", loaded=False))) as cliente:
        corpo = cliente.get("/health").json()
    assert corpo["quant"] == "int4" and corpo["vram_expected_gb"] == 6.0 and corpo["loaded"] is False
    # trabalhar pede mais que os pesos: são as ativações da decisão
    assert corpo["vram_work_gb"] == 14.5


def test_device_cpu_descarrega_e_troca(servidor):
    cliente, holder = servidor
    r = cliente.post("/v1/device", json={"device": "cpu"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "device": "cpu", "loaded": False}
    assert holder.device == "cpu"
    assert holder.unloads == 1
    assert holder.model is None
    assert cliente.get("/health").json()["device"] == "cpu"


@pytest.mark.parametrize("valor", ["tpu", "", "  ", None, "gpu", 3])
def test_device_invalido_da_422_e_nao_mexe_em_nada(servidor, valor):
    cliente, holder = servidor
    r = cliente.post("/v1/device", json={"device": valor})
    assert r.status_code == 422
    assert holder.device == "cuda"
    assert holder.unloads == 0
