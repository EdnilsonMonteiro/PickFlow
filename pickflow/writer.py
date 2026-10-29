"""Redator de prompt opcional: um LLM (qualquer provedor compatível com a API de chat da OpenAI, veja llm.py) reescreve o
pedido ou ajusta o prompt montado. Desligado por padrão. Se falhar, o PickFlow segue com o texto sem LLM e avisa o motivo."""
from __future__ import annotations

import base64
import io
from typing import Awaitable, Callable

from PIL import Image

from . import llm

MAX_IMAGES = 4
MAX_SIDE = 1024

Notify = Callable[[str], Awaitable[None]] | None


def _data_url(png: bytes) -> str:
    im = Image.open(io.BytesIO(png))
    im.load()
    if max(im.size) > MAX_SIDE:
        k = MAX_SIDE / max(im.size)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    if im.mode in ("RGBA", "LA", "P"):                     # fundo branco: JPEG não tem transparência
        base = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        base.paste(im, mask=im.getchannel("A"))
        im = base
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def rewrite(request: str, cfg: dict, notify: Notify = None) -> str:
    try:
        text = await llm.chat(cfg, [{"role": "system", "content": cfg["system"]}, {"role": "user", "content": request}],
                              max_tokens=1200)
        return text.strip('"').strip() or request
    except llm.LLMError as e:
        if notify:
            await notify(f"O redator não respondeu; segui com o pedido original. {e}")
        return request


async def refine(draft: str, request: str, loras: str, cfg: dict, images: list[bytes] | None = None, note: str = "",
                 notify: Notify = None) -> str:
    """O LLM ajusta o rascunho (instruções fixas das LoRAs + pedido) para cumprir o pedido do usuário.
    `images`: as imagens que este passo recebe, na ordem <image1>, <image2>... (só vão se o modelo está marcado como
    "enxerga imagens"). `note`: o que este passo faz e o que o LLM deve cuidar. Se falhar ou devolver vazio, vale o
    rascunho montado sem LLM (e `notify` explica por quê)."""
    system = cfg.get("refine_system") or cfg["system"]
    seen = bool(images) and bool(cfg.get("vision"))
    parts = [f"DRAFT:\n{draft}", f"USER REQUEST:\n{request.strip() or '(none)'}", f"ACTIVE LORAS:\n{loras.strip() or '(none)'}"]
    if note.strip():
        parts.append("ABOUT THIS STEP:\n" + note.strip())
    if images:
        n = min(len(images), MAX_IMAGES)
        parts.append(f"IMAGES OF THIS STEP: {n}, in the order " + ", ".join(f"<image{i + 1}>" for i in range(n)) + ". "
                     "Numbers like 'image 1' or 'image 2' in the user request may refer to the user's ORIGINAL photos, not to "
                     "these; describe things by what they look like instead of by number." + (
                         " You can see the images attached below." if seen else ""))
    user: object = "\n\n".join(parts)
    if seen:
        user = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": _data_url(b)}}
                                                   for b in images[:MAX_IMAGES]]
    try:
        text = await llm.chat(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=1500)
        return text.strip('"').strip() or draft
    except llm.LLMError as e:
        if notify:
            await notify(f"O redator não respondeu; usei o prompt montado sem ele. {e}")
        return draft
