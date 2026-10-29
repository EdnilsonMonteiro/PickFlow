---
name: pickflow-addons
description: "Cria e edita addons do PickFlow (pasta addons/, fora do git): fluxos novos em Python ou ComfyUI, ganchos de prompt e pedido, LLM, telas e botões novos na interface. Use quando o usuário pedir uma funcionalidade própria, um novo fluxo, uma nova tela ou ajuste no prompt sem mexer no código do PickFlow."
---

# Addons do PickFlow

Um addon é uma pasta `addons/<nome>/` (o `.gitignore` ignora tudo ali, menos `addons/README.md`). Prioridade dos
arquivos: **`custom/` > `addons/` > `defaults/`**. Nunca edite `defaults/` para uma necessidade pessoal e nunca ponha
prompts, LoRAs ou texto pessoal/NSFW fora de `custom/` e `addons/`. Nunca altere os workflows originais do ComfyUI do
usuário (`ComfyUI/user/default/workflows`): copie para `addons/<nome>/workflows/`.

## 1. Comece pelo modelo

```
python -m pickflow addon new meu-addon        # copia defaults/addon_template para addons/meu-addon
```

(ou Ajustes > Addons > Criar addon). O modelo já traz uma ação, um gancho, uma rota, uma tela, um botão, um bloco de
ajustes e a descrição para o Clef. Apague o que não usar. **Reinicie o PickFlow** depois de mexer em `addon.py` ou
`web/`; registros, prompts, workflows e LoRAs do addon valem na hora.

Leia antes de mexer: `addons/README.md` (API completa), `pickflow/addons.py` (ctx, ganchos, LLM, ActionCall) e, para a
interface, o bloco `window.PickFlow` no fim de `web/app.js`.

## 2. O que o usuário quer → o que criar

| Pedido | Criar |
|---|---|
| Mudar/ajudar o prompt (regras fixas, tradução, estilo) | `@ctx.hook("prompt")` ou `@ctx.hook("request")` em `addon.py`; com LLM: `await ctx.llm.ask(...)` |
| Um fluxo novo feito em código (girar, recortar, filtro, juntar imagens, chamar API) | **ação**: registro `kind: action` + `@ctx.action` |
| Um fluxo novo no ComfyUI | `workflows/*.json` + `registry/<id>.yaml` + `prompts/<id>.yaml` + descrição em `clef_prompt.yaml` |
| LoRA nova | `loras.yaml` do addon (mesmo formato de `defaults/loras.yaml`) |
| Botão, tela, janela, ajuste na interface | `web/main.js` com `window.PickFlow` |
| Pergunta ao usuário no meio de um fluxo | `await call.ask("tipo", ...)` no Python + `PickFlow.registerInput("tipo", fn)` no JS |
| Guardar algo entre execuções | `ctx.load_data()` / `ctx.save_data()` |
| Rota HTTP para o JS | `@ctx.router.get("/x")` → `PickFlow.addonApi("meu-addon", "/x")` |

## 3. Ação (fluxo em Python), passo a passo

1. `registry/meu_fluxo.yaml` (o id usa só `a-z 0-9 _`):
   ```yaml
   id: meu_fluxo
   kind: action
   action: meu-addon.minha_acao        # <nome do addon>.<nome em @ctx.action>
   title: Meu fluxo
   group: addons
   images:
   - {key: image_1, label: Imagem, required: true, hint: A imagem a processar.}
   # params: caixas, posições e máscara como nos outros fluxos (chegam em call.drawings):
   # - {key: area, type: box, image: image_1, color: green, multiple: true, label: Marque as áreas}
   ```
   Imagens opcionais usam `required: false`. Não existe `node` em ação.
2. `addon.py`:
   ```python
   def register(ctx):
       @ctx.action("minha_acao")
       async def minha_acao(call):          # ActionCall
           im = call.pil("image_1")          # PIL.Image; call.images["image_1"] = PNG em bytes
           await call.status("Trabalhando…")
           # call.request (texto do pedido), call.vars, call.drawings, call.prompt, call.llm, call.settings
           return [im.rotate(90, expand=True)]   # bytes PNG, PIL.Image ou lista deles
   ```
3. `clef_prompt.yaml` do addon: `workflows: {meu_fluxo: {enabled: true, description: "…quando usar, o que faz, o que NÃO faz…"}}`.
   O Clef escolhe pelo texto da descrição: seja específico e diga como diferenciar dos outros fluxos. Casos de uso
   (sequências; cada passo aceita `request:` com instrução própria no lugar do pedido inteiro) vão em `recipes:` e frases extras para as instruções gerais em `instructions_append:`.
