"""Inglês para o Clef: os nomes de tela (títulos de workflow, rótulos de entrada, nomes de caso de uso e de papel) são
escritos em português e traduzidos pelo catálogo da interface (`web/locales/en.json`). As perguntas ao Clef são em
inglês, então esses nomes passam por `en()` antes de entrar nelas. Nome sem tradução (um workflow seu, por exemplo)
segue como foi escrito.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "web" / "locales" / "en.json"


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, str], list[tuple[re.Pattern, str]]]:
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, []
    temps = [(re.compile("^" + "(.*?)".join(re.escape(p) for p in k.split("{}")) + "$", re.S), v)
             for k, v in (cat.get("templates") or {}).items()]
    return dict(cat.get("strings") or {}), temps


def en(text: str | None, _depth: int = 0) -> str:
    """O texto em inglês, se o catálogo tem; senão o próprio texto."""
    if not text or _depth > 3:
        return text or ""
    core = text.strip()
    strings, temps = _catalog()
    if core in strings:
        return text.replace(core, strings[core])
    for rx, value in temps:
        m = rx.match(core)
        if m:
            caps = iter(en(c, _depth + 1) for c in m.groups())
            out = re.sub(r"\{\d*\}", lambda _m: next(caps, ""), value)
            return text.replace(core, out)
    return text
