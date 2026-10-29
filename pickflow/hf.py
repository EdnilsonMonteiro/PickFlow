"""Hugging Face: ler o arquivo .env, entender links e achar os .safetensors de um repositório.

O token (HF_TOKEN) só é preciso para repositórios restritos ou que exigem aceitar uma licença. Ele fica no arquivo
`.env` da pasta do PickFlow (que o git ignora) e só é enviado ao próprio Hugging Face.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

HOSTS = ("huggingface.co", "www.huggingface.co", "hf.co")
API = "https://huggingface.co/api/models"
NEED_TOKEN = ("O Hugging Face recusou o acesso: o arquivo é restrito ou exige aceitar a licença. Crie um token em "
              "https://huggingface.co/settings/tokens (permissão de leitura), aceite a licença na página do modelo, "
              "acrescente a linha HF_TOKEN=seu_token ao arquivo .env na pasta do PickFlow e reinicie o PickFlow.")
BAD_TOKEN = ("O Hugging Face recusou o acesso mesmo com o HF_TOKEN do arquivo .env. Confira se o token está certo e "
             "se você aceitou a licença na página do modelo.")


class HFError(ValueError):
    pass


def load_env(root: Path) -> None:
    """Lê `<root>/.env` (linhas CHAVE=valor) para o ambiente, sem sobrescrever o que já existe."""
    p = Path(root) / ".env"
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and k not in os.environ:
            os.environ[k] = v


def token() -> str:
    return (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()


def is_hf(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in HOSTS


def auth_headers(url: str) -> dict[str, str]:
    t = token()
    return {"Authorization": f"Bearer {t}"} if t and is_hf(url) else {}


def denied_message() -> str:
    return BAD_TOKEN if token() else NEED_TOKEN


def parse(url: str) -> dict:
    """{'repo': 'dono/nome', 'rev': 'main', 'path': 'arquivo.safetensors' | None}"""
    u = urlparse((url or "").strip())
    if u.scheme != "https" or (u.hostname or "").lower() not in HOSTS:
        raise HFError("Cole um link do Hugging Face, como https://huggingface.co/dono/modelo.")
    parts = [unquote(x) for x in u.path.split("/") if x]
    if parts and parts[0] in ("models",):
        parts = parts[1:]
    if len(parts) < 2 or parts[0] in ("spaces", "datasets", "docs", "settings", "api"):
        raise HFError("Esse link não é de um modelo. Use o endereço da página do modelo ou de um arquivo .safetensors.")
    repo, rest = f"{parts[0]}/{parts[1]}", parts[2:]
    if len(rest) >= 3 and rest[0] in ("blob", "resolve"):
        return {"repo": repo, "rev": rest[1], "path": "/".join(rest[2:])}
    if len(rest) >= 2 and rest[0] == "tree":
        return {"repo": repo, "rev": rest[1], "path": None, "dir": "/".join(rest[2:])}
    return {"repo": repo, "rev": "main", "path": None}


def file_url(repo: str, rev: str, path: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/{rev}/{path}"


async def list_safetensors(repo: str, rev: str = "main", base_dir: str = "") -> list[dict]:
    """Os .safetensors do repositório: [{path, size, url}]."""
    headers = auth_headers(API)
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers) as c:
            r = await c.get(f"{API}/{repo}/tree/{rev}", params={"recursive": "true", "expand": "false"})
    except httpx.HTTPError as e:
        raise HFError(f"Não consegui falar com o Hugging Face ({e.__class__.__name__}). Confira a internet.") from e
    if r.status_code in (401, 403):
        raise HFError(denied_message())
    if r.status_code == 404:
        raise HFError("Não achei esse repositório no Hugging Face (ou ele é privado: nesse caso ponha o HF_TOKEN no .env).")
    if r.status_code >= 400:
        raise HFError(f"O Hugging Face respondeu {r.status_code}.")
    out = []
    for it in r.json():
        p = it.get("path", "")
        if it.get("type") == "file" and p.lower().endswith(".safetensors") and p.startswith(base_dir):
            out.append({"path": p, "size": (it.get("lfs") or {}).get("size") or it.get("size"),
                        "url": file_url(repo, rev, p), "name": p.rsplit("/", 1)[-1]})
    return out
