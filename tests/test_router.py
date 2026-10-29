import asyncio

import pytest

from pickflow.clef import Answer, MockClef, normalize_answer
from pickflow.router import Ctx, ImageRef, Router, Source, Step
from pickflow.store import Store


def img(i):
    return ImageRef(f"i{i}", f"img{i}.png", b"x", 10, 10)


def run(coro):
    return asyncio.run(coro)


def call_with(st, qid):
    """A primeira chamada ao Clef que traz a pergunta `qid` (antes dela pode haver a análise das imagens)."""
    return next(c for c in st.calls if qid in c[1])


class Stub:
    """Clef falso que devolve respostas roteirizadas e guarda as perguntas recebidas."""
    mode = "stub"

    def __init__(self, picks=None):
        self.picks, self.calls = picks or {}, []

    async def decide(self, state, questions, images=None):
        self.calls.append((state, questions, images))
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q, keys[0])
            probs = {k: (0.8 if k == pick else 0.2 / max(1, len(keys) - 1)) for k in keys}
            out[q] = Answer(pick, probs[pick], probs)
        return out


def test_normalize_answer_variants():
    a = normalize_answer({"choice": "a", "confidence": 0.9, "probabilities": {"a": 0.9, "b": 0.1}})
    assert a.choice == "a" and a.confidence == 0.9 and a.top(1)[0][0] == "a"
    n = normalize_answer({"probability": 0.8})
    assert n.choice == "yes" and n.probs["no"] == pytest.approx(0.2)
    lst = normalize_answer({"probabilities": [0.1, 0.9]}, ["x", "y"])
    assert lst.choice == "y" and lst.confidence == 0.9


