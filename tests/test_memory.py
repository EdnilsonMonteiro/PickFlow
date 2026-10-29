from pickflow.memory import GB, Memory, add_clef, from_stats, should_free_comfy, should_unload


def mem(total=24, clef=19.0, ram_free=20, loaded=True, device="cuda", ram_gb=0):
    m = from_stats({"system": {"ram_total": 32 * GB, "ram_free": ram_free * GB},
                    "devices": [{"vram_total": total * GB, "vram_free": 3 * GB}]})
    return add_clef(m, {"loaded": loaded, "device": device, "ram_gb": ram_gb, "vram": {"allocated_gb": clef}})


def test_auto_unloads_when_clef_leaves_too_little_vram():
    go, why = should_unload("auto", mem(24, 19), 16, 10, 2)
    assert go and "VRAM" in why and "19.0" in why


def test_auto_keeps_clef_when_there_is_room():
    go, why = should_unload("auto", mem(48, 19), 16, 10, 2)
    assert not go and "continua carregado" in why


def test_policies():
    assert should_unload("never", mem(24, 19), 16, 10, 2) == (False, "")
    assert should_unload("always", mem(80, 5), 16, 10, 2)[0]


def test_clef_device_comes_from_health_without_the_port():
    assert mem(24, 19, device="cuda:0").clef_device == "cuda"
    assert mem(24, 19, device="CPU").clef_device == "cpu"
    assert Memory().clef_device == ""


def test_cpu_clef_unloads_by_ram():
    go, why = should_unload("auto", mem(80, 0, ram_free=6, device="cpu", ram_gb=17), 16, 10, 2)
    assert go and "RAM" in why and "17.0" in why


def test_cuda_clef_with_tight_ram_unloads_by_ram():
    go, why = should_unload("auto", mem(80, 5, ram_free=6, device="cuda", ram_gb=3), 16, 10, 2)
    assert go and "RAM" in why and "3.0" in why


def test_unknown_memory_unloads_for_safety():
    m = Memory()
    assert should_unload("auto", m, 16, 10, 2)[0]


def test_free_comfy_before_clef():
    m = from_stats({"devices": [{"vram_total": 24 * GB, "vram_free": 2 * GB}]})
    m = add_clef(m, {"loaded": False})
    assert should_free_comfy(m, 20, 2)[0]
    m2 = from_stats({"devices": [{"vram_total": 24 * GB, "vram_free": 23 * GB}]})
    assert not should_free_comfy(add_clef(m2, {"loaded": False}), 20, 2)[0]
    assert not should_free_comfy(add_clef(m, {"loaded": True, "vram": {"allocated_gb": 19}}), 20, 2)[0]


def test_free_comfy_is_pointless_for_a_cpu_clef():
    # Mesma VRAM apertada, mas o Clef vai carregar na RAM: não adianta liberar o ComfyUI.
    base = from_stats({"devices": [{"vram_total": 24 * GB, "vram_free": 2 * GB}]})
    assert should_free_comfy(add_clef(base, {"loaded": False, "device": "cpu", "ram_gb": 5}), 20, 2) == (False, "")
    assert should_free_comfy(add_clef(base, {"loaded": True, "device": "cpu:0", "ram_gb": 5}), 20, 2) == (False, "")


def test_the_server_footprint_beats_the_setting_when_freeing_vram():
    """Com o Clef em 4 bits (~6 GB) e 10 GB livres, o ComfyUI NÃO precisa ser liberado — mesmo que o ajuste
    `clef_vram_gb` diga 20. Era isso que fazia o int4 continuar se comportando como bf16."""
    apertado = from_stats({"devices": [{"vram_total": 24 * GB, "vram_free": 10 * GB}]})
    int4 = add_clef(apertado, {"loaded": False, "device": "cuda", "quant": "nf4", "vram_expected_gb": 6.0})
    assert should_free_comfy(int4, 20, 2) == (False, "")
    bf16 = add_clef(apertado, {"loaded": False, "device": "cuda", "quant": "", "vram_expected_gb": 17.8})
    go, why = should_free_comfy(bf16, 20, 2)
    assert go and "17.8" in why


def test_the_working_set_beats_the_weights_when_deciding_to_unload():
    """Os pesos em 4 bits são 7,9 GB, mas DECIDIR chega a 16,4 GB (medido com 2 imagens) — e quem decide se o Clef
    fica ao lado do Qwen é o número de trabalho, não o dos pesos. Foi errar isso que encheu a placa e fez cada
    decisão passar de 30 s."""
    m = add_clef(Memory(), {"loaded": True, "device": "cuda", "quant": "nf4",
                            "vram": {"allocated_gb": 7.9, "peak_gb": 16.43},
                            "vram_work_gb": 16.43})
    m.vram_total_gb = 24
    go, why = should_unload("auto", m, 15.5, 10, 0.5)
    assert go and "em 4 bits" in why and "16.4" in why
    # e o mesmo número manda na hora de liberar o ComfyUI antes de o Clef carregar
    fora = add_clef(Memory(), {"loaded": False, "device": "cuda", "quant": "nf4", "vram_work_gb": 16.43})
    fora.vram_total_gb, fora.vram_free_gb = 24, 10.0
    assert should_free_comfy(fora, 20, 2)[0]


