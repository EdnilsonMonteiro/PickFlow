"""Cancelar tem que valer na hora — inclusive enquanto o Clef carrega o modelo (30 s+) ou o ComfyUI está na fila.

Antes, `Pipeline.cancel` só levantava uma bandeira e essas esperas não olhavam para ela: o botão Cancelar ficava
sem efeito até a resposta chegar.
"""
import asyncio
import time

from pickflow.clef import MockClef, Answer

from test_e2e import api, png, start, upload, wait_event  # noqa: F401  (fixture e helpers)


def test_cancel_stops_a_run_that_is_waiting_for_the_clef(api, monkeypatch):
    from pickflow import runner

    class Lento(MockClef):
        """Como o Clef carregando o modelo: a resposta demora muito."""
        mode = "systemone"

        async def health(self):
            return {"loaded": True, "device": "cuda", "vram": {"allocated_gb": 19.0}}

        async def decide(self, state, questions, images=None):
            await asyncio.sleep(30)
            return await super().decide(state, questions, images)

    monkeypatch.setattr(runner, "make_clef", lambda settings: Lento())
    a = upload(api)
    # sem rota forçada: quem decide é o Clef, então a espera é dentro da chamada a ele
    _, rid = start(api, "melhore a foto", [a["id"]])
    wait_event(api, rid, lambda e: e["type"] == "status" and "Clef" in e.get("text", ""), timeout=5)
    assert api.pipe.runs[rid].phase == "clef"

    t0 = time.time()
    assert api.post(f"/api/runs/{rid}/cancel").status_code == 200
    ev = wait_event(api, rid, lambda e: e["type"] == "error", timeout=5)
    assert ev["message"].startswith("Execução cancelada.")
    assert "carregar o modelo" in ev["message"]        # a nota honesta: a carga do Clef não para pela metade
    assert api.pipe.runs[rid].finished
    assert time.time() - t0 < 5                        # não esperou os 30 s


def test_cancel_of_a_finished_run_does_nothing(api):
    a = upload(api)
    _, rid = start(api, "gere uma praia", [a["id"]], route="t2i")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert api.post(f"/api/runs/{rid}/cancel").status_code == 200
    assert api.pipe.runs[rid].finished
