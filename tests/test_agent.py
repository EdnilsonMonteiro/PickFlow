"""Agente que cria casos de uso, travas contra excesso, e a checagem de repetição com fusão."""
import asyncio
import json

import pytest
import yaml

from pickflow.clef import Answer, ClefError
from pickflow.recipe_agent import RecipeAgent, check_recipe, recipe_overlap
from pickflow.recipe_merge import apply_merge, merge_recipes, score_pairs
from pickflow.router import Ctx, ImageRef, Router
from pickflow.store import Store, StoreError

from test_e2e import _answer, api, png, start, types, upload, wait_event  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def img(i):
    return ImageRef(f"i{i}", f"img{i}.png", b"x", 10, 10)


class FakeLLM:
    """LLM roteirizado: devolve as respostas na ordem e guarda o que recebeu."""
    configured = True

    def __init__(self, replies):
        self.replies = [json.dumps(r) if not isinstance(r, str) else r for r in replies]
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("o agente pediu mais respostas do que o roteiro tinha")
        return self.replies.pop(0)


class Stub:
    """Clef falso: `picks` escolhe a opção por pergunta; guarda as chamadas."""
    mode = "stub"

    def __init__(self, picks=None, fail=False):
        self.picks, self.calls, self.fail = picks or {}, [], fail

    async def decide(self, state, questions, images=None):
        self.calls.append((state, questions, images))
        if self.fail:
            raise ClefError("fora do ar")
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q, keys[0])
            probs = {k: (0.8 if k == pick else 0.2 / max(1, len(keys) - 1)) for k in keys}
            out[q] = Answer(pick, probs[pick], probs)
        return out


def recipe(**kw):
    """O exemplo do pedido: melhorar e colorir a imagem 2, depois colar na 1."""
    base = {
        "name": "Melhorar e colorir a pessoa antes de colar",
        "description": "Duas imagens: a segunda é uma pessoa em foto antiga ou ruim que precisa ser colorida e melhorada "
                       "antes de entrar no cenário da primeira. Não serve se a pessoa já está boa.",
        "steps": [
            {"workflow": "edit", "prompt": "livre", "inputs": {"image_1": "user:2"},
             "request": "Colorize this photo and improve its quality; keep the person identical."},
            {"workflow": "edit", "prompt": "livre", "inputs": {"image_1": "user:1", "image_2": "previous"}},
        ]}
    base.update(kw)
    return base


@pytest.fixture()
def _paste_twin(project):
    """Um caso de uso do usuário quase igual ao "Colar e fundir" (a versão antiga, sem papéis, que só invertia a ordem
    das imagens): é o exemplo de repetição que a nota de similaridade e a fusão devem achar."""
    (project / "custom").mkdir(exist_ok=True)
    (project / "custom" / "clef_prompt.yaml").write_text("""recipes:
- id: colar_e_fundir_invertido
  name: Colar e fundir (a pessoa vem primeiro)
  enabled: true
  images: 2
  description: Igual a Colar e fundir, mas a PRIMEIRA imagem é a pessoa ou objeto e a SEGUNDA é o fundo (cenário).
  steps:
  - workflow: colar_recortar
    inputs: {image_1: 'user:1'}
  - workflow: colar_posicionar
    inputs: {image_1: 'user:2', image_2: previous}
  - workflow: colar_fundir
    inputs: {image_1: previous}
""", encoding="utf-8")


@pytest.fixture()
def store(project):
    return Store(project)


# ------------------------------------------------------------------ armazenamento
def test_generated_recipes_never_overwrite_an_existing_id_and_trials_are_capped(store):
    taken = store.clef_config()["recipes"][0]["id"]
    a = store.add_generated({**recipe(), "id": taken}, "pedido a", max_trial=2)
    assert a["id"] == taken + "_2"                                   # nunca troca o existente
    assert next(r for r in store.clef_config()["recipes"] if r["id"] == taken)["name"] != recipe()["name"]
    b = store.add_generated({**recipe(), "id": "auto_b"}, "pedido b", max_trial=2)
    store.keep_generated(a["id"])                                    # mantido: não sai mais
    c = store.add_generated({**recipe(), "id": "auto_c"}, "pedido c", max_trial=1)
    ids = {r["id"] for r in store.generated_recipes()}
    assert ids == {a["id"], c["id"]} and b["id"] not in ids          # só 1 em teste: o mais antigo saiu, o mantido ficou
    assert not (store.custom_dir / "clef_prompt.yaml").exists()      # tudo na camada própria
    assert yaml.safe_load(store.generated_path.read_text(encoding="utf-8"))["recipes"][0]["generated"]["status"] == "kept"


