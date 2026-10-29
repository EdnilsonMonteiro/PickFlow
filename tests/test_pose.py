"""Mudar a pose: ler o DWPose, desenhar o mapa, descrever a mudança em palavras e o caso de uso inteiro (ComfyUI de mentira)."""
import copy
import io
import json

from PIL import Image

from pickflow import pose
from fake_comfy import fake_openpose
from test_e2e import api, png, start, upload, wait_event  # noqa: F401  (fixture e helpers)


def _people(centers=(0.3, 0.7)):
    return pose.parse(fake_openpose(centers=centers))


def test_parse_gives_fractions_and_marks_unseen_points():
    raw = fake_openpose()
    raw[0]["people"][0]["pose_keypoints_2d"][3 * 4:3 * 4 + 3] = [0, 0, 0]          # punho direito não visto
    p = pose.parse(raw)[0]
    assert len(p) == 60 and all(0 <= q[0] <= 1 and 0 <= q[1] <= 1 for q in p)       # 18 do corpo + 21 + 21 das mãos
    assert p[4] == [0.0, 0.0, 0.0] and p[1][2] >= pose.MIN_CONF
    assert abs(p[1][0] - 0.3) < 0.01                                               # pescoço em ~30% da largura
    assert pose.parse("[]") == [] and pose.parse(None) == []


def test_usable_drops_repeated_tiny_and_incomplete_people_and_sorts_left_to_right():
    a, b = _people((0.7, 0.3))
    dup = copy.deepcopy(a)
    tiny = [[0.5 + (q[0] - 0.7) * 0.05, 0.5 + (q[1] - 0.5) * 0.05, 1.0] for q in a]
    few = [[0, 0, 0] for _ in range(18)]
    few[1], few[2] = [0.4, 0.4, 1.0], [0.45, 0.4, 1.0]
    out = pose.usable([a, dup, b, tiny, few])
    assert len(out) == 2 and out[0][1][0] < out[1][1][0]


def test_describe_says_who_moved_and_how_and_stays_empty_when_nothing_moved():
    orig = _people()
    assert pose.describe(orig, copy.deepcopy(orig), 200, 300) == ""
    e = copy.deepcopy(orig)
    e[1][3] = [e[1][2][0] + 0.02, e[1][2][1] - 0.1, 1.0]                                  # 2ª pessoa: cotovelo para cima
    e[1][4] = [e[1][2][0] + 0.03, e[1][2][1] - 0.22, 1.0]                                 # punho acima da cabeça
    t = pose.describe(orig, e, 200, 300)
    assert t.startswith("Pose change: the second person from the left") and "pointing up" in t and "above the head" in t
    assert "first person" not in t and t.endswith("Everyone else keeps their pose.")
    assert pose.describe(orig[:1], [e[1]][:0] or [copy.deepcopy(orig[0])], 200, 300) == ""
    solo = pose.describe(orig[:1], [e[0]], 200, 300)
    assert solo == ""                                                                      # a 1ª pessoa não mexeu


