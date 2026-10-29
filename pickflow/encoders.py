"""Codificador de texto (CLIP) dos workflows: o Qwen3-VL 8B em duas versões.

Os workflows do PickFlow carregam o codificador num `CLIPLoader`. Os de editar, transferir, pose, 3D e colar usam o
**fp8 (heretic)**; os de trocar pessoa e trocar roupa (AusBoss) usam o **int8 (convrot)**. Quem tem as duas versões no
ComfyUI pode escolher uma em Ajustes > ComfyUI (`settings.comfyui.text_encoder`): `auto` deixa cada workflow com o seu;
`fp8` ou `int8` troca o arquivo em todos eles, no grafo enviado ao ComfyUI (os `.json` do workflow nunca mudam).
Só entram na troca os `CLIPLoader` que já carregam uma dessas duas versões: outros codificadores (o PE do gerar
imagem, por exemplo) ficam como estão.
"""
from __future__ import annotations

AUTO = "auto"
#: id -> arquivo e nome na tela. Os dois são o mesmo modelo (Qwen3-VL 8B) em precisões diferentes.
ENCODERS = {
    "fp8": {"file": "qwen3vl_8b_fp8_heretic.safetensors", "title": "Qwen3-VL 8B fp8 (heretic)"},
    "int8": {"file": "qwen3vl_8b_int8_convrot.safetensors", "title": "Qwen3-VL 8B int8 (convrot)"},
}


def _base(name: str) -> str:
    return str(name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()


def normalize(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in ENCODERS else AUTO


def available(oi: dict | None) -> dict[str, bool | None]:
    """id -> o ComfyUI lista o arquivo? (`None` quando não deu para ler o `object_info`)."""
    spec = ((((oi or {}).get("CLIPLoader") or {}).get("input") or {}).get("required") or {}).get("clip_name")
    if not spec or not isinstance(spec[0], list):
        return {k: None for k in ENCODERS}
    have = {_base(o) for o in spec[0]}
    return {k: _base(e["file"]) in have for k, e in ENCODERS.items()}


def apply(api: dict, choice: str, oi: dict | None = None) -> tuple[list[str], str]:
    """Troca o arquivo dos `CLIPLoader` do grafo da API. Devolve (ids dos nós trocados, aviso).

    Sem escolha (`auto`), ou com a escolhida ausente do ComfyUI, nada muda; no segundo caso o aviso diz por quê."""
    choice = normalize(choice)
    if choice == AUTO:
        return [], ""
    target = ENCODERS[choice]
    if available(oi).get(choice) is False:
        return [], (f"O codificador de texto escolhido ({target['title']}) não está no ComfyUI; cada workflow ficou com o "
                    "seu. Instale o arquivo ou escolha outro em Ajustes > ComfyUI.")
    ours = {_base(e["file"]) for e in ENCODERS.values()}
    swapped = []
    for nid, node in api.items():
        inputs = node.get("inputs") or {}
        if node.get("class_type") == "CLIPLoader" and _base(inputs.get("clip_name")) in ours \
                and _base(inputs.get("clip_name")) != _base(target["file"]):
            inputs["clip_name"] = target["file"]
            swapped.append(str(nid))
    return swapped, ""