# ------------------------------------------------------------------ conferência
def test_check_accepts_the_example_and_gives_it_a_fresh_id(store):
    ck = check_recipe(store, recipe(), 2)
    assert ck.ok, ck.errors
    assert ck.recipe["id"].startswith("auto_") and ck.recipe["images"] == 2
    assert ck.recipe["steps"][0]["request"].startswith("Colorize")


@pytest.mark.parametrize("patch,needle", [
    ({"steps": [{"workflow": "nao_existe"}]}, "inexistente"),
    ({"steps": [{"workflow": "edit", "prompt": "nada"}, {"workflow": "edit"}]}, "não existe"),
    ({"steps": [{"workflow": "edit", "inputs": {"foto": "user:1"}}, {"workflow": "edit"}]}, "entrada 'foto'"),
    ({"steps": [{"workflow": "edit", "inputs": {"image_1": "previous"}}, {"workflow": "edit"}]}, "não tem 'previous'"),
    ({"steps": [{"workflow": "edit", "inputs": {"image_1": "user:5"}}, {"workflow": "edit"}]}, "user:5 não existe"),
    ({"steps": [{"workflow": "edit", "inputs": {"image_1": "role:x"}}, {"workflow": "edit"}]}, "não foi declarado"),
    ({"steps": [{"workflow": "edit", "mode": "turbo2"}, {"workflow": "edit"}]}, "modo 'turbo2'"),
    ({"steps": [{"workflow": "edit", "loras": ["fantasma"]}, {"workflow": "edit"}]}, "LoRA inexistente"),
    ({"description": "curta"}, "QUANDO usar"),
    ({"name": ""}, "nome"),
    ({"images": 3}, "images deve ser 2"),
    ({"steps": [{"workflow": "edit"}] * 5}, "No máximo 4"),
    ({"steps": [{"workflow": "edit", "prompt": "livre"}]}, "workflow solto"),
])
def test_check_rejects_what_would_break(store, patch, needle):
    ck = check_recipe(store, recipe(**patch), 2)
    assert not ck.ok and any(needle in e for e in ck.errors), ck.errors


def test_check_flags_a_copy_of_an_existing_recipe_and_warns_about_unused_images(store):
    dup = json.loads(json.dumps(next(r for r in store.clef_config()["recipes"] if r["id"] == "colar_e_fundir")))
    dup.update(name="Outra colagem", description="Cola a pessoa da segunda imagem no cenário da primeira e funde a luz.")
    ck = check_recipe(store, dup, 2)
    assert ck.ok and ck.same_as == "colar_e_fundir" and ck.similar[0]["id"] == "colar_e_fundir"
    assert recipe_overlap(dup, dup) == 1.0
    # um passo a mais antes (o caso do pedido) NÃO é "o mesmo caso": é parecido, mas é outro
    pre = json.loads(json.dumps(dup))
    pre["steps"].insert(0, {"workflow": "edit", "prompt": "livre", "inputs": {"image_1": "user:2"}, "request": "Colorize this photo."})
    pre["steps"][1]["inputs"] = {"image_1": "previous"}
    ck2 = check_recipe(store, pre, 2)
    assert ck2.ok and ck2.same_as is None and ck2.similar[0]["id"] == "colar_e_fundir" and ck2.similar[0]["score"] >= 0.7
    four = check_recipe(store, recipe(), 4)                     # imagens 3 e 4 ficam sem uso
    assert any("3, 4" in w for w in four.warnings)


# -------------------------------------------------------------------- a conversa
def build(store, llm, clef=None, n=2, gap=None):
    ctx = Ctx("Adicione a pessoa da imagem 2 fazendo yoga no ambiente da imagem 1. Antes, deixe-a colorida.",
              [img(i) for i in range(1, n + 1)])
    clef = clef or Stub()
    said = []

    async def say(t):
        said.append(t)

    built = run(RecipeAgent(store, llm).build(ctx, Router(store, clef), gap or {"route_name": "Colar e fundir"}, say))
    return built, clef, said


