"""Sobe o app com ComfyUI de mentira e Clef em modo de teste, para ver a interface sem GPU.

    python tests/demo_server.py   ->  http://127.0.0.1:7861
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from pickflow.app import create_app  # noqa: E402
from pickflow.store import Store  # noqa: E402
from fake_comfy import FakeComfy, load_object_info  # noqa: E402

if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="pickflow_demo_"))
    for d in ("defaults", "web"):
        shutil.copytree(ROOT / d, tmp / d)
    (tmp / "data").mkdir()
    if os.environ.get("DEMO_ADDON"):               # um addon criado a partir do modelo, para ver a interface dos addons
        from pickflow.addons import new_addon
        new_addon(tmp / "addons", "meu-addon", ROOT / "defaults" / "addon_template")
    import os
    if os.environ.get("DEMO_SEED_CUSTOM"):        # um workflow "seu" ainda não registrado, para ver o importador
        (tmp / "custom" / "workflows").mkdir(parents=True)
        shutil.copy(ROOT / "defaults" / "workflows" / "Qwen2.1 Image Edit Quality.json", tmp / "custom" / "workflows" / "Meu edit.json")
    oi = load_object_info()
    if os.environ.get("DEMO_MODELS"):              # um 2º modelo de imagem (workflow + arquivo no ComfyUI de mentira): o seletor "Modelo"
        import yaml
        edit = yaml.safe_load((ROOT / "defaults" / "registry" / "edit.yaml").read_text(encoding="utf-8"))
        (tmp / "custom" / "registry").mkdir(parents=True, exist_ok=True)
        (tmp / "custom" / "registry" / "edit_moss.yaml").write_text(yaml.dump(
            {**edit, "id": "edit_moss", "title": "Editar (MOSS)", "models": ["moss_image"], "implements": "edit"}), encoding="utf-8")
        oi["UNETLoader"]["input"]["required"]["unet_name"][0].append("moss_image_v1.safetensors")
    settings = {"comfyui": {"url": ""}, "clef": {"mode": "mock"}}
    if os.environ.get("DEMO_SETUP"):               # ComfyUI "novo": sem os nós, LoRAs e modelos; downloads de mentira, lentos
        import threading
        import time as _t
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import yaml
        for cls in ("DrawBox", "MoveBoxes", "DrawMoveBoxes", "ViggleTurboLora", "ViggleTurboSigmas"):
            oi.pop(cls, None)
        for cls, key in (("LoraLoader", "lora_name"), ("UNETLoader", "unet_name"), ("CLIPLoader", "clip_name"), ("VAELoader", "vae_name")):
            oi[cls]["input"]["required"][key][0] = []
        root = tmp / "ComfyUI"
        (root / "custom_nodes").mkdir(parents=True)
        (root / "models").mkdir()
        settings["comfyui"]["path"] = str(root)

        class Slow(BaseHTTPRequestHandler):
            size = 40 * 1024 * 1024

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.send_response(200); self.send_header("Content-Length", str(self.size)); self.end_headers()

            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", str(self.size)); self.end_headers()
                for _ in range(self.size // (1 << 20)):
                    self.wfile.write(b"x" * (1 << 20)); _t.sleep(0.12)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        (tmp / "custom").mkdir(exist_ok=True)
        cat = yaml.safe_load((ROOT / "defaults" / "models.yaml").read_text(encoding="utf-8"))["files"]
        for e in cat:
            e["url"] = f"http://127.0.0.1:{srv.server_address[1]}/{e['file']}"
        (tmp / "custom" / "models.yaml").write_text(yaml.dump({"files": cat}), encoding="utf-8")
    fake = FakeComfy(oi, delay=float(os.environ.get("DEMO_DELAY", "0")))
    fake.view_rgba = bool(os.environ.get("DEMO_RGBA"))        # recortes com transparência, como o RMBG
    if os.environ.get("DEMO_SPLAT"):               # um .spz de verdade para o passo 3D (o SaveGLB de mentira devolve este arquivo)
        spz = Path(os.environ["DEMO_SPLAT"]).read_bytes()
        for nid in ("51",):
            fake.views[f"splat_{nid}.spz"] = spz
    settings["comfyui"]["url"] = "http://127.0.0.1:9" if os.environ.get("DEMO_NO_COMFY") else fake.url   # porta fechada
    if os.environ.get("DEMO_NO_COMFY"):
        settings["comfyui"].pop("path", None)
    if os.environ.get("DEMO_AGENT"):                # agente ligado e um LLM de mentira que responde roteirizado
        settings["agent"] = {"enabled": True, "confirm": True}
    Store(tmp).save_settings(settings)
    print("demo em http://127.0.0.1:7861  (dados em", tmp, ")", flush=True)
    app = create_app(tmp)
    if os.environ.get("DEMO_AGENT"):
        import json

        class DemoLLM:
            configured = True

            async def chat(self, messages, **kw):
                text = " ".join(m["content"] for m in messages if isinstance(m["content"], str))
                if "CASOS DE USO A FUNDIR" in text:
                    return json.dumps({"mergeable": True, "reason": "Os dois só diferem na ordem das imagens.",
                                       "notes": "A ordem fixa virou dois papéis que o Clef reconhece.", "recipe": {
                        "name": "Colar pessoa ou objeto no cenário", "images": 2,
                        "description": "Duas imagens: um cenário e uma pessoa ou objeto, em qualquer ordem de envio. Recorta, posiciona e funde.",
                        "roles": [{"key": "cenario", "label": "Cenário", "about": "O fundo.", "default": 1},
                                  {"key": "sujeito", "label": "Pessoa ou objeto", "about": "O que será colado.", "default": 2}],
                        "steps": [{"workflow": "colar_recortar", "inputs": {"image_1": "role:sujeito"}},
                                  {"workflow": "colar_posicionar", "inputs": {"image_1": "role:cenario", "image_2": "previous"}},
                                  {"workflow": "colar_fundir", "inputs": {"image_1": "previous"}}]}})
                return json.dumps({"action": "finish", "recipe": {
                    "name": "Melhorar e colorir a pessoa, depois colar", "images": 2,
                    "description": "Duas imagens: um cenário e uma pessoa em foto antiga ou ruim que precisa ser colorida e melhorada antes de entrar no cenário.",
                    "steps": [{"workflow": "edit", "prompt": "livre", "inputs": {"image_1": "user:2"},
                               "request": "Colorize this photo and improve its quality; keep the person identical."},
                              {"workflow": "colar_recortar", "inputs": {"image_1": "previous"}},
                              {"workflow": "colar_posicionar", "inputs": {"image_1": "user:1", "image_2": "previous"}},
                              {"workflow": "colar_fundir", "inputs": {"image_1": "previous"}}]}})
        app.state.pipe.llm = DemoLLM()
    uvicorn.run(app, host="127.0.0.1", port=7861, log_level="warning")
