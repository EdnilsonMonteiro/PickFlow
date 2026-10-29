"""Chaves de API no arquivo `.env` da pasta do PickFlow (o git ignora). A interface grava e apaga linhas daqui, mas nunca
recebe a chave de volta: só sabe se ela existe."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .store import atomic_write

NAME_RE = re.compile(r"^(HF_TOKEN|LLM_[A-Z0-9_]{1,50})$")


def _lines(root: Path) -> list[str]:
    try:
        return (Path(root) / ".env").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []


def _key(line: str) -> str:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return ""
    return s.split("=", 1)[0].strip().removeprefix("export ").strip()


def is_set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def set_var(root: Path, name: str, value: str) -> None:
    """Grava `NAME=valor` no .env (troca a linha se já existe) e já vale neste processo. Valor vazio apaga a chave."""
    if not NAME_RE.match(name):
        raise ValueError("Nome de variável inválido: use HF_TOKEN ou um nome que comece com LLM_ (letras maiúsculas, números e _).")
    value = str(value or "").strip()
    if "\n" in value or "\r" in value:
        raise ValueError("A chave não pode ter quebra de linha.")
    lines = [ln for ln in _lines(root) if _key(ln) != name]
    if value:
        lines.append(f"{name}={value}")
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    atomic_write(Path(root) / ".env", "\n".join(lines).rstrip("\n") + "\n" if lines else "")
    if os.name == "posix":
        try:
            os.chmod(Path(root) / ".env", 0o600)          # chave de API: só o dono lê
        except OSError:
            pass
