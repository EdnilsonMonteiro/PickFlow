"""Feedback no fluxo de ponta a ponta: a opinião vai para o histórico, o download conta como positivo fraco e um
pedido parecido com o anterior, com rota diferente, entra como "não era isso" — sem ninguém precisar opinar.
"""
import time

import pytest

from pickflow.feedback import FeedbackStore, similarity
from pickflow.runner import Pipeline
from pickflow.router import ImageRef

from test_e2e import api, start, upload, wait_event  # noqa: F401  (fixture e helpers)


def _entry(verdict="down", route="edit", **kw):
    base = {"conv": "c", "run": "r1", "request": "melhore a foto", "route": route, "preset": "edit/livre",
            "confidence": 0.6, "verdict": verdict, "picked": "", "source": "user"}
    base.update(kw)
    return base


def test_the_done_event_carries_what_the_feedback_needs(api):
    a = upload(api)
    _, rid = start(api, "melhore a foto", [a["id"]], route="edit")
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    assert done["run"] == rid
    assert done["decision"]["route"] == "edit"
    assert done["decision"]["request"] == "melhore a foto"
    assert done["decision"]["presets"] and done["decision"]["presets"][0].startswith("edit/")


def test_the_feedback_route_records_the_opinion(api):
    a = upload(api)
    cid, rid = start(api, "melhore a foto", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    r = api.post(f"/api/conversations/{cid}/feedback", json={"run_id": rid, "verdict": "down"})
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["verdict"] == "down"
    store = FeedbackStore(api.pipe.store.data_dir)
    got = [e for e in store.entries() if e["run"] == rid]
    assert got and got[-1]["route"] == "edit" and got[-1]["source"] == "user"
    # a mesma execução julgada duas vezes: vale a última, sem contar duas vezes
    r = api.post(f"/api/conversations/{cid}/feedback", json={"run_id": rid, "verdict": "other", "picked": "troca_roupa"})
    assert r.status_code == 200
    effective = [e for e in store.effective() if e["run"] == rid]
    assert len(effective) == 1 and effective[0]["verdict"] == "other" and effective[0]["picked"] == "troca_roupa"


def test_download_counts_as_a_weak_positive(api):
    a = upload(api)
    cid, rid = start(api, "melhore a foto", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    r = api.post(f"/api/conversations/{cid}/feedback", json={"run_id": rid, "verdict": "download"})
    assert r.status_code == 200 and r.json()["summary"]["weak"] == 1


def test_feedback_for_an_unknown_run_is_refused(api):
    cid = api.post("/api/conversations").json()["id"]
    r = api.post(f"/api/conversations/{cid}/feedback", json={"run_id": "nao-existe", "verdict": "up"})
    assert r.status_code == 404
    r = api.post(f"/api/conversations/{cid}/feedback", json={"run_id": "x", "verdict": "talvez"})
    assert r.status_code == 400


def test_prev_done_reads_the_last_run_of_the_conversation():
    conv = {"messages": [
        {"role": "user", "text": "põe ele na praia"},
        {"role": "assistant", "events": [{"type": "done", "run": "abc", "decision": {
            "route": "recipe__colar_e_fundir", "presets": ["colar_fundir/pessoa"], "confidence": 0.66}}]},
        {"role": "user", "text": "agora de noite"},
    ]}
    prev = Pipeline._prev_done(conv)
    assert prev["run"] == "abc" and prev["route"] == "recipe__colar_e_fundir"
    assert prev["text"] == "põe ele na praia" and prev["preset"] == "colar_fundir/pessoa"
    assert Pipeline._prev_done({"messages": [{"role": "user", "text": "x"}]}) is None


def test_a_reworded_request_with_another_route_is_recorded_as_a_redo(tmp_path):
    pipe = Pipeline.__new__(Pipeline)                     # só o que o teste usa: o histórico
    pipe.feedback = FeedbackStore(tmp_path)
    novo = "põe o cachorro na praia ao pôr do sol"
    conv = {"messages": [
        {"role": "user", "text": "põe o cachorro na praia"},
        {"role": "assistant", "events": [{"type": "done", "run": "r1", "decision": {"route": "edit"}}]},
        {"role": "user", "text": novo},
    ]}
    import asyncio
    asyncio.run(pipe._implicit_feedback(_FakeRun("c"), novo, conv, "colar_fundir"))
    got = pipe.feedback.entries()
    assert got and got[-1]["run"] == "r1" and got[-1]["verdict"] == "redo" and got[-1]["source"] == "redo"
    # a mesma rota: é ajuste fino, não erro (fica registrado como "same", que não conta)
    asyncio.run(pipe._implicit_feedback(_FakeRun("c"), novo, conv, "edit"))
    assert pipe.feedback.entries()[-1]["verdict"] == "same"
    # outro assunto: não registra nada
    before = len(pipe.feedback.entries())
    asyncio.run(pipe._implicit_feedback(_FakeRun("c"), "gere um gato de chapéu", conv, "t2i"))
    assert len(pipe.feedback.entries()) == before


# ------------------------------------- rota que RODOU (e não a que o Clef deu primeiro)
def test_the_route_chosen_in_the_doubt_modal_is_the_one_judged(api, project):
    """Se a pessoa responde o modal "o Clef ficou em dúvida", o feedback tem que falar da rota que rodou."""
    from pickflow.store import Store
    Store(project).save_settings({"clef": {"ask_below": 0.999}})      # qualquer rota fica ambígua
    a, b = upload(api), upload(api)
    _, rid = start(api, "algo vago", [a["id"], b["id"]])
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["kind"] == "choose"
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": "t2i"})
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    assert done["decision"]["route"] == "t2i"           # a rota do modal, não a primeira do Clef
    assert done["decision"]["origin"] == "user"         # escolha da pessoa: não ensina o Clef


def test_a_forced_route_is_recorded_without_the_mode_and_as_chosen_by_the_user(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit@quality")
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    assert done["decision"]["route"] == "edit"          # sem o "@quality" colado no id
    assert done["decision"]["origin"] == "user"         # rota forçada: o histórico não aprende com ela


def test_the_route_of_the_plan_is_the_final_one():
    """`plan.route` manda: nem a 2ª análise nem a correção do histórico podem ficar de fora do julgamento."""
    from pickflow.router import PlanResult

    dec = [{"question": "route", "chosen": "edit", "confidence": 0.6, "source": "systemone"},
           {"question": "route_2", "chosen": "troca_roupa", "confidence": 0.9, "source": "systemone"}]
    assert Pipeline._route_of(PlanResult([], dec, route="troca_roupa")) == ("troca_roupa", 0.9, "clef")

    dec = [{"question": "route", "chosen": "edit", "confidence": 0.5, "source": "systemone"},
           {"question": "route_learn", "chosen": "troca_pessoa", "confidence": 0.55, "source": "histórico"}]
    assert Pipeline._route_of(PlanResult([], dec)) == ("troca_pessoa", 0.55, "history")

    dec = [{"question": "route", "chosen": "edit@quality", "confidence": 1.0, "source": "user"}]
    assert Pipeline._route_of(PlanResult([], dec)) == ("edit", 1.0, "user")

    dec = [{"question": "route", "chosen": "t2i", "confidence": 1.0, "source": "único"}]
    assert Pipeline._route_of(PlanResult([], dec))[2] == "user"       # "única opção" também não ensina

    assert Pipeline._route_of(PlanResult([], [], route="recipe__colar_e_fundir")) == (
        "recipe__colar_e_fundir", 0.0, "clef")


def test_the_prompt_verdict_goes_to_the_prompt_and_confirms_the_route(api):
    """"O workflow estava certo, o prompt não": mexe no histórico do prompt, e a rota forçada não ganha crédito."""
    a = upload(api)
    cid, rid = start(api, "melhore a foto", [a["id"]], route="edit")
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    antes = done["decision"]["presets"][0]
    assert done["decision"]["origin"] == "user"             # rota escolhida na mão
    r = api.post(f"/api/conversations/{cid}/feedback",
                 json={"run_id": rid, "verdict": "preset", "picked": "edit/melhorar"})
    assert r.status_code == 200, r.text
    store = FeedbackStore(api.pipe.store.data_dir)
    entry = [e for e in store.entries() if e["run"] == rid][-1]
    assert entry["verdict"] == "preset" and entry["picked"] == "edit/melhorar"
    assert store.counts("preset").get("edit/melhorar", (0, 0))[0] >= 1
    assert store.counts("preset").get(antes, (0, 0))[1] >= 1
    assert store.counts().get("edit", (0, 0))[0] == 0       # o Clef não escolheu essa rota: sem crédito


class _FakeRun:
    """O que o feedback implícito usa de uma execução: o id da conversa."""

    def __init__(self, conv_id: str):
        self.conv_id = conv_id
