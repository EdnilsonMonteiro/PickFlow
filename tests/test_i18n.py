"""Idioma da interface: catálogo inglês completo e coerente (web/locales/en.json)."""
import json
import re
from pathlib import Path

import uitexts
from pickflow.store import DEFAULT_SETTINGS, Store

ROOT = Path(__file__).resolve().parent.parent


def test_english_is_the_default_language():
    assert DEFAULT_SETTINGS["ui"]["language"] == "en"


def test_language_is_saved_in_settings(project):
    s = Store(project)
    assert s.settings()["ui"]["language"] == "en"
    assert s.save_settings({"ui": {"language": "pt"}})["ui"]["language"] == "pt"


def test_catalog_covers_every_interface_text():
    # textos de web/app.js, web/index.html e os nomes que defaults/ mostra na tela; para o que faltar, acrescente a
    # tradução em web/locales/en.json ("strings" para texto fixo, "templates" com {} para texto com partes variáveis)
    assert uitexts.missing() == []


def test_catalog_has_no_duplicate_keys_and_templates_keep_their_placeholders():
    raw = (ROOT / "web" / "locales" / "en.json").read_text(encoding="utf-8")
    sections = json.loads(raw, object_pairs_hook=lambda pairs: pairs)
    for name, items in sections:
        keys = [k for k, _ in items]
        assert len(keys) == len(set(keys)), f"chave repetida em {name}"
        for k, v in items:
            assert v.strip(), f"tradução vazia: {k!r}"
            assert k == k.strip(), f"chave com espaço na ponta (a página compara o texto sem as pontas): {k!r}"
            if name == "templates":
                n = k.count("{}")
                used = re.findall(r"\{(\d*)\}", v)
                plain = [u for u in used if u == ""]
                assert len(plain) == n or sorted(int(u) for u in used if u) == list(range(n)), \
                    f"modelo com {n} partes variáveis, tradução com {used}: {k!r}"


def test_page_loads_the_translation_before_the_app():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/i18n.js") < html.index("/static/app.js")