def test_the_agent_looks_things_up_asks_the_clef_fixes_its_draft_and_delivers(store):
    bad = recipe(steps=[{"workflow": "edit", "prompt": "invento"}, {"workflow": "edit"}])
    llm = FakeLLM([
        {"thought": "ver o edit", "action": "show_workflow", "id": "edit"},
        {"action": "ask_clef", "question": "Qual prompt do edit serve para colorir?",
         "options": {"livre": "pedido livre", "exposicao": "só luz"}},
        {"action": "draft", "recipe": bad},
        {"action": "draft", "recipe": recipe()},
        {"action": "finish"},                                             # sem recipe: vale o último rascunho válido
    ])
    built, clef, said = build(store, llm, Stub({"q": "livre"}))
    assert built.recipe and built.recipe["steps"][0]["prompt"] == "livre" and built.rounds == 5
    assert len(clef.calls) == 1 and clef.calls[0][0]["task"] == "agent_consult" and "Agente: consultando o Clef…" in said
    # o agente recebeu o catálogo, o que o Clef viu e os erros do rascunho ruim
    first = llm.calls[0][1]["content"]
    assert "WORKFLOWS ATIVOS" in first and "colar_e_fundir" in first and "Adicione a pessoa" in first
    assert "invento" in llm.calls[3][-1]["content"] and "erros" in llm.calls[3][-1]["content"]
    assert "Colar e fundir" in llm.calls[0][1]["content"]
    assert store.generated_recipes() == []                                # quem grava é o pipeline, depois da confirmação


def test_the_agent_stops_at_the_round_limit_and_never_loops_forever(store):
    store.save_settings({"agent": {"max_rounds": 3}})
    llm = FakeLLM([{"action": "list_loras"}, {"action": "show_workflow", "id": "edit"}, {"action": "list_loras"}])
    built, _, _ = build(store, llm)
    assert built.recipe is None and "3 respostas" in built.reason and llm.replies == []


def test_the_agent_can_give_up_and_garbage_replies_do_not_crash_it(store):
    built, _, _ = build(store, FakeLLM(["isto não é JSON", {"action": "dança"}, {"action": "give_up", "reason": "colar_e_fundir já serve"}]))
    assert built.recipe is None and built.reason == "colar_e_fundir já serve" and built.rounds == 3


def test_a_recipe_identical_to_an_existing_one_is_reused_instead_of_created(store):
    dup = json.loads(json.dumps(next(r for r in store.clef_config()["recipes"] if r["id"] == "colar_e_fundir")))
    dup.update(name="Colagem", description="Cola a pessoa da segunda imagem no cenário da primeira e funde a luz.")
    built, _, _ = build(store, FakeLLM([{"action": "finish", "recipe": dup}]))
    assert built.recipe is None and built.reuse == "colar_e_fundir"


# ------------------------------------------------------------------ lacuna
def plan_with(store, clef, n=1, text="melhore a imagem e depois troque o fundo"):
    ctx = Ctx(text, [img(i) for i in range(1, n + 1)])
    return run(Router(store, clef).plan(ctx))


def test_the_gap_check_only_wakes_the_agent_when_enabled_and_when_the_clef_says_so(store):
    off = plan_with(store, Stub({"route": "edit", "q": "gap"}))
    assert off.gap is None and not any(d["question"] == "gap" for d in off.decisions)
    store.save_settings({"agent": {"enabled": True}})
    covered = plan_with(store, Stub({"route": "edit", "q": "covered"}))
    assert covered.gap is None
    gap = plan_with(store, Stub({"route": "edit", "q": "gap"}))
    assert gap.gap and gap.gap["route"] == "edit" and gap.gap["p"] >= 0.6
    assert any(d["question"] == "gap" for d in gap.decisions)
    # a pergunta cita o plano escolhido
    clef = Stub({"route": "edit", "q": "gap"})
    plan_with(store, clef)
    q = next(c for c in clef.calls if c[0]["task"] == "route_gap")[1]["q"]
    assert "Chosen plan" in q["instructions"] and set(q["criteria"]) == {"covered", "gap"}


# ----------------------------------------------------------- fluxo completo (API)
AGENT = {"agent": {"enabled": True, "confirm": False}}


def scripted(api, rec=None):
    api.pipe.llm = FakeLLM([{"action": "finish", "recipe": rec or recipe()}])
    return api.pipe.llm


def prompt_texts(api):
    return [p["6"]["inputs"]["value"] for p in api.fake.prompts.values()]       # nó 6 = texto do workflow edit