4. Teste: Ajustes > Checagem dos workflows (a ação precisa existir), depois escolha o fluxo no seletor da conversa.

Erro dentro da ação vira mensagem na conversa ("A ação … falhou: …"); erro ao carregar `addon.py` desliga só o addon e
aparece em Ajustes > Addons.

## 4. Fluxo novo no ComfyUI (registro `kind` omitido)

Copie o `.json` do workflow para `addons/<nome>/workflows/` e crie o registro com os números dos nós do JSON:

```yaml
id: meu_fluxo
file: meu_fluxo.json
title: Meu fluxo
group: addons
images:        # um LoadImage por entrada
- {key: image_1, node: 12, label: Imagem, required: true}
prompt: {node: 8, widget: prompt}
set:  [{node: 5, widget: seed, value: random}]      # valores fixos / semente
outputs: [20]                                        # SaveImage/PreviewImage que entregam o resultado
# modes: {turbo: {title: Rápido, file: …, outputs: […], when: …}, quality: {…}}   # variações (o Clef escolhe)
# params: caixa (type: box, node + widgets x/y/w/h, ou só queimada na imagem), crop, place (camadas com giro)
```

Exemplos reais de todos os campos: `defaults/registry/*.yaml` (ex.: `transfer_multi.yaml` para várias caixas com a
imagem de cada uma, `replace_redbox.yaml` para vários quadrados, `colar_recortar.yaml` para recorte e posição com giro).
`prompts/<id>.yaml`: `presets` com `text` (use `{request}` onde o pedido entra), `needs` (imagens usadas), `vars`
(escolhas/contagens/`pick` que o Clef decide), `loras`. Uma ação sem `prompts/<id>.yaml` ganha um prompt `{request}` sozinho; um fluxo do ComfyUI precisa do arquivo.

## 5. Interface (`web/main.js`)

```js
const CF = window.PickFlow, h = CF.h;                   // h(tag, attrs, ...filhos); texto sempre via h/textContent
CF.addButton({ label, onClick })                        // ao lado de "Anexar imagem"
CF.addResultAction({ label, onClick: (imagem) => … })   // em cada resultado
CF.addView({ id, title, mount(container) })             // item novo no menu
CF.addSettings({ id, title, mount(container) })         // Ajustes > Addons
CF.registerInput('tipo', async (ev) => valor)           // tela chamada por call.ask; null cancela
CF.on('run:end', ({images}) => …)                       // ready, view, run:event, run:end
CF.boxEditor({ img, color, multiple, … })               // editor de caixas (× tira, cantos redimensionam)
CF.modal · CF.toast · CF.api · CF.addonApi · CF.config · CF.saveConfig · CF.setText · CF.attach · CF.send · CF.state
CF.lang ('en' | 'pt', padrão 'en') · CF.tr(texto)       // a interface traduz pelo catálogo web/locales/en.json
```

A interface abre em inglês. Textos do addon fora do catálogo aparecem como foram escritos: escreva no idioma do usuário
ou escolha pelo `CF.lang`. Não acrescente textos pessoais de addon ao catálogo do repositório.

Prefixe ids e classes CSS com o nome do addon. Nunca use `innerHTML` com texto que veio de fora.

## 6. Conferir

- `python -m pytest -q` (a suíte do projeto; `tests/test_addons.py` mostra como testar um addon: criar em `tmp/addons/`,
  subir `create_app` e rodar a ação pela rota `route="meu_fluxo"`).
- Interface: `python tests/demo_server.py` (ComfyUI de mentira; `DEMO_ADDON=1` cria um addon do modelo) em
  `http://127.0.0.1:7861`.
- Se algo não aparece: Ajustes > Addons mostra o erro de carregamento e os ganchos/ações registrados.

## Limites

- Ganchos `plan`, `step_done` e `run_done` só observam. `request` e `prompt` devolvem o texto novo (ou `None`).
- Um addon não troca código do PickFlow; para mudar o comportamento de um fluxo padrão, copie o registro/prompt com o
  mesmo `id` dentro do addon (vale mais que `defaults/`) ou em `custom/` (vale mais que tudo).
- Config do addon: `addon.yaml > config`, sobrescrita por `settings.yaml > addons.config.<nome>`.
