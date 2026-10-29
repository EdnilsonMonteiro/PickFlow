"""LLMs do redator de prompt, do agente e dos addons: vários provedores (cada um com seu endereço e a sua chave de API) e,
em cada provedor, vários modelos. Qualquer servidor compatível com a API de chat da OpenAI serve (OpenAI, OpenRouter,
Gemini, Groq, DeepSeek, Anthropic, llama-server, Ollama, LM Studio...).

Em `settings.writer`:
    providers  [{id, name, url, key_env, models: [{id, vision}]}]      (a chave NÃO fica aqui)
    active     {provider, model}                                        (o que está valendo agora)
A chave de cada provedor fica no arquivo `.env` (variável `key_env`, por padrão LLM_API_KEY_<ID>), que o git ignora.
Sem `providers`, vale o formato antigo (`url` + `model`), como um provedor só chamado "padrao".
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

PRESETS = [
    {"id": "openai", "name": "OpenAI", "url": "https://api.openai.com/v1", "models": ["gpt-5-mini", "gpt-5"]},
    {"id": "openrouter", "name": "OpenRouter", "url": "https://openrouter.ai/api/v1", "models": ["deepseek/deepseek-chat"]},
    {"id": "gemini", "name": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "models": ["gemini-2.5-flash"]},
    {"id": "anthropic", "name": "Anthropic", "url": "https://api.anthropic.com/v1", "models": ["claude-sonnet-5-5"]},
    {"id": "deepseek", "name": "DeepSeek", "url": "https://api.deepseek.com/v1", "models": ["deepseek-chat"]},
    {"id": "groq", "name": "Groq", "url": "https://api.groq.com/openai/v1", "models": ["llama-3.3-70b-versatile"]},
    {"id": "local", "name": "Servidor local (sem chave)", "url": "http://127.0.0.1:8123/v1", "models": [""]},
]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,30}$")
ENV_RE = re.compile(r"^LLM_[A-Z0-9_]{1,50}$")


class LLMError(Exception):
    pass


def default_key_env(pid: str) -> str:
    return "LLM_API_KEY_" + re.sub(r"[^A-Z0-9]+", "_", str(pid).upper()).strip("_")


def _models(raw: Any) -> list[dict]:
    out, seen = [], set()
    for m in raw or []:
        mid = str(m.get("id") if isinstance(m, dict) else m or "").strip()
        if mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "vision": bool(m.get("vision")) if isinstance(m, dict) else False})
    return out


def providers(w: dict) -> list[dict]:
    """Provedores normalizados. Sem lista nova, o `url`/`model` do formato antigo vira o provedor "padrao"."""
    out = []
    for p in w.get("providers") or []:
        if not isinstance(p, dict) or not ID_RE.match(str(p.get("id") or "")):
            continue
        env = str(p.get("key_env") or "").strip().upper()
        out.append({"id": p["id"], "name": str(p.get("name") or p["id"]), "url": str(p.get("url") or "").strip(),
                    "key_env": env if ENV_RE.match(env) else default_key_env(p["id"]), "models": _models(p.get("models"))})
    if not out and w.get("url"):
        out.append({"id": "padrao", "name": "Padrão", "url": str(w["url"]).strip(), "key_env": "LLM_API_KEY",
                    "models": _models([w.get("model")] if w.get("model") else [])})
    return out


def resolve(w: dict, provider: str | None = None, model: str | None = None) -> dict:
    """As configurações do redator já com o provedor e o modelo escolhidos: `url`, `model`, `api_key`, `vision`.
    `provider`/`model` vazios: vale o `active` (ou o primeiro provedor, com o primeiro modelo)."""
    provs = providers(w)
    act = w.get("active") or {}
    pid = provider or act.get("provider") or (provs[0]["id"] if provs else "")
    p = next((x for x in provs if x["id"] == pid), provs[0] if provs else None)
    out = dict(w)
    if not p:
        return {**out, "provider": "", "api_key": "", "vision": False}
    mid = model if model is not None else (act.get("model") if act.get("provider") == p["id"] else None)
    models = p["models"]
    pick = next((m for m in models if m["id"] == mid), models[0] if models else {"id": mid or "", "vision": False})
    out.update(provider=p["id"], provider_name=p["name"], url=p["url"], model=pick["id"], vision=bool(pick.get("vision")),
               key_env=p["key_env"], api_key=os.environ.get(p["key_env"], "").strip())
    return out


def headers(cfg: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        h["Authorization"] = "Bearer " + cfg["api_key"]
    return h


def explain(cfg: dict, status: int, body: str) -> str:
    """Erro de HTTP em português, dizendo o que fazer."""
    where = f"{cfg.get('provider_name') or cfg.get('provider') or 'o LLM'} ({cfg.get('url', '')})"
    if status in (401, 403):
        tip = (f"Falta a chave de API ou ela está errada: ponha {cfg.get('key_env') or 'LLM_API_KEY'}=sua_chave no arquivo .env "
               "(ou cole em Ajustes > Redator de prompt) e tente de novo.")
        if not cfg.get("api_key"):
            tip = f"Nenhuma chave de API configurada para este provedor. {tip}"
        return f"{where} recusou o acesso ({status}). {tip}"
    if status == 404:
        return f"{where} respondeu 404: confira o endereço (costuma terminar em /v1) e o nome do modelo '{cfg.get('model')}'."
    if status == 429:
        return f"{where} pediu para esperar (429): limite de uso ou saldo acabou."
    return f"{where} respondeu {status}: {body[:300]}"


async def chat(cfg: dict, messages: list[dict], *, temperature: float | None = 0.3, max_tokens: int = 600,
               json_mode: bool = False) -> str:
    """Uma conversa na API de chat. Trata as diferenças entre provedores: modelos novos da OpenAI pedem
    `max_completion_tokens` no lugar de `max_tokens` e alguns só aceitam a temperatura padrão."""
    if not cfg.get("url"):
        raise LLMError("Nenhum LLM configurado: acrescente um provedor em Ajustes > Redator de prompt.")
    body: dict[str, Any] = {"model": cfg.get("model") or "default", "stream": False, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    body["max_completion_tokens" if "api.openai.com" in cfg["url"] else "max_tokens"] = max_tokens
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    url = cfg["url"].rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout_s", 120))) as cl:
            for _try in range(3):
                r = await cl.post(url, json=body, headers=headers(cfg))
                if r.status_code == 400:                                   # parâmetro que este modelo não aceita
                    t = r.text.lower()
                    if "max_tokens" in t and "max_tokens" in body:
                        body["max_completion_tokens"] = body.pop("max_tokens")
                        continue
                    if "max_completion_tokens" in t and "max_completion_tokens" in body:
                        body["max_tokens"] = body.pop("max_completion_tokens")
                        continue
                    if "temperature" in t and "temperature" in body:
                        body.pop("temperature")
                        continue
                    if "response_format" in t and "response_format" in body:
                        body.pop("response_format")
                        continue
                break
            if r.status_code >= 400:
                raise LLMError(explain(cfg, r.status_code, r.text))
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
    except LLMError:
        raise
    except httpx.HTTPError as e:
        raise LLMError(f"O LLM não respondeu ({cfg['url']}): {e.__class__.__name__} {e}".strip()) from e
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(f"Resposta inesperada do LLM ({cfg['url']}): {e}") from e


async def test(cfg: dict) -> dict:
    """Pergunta simples para conferir endereço, chave e modelo. Devolve {ok, ms, text | error}."""
    t0 = time.monotonic()
    try:
        text = await chat({**cfg, "timeout_s": min(float(cfg.get("timeout_s", 120)), 40)},
                          [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=20)
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000), "text": text[:80]}
    except LLMError as e:
        return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "error": str(e)}
