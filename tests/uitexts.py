"""Textos de interface que precisam de tradução: os da página (`web/app.js`, `web/index.html`) e os nomes que os arquivos
de `defaults/` mostram na tela (títulos de workflow, nomes de prompt e de opção, papéis, LoRAs, arquivos do assistente).

As descrições e instruções lidas pelo Clef (`description`, `when`, `question`, `instructions`) e os textos dos prompts NÃO
entram: são texto para o modelo, editados como estão. `python tests/uitexts.py` lista o que falta no catálogo.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from jsstrings import ui_texts  # noqa: E402

# textos que parecem português mas não são de interface (nomes próprios, código, exemplos neutros)
IGNORE = {
    "use strict", "Load Image", "ComfyUI", "Clef", "Workflow", "LoRA", "LoRAs", "Prompt", "GitHub", "Node", "Addons",
    "Anthropic", "Groq", "Google Gemini", "Natural Exposure", "Shift + `", "Shift / Alt", "Shift + 4 / 6", "PickFlow",
    "Português (Brasil)", "btn secondary hidden", "btn secondary sm", "modal wide splat-modal", "natexp = 0\nflymy = 0.3",
    '[addon] erro em "{}":',
}


def yaml_texts(root: Path = ROOT / "defaults") -> list[str]:
    out: list[str] = []

    def add(s):
        s = s.replace("{n}", "{}").strip() if isinstance(s, str) else s     # {n}: o número que o runner põe
        if isinstance(s, str) and s and s not in out:
            out.append(s)

    def images(lst):
        for im in lst or []:
            add(im.get("label"))
            add(im.get("hint"))

    for f in sorted((root / "registry").glob("*.yaml")):
        m = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        add(m.get("title"))
        add(m.get("group"))
        add(m.get("note"))
        images(m.get("images"))
        for p in m.get("params") or []:
            add(p.get("label"))
            add(p.get("hint"))
        fb = m.get("fallback") or {}                 # nomes das opções da porta de exceção (a pergunta e o `when` são do Clef)
        add((fb.get("main") or {}).get("name"))
        for o in (fb.get("options") or {}).values():
            add((o or {}).get("name"))
        modes = m.get("modes") or {}
        for md in (modes.values() if isinstance(modes, dict) else modes):
            add(md.get("title"))                # o `when` do modo é texto do Clef (inglês), não entra
            add(md.get("note"))
            images(md.get("images"))
            for p in md.get("params") or []:
                add(p.get("label"))
                add(p.get("hint"))
    for f in sorted((root / "prompts").glob("*.yaml")):
        m = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for p in m.get("presets") or []:
            add(p.get("name"))
            for v in p.get("vars") or []:
                add(v.get("label"))
                for o in v.get("options") or []:
                    add(o.get("name"))
    for lo in (yaml.safe_load((root / "loras.yaml").read_text(encoding="utf-8")) or {}).get("loras") or []:
        add(lo.get("title"))
    for r in (yaml.safe_load((root / "clef_prompt.yaml").read_text(encoding="utf-8")) or {}).get("recipes") or []:
        add(r.get("name"))
        for ro in r.get("roles") or []:
            add(ro.get("label"))
    for x in (yaml.safe_load((root / "models.yaml").read_text(encoding="utf-8")) or {}).get("files") or []:
        add(x.get("title"))
        add(x.get("why"))
        add(x.get("license"))
    return out


def html_texts() -> list[str]:
    src = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    src = re.sub(r"<(script|style|svg)[\s\S]*?</\1>", "", src)
    out = [t.strip() for t in re.findall(r">([^<>]+)<", src) if t.strip()]
    out += re.findall(r'(?:title|aria-label|placeholder)="([^"]+)"', src)
    return out


def catalog() -> dict:
    return json.loads((ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8"))


def _template_res(cat: dict) -> list[re.Pattern]:
    res = []
    for key in cat.get("templates", {}):
        parts = [re.escape(p) for p in key.split("{}")]
        res.append(re.compile("^" + "(.*?)".join(parts) + "$", re.S))
    return res


def covered(text: str, cat: dict, res: list[re.Pattern] | None = None) -> bool:
    """O texto tem tradução (exata, ou por um modelo com `{}`)? Para um modelo extraído do JS (com `{}`), basta haver
    um modelo idêntico ou um que o cubra trocando cada `{}` por um valor de exemplo."""
    if text in IGNORE or text in cat.get("strings", {}) or text in cat.get("templates", {}):
        return True
    res = res if res is not None else _template_res(cat)
    sample = text.replace("{}", "7")
    return any(r.match(sample) or r.match(text) for r in res)


def missing() -> list[str]:
    cat = catalog()
    res = _template_res(cat)
    texts = ui_texts((ROOT / "web" / "app.js").read_text(encoding="utf-8")) + html_texts() + yaml_texts()
    return [t for t in dict.fromkeys(texts) if not covered(t, cat, res)]


if __name__ == "__main__":
    for t in missing():
        print(repr(t))