def test_manual_agent_builds_saves_and_runs_a_two_step_recipe_with_a_step_instruction(api):
    from pickflow.store import Store
    Store(api.pipe.store.root).save_settings(AGENT)
    scripted(api)
    a, b = upload(api, "cena.png", png((10, 90, 10))), upload(api, "pessoa.png", png((200, 10, 10)))
    request = "Adicione a pessoa da imagem 2 fazendo yoga no ambiente da imagem 1; antes deixe-a colorida"
    _, rid = start(api, request, [a["id"], b["id"]], route="agent")
    wait_event(api, rid, lambda e: e["type"] == "done", timeout=20)
    assert len(api.fake.prompts) == 2
    texts = prompt_texts(api)
    assert "Colorize this photo" in texts[0] and "yoga" not in texts[0]          # o passo 1 recebeu SÓ a sua instrução
    assert "yoga" in texts[1]                                                    # o passo 2 recebeu o pedido inteiro
    saved = api.pipe.store.generated_recipes()
    assert len(saved) == 1 and saved[0]["generated"]["status"] == "trial" and saved[0]["generated"]["request"].startswith("Adicione")
    agent_dec = next(e for e in api.pipe.runs[rid].events if e["type"] == "decision" and e["question"] == "agent")
    assert "guardado em teste" in agent_dec["chosen_name"]
    assert any(r["id"] == saved[0]["id"] for r in api.get("/api/clef-config").json()["recipes"])


def test_the_user_confirms_before_anything_is_saved_and_can_use_it_just_once(api):
    from pickflow.store import Store
    Store(api.pipe.store.root).save_settings({"agent": {"enabled": True, "confirm": True}})
    scripted(api)
    a, b = upload(api), upload(api, "b.png")
    _, rid = start(api, "melhore a imagem 2 e cole na 1", [a["id"], b["id"]], route="agent")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["kind"] == "agent_recipe" and need["recipe"]["steps"][0]["request"].startswith("Colorize")
    assert api.pipe.store.generated_recipes() == []
    _answer(api, rid, need, "once")
    wait_event(api, rid, lambda e: e["type"] == "done", timeout=20)
    assert api.pipe.store.generated_recipes() == [] and len(api.fake.prompts) == 2


def test_the_limit_of_kept_recipes_stops_the_agent_before_it_calls_the_llm(api):
    from pickflow.store import Store
    s = api.pipe.store
    Store(s.root).save_settings({"agent": {"enabled": True, "confirm": False, "max_generated": 1}})
    k = s.add_generated({**recipe(), "id": "auto_k"}, "x")
    s.keep_generated(k["id"])
    llm = scripted(api)
    a, b = upload(api), upload(api, "b.png")
    _, rid = start(api, "melhore a imagem 2 e cole na 1", [a["id"], b["id"]], route="agent")
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert llm.calls == [] and any("não cria mais casos de uso" in e.get("text", "") for e in api.pipe.runs[rid].events)
    assert "não conseguiu" in err["message"]


def test_keep_endpoint_promotes_a_trial_recipe(api):
    g = api.pipe.store.add_generated({**recipe(), "id": "auto_y"}, "pedido")
    r = api.post(f"/api/recipes/{g['id']}/keep")
    assert r.status_code == 200
    assert next(x for x in r.json()["recipes"] if x["id"] == g["id"])["generated"]["status"] == "kept"
    assert api.post("/api/recipes/nao_existe/keep").status_code == 404


# ------------------------------------------------------------ repetição e fusão
@pytest.mark.usefixtures("_paste_twin")
def test_pair_scores_combine_the_clef_opinion_with_the_structure(store):
    ids = ["colar_e_fundir", "colar_e_fundir_invertido", "colar_sem_fundir"]
    res = run(score_pairs(store, Stub({"p0": "same", "p1": "overlap", "p2": "different"}), ids))
    by = {(p["a"], p["b"]): p for p in res["pairs"]}
    assert res["source"] == "clef" and res["checked"] == 3
    top = res["pairs"][0]
    assert top["score"] >= 0.7 and top["level"] == "alta" and top["clef"]["same"] > 0.5
    assert res["pairs"] == sorted(res["pairs"], key=lambda p: -p["score"])
    assert all(0 <= p["score"] <= 1 for p in res["pairs"])
    assert res["groups"] and set(res["groups"][0]) <= set(ids)
    assert by                                                                      # há pares nomeados


