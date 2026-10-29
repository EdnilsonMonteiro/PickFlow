"""Gera `tests/object_info.json` a partir de um dump do `/object_info` do seu ComfyUI.

Fica só com os tipos de nó que os workflows de `defaults/` usam e troca as listas de
modelos/LoRAs/imagens (que são da sua máquina) pelos nomes que os workflows padrão citam.

    python tests/make_object_info.py caminho/para/object_info.json
"""
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRA_LORAS = ["qwen-image-2.1-outfit-swap.safetensors", "casual_snapshot_realism_qwen21.safetensors",
               "Qwen2.1_viewpoint_orbit.safetensors", "qwen-image-2.1-restore.safetensors",
               "qwen-image-2.1-outpaint-v2.safetensors"]
# modelos que só aparecem DENTRO de subgraphs (o nó de fora guarda o valor) ou em widgets_values sem nomes: TripoSplat do
# fluxo 3D e o codificador dos fluxos de trocar pessoa e roupa (AusBoss)
SUBGRAPH_FILES = {("VAELoader", "vae_name"): ["triposplat_vae_decoder_fp16.safetensors", "flux2-vae.safetensors"],
                  ("UNETLoader", "unet_name"): ["triposplat_fp16.safetensors"],
                  ("CLIPLoader", "clip_name"): ["qwen3vl_8b_int8_convrot.safetensors"]}
FILE_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".sft")


def main(src: str):
    oi = json.load(open(src, encoding="utf-8"))
    types: set[str] = set()
    used: dict[tuple[str, str], set] = {}
    import yaml
    for e in (yaml.safe_load((ROOT / "defaults" / "loras.yaml").read_text(encoding="utf-8")) or {}).get("loras", []):
        for t in ("LoraLoader", "LoraLoaderModelOnly"):          # LoRAs da biblioteca (entram na cadeia na hora de rodar)
            used.setdefault((t, "lora_name"), set()).add(e["file"])
    for f in glob.glob(str(ROOT / "defaults" / "workflows" / "*.json")) + glob.glob(str(ROOT / "tests" / "legacy" / "workflows" / "*.json")):
        wf = json.load(open(f, encoding="utf-8"))
        nodes = list(wf["nodes"])
        for sg in (wf.get("definitions") or {}).get("subgraphs", []):
            nodes += sg["nodes"]
        for n in nodes:
            types.add(n["type"])
            for k, v in (n.get("widgets_values_named") or {}).items():
                if isinstance(v, str):
                    if v.lower().endswith(FILE_EXT):                  # só o nome do arquivo: a pasta (Qwen-image-2.1\) é da máquina
                        v = v.replace("\\", "/").rsplit("/", 1)[-1]
                    used.setdefault((n["type"], k), set()).add(v)
            for v in n.get("widgets_values") or []:
                if isinstance(v, dict) and v.get("lora"):
                    used.setdefault(("Power Lora Loader (rgthree)", "lora"), set()).add(v["lora"])
    out = {t: json.loads(json.dumps(oi[t])) for t in sorted(types) if t in oi}

    def scrub(t, spec):
        if not spec or not isinstance(spec, list):
            return
        opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        options = spec[0] if isinstance(spec[0], list) else opts.get("options")
        if not options or not any(isinstance(o, str) and o.lower().endswith(FILE_EXT) for o in options):
            return
        return True

    for t, info in out.items():
        for sec in ("required", "optional"):
            for name, spec in ((info.get("input") or {}).get(sec) or {}).items():
                if not scrub(t, spec):
                    continue
                keep = sorted(used.get((t, name), set()) | set(SUBGRAPH_FILES.get((t, name), []))) or ["modelo.safetensors"]
                if t == "LoraLoader" and name == "lora_name":      # LoRAs da biblioteca que nenhum workflow cita
                    keep += [x for x in EXTRA_LORAS if x not in keep]
                if isinstance(spec[0], list):
                    spec[0] = keep
                else:
                    spec[1]["options"] = keep
        img = ((info.get("input") or {}).get("required") or {}).get("image")
        if t == "LoadImage" or (img and len(img) > 1 and isinstance(img[1], dict) and img[1].get("image_upload")):
            info["input"]["required"]["image"] = [["exemplo.png"], {"image_upload": True}]     # nunca os arquivos de quem gerou
    (ROOT / "tests" / "object_info.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(out), "tipos de nó")


if __name__ == "__main__":
    main(sys.argv[1])
