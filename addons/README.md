# addons/ — extensões suas

Esta pasta é ignorada pelo git (veja o `.gitignore`): o que você cria aqui fica só na sua máquina e `git pull`
nunca mexe. Cada addon é uma pasta `addons/<nome>/`. A ordem de prioridade dos arquivos é
**`custom/` > `addons/` > `defaults/`**.

Criar um addon novo a partir do modelo:

```
python -m pickflow addon new meu-addon        # ou: Ajustes > Addons > Criar addon
```

Reinicie o PickFlow. O addon aparece em **Ajustes > Addons** (onde também liga e desliga). Código Python e telas só
valem depois de reiniciar; workflows, prompts e LoRAs do addon valem na hora.

## O que um addon pode ter

```
addons/meu-addon/
├── addon.yaml          título, versão, config padrão, LLM próprio (opcional)
├── addon.py            register(ctx): ganchos, ações, rotas, LLM, dados
├── registry/           ligação técnica de fluxos novos (mesmo formato de defaults/registry)
├── workflows/          .json do ComfyUI desses fluxos
├── prompts/            prompts dos fluxos (mesmo formato de defaults/prompts)
├── loras.yaml          LoRAs novas
├── clef_prompt.yaml    descrição dos fluxos para o Clef, casos de uso (recipes), instructions_append
└── web/                main.js, style.css (carregados sozinhos): interface nova
```

Tudo é opcional. Só recursos (sem `addon.py`) já é um addon válido: um fluxo novo do ComfyUI, por exemplo.

## addon.py: `register(ctx)`

| `ctx.…` | Para quê |
|---|---|
| `@ctx.hook("request")` | muda o texto do pedido antes de o Clef ler (`def f(texto, info)` → novo texto) |
| `@ctx.hook("prompt")` | muda o prompt final de cada passo antes do ComfyUI (`info`: workflow, preset, mode, vars, loras, request) |
| `@ctx.hook("plan" \| "step_done" \| "run_done")` | observadores (log, avisos, histórico) |
| `@ctx.action("nome")` | fluxo feito em Python, sem ComfyUI; ligado por um registro com `kind: action` e `action: <addon>.nome` |
| `ctx.llm` | o LLM de Ajustes: `await ctx.llm.ask(texto, system=…, images=[png])`, `ask_json(…)`, `.configured` |
| `ctx.router` | rotas FastAPI próprias, em `/api/addons/<nome>/…` |
| `ctx.config` | `addon.yaml > config` + `settings.yaml > addons.config.<nome>` |
| `ctx.load_data()` / `ctx.save_data(d)` | dados do addon que sobrevivem ao reiniciar (`data/addons/<nome>.json`) |
| `ctx.store`, `ctx.app`, `ctx.log(msg)` | acesso ao Store, ao FastAPI e ao log |

Ganchos podem ser `def` ou `async def`. Erro num gancho é registrado e ignorado; um addon que quebra ao carregar é
desligado e o erro aparece em Ajustes > Addons.

### Ações (fluxos novos em Python)

`registry/meu_fluxo.yaml`:

```yaml
id: meu_fluxo
kind: action
action: meu-addon.minha_acao
title: Meu fluxo
images: [{key: image_1, label: Imagem, required: true}]
params: []        # caixas, posições, máscara: os mesmos tipos dos outros fluxos; chegam em call.drawings
```

`addon.py`:

```python
@ctx.action("minha_acao")
async def minha_acao(call):          # call: ActionCall
    im = call.pil("image_1")         # call.images["image_1"] = PNG em bytes
    await call.status("Trabalhando…")           # mensagem na conversa
    escolha = await call.ask("meu_tipo", title="Qual?", opcoes=["a", "b"])   # abre a tela registrada no JS
    return [im.rotate(90, expand=True)]        # bytes PNG, PIL.Image ou lista
```

`call` também traz `request`, `vars`, `drawings`, `prompt`, `llm`, `settings`. Para o Clef escolher o fluxo, descreva-o
em `clef_prompt.yaml > workflows > meu_fluxo > description`.

## web/main.js: `window.PickFlow`

```js
const CF = window.PickFlow, h = CF.h;                      // h(tag, attrs, ...filhos) cria elementos (sem innerHTML)
CF.addButton({ label, title, onClick })                    // ao lado de "Anexar imagem"
CF.addResultAction({ label, onClick(imagem) })             // em cada imagem de resultado
CF.addView({ id, title, mount(container) })                // tela nova no menu
CF.addSettings({ id, title, mount(container) })            // bloco em Ajustes > Addons
CF.registerInput('meu_tipo', async (evento) => valor)      // tela pedida pelo Python (call.ask); null cancela
CF.on('run:end', ({images}) => …)                          // 'ready', 'view', 'run:event', 'run:end'
CF.addonApi('meu-addon', '/rota', { method, body })        // chama as rotas do addon
CF.config(nome) / CF.saveConfig(nome, obj)                 // configuração do addon
CF.modal({ title, body, buttons }) · CF.toast(texto) · CF.api(caminho, opts)
CF.setText(texto) · CF.attach(arquivo) · CF.send({ text })  // controlar a conversa
CF.boxEditor({ img, color, multiple, … })                  // o editor de caixas do PickFlow, para telas suas
CF.state                                                   // conversa, imagens anexadas, ajustes, workflows (leitura)
CF.lang · CF.tr(texto)                                     // idioma da interface ('en' | 'pt') e tradução pelo catálogo
```

Texto vindo de fora entra no DOM com `textContent`/`h()`, nunca `innerHTML`. A interface abre em inglês: o PickFlow traduz
na tela o que está no catálogo `web/locales/en.json`; para os textos do seu addon, escreva direto no idioma que quiser ou
escolha pelo `CF.lang` (o que não está no catálogo aparece como foi escrito).

## Boas práticas

- Prefixe ids (`meu_addon_…`), classes CSS e rotas com o nome do addon.
- Conteúdo pessoal (prompts, LoRAs) fica aqui ou em `custom/`, nunca em `defaults/`.
- `python -m pickflow addon new` e a skill **pickflow-addons** (`.claude/skills/pickflow-addons`) mostram o caminho
  completo; Ajustes > Checagem dos workflows confere se as ações dos addons existem.