MERGED = {"name": "Colar pessoa ou objeto no cenário", "description": "Duas imagens: um cenário e uma pessoa ou objeto, "
          "em qualquer ordem de envio. Recorta, posiciona e funde. Substitui Colar e fundir e a versão invertida.",
          "images": 2, "roles": [
              {"key": "cenario", "label": "Cenário", "about": "O fundo onde a pessoa vai ficar.", "default": 1},
              {"key": "sujeito", "label": "Pessoa ou objeto", "about": "O que será colado.", "default": 2}],
          "steps": [
              {"workflow": "colar_recortar", "inputs": {"image_1": "role:sujeito"}},
              {"workflow": "colar_posicionar", "inputs": {"image_1": "role:cenario", "image_2": "previous"}},
              {"workflow": "colar_fundir", "inputs": {"image_1": "previous"}}]}


@pytest.mark.usefixtures("_paste_twin")
def test_merge_proposes_validates_and_only_applies_after_approval(store):
    ids = ["colar_e_fundir", "colar_e_fundir_invertido"]
    before = store.clef_config()["recipes"]
    llm = FakeLLM([{"mergeable": True, "reason": "só muda a ordem das imagens", "notes": "usa papéis", "recipe": MERGED}])
    out = run(merge_recipes(store, llm, ids))
    assert out["mergeable"] and out["replaces"] == ids and out["recipe"]["roles"] and out["recipe"]["images"] == 2
    assert "CASOS DE USO A FUNDIR" in llm.calls[0][1]["content"] and "colar_e_fundir_invertido" in llm.calls[0][1]["content"]
    assert store.clef_config()["recipes"] == before                          # nada gravado ainda
    new = apply_merge(store, out["recipe"], ids)
    cfg = {r["id"]: r for r in store.clef_config()["recipes"]}
    assert cfg[new["id"]]["enabled"] and not cfg["colar_e_fundir"]["enabled"] and not cfg["colar_e_fundir_invertido"]["enabled"]
    assert cfg["colar_sem_fundir"]["enabled"]                                # os outros ficam como estavam
    new2 = apply_merge(store, out["recipe"], [new["id"]], mode="delete")    # mesmo id pedido de novo: nunca sobrescreve
    ids_now = {r["id"] for r in store.clef_config()["recipes"]}
    assert new2["id"] != new["id"] and new["id"] not in ids_now and new2["id"] in ids_now


@pytest.mark.usefixtures("_paste_twin")
def test_merge_retries_with_the_errors_and_can_say_the_recipes_are_too_different(store):
    broken = {**MERGED, "steps": [{"workflow": "colar_recortar", "inputs": {"image_1": "role:ninguem"}}] + MERGED["steps"][1:]}
    llm = FakeLLM([{"mergeable": True, "recipe": broken}, {"mergeable": True, "recipe": MERGED}])
    out = run(merge_recipes(store, llm, ["colar_e_fundir", "colar_e_fundir_invertido"]))
    assert out["mergeable"] and "ninguem" in llm.calls[1][-1]["content"]
    no = run(merge_recipes(store, FakeLLM([{"mergeable": False, "reason": "um troca pessoa, o outro só cola"}]),
                           ["colar_e_fundir", "trocar_pessoa_e_fundo"]))
    assert not no["mergeable"] and "troca pessoa" in no["reason"]
    with pytest.raises(StoreError):
        run(merge_recipes(store, FakeLLM([]), ["colar_e_fundir"]))
    with pytest.raises(StoreError):
        run(merge_recipes(store, FakeLLM([]), ["colar_e_fundir", "fantasma"]))


# ------------------------------------------------- gatilho automático no pedido normal
def test_the_gap_found_by_the_clef_wakes_the_agent_in_a_normal_request_and_covered_does_not(api, monkeypatch):
    from pickflow.store import Store
    Store(api.pipe.store.root).save_settings(AGENT)
    llm = scripted(api)
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"route": "edit", "q": "covered"}))
    a, b = upload(api), upload(api, "b.png")
    _, rid = start(api, "troque a cor da camisa", [a["id"], b["id"]])
    wait_event(api, rid, lambda e: e["type"] == "done", timeout=20)
    assert llm.calls == [] and api.pipe.store.generated_recipes() == []             # cobriu: o agente nem foi chamado
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Stub({"route": "edit", "q": "gap"}))
    _, rid = start(api, "melhore a imagem 2 e depois cole na 1", [a["id"], b["id"]])
    wait_event(api, rid, lambda e: e["type"] == "done", timeout=20)
    assert len(llm.calls) == 1 and len(api.pipe.store.generated_recipes()) == 1
    assert any("não cobre o pedido todo" in e.get("text", "") for e in api.pipe.runs[rid].events)


