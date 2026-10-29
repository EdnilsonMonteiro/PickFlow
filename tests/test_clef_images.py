"""Janelas de imagens do `ClefClient.decide`: mais imagens que o limite do modelo.

O Clef aceita no máximo `max_images` imagens por requisição. Em vez de descartar as
que sobram em silêncio, o cliente faz uma requisição por janela (em sequência) e
combina as respostas. Aqui um `httpx.MockTransport` guarda o corpo de cada requisição
e devolve respostas fabricadas, sem servidor nenhum.
"""
import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from pickflow.clef import ClefClient, ClefError, choice_question

QUESTIONS = {"route": choice_question("qual fluxo?", {"none": "nada", "edit": "editar", "orbit": "girar"})}


def png(color) -> bytes:
    """Imagem minúscula (2x2) de uma cor: cada cor vira um base64 diferente."""
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, "PNG")
    return buf.getvalue()


def photos() -> list[bytes]:
    return [png((40 * i, 255 - 40 * i, 30 * i)) for i in range(8)]


def answers_for(choice: str, confidence: float) -> dict:
    """Resposta no formato System One: a opção escolhida manda, o resto divide o resto."""
    others = [k for k in QUESTIONS["route"]["criteria"] if k != choice]
    probs = {choice: confidence}
    for k in others:
        probs[k] = (1.0 - confidence) / len(others)
    return {"answers": {"route": {"choice": choice, "confidence": confidence, "probabilities": probs}}}


@pytest.fixture()
def clef(monkeypatch):
    """Devolve uma fábrica: `clef([respostas], max_images=4)` -> (`client`, `corpos`, `chamadas`)."""

    def make(replies, max_images=4):
        bodies: list[dict] = []
        statuses = [r if isinstance(r, int) else 200 for r in replies]

        def handler(request: httpx.Request) -> httpx.Response:
            i = len(bodies)
            bodies.append(json.loads(request.content))
            status = statuses[i] if i < len(statuses) else 200
            if status != 200:
                return httpx.Response(status, text="boom")
            return httpx.Response(200, json=replies[i] if i < len(replies) else {})

        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient

        def client(*a, **kw):  # o endpoint (transporte de mentira) entra por baixo
            kw["transport"] = transport
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", client)
        return ClefClient("http://clef.falso", max_images=max_images), bodies

    return make


def run(client, images):
    return asyncio.run(client.decide({"request": "deixe o céu azul"}, QUESTIONS, images))


def sent_images(body: dict) -> list[str]:
    return body.get("images") or []


def test_oito_imagens_viram_duas_requisicoes_de_quatro(clef):
    client, bodies = clef([answers_for("edit", 0.9), answers_for("orbit", 0.8)])
    photos_ = photos()
    run(client, photos_)

    assert len(bodies) == 2
    assert [len(sent_images(b)) for b in bodies] == [4, 4]
    # cada requisição leva exatamente quatro data URLs PNG
    assert json.dumps(bodies[0]).count("data:image/png;base64,") == 4
    assert json.dumps(bodies[1]).count("data:image/png;base64,") == 4
    # nenhuma imagem repetida entre as janelas (as duas juntas são as oito enviadas)
    primeira, segunda = set(sent_images(bodies[0])), set(sent_images(bodies[1]))
    assert len(primeira) == len(segunda) == 4
    assert not (primeira & segunda)
    # o mesmo state e as mesmas perguntas em todas as janelas
    assert bodies[0]["state"] == bodies[1]["state"] == {"request": "deixe o céu azul"}
    assert bodies[0]["questions"] == bodies[1]["questions"] == QUESTIONS


def test_resposta_combinada_tem_um_answer_por_pergunta(clef):
    client, bodies = clef([answers_for("edit", 0.9), answers_for("orbit", 0.8)])
    out = run(client, photos())

    assert len(bodies) == 2
    assert list(out) == ["route"]
    a = out["route"]
    assert sum(a.probs.values()) == pytest.approx(1.0)
    assert set(a.probs) == set(QUESTIONS["route"]["criteria"])
    assert a.choice == max(a.probs, key=a.probs.get)
    assert 0.0 < a.confidence <= 1.0
    assert a.confidence == pytest.approx(a.probs[a.choice])
    # a primeira janela (confiança 0,9) pesa mais que a segunda (0,8)
    assert a.choice == "edit"


def test_janela_que_falha_nao_derruba_a_decisao(clef):
    client, bodies = clef([answers_for("edit", 0.9), 500])
    a = run(client, photos())["route"]

    assert len(bodies) == 2  # as duas foram tentadas
    assert a.choice == "edit" and a.confidence == pytest.approx(0.9)


def test_todas_as_janelas_falhando_propagam_o_erro(clef):
    client, bodies = clef([500, 500])
    with pytest.raises(ClefError, match="500"):
        run(client, photos())
    assert len(bodies) == 2