def test_plan_uses_choice_questions_within_clef_limits(project):
    st = Stub({"route": "edit", "then": "none"})
    r = Router(Store(project), st)
    plan = run(r.plan(Ctx("troque o corpo", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit"]
    assert set(st.calls[0][1]) == {"img_1", "img_2"}                          # 1ª análise: o que há em cada imagem
    qs = call_with(st, "route")[1]
    assert set(qs) == {"route", "then"} and all(2 <= len(q["criteria"]) <= 26 for q in qs.values())
    assert qs["route"]["criteria"]["edit"].startswith("General image editor")
    assert "recipe__trocar_pessoa_e_fundo" not in qs["route"]["criteria"]    # a receita fixa a imagem 3
    assert call_with(st, "route")[0]["attached_images"][1]["name"] == "img2.png"


def test_clef_can_chain_a_second_workflow(project):
    st = Stub({"route": "edit", "then": "background_removal"})
    plan = run(Router(Store(project), st).plan(Ctx("troque o corpo e depois tire o fundo", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit", "background_removal"]


def test_then_step_needs_the_images_and_some_confidence(project):
    # "Make both woman from the <image1> kissing each other": o Clef real escolhia editar e depois 'transferir objeto
    # entre imagens', que precisa de 2 fotos e só tinha 1. A etapa seguinte recebe o resultado NO LUGAR de uma foto.
    st = Stub({"route": "edit", "then": "none"})
    run(Router(Store(project), st).plan(Ctx("make both women kiss", [img(1)])))
    then = call_with(st, "route")[1]["then"]["criteria"]
    assert "edit" in then and "background_removal" in then
    assert "transfer_object" not in then and "transfer_multi" not in then

    class Unsure(Stub):                                  # etapa seguinte escolhida com pouca certeza: fica de fora
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            if "then" in out:
                out["then"] = Answer("background_removal", 0.3, {"background_removal": 0.3, "none": 0.29})
            return out
    plan = run(Router(Store(project), Unsure({"route": "edit"})).plan(Ctx("x", [img(1)])))
    assert [s.workflow for s in plan.steps] == ["edit"]


def test_workflows_that_need_more_images_are_not_offered(project):
    st = Stub()
    run(Router(Store(project), st).plan(Ctx("x", [img(1)])))
    crit = st.calls[0][1]["route"]["criteria"]
    assert "edit" in crit and "transfer_object" not in crit        # transferir precisa de 2 imagens


def test_disabled_workflow_is_never_offered_and_recipe_hidden(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["troca_pessoa"]["enabled"] = False
    s.save_clef_config(cfg)
    st = Stub()
    run(Router(s, st).plan(Ctx("x", [img(1), img(2), img(3)])))
    crit = call_with(st, "route")[1]["route"]["criteria"]
    assert "troca_pessoa" not in crit and "recipe__trocar_pessoa_e_fundo" not in crit and "edit" in crit


def test_recipe_is_a_route_and_expands_to_steps(project):
    st = Stub({"route": "recipe__trocar_pessoa_e_fundo", "role_scene": "user_2", "role_person": "user_3",
               "role_background": "user_1"})
    plan = run(Router(Store(project), st).plan(Ctx("troque a pessoa e o fundo", [img(1), img(2), img(3)])))
    assert [s.workflow for s in plan.steps] == ["troca_pessoa", "transfer_object"]
    assert plan.steps[0].mode == "turbo" and plan.steps[0].vars == {"corpo": "both", "quem": "person"} and plan.steps[1].mode == "turbo"
    # os papéis, não a ordem de envio, decidem quem é quem: cena = imagem 2, pessoa = imagem 3, ambiente = imagem 1
    assert plan.steps[0].inputs == {"image_1": "user:2", "image_2": "user:3"}
    assert plan.steps[1].inputs == {"image_1": "user:1", "image_2": "previous"}
    assert {d["question"] for d in plan.decisions} >= {"role_scene", "role_person", "role_background"}


def test_forced_route_skips_clef(project):
    st = Stub()
    plan = run(Router(Store(project), st).plan(Ctx("x", [img(1), img(2)]), forced="edit"))
    assert plan.steps[0].workflow == "edit" and not st.calls and plan.decisions[0]["source"] == "user"


def test_low_confidence_marks_plan_ambiguous(project):
    s = Store(project)
    s.save_settings({"clef": {"ask_below": 0.95}})
    plan = run(Router(s, Stub()).plan(Ctx("x", [img(1), img(2)])))
    assert plan.ambiguous and len(plan.ambiguous) == 3


def test_more_than_26_options_are_split_in_two_stages(project):
    st = Stub()
    r = Router(project and Store(project), st)
    opts = {f"w{i}": (f"descricao {i}", f"g{i % 5}") for i in range(40)}
    ans = run(r._ask(Ctx("x", [img(1)]), "t", [("route", "qual?", opts)]))
    assert len(st.calls) == 2 and all(len(q["criteria"]) <= 26 for c in st.calls for q in c[1].values())
    assert ans["route"].choice in opts


def test_bind_assigns_each_slot_a_different_image_following_clef(project):
    s = Store(project)
    st = Stub({"slot_image_1": "user_2", "slot_image_2": "user_1", "preset": "livre"})
    ctx = Ctx("coloque a pessoa da imagem 1 na cena da imagem 2", [img(1), img(2)])
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    b = run(Router(s, st).bind(ctx, Step("edit"), src))
    assert b.slots == slots8(image_1="user_2", image_2="user_1")
    assert b.preset_id == "livre"


def test_bind_never_reuses_an_image_when_clef_repeats_itself(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "user_1"})
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit", prompt="estilo"), src))
    assert sorted(v for v in b.slots.values() if v) == ["user_1", "user_2"]


def test_bind_respects_recipe_pins_and_skips_clef(project):
    st = Stub()
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b"), Source("previous", "prev", b"c")]
    step = Step("transfer_object", "objeto", {"image_1": "user:2", "image_2": "previous"}, "turbo", None, {"relacao": "adicionar"})
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), step, src))
    assert b.slots == {"image_1": "user_2", "image_2": "previous"} and b.preset_id == "objeto" and not st.calls


def test_only_prompts_selected_in_clef_file_are_offered(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["edit"]["prompts"] = ["estilo"]
    s.save_clef_config(cfg)
    st = Stub()
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(s, st).bind(Ctx("x", [img(1), img(2)]), Step("edit"), src))
    assert b.preset_id == "estilo" and "preset" not in st.calls[0][1]


def test_move_and_swap_route_to_the_red_green_workflow(project):
    r = Router(Store(project), MockClef())
    plan = run(r.plan(Ctx("make two objects swap places inside the image, I marked the red and green boxes", [img(1)])))
    assert plan.steps[0].workflow == "object_move"


def test_internal_steps_are_never_offered_alone_but_the_recipes_that_use_them_are(project):
    st = Stub()
    run(Router(Store(project), st).plan(Ctx("cole a pessoa neste cenário", [img(1), img(2)])))
    crit = call_with(st, "route")[1]["route"]["criteria"]
    assert "colar_recortar" not in crit and "colar_posicionar" not in crit
    assert {"recipe__colar_e_fundir", "recipe__colar_sem_fundir", "colar_fundir"} <= set(crit)
    assert "colar_recortar" not in call_with(st, "route")[1].get("then", {}).get("criteria", {})


def test_paste_recipes_route_by_the_request_and_pin_the_right_images(project):
    r = Router(Store(project), Stub({"role_background": "user_1", "role_subject": "user_2"}))
    plan = run(r.plan(Ctx("colar a pessoa da segunda imagem no cenário da primeira", [img(1), img(2)]), "recipe__colar_e_fundir"))
    assert [s.workflow for s in plan.steps] == ["colar_recortar", "colar_posicionar", "colar_fundir"]
    assert plan.steps[0].inputs == {"image_1": "user:2"} and plan.steps[1].inputs == {"image_1": "user:1", "image_2": "previous"}
    assert plan.steps[2].inputs == {"image_1": "previous"}
    swapped = run(Router(Store(project), Stub({"role_background": "user_2", "role_subject": "user_1"})).plan(
        Ctx("cole a cadeira da imagem 1 no quarto da imagem 2", [img(1), img(2)]), "recipe__colar_e_fundir"))
    assert swapped.steps[0].inputs == {"image_1": "user:1"} and swapped.steps[1].inputs["image_1"] == "user:2"   # papéis, não ordem
    with_one = run(Router(Store(project), MockClef()).plan(Ctx("colar a pessoa no cenário", [img(1)])))
    assert [s.workflow for s in with_one.steps] != ["colar_recortar", "colar_posicionar", "colar_fundir"]   # uma imagem só: não há o que colar


# ------------------------------------------------- modos e LoRAs escolhidas pelo Clef
def slots8(**kw):
    """As oito entradas de imagem do grafo; as não citadas ficam vazias."""
    return {f"image_{i}": kw.get(f"image_{i}") for i in range(1, 9)}


def _src(n):
    return [Source(f"user_{i}", f"Imagem {i}", b"x") for i in range(1, n + 1)]


def test_mode_is_a_separate_question_and_defaults_to_turbo(project):
    st = Stub()
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1)]), Step("object_move", "mover"), _src(1)))
    assert b.mode == "turbo" and set(st.calls[0][1]) == {"mode"}
    crit = st.calls[0][1]["mode"]["criteria"]
    assert list(crit) == ["turbo", "quality"] and "default" in crit["turbo"]
    q = run(Router(Store(project), Stub({"mode": "quality"})).bind(Ctx("x", [img(1)]), Step("object_move", "mover"), _src(1)))
    assert q.mode == "quality"
    pinned = Stub()
    p = run(Router(Store(project), pinned).bind(Ctx("x", [img(1)]), Step("object_move", "mover", mode="quality"), _src(1)))
    assert p.mode == "quality" and not pinned.calls              # o caso de uso fixou o modo: sem pergunta