def test_combine_prefers_the_separated_people_and_keeps_only_extra_people_the_sam_missed():
    a, b = _people((0.35, 0.5))                                                           # duas pessoas coladas, achadas separadas
    mashed = [[(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, 1.0] for p, q in zip(a, b)]          # o que o DWPose faz na foto inteira: um esqueleto no meio
    far = _people((0.9,))[0]                                                              # alguém que o SAM3 não pegou
    out = pose.combine([[a], [b]], [mashed, far])
    assert len(out) == 3 and out[0] == a and out[1] == b and out[2] == far
    assert pose.combine([[a], [b]], [a, b]) == [a, b]                                      # as mesmas pessoas na foto inteira não repetem
    assert pose.combine([[], []], [far]) == [far]                                          # o SAM3 não achou ninguém: vale a foto inteira
    frames = pose.parse_frames(json.dumps([{"people": fake_openpose()[0]["people"][:1], "canvas_width": 200, "canvas_height": 300},
                                           {"people": [], "canvas_width": 200, "canvas_height": 300}]))
    assert len(frames) == 2 and len(frames[0]) == 1 and frames[1] == []


def test_erase_boxes_and_text_cover_only_the_limbs_that_moved_where_they_used_to_be():
    orig = _people()
    e = copy.deepcopy(orig)
    e[1][3] = [e[1][2][0] + 0.02, e[1][2][1] - 0.1, 1.0]                                  # cotovelo e punho da 2ª pessoa vão para cima
    e[1][4] = [e[1][2][0] + 0.03, e[1][2][1] - 0.22, 1.0]
    boxes = pose.erase_boxes(orig, e, 200, 300)
    assert len(boxes) == 1
    x, y, w, h = boxes[0]
    inside = lambda pt: x <= pt[0] <= x + w and y <= pt[1] <= y + h                        # noqa: E731
    assert inside(orig[1][2]) and inside(orig[1][3]) and inside(orig[1][4])                # o braço antigo (ombro, cotovelo, punho)
    assert not inside(e[1][4]) and not inside(orig[1][10]) and not inside(orig[0][4])      # nem o braço novo, nem a perna, nem a outra pessoa
    text = pose.erase_text(orig, e, 200, 300)
    assert "the old arm on the" in text and "second person from the left" in text and "Rebuild what was behind it" in text
    assert pose.erase_boxes(orig, copy.deepcopy(orig), 200, 300) == [] and pose.erase_text(orig, copy.deepcopy(orig), 200, 300) == ""
    only_visible = copy.deepcopy(e)
    only_visible[1][10] = [0, 0, 0]                                                       # um ponto da perna sumiu: a perna não conta como mexida
    assert len(pose.erase_boxes(orig, only_visible, 200, 300)) == 1


def test_every_edit_the_screen_counts_is_also_counted_by_the_backend():
    # a tela conta qualquer ponto mexido; o backend recusava ("nenhuma pose foi alterada") tudo que não fosse um braço ou perna girando muito
    orig = _people()

    def edit(fn):
        e = copy.deepcopy(orig); fn(e[1]); return e
    shift = lambda p: [p.__setitem__(i, [p[i][0] + 0.1, p[i][1], p[i][2]]) for i in range(18) if p[i][2] > 0.3]      # noqa: E731
    cases = {
        "neck drag (whole person)": edit(shift),
        "head tilt": edit(lambda p: p.__setitem__(0, [p[0][0] + 0.05, p[0][1] + 0.01, 1.0])),
        "shoulder up": edit(lambda p: p.__setitem__(2, [p[2][0], p[2][1] - 0.04, 1.0])),
        "small elbow move": edit(lambda p: p.__setitem__(3, [p[3][0] - 0.02, p[3][1], 1.0])),
        "tiny move": edit(lambda p: p.__setitem__(3, [p[3][0] + 0.005, p[3][1], 1.0])),
    }
    for name, e in cases.items():
        assert pose.changed(orig[1], e[1]), name
        assert pose.describe(orig, e, 200, 300).startswith("Pose change: the second person from the left"), name
    assert "moved to the right" in pose.describe(orig, cases["neck drag (whole person)"], 200, 300)
    assert "head tilted" in pose.describe(orig, cases["head tilt"], 200, 300)
    assert pose.describe(orig, copy.deepcopy(orig), 200, 300) == ""                        # nada mexeu: segue vazio (e o fluxo recusa)


def test_clean_validates_what_comes_from_the_screen():
    ok = pose.clean(_people())
    assert len(ok) == 2 and all(len(p) == 18 for p in ok)
    for bad in ("x", [[[0, 0, 1]] * 17], None):
        try:
            pose.clean(bad)
            assert False, bad
        except ValueError:
            pass
    far = pose.clean([[[5, -3, 0.9]] * 18])
    assert far[0][0] == [1.0, 0.0, 1.0]


# ------------------------------------------------------------------ mãos
def _raise_arm(orig, k=1):
    e = copy.deepcopy([p[:pose.BODY] for p in orig])
    e[k][3] = [e[k][2][0] + 0.02, e[k][2][1] - 0.1, 1.0]
    e[k][4] = [e[k][2][0] + 0.03, e[k][2][1] - 0.22, 1.0]
    return e


def test_parse_reads_the_hands_and_they_do_not_count_as_body_points():
    p = _people()[0]
    assert len(p) == 60 and pose.hand_points(p, "r") and pose.hand_points(p, "l")
    assert abs(p[pose.HAND_R][0] - p[4][0]) < 0.02 and abs(p[pose.HAND_L][1] - p[7][1]) < 0.05        # a mão começa no punho do mesmo lado
    assert pose.seen(p) == pose.seen(p[:18]) and pose.bbox(p) == pose.bbox(p[:18])                       # a caixa da pessoa é só do corpo
    assert pose.changed(p, p) is False and len(pose.clean([p[:18]])[0]) == 18
    raw = fake_openpose()
    raw[0]["people"][0]["hand_right_keypoints_2d"] = [0] * 63                                           # mão não vista: zeros
    q = pose.parse(raw)[0]
    assert pose.hand_points(q, "r") is None and pose.hand_points(q, "l")


def test_the_hand_follows_the_arm_and_an_open_hand_is_drawn_when_the_detector_had_none():
    orig = _people()
    e = _raise_arm(orig)
    full = pose.with_hands(orig, e, 200, 300)
    assert all(len(p) == 60 for p in full) and [p[:18] for p in full] == e
    hand = pose.hand_points(full[1], "r")
    assert hand and abs(hand[0][0] - e[1][4][0]) < 0.03 and abs(hand[0][1] - e[1][4][1]) < 0.03      # o punho da mão é o punho novo
    assert all(h[1] < e[1][4][1] + 0.02 for h in hand if h[2] > 0)                                    # o antebraço aponta para cima: os dedos também
    assert pose.with_hands(orig, [p[:18] for p in orig], 200, 300)[1][pose.HAND_R:pose.HAND_R + 21] == orig[1][pose.HAND_R:pose.HAND_R + 21]   # quem não mexeu mantém a mão
    bare = copy.deepcopy(orig)
    bare[1][pose.HAND_R:pose.HAND_R + 21] = [[0.0, 0.0, 0.0]] * 21                                    # o detector não viu esta mão
    tpl = pose.hand_points(pose.with_hands(bare, e, 200, 300)[1], "r")
    assert tpl and len(tpl) == 21 and min(h[1] for h in tpl) < e[1][4][1] - 0.03                      # mão aberta de modelo, para cima
    still = pose.with_hands(bare, [p[:18] for p in bare], 200, 300)
    assert pose.hand_points(still[1], "r") is None                                                    # braço parado e mão desconhecida: nada a desenhar


def test_change_mask_covers_only_the_moved_limbs_old_and_new_places():
    orig = _people()
    e = _raise_arm(orig)
    m = pose.change_mask(orig, pose.with_hands(orig, e, 200, 300), 200, 300)
    at = lambda pt: m.getpixel((round(pt[0] * 200), round(pt[1] * 300)))                                # noqa: E731
    assert m.size == (200, 300) and at(orig[1][3]) > 200 and at(orig[1][4]) > 200                        # onde o braço estava
    assert at(e[1][4]) > 200 and at(e[1][3]) > 200                                                      # onde está agora
    assert at(orig[0][4]) == 0 and at(orig[0][3]) == 0 and at(orig[1][10]) == 0                          # outra pessoa e a perna: fica a foto original
    assert at(orig[1][11]) < 40 and at(orig[1][13]) == 0                                                 # o quadril e o tornozelo, longe do braço, também
    nothing = pose.change_mask(orig, [p[:18] for p in orig], 200, 300)
    assert nothing.getextrema() == (0, 0)


def test_blend_masked_keeps_the_original_pixels_outside_the_mask_and_resizes_the_result():
    from pickflow.compose import blend_masked
    orig = png((10, 120, 200), (40, 60))
    res = png((250, 0, 0), (20, 30))                                                                    # o modelo devolveu outro tamanho
    mask = Image.new("L", (40, 60), 0)
    mask.paste(255, (0, 0, 40, 30))
    out = Image.open(io.BytesIO(blend_masked(orig, res, mask))).convert("RGB")
    assert out.size == (40, 60) and out.getpixel((5, 5)) == (250, 0, 0) and out.getpixel((5, 55)) == (10, 120, 200)


# ------------------------------------------------------------------ o caso de uso inteiro
def _need(api, rid):
    return wait_event(api, rid, lambda e: e["type"] == "needs_input" and e.get("kind") == "pose")


def _answer(api, rid, ev, people):
    r = api.post(f"/api/runs/{rid}/input", json={"input_id": ev["input_id"], "value": {"people": people} if people is not None else None})
    assert r.status_code == 200, r.text


def test_pose_recipe_asks_for_the_pose_then_applies_it_with_a_written_description(api):
    a = upload(api, "grupo.png", png((10, 120, 200), (200, 300)))
    _cid, rid = start(api, "mude a pose da pessoa da direita", [a["id"]], route="recipe__mudar_pose")
    ev = _need(api, rid)
    assert ev["size"] == [200, 300] and len(ev["people"]) == 2 and len(ev["people"][0]) == 18
    people = copy.deepcopy(ev["people"])
    sh = people[1][2]
    people[1][3] = [sh[0] + 0.02, sh[1] - 0.1, 1.0]
    people[1][4] = [sh[0] + 0.03, sh[1] - 0.22, 1.0]
    _answer(api, rid, ev, people)
    done = wait_event(api, rid, lambda e: e["type"] in ("done", "error"), timeout=20)
    assert done["type"] == "done", done
    events = api.pipe.runs[rid].events
    prompt = next(e["prompt"] for e in events if e["type"] == "step_start" and e["workflow"] == "pose_aplicar")
    assert "Pose change: the second person from the left" in prompt and "mude a pose da pessoa da direita" in prompt
    assert "{pose_change}" not in prompt
    g = list(api.fake.prompts.values())[-1]                                                # o grafo do ComfyUI (passo 2)
    assert g["7"]["class_type"] == "QwenImage21UnionApply" and g["7"]["inputs"]["control_mode"] == "Pose"
    ctl = Image.open(io.BytesIO(api.fake.uploads[g["5"]["inputs"]["image"].split("/")[-1]])).convert("RGB")
    ref = Image.open(io.BytesIO(api.fake.uploads[g["14"]["inputs"]["image"].split("/")[-1]]))
    assert ctl.size == (200, 300)                                                          # o mapa tem o tamanho da foto
    assert g["8"]["inputs"]["steps"] == 30
    # o membro antigo é apagado antes (remover no quadrado vermelho, posto sozinho) e o Union usa a foto limpa como referência
    assert [e["workflow"] for e in events if e["type"] == "step_start"] == ["pose_detectar", "replace_redbox", "pose_aplicar"]
    erase = next(e for e in events if e["type"] == "step_start" and e["workflow"] == "replace_redbox")
    assert erase["preset"] == "remover" and "the old arm on the" in erase["prompt"] and "second person from the left" in erase["prompt"]
    assert ref.size == (64, 48)                                                            # a referência é o resultado do passo de apagar (a de mentira devolve 64x48), não a upload de 200x300


def test_pose_screen_with_nothing_moved_stops_with_a_clear_message_and_cancel_stops_quietly(api):
    a = upload(api, "grupo.png", png((10, 120, 200), (200, 300)))
    _cid, rid = start(api, "mude a pose", [a["id"]], route="recipe__mudar_pose")
    ev = _need(api, rid)
    _answer(api, rid, ev, ev["people"])                                                    # confirmou sem mexer em nada
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "Nenhuma pose foi alterada" in err["message"]
    assert not any(n["class_type"] == "QwenImage21UnionApply" for g in api.fake.prompts.values() for n in g.values())
    _cid, rid2 = start(api, "mude a pose", [a["id"]], route="recipe__mudar_pose")
    ev2 = _need(api, rid2)
    _answer(api, rid2, ev2, None)                                                          # cancelou a tela
    wait_event(api, rid2, lambda e: e["type"] in ("done", "error", "status") and "ancel" in (e.get("message") or e.get("text") or "") or e["type"] == "error")
    assert not any(e["type"] == "done" for e in api.pipe.runs[rid2].events)


def test_the_result_is_the_original_photo_except_where_the_arm_moved_and_the_screen_gets_only_the_body(api):
    a = upload(api, "grupo.png", png((10, 120, 200), (200, 300)))
    _cid, rid = start(api, "mude a pose da pessoa da direita", [a["id"]], route="recipe__mudar_pose")
    ev = _need(api, rid)
    assert all(len(p) == 18 for p in ev["people"])                                                    # a tela não recebe as mãos
    people = copy.deepcopy(ev["people"])
    sh = people[1][2]
    people[1][3] = [sh[0] + 0.02, sh[1] - 0.1, 1.0]
    people[1][4] = [sh[0] + 0.03, sh[1] - 0.22, 1.0]
    _answer(api, rid, ev, people)
    done = wait_event(api, rid, lambda e: e["type"] in ("done", "error"), timeout=20)
    assert done["type"] == "done", done
    out = Image.open(io.BytesIO(api.get(done["images"][0]["url"]).content)).convert("RGB")
    assert out.size == (200, 300)                                                                      # o resultado tem o tamanho da foto, não o do modelo (64x48)
    assert out.getpixel((5, 295)) == (10, 120, 200) and out.getpixel((190, 5)) == (10, 120, 200)       # longe do braço: a foto original
