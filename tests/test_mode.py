"""Modo do workflow escolhido à mão na tela (turbo = "Rápido" / quality = "Qualidade"): vai direto para o passo,
sem passar pelo Clef; `None`/vazio/"auto" mantém o comportamento antigo (o Clef decide)."""
import pytest

from pickflow.clef import Answer
from test_e2e import api, png, upload, wait_event  # noqa: F401  (fixture e helpers)


class Stub:
    """Clef falso: cada pergunta tem a resposta escolhida em `picks` (a primeira opção se faltar), 0,9 de confiança."""
    mode = "stub"

    def __init__(self, picks=None):
        self.picks, self.calls = picks or {}, []

    async def decide(self, state, questions, images=None):
        self.calls.append(questions)
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q, keys[0])
            out[q] = Answer(pick, 0.9, {k: (0.9 if k == pick else 0.1 / max(1, len(keys) - 1)) for k in keys})
        return out


UNSET = object()


def send(c, text, ids, route=None, mode=UNSET):
    """Manda um pedido na conversa nova; sem `mode` o campo nem aparece (o jeito antigo)."""
    cid = c.post("/api/conversations").json()["id"]
    body = {"text": text, "image_ids": ids, "route": route}
    if mode is not UNSET:
        body["mode"] = mode
    r = c.post(f"/api/conversations/{cid}/messages", json=body)
    assert r.status_code == 200, r.text
    return cid, r.json()["run_id"]


def events(c, rid):
    return c.pipe.runs[rid].events


def step_start(c, rid):
    return next(e for e in events(c, rid) if e["type"] == "step_start")


def ksampler_steps(c):
    p = list(c.fake.prompts.values())[-1]
    return next(n for n in p.values() if n["class_type"] == "KSampler")["inputs"]["steps"]


def loras(c):
    p = list(c.fake.prompts.values())[-1]
    return [n["inputs"]["lora_name"] for n in p.values() if n["class_type"] == "LoraLoader"]


# ------------------------------------------------------------------ à mão: quality
def test_the_mode_chosen_by_the_user_goes_straight_to_the_step(api, monkeypatch):
    """`mode: "quality"` na mensagem: o passo roda em Qualidade e o Clef não é perguntado sobre o modo."""
    stub = Stub()
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: stub)
    a = upload(api)
    cid, rid = send(api, "troque a cor da camisa para vermelho", [a["id"]], route="edit", mode="quality")
    done = wait_event(api, rid, lambda e: e["type"] in ("done", "error"))
    assert done["type"] == "done", done.get("message")
    assert done["images"] and api.get(done["images"][0]["url"]).status_code == 200
    assert step_start(api, rid)["mode"] == "quality"
    # a pergunta do modo nem chega ao Clef: a decisão (se aparecer na tela) é do usuário, com confiança cheia
    assert all("mode" not in q for q in stub.calls)
    dec = [e for e in events(api, rid) if e["type"] == "decision" and e["question"] == "mode"]
    assert not [e for e in dec if e["source"] == stub.mode]
    assert all(e["source"] == "user" and e["chosen"] == "quality" for e in dec)
    assert ksampler_steps(api) == 25                                   # gráfico do modo Qualidade
    conv = api.get(f"/api/conversations/{cid}").json()
    assert conv["messages"][0]["mode"] == "quality"


# ------------------------------------------------------------------ o comportamento antigo
@pytest.mark.parametrize("campo", ["auto", "AUTOMATICO", None])
def test_auto_or_empty_keeps_the_clef_deciding_the_mode(api, monkeypatch, campo):
    """Sem escolha do usuário, o Clef volta a responder a pergunta `mode` (como hoje)."""
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub())
    a = upload(api)
    cid, rid = send(api, "troque a cor da camisa", [a["id"]], route="edit", mode=campo)
    wait_event(api, rid, lambda e: e["type"] == "done")
    dec = next(e for e in events(api, rid) if e["type"] == "decision" and e["question"] == "mode")
    assert dec["chosen"] == "turbo" and dec["source"] == "stub"
    assert step_start(api, rid)["mode"] == "turbo"
    assert api.get(f"/api/conversations/{cid}").json()["messages"][0]["mode"] is None


def test_a_mode_the_workflow_does_not_have_falls_back_to_the_default(api, monkeypatch):
    """Formato válido mas desconhecido: sem erro; o `router` usa o padrão do workflow."""
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub())
    a = upload(api)
    _, rid = send(api, "troque a cor da camisa", [a["id"]], route="edit", mode="Turbo_Rápido")
    ev = wait_event(api, rid, lambda e: e["type"] in ("done", "error"))
    assert ev["type"] == "done" and ev["images"]
    _, rid2 = send(api, "troque a cor da camisa", [a["id"]], route="edit", mode="desenho")
    ev2 = wait_event(api, rid2, lambda e: e["type"] in ("done", "error"))
    assert ev2["type"] == "done" and ev2["images"]               # modo inexistente: ignora, sem erro
    assert step_start(api, rid2)["mode"] == "turbo"


# ------------------------------------------------------------------ normalização e validação do corpo
def test_norm_mode_accepts_only_a_short_lowercase_id():
    from pickflow.app import norm_mode
    assert norm_mode(" Quality ") == "quality" and norm_mode("TURBO") == "turbo"
    assert norm_mode("auto") is None and norm_mode("Automatico") is None
    assert norm_mode("") is None and norm_mode("   ") is None and norm_mode(None) is None
    assert norm_mode("Qualidade Máxima!") is None and norm_mode("qualidade-maxima") is None
    assert norm_mode("1turbo") is None and norm_mode("turbo quality") is None
    assert norm_mode("a" * 17) is None and norm_mode("a" * 16) == "a" * 16


def test_a_message_with_only_a_mode_and_no_text_or_image_is_still_refused(api):
    cid = api.post("/api/conversations").json()["id"]
    r = api.post(f"/api/conversations/{cid}/messages", json={"text": "", "image_ids": [], "mode": "quality"})
    assert r.status_code == 400