def test_clef_decides_loras_one_question_each(project):
    st = Stub({"lora_bfs_body": "usar", "preset": "livre"})
    b = run(Router(Store(project), st).bind(Ctx("troque o corpo", [img(1), img(2)]), Step("edit"), _src(2)))
    qs = st.calls[0][1]
    assert {"lora_bfs_body", "lora_bfs_head", "mode", "preset"} <= set(qs)
    assert list(qs["lora_bfs_body"]["criteria"]) == ["none", "usar"] and "BODY" in qs["lora_bfs_body"]["criteria"]["usar"]
    assert b.loras == ["bfs_body"] and any(d["question"] == "lora_bfs_body" for d in b.decisions)


def test_loras_of_the_same_group_are_not_turned_on_together(project):
    class Both(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            out["lora_bfs_body"] = Answer("usar", 0.9, {"usar": 0.9, "none": 0.1})
            out["lora_bfs_head"] = Answer("usar", 0.95, {"usar": 0.95, "none": 0.05})
            return out
    b = run(Router(Store(project), Both({"preset": "livre"})).bind(Ctx("x", [img(1), img(2)]), Step("edit"), _src(2)))
    assert b.loras == ["bfs_head"]                       # as duas trocam pessoa: fica a mais segura
    c = run(Router(Store(project), Both({"preset": "trocar_pessoas"})).bind(Ctx("x", [img(1), img(2)]), Step("edit"), _src(2)))
    assert c.loras == []                                 # o prompt já trouxe uma LoRA de troca


def test_loras_that_need_two_images_are_not_asked_with_one_image(project):
    st = Stub()
    run(Router(Store(project), st).bind(Ctx("x", [img(1)]), Step("edit"), _src(1)))
    asked = {q for q in st.calls[0][1] if q.startswith("lora_")}
    assert "lora_bfs_body" not in asked and "lora_outfit_swap" not in asked      # as de troca precisam de 2 imagens


def test_clef_picks_person_or_object_cutout_from_the_request(project):
    src = [Source("user_1", "i1", b"a")]
    bind = lambda picks, text: run(Router(Store(project), Stub(picks)).bind(Ctx(text, [img(1)]), Step("colar_recortar"), src))  # noqa: E731
    per = bind({"var_alvo": "person"}, "cole ela neste cenário")
    obj = bind({"var_alvo": "chair", "var_cor": "blue"}, "cole a cadeira azul neste cenário")
    alone = bind({"var_alvo": "alone"}, "cole este produto")
    assert (per.mode, obj.mode, alone.mode) == ("pessoa", "objeto", "fundo")       # a opção escolhida diz o modo
    assert obj.vars == {"alvo": "chair", "cor": "blue"}
    store = Store(project)
    from pickflow import pvars
    preset = store.prompts("colar_recortar")["presets"][0]
    assert pvars.render(preset, obj.vars)["text"] == "blue chair"                  # o que o SAM3 procura
    assert store.manifest("colar_recortar", "pessoa")["prompt"]["node"] == 4 and store.manifest("colar_recortar", "objeto")["prompt"]["node"] == 4
    assert store.manifest("colar_recortar", "objeto")["file"] == "PickFlow Colar - Recortar Pessoa.json"
    assert store.manifest("colar_recortar", "fundo")["file"] == "PickFlow Colar - Recortar.json" and not store.manifest("colar_recortar", "fundo").get("prompt")
    assert store.manifest("colar_recortar", "pessoa")["review"] == "cutout"


def test_recipes_only_appear_with_the_exact_number_of_images(project):
    for n, has_paste, has_swap in ((2, True, False), (3, False, True), (4, False, False)):
        st = Stub()
        run(Router(Store(project), st).plan(Ctx("x", [img(i) for i in range(1, n + 1)])))
        crit = call_with(st, "route")[1]["route"]["criteria"]
        assert ("recipe__colar_e_fundir" in crit) is has_paste and ("recipe__trocar_pessoa_e_fundo" in crit) is has_swap


def test_images_are_described_first_and_the_labels_reach_every_later_question(project):
    class Seer(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            for q in questions:
                if q.startswith("img_"):
                    kind = {"img_1": "place", "img_2": "person", "img_3": "person"}[q]
                    out[q] = Answer(kind, 0.9, {kind: 0.9})
            return out
    st = Seer({"route": "recipe__trocar_pessoa_e_fundo"})
    ctx = Ctx("coloque a pessoa da imagem 3 na pose da imagem 2. O lugar é a imagem 1.", [img(1), img(2), img(3)])
    run(Router(Store(project), st).plan(ctx))
    assert ctx.contents == {1: "place/setting without people", 2: "person", 3: "person"}
    seen = call_with(st, "route")[0]["attached_images"]
    assert seen[0]["content"] == "place/setting without people" and seen[1]["content"] == "person"
    role = call_with(st, "role_background")[1]["role_background"]
    assert "Image 1 (place/setting without people)" in role["criteria"]["user_1"]


def test_roles_follow_the_clef_even_when_positions_disagree_and_never_repeat_an_image(project):
    r = Router(Store(project), Stub({"route": "recipe__trocar_pessoa_e_fundo", "role_scene": "user_2",
                                     "role_person": "user_2", "role_background": "user_1"}))
    plan = run(r.plan(Ctx("x", [img(1), img(2), img(3)])))
    used = [plan.steps[0].inputs["image_1"], plan.steps[0].inputs["image_2"], plan.steps[1].inputs["image_1"]]
    assert len(set(used)) == 3                                     # a mesma imagem não vira dois papéis


def test_roles_fall_back_to_the_order_of_upload_when_the_clef_has_no_opinion(project):
    class Blank(Stub):
        async def decide(self, state, questions, images=None):
            return {q: Answer(next(iter(sp["criteria"])), 0.34, {k: 1 / len(sp["criteria"]) for k in sp["criteria"]})
                    for q, sp in questions.items()}
    r = Router(Store(project), Blank())
    plan = run(r.plan(Ctx("x", [img(1), img(2), img(3)]), forced="recipe__trocar_pessoa_e_fundo"))
    assert plan.steps[0].inputs == {"image_1": "user:1", "image_2": "user:2"} and plan.steps[1].inputs["image_1"] == "user:3"


def test_a_doubtful_route_gets_a_second_look_among_the_top_candidates(project):
    class Doubt(Stub):
        async def decide(self, state, questions, images=None):
            self.calls.append((state, questions, images))
            out = {}
            for q, sp in questions.items():
                keys = list(sp["criteria"])
                if q == "route" and len(keys) > 3:                  # 1ª vez: muita dúvida
                    out[q] = Answer(keys[0], 0.3, {k: (0.3 if i == 0 else 0.7 / (len(keys) - 1)) for i, k in enumerate(keys)})
                elif q == "route":                                  # 2ª vez, só 3 opções: escolhe a última com firmeza
                    out[q] = Answer(keys[-1], 0.7, {k: (0.7 if k == keys[-1] else 0.15) for k in keys})
                else:
                    out[q] = Answer(keys[0], 0.8, {k: 0.8 if k == keys[0] else 0.1 for k in keys})
            return out
    st = Doubt()
    plan = run(Router(Store(project), st).plan(Ctx("x", [img(1), img(2)])))
    routes = [c for c in st.calls if "route" in c[1]]
    assert len(routes) == 2 and len(routes[1][1]["route"]["criteria"]) == 3
    assert any(d["question"] == "route_2" for d in plan.decisions) and plan.ambiguous is None


def test_recipe_images_and_roles_survive_saving_the_clef_config(project):
    s = Store(project)
    cfg = s.clef_config()
    swap = next(r for r in cfg["recipes"] if r["id"] == "trocar_pessoa_e_fundo")
    assert swap["images"] == 3 and [x["key"] for x in swap["roles"]] == ["scene", "person", "background"]
    swap["roles"][2]["about"] = "meu texto"
    s.save_clef_config(cfg)
    again = next(r for r in Store(project).clef_config()["recipes"] if r["id"] == "trocar_pessoa_e_fundo")
    assert again["roles"][2]["about"] == "meu texto" and again["images"] == 3


def test_multi_transfer_puts_the_picked_destination_first_and_the_objects_after(project):
    st = Stub({"var_dest": "3", "var_objects": "2", "mode": "turbo"})
    ctx = Ctx("coloque os objetos das imagens 1 e 2 na imagem 3", [img(1), img(2), img(3)])
    b = run(Router(Store(project), st).bind(ctx, Step("transfer_multi"), _src(3)))
    assert b.preset_id == "varios"
    assert b.slots["image_1"] == "user_3"                       # o destino vira a entrada 1
    assert b.slots["image_2"] == "user_1" and b.slots["image_3"] == "user_2"
    assert b.vars["objects"] == "2" and b.vars["dest"] == "3"
    assert not b.slots["image_4"]


# ------------------------------------------------------- levar algo de uma imagem para a outra: intenção
def test_replace_intent_moves_a_paste_route_to_transfer_with_the_replace_variant(project):
    st = Stub({"route": "recipe__colar_e_fundir", "then": "none", "paste_intent": "substituir"})
    plan = run(Router(Store(project), st).plan(Ctx("coloque o coringa no lugar do cara de vermelho", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["transfer_object"] and plan.steps[0].vars == {"relacao": "substituir"}
    d = {x["question"]: x for x in plan.decisions}
    assert d["paste_intent"]["chosen"] == "substituir" and d["route_intent"]["chosen"] == "transfer_object"


def test_add_intent_moves_transfer_or_edit_to_paste_and_keeps_only_paste(project):
    st = Stub({"route": "transfer_object", "then": "none", "paste_intent": "acrescentar"})
    plan = run(Router(Store(project), st).plan(Ctx("coloque o batman sentado no banco", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["colar_recortar", "colar_posicionar", "colar_fundir"]
    st = Stub({"route": "recipe__colar_sem_fundir", "then": "none", "paste_intent": "acrescentar"})
    plan = run(Router(Store(project), st).plan(Ctx("só cole o peão ao lado", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["colar_recortar", "colar_posicionar"]


def test_person_swap_intent_moves_the_route_to_the_person_swap_flow_and_the_next_step_never_repeats_the_asked_route(project):
    # "replace the woman in image 1 with the man in image 2, face and body": o fluxo de trocar pessoa, não o transferir objeto
    st = Stub({"route": "edit", "then": "none", "paste_intent": "trocar"})
    plan = run(Router(Store(project), st).plan(Ctx("replace the woman in image 1 with the man in image 2, face and body", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["troca_pessoa"]
    st = Stub({"route": "transfer_object", "then": "none", "paste_intent": "trocar"})
    plan = run(Router(Store(project), st).plan(Ctx("body swap the woman with the man", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["troca_pessoa"]
    # o `then` é respondido antes de a rota mudar pela intenção: se ele pede a rota de que partiu, não vira um 2º passo igual
    st = Stub({"route": "edit", "then": "edit", "paste_intent": "substituir"})
    plan = run(Router(Store(project), st).plan(Ctx("put the man in place of the woman", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["transfer_object"]


def test_the_intent_question_is_only_asked_with_two_images_and_a_paste_like_route(project):
    st = Stub({"route": "object_move", "then": "none"})
    run(Router(Store(project), st).plan(Ctx("mova a cadeira", [img(1), img(2)])))
    assert not any("paste_intent" in c[1] for c in st.calls)
    st = Stub({"route": "edit", "then": "none"})
    run(Router(Store(project), st).plan(Ctx("deixe mais quente", [img(1)])))
    assert not any("paste_intent" in c[1] for c in st.calls)


# ------------------------------------------------------------------ modo escolhido no chat
def test_mode_chosen_in_the_chat_beats_the_clef_and_the_question_is_not_asked(project):
    st = Stub({"preset": "livre"})
    src = [Source("user_1", "Imagem 1", b"a")]
    b = run(Router(Store(project), st).bind(Ctx("deixe a luz mais suave", [img(1)]), Step("edit"), src, mode="quality"))
    assert b.mode == "quality"
    assert not any("mode" in q for _s, q, _i in st.calls)          # a pergunta de modo não vai mais ao Clef
    d = next(d for d in b.decisions if d["question"] == "mode")
    assert d["source"] == "user" and d["chosen"] == "quality" and d["chosen_name"] == "Qualidade"


def test_the_mode_chosen_in_the_chat_also_beats_a_mode_pinned_by_the_use_case(project):
    """Um caso de uso que fixa `mode: turbo` não impede você de pedir Qualidade naquele pedido."""
    st = Stub()
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    rec = next(r for r in Store(project).clef_config()["recipes"] if (r["steps"][0].get("mode") or "") == "turbo")
    step = Router(Store(project), st).steps_from_recipe(rec)[0]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), step, src, mode="quality"))
    assert b.mode == "quality"


def test_describe_images_asks_each_window_about_its_own_images(project):
    """O Clef só recebe `max_images` por requisição: com 5 imagens, cada janela leva as perguntas das suas."""
    st = Stub()
    st.max_images = 2
    ctx = Ctx("x", [img(1), img(2), img(3), img(4), img(5)])
    run(Router(Store(project), st).describe_images(ctx))
    assert [sorted(q) for _s, q, _i in st.calls] == [["img_1", "img_2"], ["img_3", "img_4"], ["img_5"]]
    assert [len(i) for _s, _q, i in st.calls] == [2, 2, 1]         # nenhuma requisição acima do limite
    assert set(ctx.contents) == {1, 2, 3, 4, 5}


# --------------------------------------------------------- aprendizado com o feedback
def test_the_state_carries_the_measured_quality_of_each_image(project):
    """O Clef decide melhor sabendo tamanho e nitidez (é o que separa restaurar de melhorar sem aumentar)."""
    im = ImageRef("i1", "a.png", b"x", 640, 480, "640x480, 0.3 MP, soft focus")
    st = Router(Store(project), Stub())._state(Ctx("melhore", [im]), "route_image_request")
    assert st["attached_images"][0]["quality"] == "640x480, 0.3 MP, soft focus"
    sem = Router(Store(project), Stub())._state(Ctx("x", [img(1)]), "t")
    assert "quality" not in sem["attached_images"][0]              # sem medida, o campo nem aparece


def test_the_history_corrects_the_route_and_shows_why(project, tmp_path):
    from pickflow.feedback import FeedbackStore

    fb = FeedbackStore(tmp_path)
    for i in range(10):                                           # o histórico diz que "edit" erra e troca_roupa acerta
        fb.add("c", f"a{i}", "troque a roupa", "edit", "edit/livre", 0.7, "down", "", "user")
        fb.add("c", f"b{i}", "troque a roupa", "troca_roupa", "troca_roupa/padrao", 0.7, "up", "", "user")
    ans = Answer("edit", 0.7, {"edit": 0.7, "troca_roupa": 0.3})
    decisions = []
    out = Router(Store(project), Stub(), fb)._correct(ans, ans.probs, "route", "", "Rota (pelo seu histórico)",
                                                      {"edit": "Editar imagem", "troca_roupa": "Trocar roupa"}, decisions)
    assert out.choice == "troca_roupa"
    assert decisions and decisions[0]["source"] == "histórico" and decisions[0]["chosen"] == "troca_roupa"
    # sem histórico e com o peso em 0, nada muda
    vazio = Router(Store(project), Stub(), FeedbackStore(tmp_path / "vazio"))
    assert vazio._correct(ans, ans.probs, "route", "", "x", {}, []).choice == "edit"


def test_a_low_confidence_preset_asks_on_screen_and_a_closed_window_keeps_the_clef(project):
    class Real(Stub):
        mode = "systemone"                                       # só o Clef de verdade pergunta

    asked = []

    async def ask(payload):
        asked.append(payload)
        return "livre"

    r = Router(Store(project), Real({"preset": "estilo"}))
    ans = Answer("estilo", 0.3, {"estilo": 0.3, "livre": 0.28})
    decisions = []
    out = run(r._maybe_ask(ask, "preset", "Qual usar?", "Prompt", ans,
                           {"estilo": "Mesmo estilo", "livre": "Pedido do usuário"}, 0.5, decisions))
    assert asked and asked[0]["kind"] == "choose" and len(asked[0]["options"]) == 2
    assert out.choice == "livre" and decisions[0]["source"] == "user"

    async def fechou(payload):
        return "__auto__"                                        # fechar a janela = "segue o Clef"

    assert run(r._maybe_ask(fechou, "preset", "t", "Prompt", ans, {}, 0.5, [])).choice == "estilo"
    # confiança acima do limite não pergunta; e o modo de teste (mock) nunca pergunta
    assert run(r._maybe_ask(fechou, "preset", "t", "Prompt", Answer("livre", 0.9, {}), {}, 0.5, [])).choice == "livre"
    assert run(Router(Store(project), Stub())._maybe_ask(fechou, "preset", "t", "Prompt", ans, {}, 0.5, [])).choice == "estilo"


def test_similar_requests_that_worked_go_into_the_state(project, tmp_path):
    from pickflow.feedback import FeedbackStore

    fb = FeedbackStore(tmp_path)
    fb.add("c", "r1", "põe o cachorro na praia", "recipe__colar_e_fundir", "", 0.8, "up", "", "user")
    r = Router(Store(project), Stub(), fb)
    st = r._examples(Ctx("põe o cachorro na praia ao pôr do sol", [img(1)]), {"recipe__colar_e_fundir": "Colar e fundir"})
    assert st and st["similar_requests"][0]["chosen_flow"] == "Colar e fundir"
    # nada parecido: não injeta nada
    assert r._examples(Ctx("gere um gato de chapéu", [img(1)]), {}) is None


# ------------------------------------ vestir a pessoa e levar para outro lugar (peça de roupa sozinha)
def test_the_clothing_recipe_expands_to_dress_then_place(project):
    """Pessoa + roupa (peça sozinha) + lugar: troca a roupa e leva ela para o lugar — não é troca de pessoa."""
    st = Stub({"route": "recipe__vestir_e_levar", "then": "none"})
    plan = run(Router(Store(project), st).plan(
        Ctx("make the woman of image 1 wear the clothes of image 2 at image 3", [img(1), img(2), img(3)])))
    assert [s.workflow for s in plan.steps] == ["troca_roupa", "colar_recortar", "colar_posicionar", "colar_fundir"]
    # os papéis foram resolvidos pela ordem de envio (person=imagem 1, roupa=imagem 2)
    assert plan.steps[0].inputs == {"image_1": "user:1", "image_2": "user:2"}
    assert plan.steps[1].mode == "pessoa" and plan.steps[3].prompt == "pessoa"
    assert plan.route == "recipe__vestir_e_levar"


# ------------------- vestir com a roupa da referência e DEPOIS trocar a pessoa (roupa explícita no pedido)
def test_the_dress_then_swap_recipe_uses_the_dressed_base_as_the_new_base(project):
    """Roupa explícita: veste a base com o outfit da referência e só então troca a pessoa em cima do resultado."""
    st = Stub({"route": "recipe__vestir_e_trocar", "then": "none"})
    plan = run(Router(Store(project), st).plan(
        Ctx("swap the person of image 1 for the woman of image 2 wearing her clothes", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["troca_roupa", "troca_pessoa"]
    assert plan.steps[0].inputs == {"image_1": "user:1", "image_2": "user:2"}
    # o 2º passo recebe o RESULTADO do 1º (a base já vestida) e a mesma referência
    assert plan.steps[1].inputs == {"image_1": "previous", "image_2": "user:2"}
    assert plan.steps[1].vars == {"corpo": "both", "quem": "person"}
    assert plan.route == "recipe__vestir_e_trocar"


def test_swap_then_dress_with_the_scene_clothes_then_place_recipe(project):
    """Pose e roupa da imagem 1, pessoa da imagem 2, lugar da imagem 3: troca a pessoa, veste com a roupa da CENA (a troca
    traz a roupa da referência) e só então recorta e funde no lugar."""
    st = Stub({"route": "recipe__trocar_pessoa_roupa_e_fundo", "then": "none",
               "role_scene": "user_1", "role_person": "user_2", "role_background": "user_3"})
    plan = run(Router(Store(project), st).plan(Ctx(
        "swap the person of image 2 to make the same pose of image 1, using the clothes of image 1, at the mirror of image 3",
        [img(1), img(2), img(3)])))
    assert [s.workflow for s in plan.steps] == ["troca_pessoa", "troca_roupa", "colar_recortar", "colar_posicionar", "colar_fundir"]
    assert plan.steps[0].inputs == {"image_1": "user:1", "image_2": "user:2"} and plan.steps[0].vars == {"corpo": "both", "quem": "person"}
    # a roupa é vestida em cima do resultado da troca, tendo a CENA (imagem 1) como referência de roupa
    assert plan.steps[1].inputs == {"image_1": "previous", "image_2": "user:1"} and plan.steps[1].vars == {"quem": "person"}
    assert plan.steps[3].inputs == {"image_1": "user:3", "image_2": "previous"}
    assert plan.route == "recipe__trocar_pessoa_roupa_e_fundo"


def test_outpaint_has_its_own_route_that_pins_the_expand_prompt(project):
    """"Outpaint this image" precisa de uma opção própria na rota (antes só existia o prompt `expandir` dentro do `edit`,
    e o Clef real dava a rota a `pessoa_angulo` e `background_removal`)."""
    st = Stub({"route": "recipe__expandir_imagem", "then": "none"})
    plan = run(Router(Store(project), st).plan(Ctx("Outpaint this image", [img(1)])))
    crit = next(c for c in st.calls if "route" in c[1])[1]["route"]["criteria"]
    assert "outpaint" in crit["recipe__expandir_imagem"].lower()
    assert [(s.workflow, s.prompt, s.inputs) for s in plan.steps] == [("edit", "expandir", {"image_1": "user:1"})]
    assert plan.route == "recipe__expandir_imagem"
    assert "outpaint" not in crit["edit"].lower()          # o `edit` não disputa mais o pedido


def test_the_clef_can_say_an_image_is_clothing_alone(project):
    """A análise da imagem distingue "roupa sozinha" de "pessoa": é o que evita o fluxo errado."""

    class Garment(Stub):
        async def decide(self, state, questions, images=None):
            out = {}
            for q, spec in questions.items():
                keys = list(spec["criteria"])
                assert "garment" in keys
                out[q] = Answer("garment", 0.9, {k: (0.9 if k == "garment" else 0.02) for k in keys})
            return out

    ctx = Ctx("x", [img(1), img(2)])
    run(Router(Store(project), Garment()).describe_images(ctx))
    assert ctx.contents == {1: "clothing alone (nobody wearing it)", 2: "clothing alone (nobody wearing it)"}


