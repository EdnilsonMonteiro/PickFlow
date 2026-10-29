"""Addon __NAME__: ponto de partida. Apague o que não usar.

Este arquivo é carregado quando o PickFlow inicia (reinicie depois de mudar). `register(ctx)` recebe tudo que um
addon pode usar. Erros aqui só desligam o addon (aparecem em Ajustes > Addons); o PickFlow continua de pé.
"""
import io
import re

from PIL import Image


def register(ctx):
    # --------------------------------------------------------------- 1. AÇÃO: um fluxo feito em Python
    # O registro registry/__ID___girar.yaml (kind: action) aponta para "__NAME__.girar". O Clef escolhe esse fluxo
    # pela descrição em clef_prompt.yaml, e o PickFlow chama esta função com as imagens da conversa.
    @ctx.action("girar")
    async def girar(call):
        achou = re.search(r"(\d{1,3})\s*(?:°|º|graus?)", call.request)
        graus = int(achou.group(1)) if achou else int(ctx.config.get("graus_padrao", 90))
        if re.search(r"\b(esquerda|anti-?hor[aá]rio)\b", call.request, re.I):
            graus = -graus
        await call.status(f"Girando a imagem {graus}° no sentido horário.")
        im = call.pil("image_1").convert("RGBA")
        out = im.rotate(-graus, expand=True, resample=Image.BICUBIC)
        return [out]                       # bytes PNG, PIL.Image ou lista deles

    # --------------------------------------------------------------- 2. GANCHOS: mexer no pedido e no prompt
    # Eventos: request, prompt, plan, step_done, run_done (veja addons/README.md).
    @ctx.hook("prompt")
    def prompt(text, info):
        # Exemplo: acrescenta uma regra a todo prompt. Devolva o texto novo (ou None para não mudar).
        # return text + "\nKeep the original image resolution."
        return None

    # --------------------------------------------------------------- 3. LLM: ajudar no prompt ou decidir algo
    # `ctx.llm` usa o servidor configurado em Ajustes (Redator de prompt). Exemplo (descomente):
    #
    # @ctx.hook("request")
    # async def traduz(text, info):
    #     if ctx.llm.configured and text.strip():
    #         return await ctx.llm.ask(text, system="Translate to English. Output only the translation.")

    # --------------------------------------------------------------- 4. ROTAS HTTP próprias (para o JS do addon)
    # Chamadas de web/main.js com PickFlow.addonApi("__NAME__", "/ola").
    @ctx.router.get("/ola")
    async def ola():
        return {"mensagem": "Olá do addon __NAME__", "config": ctx.config}

    # --------------------------------------------------------------- 5. DADOS que sobrevivem ao reiniciar
    # dados = ctx.load_data(); dados["vezes"] = dados.get("vezes", 0) + 1; ctx.save_data(dados)
