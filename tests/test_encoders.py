"""Codificador de texto (CLIP): fp8 (heretic) ou int8 (convrot), escolhido em Ajustes."""
import json
from pathlib import Path

from pickflow import encoders
from pickflow.store import Store

from test_e2e import api, start, upload, wait_event  # noqa: F401  (fixture `api` e helpers)

FP8, INT8 = "qwen3vl_8b_fp8_heretic.safetensors", "qwen3vl_8b_int8_convrot.safetensors"
PE = "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors"
OI = {"CLIPLoader": {"input": {"required": {"clip_name": [[PE, FP8, INT8]]}}}}


def graph():
    return {"4": {"class_type": "CLIPLoader", "inputs": {"clip_name": FP8, "type": "qwen_image"}},
            "9": {"class_type": "CLIPLoader", "inputs": {"clip_name": PE, "type": "stable_diffusion"}},
            "7": {"class_type": "LoraLoader", "inputs": {"lora_name": FP8}}}


def test_swaps_only_the_qwen3vl_clip_loaders():
    g = graph()
    nodes, why = encoders.apply(g, "int8", OI)
    assert nodes == ["4"] and why == "" and g["4"]["inputs"]["clip_name"] == INT8
    assert g["9"]["inputs"]["clip_name"] == PE and g["7"]["inputs"]["lora_name"] == FP8     # outros codificadores ficam
    g = {"4": {"class_type": "CLIPLoader", "inputs": {"clip_name": INT8}}}
    assert encoders.apply(g, "fp8", OI)[0] == ["4"] and g["4"]["inputs"]["clip_name"] == FP8


def test_auto_and_unknown_values_change_nothing():
    for choice in ("auto", "", None, "bf16"):
        g = graph()
        assert encoders.apply(g, choice, OI) == ([], "") and g == graph()


def test_a_missing_file_is_not_forced_and_says_why():
    g = graph()
    oi = {"CLIPLoader": {"input": {"required": {"clip_name": [[FP8]]}}}}
    nodes, why = encoders.apply(g, "int8", oi)
    assert nodes == [] and g == graph() and "int8" in why and "Ajustes" in why
    assert encoders.available(oi) == {"fp8": True, "int8": False}
    assert encoders.available(None) == {"fp8": None, "int8": None}
    assert encoders.apply(graph(), "int8", None)[0] == ["4"]            # sem `object_info` o ComfyUI decide


def test_api_lists_the_encoders_and_what_comfyui_has(api):
    r = api.get("/api/encoders").json()
    assert r["current"] == "auto" and [o["id"] for o in r["options"]] == ["fp8", "int8"]
    assert all(o["installed"] is True for o in r["options"])         # o ComfyUI de mentira tem os dois


def test_the_chosen_encoder_goes_into_the_graph_sent_to_comfyui(api):
    a = upload(api)
    assert api.put("/api/settings", json={"comfyui": {"text_encoder": "int8"}}).status_code == 200
    _, rid = start(api, "troque a cor da camisa para vermelho", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert prompt["4"]["inputs"]["clip_name"] == INT8                  # o `edit` vem com o fp8: foi trocado


def test_setup_assistant_stops_asking_for_the_encoder_that_was_not_chosen(api):
    def files():
        return {f["file"]: f for f in api.get("/api/setup").json()["files"]}

    assert not files()[FP8]["unused"] and not files()[INT8]["unused"]
    api.put("/api/settings", json={"comfyui": {"text_encoder": "fp8"}})
    assert files()[INT8]["unused"] and not files()[FP8]["unused"]
    api.put("/api/settings", json={"comfyui": {"text_encoder": "auto"}})
    assert not files()[INT8]["unused"]
