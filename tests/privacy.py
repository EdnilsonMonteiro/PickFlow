"""Palavras que não podem aparecer em `defaults/` (o que acompanha o repositório).

A lista é SUA e fica fora do git, em `custom/blocklist.txt` (uma expressão regular por linha; `#` comenta). O repositório só
traz o termo genérico abaixo, para o teste existir mesmo num clone novo, sem mostrar o que você barra. Sem o arquivo, a
checagem é só a genérica: mantenha o seu `custom/blocklist.txt` atualizado na máquina em que você publica.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERIC = ["nsfw"]


def words() -> list[str]:
    out = list(GENERIC)
    f = ROOT / "custom" / "blocklist.txt"
    if f.is_file():
        out += [w.strip() for w in f.read_text(encoding="utf-8").splitlines() if w.strip() and not w.lstrip().startswith("#")]
    return out


def pattern() -> re.Pattern:
    return re.compile("|".join(f"(?:{w})" for w in words()), re.I)
