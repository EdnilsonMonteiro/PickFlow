"""LoRA de restauração: opções do prompt 'melhorar', força, conflito com a Consistency e LoRA escondida."""
import yaml

from pickflow import loras as L
from pickflow.clef import Answer
from pickflow.store import Store

from test_agent import run  # noqa: F401
from test_e2e import _chain, api, png, start, upload, wait_event  # noqa: F401

RESTORE = "qwen-image-2.1-restore.safetensors"


class Picks:
    """Clef falso: responde o que está em `picks`; nas LoRAs sugeridas diz que não; no resto, a primeira opção."""
    mode = "stub"

    def __init__(self, picks):
        self.picks = picks

    async def decide(self, state, questions, images=None):
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q) or ("none" if q.startswith("lora_") and "none" in keys else keys[0])
            out[q] = Answer(pick, 0.9, {k: (0.9 if k == pick else 0.1 / max(1, len(keys) - 1)) for k in keys})
        return out


def names(stack):
    return [i["id"] for i in stack]


def option(s, preset_id, option_id):
    """O preset e uma das opções da variável dele."""
    preset = next(p for p in s.prompts("edit")["presets"] if p["id"] == preset_id)
    return preset, next(o for v in preset["vars"] for o in v["options"] if o["id"] == option_id)


def test_the_restore_option_uses_the_lora_at_full_strength_without_consistency_flymy_and_natexp(project):
    s = Store(project)
    lib = s.loras()
    preset, opt = option(s, "melhorar", "restaurar")
    st = L.effective_loras(lib, s.manifest("edit")["lora_base"], opt["loras"], [], "turbo")
    assert names(st) == ["restore"] and st[0]["strength"] == 1.0                 # a de consistência cedeu; realismo desligado
    cfg = L.merged_settings(lib, st, preset.get("settings"))
    assert cfg["scene_mp"] == 2.0 and cfg["resolution"] == 0 and cfg["ref_mp"] == "off"
    kinds = {o["id"]: o["text"] for v in preset["vars"] for o in v["options"]}
    assert kinds["restaurar"].startswith("Restore the photo: remove blur, noise and compression artifacts")
    assert kinds["antiga"].startswith("Restore the old photo: remove scratches, dust, fading and grain")


def test_improving_in_place_keeps_the_size_and_stacks_the_simple_improvement_loras(project):
    s = Store(project)
    _, opt = option(s, "melhorar", "sutil")
    st = L.effective_loras(s.loras(), s.manifest("edit")["lora_base"], opt["loras"], [], "turbo")
    assert names(st) == ["natexp", "consistency"]                                # flymy em 0 sai da pilha; nada de restore
    assert not [i for i in st if i["strength"] == 0 or i["id"] == "restore"]
    cfg = L.merged_settings(s.loras(), st, None)
    assert cfg.get("scene_mp") != 2.0                                            # não redesenha em 2 MP


def test_hiding_the_lora_keeps_the_prompt_but_the_restore_options_lose_the_lora(project):
    s = Store(project)
    ids = lambda: {p["id"] for p in s.active_presets("edit")}          # noqa: E731
    assert {"melhorar", "livre"} <= ids()
    s.custom_dir.mkdir(exist_ok=True)
    (s.custom_dir / "loras.yaml").write_text(yaml.dump({"loras": [{"id": "restore", "disabled": True}]}), encoding="utf-8")
    # o preset fica: melhorar sem aumentar não precisa da LoRA de restauração
    assert "melhorar" in ids() and "livre" in ids() and "trocar_pessoas" in ids()
    _, opt = option(s, "melhorar", "restaurar")
    st = L.effective_loras(s.loras(), s.manifest("edit")["lora_base"], opt["loras"], [], "turbo")
    assert not [i for i in st if i["id"] == "restore"]                           # id fora da biblioteca é ignorado


def test_restore_request_runs_edit_with_the_lora_and_the_standard_prompt(api, monkeypatch):
    monkeypatch.setattr("pickflow.runner.make_clef", lambda s: Picks({"preset": "melhorar", "var_modo": "antiga"}))
    a = upload(api, "velha.png", png((120, 110, 100), (160, 120)))
    _, rid = start(api, "restaure esta foto antiga", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done", timeout=20)
    (prompt,) = api.fake.prompts.values()
    chain = _chain(prompt)
    assert [c for c in chain if "restore" in c[0]] == [(RESTORE, 1.0, 1.0)]
    assert not [c for c in chain if "consistency" in c[0]]
    assert prompt["6"]["inputs"]["value"].startswith("Restore the old photo")       # só o prompt padrão, sem o texto do pedido
    assert prompt["40"]["inputs"]["megapixels"] == 2.0                              # a imagem 1 vai a cerca de 2 MP
