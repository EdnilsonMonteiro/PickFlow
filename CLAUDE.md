# CLAUDE.md — guia para trabalhar no PickFlow

Leia isto antes de mexer. Responda ao usuário em **português do Brasil**, de forma **concisa e prática** (ele é
desenvolvedor, gosta de ir direto ao ponto). Termine entregas dizendo o que pode estar faltando ou o que ainda
não foi testado de verdade (ele roda o ComfyUI e o Clef reais; aqui só existem versões de mentira).

## 1. O que é

PickFlow é um chat (FastAPI + página web em JavaScript puro, sem build) que fica na frente do **ComfyUI**.
A pessoa escreve o pedido e anexa imagens; o **Clef** (modelo que NÃO escreve texto: recebe um `state`, imagens e
perguntas de múltipla escolha e devolve probabilidades) escolhe qual workflow do ComfyUI, ou qual sequência
(caso de uso), rodar; o PickFlow liga as imagens, monta o prompt, converte o workflow e executa no ComfyUI por HTTP.
Os workflows que acompanham o projeto usam **Qwen-Image 2.1** (editar, trocar pessoa/cabeça/roupa, mover e
transferir objetos, recortar e colar, remover fundo, gerar do zero, expandir com Outpaint e mudar o ângulo da câmera com
3D: TripoSplat + LoRA AnyAngle).

```
Usuário → PickFlow (porta 7860) → Clef (decide)      [clef.py, router.py]
                               → ComfyUI (executa)   [comfy.py, converter.py, runner.py]
                               → LLM opcional (redator de prompt, agente, fusão)   [addons.LLM, writer.py]
```

Rodar: `start.bat` / `bash start.sh` (ou `python -m pickflow`). Clef local: `clef_server/start_clef.bat` / `bash clef_server/start_clef.sh`
(os dois só chamam `start_clef.py`, que pergunta **bf16 ou int4**). Testes: `python -m pytest -q` (**387 passam**; ~5 min).
CLI extra: `python -m pickflow addon new <nome>`.

## 2. Regras duras (não quebre)

1. **Camadas de arquivos: `custom/` > `addons/` > `defaults/`.** O app NUNCA escreve em `defaults/` (tudo que a
   interface salva vai para `custom/`, só a diferença em relação ao padrão). `custom/` e `addons/<nome>/` são
   ignorados pelo git. Pastas na raiz como `config/`, `prompts/`, `registry/`, `workflows/` são restos do formato
   antigo: não use nem edite.
2. **Conteúdo pessoal ou adulto (prompts, LoRAs, textos, workflows) nunca vai para o repositório**: só `custom/` ou
   `addons/`. O teste `test_defaults_ship_no_personal_or_nsfw_text` vigia `defaults/`; mantenha-o passando. As palavras que ele barra ficam em
   `custom/blocklist.txt` (fora do git, uma regex por linha; `tests/privacy.py` lê): nunca escreva essa lista no repositório.
3. **Os `.json` de workflow nunca são alterados pelo PickFlow**: ele converte uma cópia em memória a cada execução.
   Os workflows originais do usuário no ComfyUI (`ComfyUI/user/default/workflows`) **nunca** podem ser alterados;
   copie para `defaults/workflows/` ou `custom/workflows/`.
4. **Não faça commit nem push** sem o usuário pedir. Quando pedir, termine o commit com as linhas de atribuição que
   a sessão indicar.
5. **Não distribua o `custom/settings.yaml` pessoal** (endereços, caminhos) nem `data/` (conversas e imagens).
6. Pode haver duas cópias do projeto na máquina do usuário: uma "limpa" de uso final (com `.git`) e uma pessoal
   (com `custom/` cheio). Só mexa na que ele indicar naquele momento.
7. Texto de interface em português, sentença normal, verbos claros ("Salvar", "Manter"); erros dizem o que
   houve e como resolver. `innerHTML` nunca com texto vindo de fora. **Todo texto novo de tela (app.js, mensagens do
   servidor que vão para a conversa, nomes em `defaults/`) precisa da tradução em `web/locales/en.json`** (o inglês é o
   idioma padrão; `python tests/uitexts.py` lista o que falta). Texto montado em pedaços: traduza cada pedaço com `tr()`
   ou use um modelo com `{}` no catálogo. O que o Clef lê (descrições, `when`, `question`, instruções, `about` dos papéis, perguntas montadas no código) fica em
   **inglês**; `name`/`label` são texto de tela (português) e passam por `pickflow/i18n.py: en()` ao entrar numa pergunta.

## 3. Mapa do código

| Arquivo | Papel |
|---|---|
| `pickflow/app.py` | rotas FastAPI (`/api/...`), arquivos estáticos, SSE da execução, addons, casos de uso (agente/repetição/fusão) |
| `pickflow/runner.py` | `Pipeline`: um turno do chat (`_turn`), passos (`_run_step`), ações Python (`_run_action`), recorte (`_review_cutout`), agente (`_agent_plan`), perguntas ao usuário (`ask_user`), encolher fotos grandes (`_shrink`) |
| `pickflow/router.py` | `Router.plan` (rota + `then` + lacuna), `_paste_intent`, `_engine_gate` (fluxo padrão com reserva), `bind` (prompt, modo, LoRAs, variáveis, qual imagem entra em cada entrada), papéis (`resolve_roles`), `steps_from_route/recipe`, `consult`, `check_gap` |
| `pickflow/clef.py` | cliente do Clef (`ClefClient`, formato System One), `MockClef` (palavras em comum), `choice_question`, `Answer` |
| `pickflow/store.py` | leitura/escrita em camadas: settings, manifests, prompts, LoRAs, clef_prompt, casos de uso, camada do agente |
| `pickflow/converter.py` | converte o JSON do ComfyUI (formato de UI) para a API, aplica overrides, subgraphs |
| `pickflow/loras.py` | pilha de LoRAs (`effective_loras`), conflitos, ajustes do grafo (`knobs`), `compose` (monta o prompt) |
| `pickflow/families.py` | modelos de imagem: tag `models` (sem tag = `qwen21`, `*` = todos), `implements` (workflow que faz o papel de outro para um modelo), `load` (`families.yaml`), `model_files`/`available` (o que o chat oferece: família com workflow no PickFlow E arquivo no ComfyUI; o padrão sempre), `ModelView` (o Store visto por um modelo: `enabled_workflows/recipes`, `loras`, `prompts`, `resolve`; `Store.for_model`) |
| `pickflow/encoders.py` | codificador de texto (CLIP): `ENCODERS` fp8 (heretic) × int8 (convrot), `apply` (troca o arquivo dos `CLIPLoader` Qwen3-VL no grafo da API, depois do conversor), `available` (o que o `object_info` lista); escolha em `settings.comfyui.text_encoder` (`auto`/`fp8`/`int8`), `GET /api/encoders` |
| `clef_server/` | servidor do Clef que vem no projeto: `server.py` (`--quant bf16|int4`, `--device cuda|cpu`; `check_setup` recusa na partida int4 na CPU, CUDA ausente e falta de bitsandbytes), `start_clef.py` (um só script para Windows e Linux: cria o `.venv`, instala o torch CUDA 12.8 ou CPU, **pergunta a quantização**, sobe o servidor), `start_clef.bat`/`.sh` (só acham o Python) |
| `docs/` | documentação por assunto (installation, workflows, use-cases, clef, customizing, troubleshooting, development — em inglês); o `README.md` é só o caminho curto |
| `pickflow/pvars.py` | variáveis de prompt (`choice`, `count`, `angle`, `pick`) e tokens |
| `pickflow/pose.py` | poses de pessoas: `parse` (DWPose -> 18 pontos em frações), `usable` (tira repetidas/minúsculas, ordena da esquerda para a direita), `draw` (mapa OpenPose), `describe` (frase em inglês do que mudou), `clean` (valida o que vem da tela) |
| `pickflow/quality.py` | medidas baratas da foto com PIL (sem numpy): `measure` (megapixels + nitidez = variância do mapa de bordas a 768 px) e `describe` (frase em inglês que vai no `state` do Clef). Limiares calibrados em 241 fotos reais (`SOFT` = p10, `CRISP` = mediana) |
| `pickflow/feedback.py` | feedback do usuário em `data/feedback.jsonl`: `add`, `effective` (a última opinião explícita vence; download = positivo fraco; `same` não conta), `counts`/`apply` (Beta-Binomial **semeado pela probabilidade do Clef**), `similar`/`similarity` (kNN por palavras, sem modelo) e `summary` |
| `pickflow/compose.py`, `cutout.py` | caixas desenhadas, camadas posicionadas, megapixels; recorte de pessoa (SAM3 + RMBG) |
| `pickflow/splats.py` | modelos 3D (`.spz`) em `data/splats/` com a foto de origem; chave = conteúdo da foto + tipo (a mesma foto não é gerada de novo); biblioteca (`saved`, 12 não guardados) |
| `pickflow/llm.py`, `envfile.py`, `writer.py` | LLM do redator/agente/addons: `settings.writer.providers` (vários provedores, cada um com `models[{id, vision}]` e `key_env`) + `active`; a chave só no `.env` (`envfile.set_var`, rotas `PUT /api/env`, `GET /api/llm`, `POST /api/llm/test`); `llm.chat` trata `max_tokens`/`max_completion_tokens`, 401 explicado; `writer.refine` manda as imagens do passo se o modelo é `vision` e avisa falhas (`notify`) |
| `pickflow/addons.py` | sistema de addons, classe `LLM` (OpenAI-compatível), `ActionCall` |
| `pickflow/recipe_agent.py` | agente que cria casos de uso (`RecipeAgent`), `check_recipe`, `recipe_overlap`, `recipe_signature` |
| `pickflow/recipe_merge.py` | nota de repetição (`score_pairs`) e fusão por LLM (`merge_recipes`, `apply_merge`) |
| `pickflow/setup.py`, `hf.py`, `install_nodes.py` | assistente de instalação (nós, LoRAs, modelos) |
| `pickflow/doctor.py`, `importer.py` | "Verificar workflows" e importar workflow novo |
| `web/app.js`, `web/style.css`, `web/index.html` | interface inteira (um arquivo JS, funções `mountX`, modais, `window.PickFlow` para addons) |
| `web/i18n.js`, `web/locales/en.json` | idioma da interface (`settings.ui.language`, `en` padrão): um `MutationObserver` traduz nós de texto e `title/placeholder/aria-label/alt/label` pelo catálogo (`strings` exatos + `templates` com `{}`, pedaços traduzidos recursivamente); não toca `input/textarea` (só os atributos), `code/pre` e `translate="no"`; `tr()` no app.js para textos montados e valores de campo; `alert/confirm/prompt` traduzidos |
| `web/splat.js`, `web/vendor/` | visualizador 3D (three.js + Spark, embutidos; mapa de import no `index.html`), carregado sob demanda por `splatStage` |
| `defaults/` | `workflows/*.json`, `registry/<id>.yaml`, `prompts/<id>.yaml`, `loras.yaml`, `models.yaml`, `clef_prompt.yaml`, `addon_template/` |
| `.claude/skills/pickflow-addons/SKILL.md` | como criar addons (leia se o pedido for uma extensão pessoal) |

## 4. Como um pedido vira imagens (fluxo de `Pipeline._turn`)

1. Anexos viram `ImageRef`; fotos acima de `settings.comfyui.max_input_mp` (2 MP) são reduzidas (`_shrink`).
2. Gancho de addon `request`. Depois o Clef é liberado/carregado conforme a memória (`_make_room_for_clef`).
3. `Router.plan`: (se houver 2+ imagens) o Clef descreve cada imagem — pessoa / lugar / `garment` (roupa sozinha, sem
   ninguém vestindo) / objeto / outro — em janelas de
   `clef.max_images` (4 é o limite do modelo): com 5+ imagens, cada janela leva só as perguntas das suas; pergunta `route`
   entre workflows e casos de uso que cabem no nº de imagens; se a confiança for baixa faz uma 2ª análise
   (`_refine_route`); se a rota declara `fallback` no registro (`troca_pessoa`, `troca_roupa`), pergunta `engine` (normal ×
   exceção) e só a exceção manda o pedido para a reserva (`edit` + prompt fixado); pergunta `then` (precisa de mais um workflow depois? só workflows que cabem em `max(nº de imagens, 1)`
   e com confiança ≥ `clef.then_min`, 0,5, depois de `weigh_option(then, none, clef.then_none_weight=1,5)`: o Clef real dava 14-35% a "transferir" depois de editar uma foto só); resolve papéis do caso de uso; se o
   agente estiver ligado, pergunta se falta uma etapa (`check_gap`).
4. Se houver lacuna e o agente estiver ligado, `Pipeline._agent_plan` cria um caso de uso novo (seção 8).
5. Para cada passo: `Router.bind` (escolhe prompt, LoRAs `auto`, variáveis, e qual fonte
   `user_N`/`previous` vai em cada entrada; o **modo** turbo/quality só é perguntado ao Clef quando o usuário deixou o
   seletor do chat em *automático* — um `mode` escolhido na tela vale mais e a pergunta nem é feita) → `_run_step`: desenhos do usuário (`needs_input`), prompt final
   (`compose`, redator opcional, gancho `prompt`), pilha de LoRAs, overrides do grafo, upload, execução, resultado.
6. Eventos vão por SSE (`status`, `decision`, `plan`, `needs_input`, `progress`, `done`, `error`); a interface mostra
   "Como o Clef decidiu". `needs_input` tem `kind`: `draw`, `angle`, `cutout`, `choose`, `agent_recipe`, `splat` e os de addons.
7. Passo 3D (registro com `splat`): a saída é um `.spz` (SaveGLB), não imagem. `_run_step` reaproveita o splat da mesma foto
   (`splats.find`), senão roda e grava; `_splat_choose` pede `kind: splat` (a pessoa gira, baixa PNG/.spz, guarda) e o PNG do
   ângulo volta como imagem do passo. Ação `final` põe o `splat.then` logo depois (pessoa: AnyAngle; ambiente: `edit`/`expandir` com `vars.expand: gray`, completa o cinza do render), `render` o tira; a foto de origem
   vira a fonte `origin` dos passos seguintes; o plano é reemitido (a interface preserva o estado das etapas já feitas).

## 5. Formatos que você vai editar

**Registro (`registry/<id>.yaml`)**: `id`, `file`, `title`, `group`, `images` (`key`, `node`, `label`, `required`),
`prompt {node, widget}`, `set` (valores fixos; `value: random` sorteia semente), `drop`, `outputs`, `internal`
(passo de caso de uso: o Clef não escolhe sozinho), `trim_alpha`, `review: cutout`, `merge: person`,
`requires_nodes`, `fallback` (fluxo prioritário com reserva: `workflow` + `prompt` da reserva, `question` e `main`/`options`
`{name, when}` que o Clef responde numa pergunta só, `id` `engine`; ver `Router._engine_gate`), `splat` (`node` do SaveGLB, `kind` ambiente/pessoa, `then: {workflow, inputs}` com `origin`/`previous`), `modes` (turbo/quality: cada um pode trocar arquivo, saídas e `images`), `lora_chain` +
`lora_base`, `knobs` (ajustes do grafo: `resolution`, `scene_mp`, `ref_mp`, `cfg`...), `params` (`box`, `crop`,
`place` com `multiple`, `numbered`, `refs`, `optional`, `hint`), `kind: action` + `action: "<addon>.<fn>"`.

**Prompts (`prompts/<id>.yaml`)**: `default` e `presets` (a opção de uma variável também pode ter `rewrite: true`, `note` para o redator e `settings`). Cada preset: `id`, `name`, `when` (o Clef lê isto para
escolher), `text` (com `{request}`, `{loras}` e tokens das variáveis), `needs` (nº mínimo de imagens), `slots: order`,
`auto_loras: false` (o Clef não acrescenta LoRAs a este prompt; as perguntas `lora_*` nem aparecem nas decisões),
`verify: {kind: gone, box, skip, min_change, fix: {workflow, prompt, request}}` (depois de gerar, `_verify_step` mede pelos
pixels (`compose.region_change`) se a caixa de origem mudou; se não, roda o passo `fix` sobre o resultado com a mesma
caixa em `forced`),
`verify: {kind: gray, max_gray, tries, vars}` (Outpaint: `_verify_gray` mede o #808080 que sobrou com `compose.gray_fraction` e repete o prompt sobre o resultado),
`loras` (ids ou `{id, strength, mode}`), `settings`, `rewrite`, `requires` (LoRAs que precisam estar ativas; se a
LoRA estiver escondida, o prompt some) e `vars`. Variável: `key`, `kind` (`choice` com `options[{id,name,when,text}]`,
`count`, `angle`, `pick`), `question`, `default`. Opção de `choice` pode ter `mode` (escolhe o modo do workflow; o Clef
não recebe a pergunta de modo; `fallback: true` marca a opção usada quando a tela troca para esse modo) e `expand` (`left/right/top/bottom/all` em frações):
o runner aumenta a 1ª imagem com cinza #808080 (múltiplo de 32) e cola o original de volta no resultado (`compose.pad_gray/paste_back`). A pergunta ao Clef de cada variável tem id `var_<key>`; a do prompt é `preset`.

**LoRAs (`loras.yaml`)**: `id`, `title`, `file` (aceita estar em subpasta do ComfyUI), `strength`, `auto` (o Clef pode
ligar sozinho), `description` (o Clef lê), `prompt` (texto fixo, ou por modo), `adjust` (força de OUTRAS LoRAs
enquanto esta estiver ligada; **só vale quando o preset não fixa `strength` explícito**), `settings` (ajustes do
grafo), `conflicts` (vale nos dois sentidos), `needs`, `group`, `catalog` (id em `models.yaml`). Ordem da pilha:
`lora_base` do workflow → LoRAs do prompt → LoRAs que o Clef acrescentou. Uma LoRA do Clef nunca derruba uma do prompt.

**Casos de uso (`clef_prompt.yaml > recipes`)**: `id` (`a-z0-9_`), `name`, `description` (o Clef lê: diga QUANDO
usar, em termos gerais), `enabled`, `images` (nº exato de imagens; fixa o caso), `roles` (`key`, `label`, `about`,
`default`: o Clef decide quem é quem), `steps`: `workflow`, `prompt`, `mode`, `loras`, `vars`, `request`
(instrução só deste passo; `{request}` insere o pedido inteiro) e `inputs` (entrada do workflow → `user:N`,
`previous`, `role:<papel>` ou `none`). Validação única: `Store.normalize_recipe`.

**`clef_prompt.yaml`**: `instructions` (texto geral que vai a toda pergunta; é um escalar entre aspas simples: apóstrofo
tem que ser `''`), `workflows.<id>.{enabled, description, prompts}`, `recipes`. A lista `recipes` em `custom/`
substitui a dos defaults, mas casos padrão NOVOS (id ausente) são somados; os padrão que o usuário apagou ficam em
`hidden_recipes` (escrito por `save_clef_config`) e não voltam. Casos de addons são somados se o id não existir. Do mesmo modo,
`custom/prompts/<id>.yaml` substitui o arquivo inteiro de prompts daquele workflow: um prompt novo em `defaults/`
não aparece para quem já personalizou.

**Settings** (`custom/settings.yaml`, padrões em `store.DEFAULT_SETTINGS`): `comfyui` (url, `max_input_mp`...),
`clef` (modo `systemone`/`mock`, `device` `cpu`/`gpu` — onde o Clef roda, decide qual política de memória vale: RAM ou
VRAM, `max_steps`, `ask_below`, `refine_below`...), `writer` (LLM OpenAI-compatível;
desligado por padrão), `addons`, `agent`.

## 6. Addons

Pasta `addons/<nome>/` (fora do git) com recursos no mesmo formato de `defaults/` e/ou `addon.py` com
`register(ctx)` (ganchos `request`, `prompt`, `plan`, `step_done`, `run_done`; ações Python; rotas; `ctx.llm`) e/ou
`web/main.js` usando `window.PickFlow`. Detalhes em `addons/README.md` e na skill `pickflow-addons`.
Uma ação (`kind: action`) é um fluxo em Python sem ComfyUI.

## 7. Interface (`web/app.js`)

Sem framework: `h(tag, attrs, ...filhos)` cria elementos (filhos `null` são ignorados); estado em `S`; telas são
`mountChat/mountWorkflows/mountLoras/mountRecipes/mountSettings`; modais via `openModal`; editores de caixa
(`boxEditor`, `cropStage`, `boxStage`, `placeStage` com bolinha de giro, `cutoutModal`). `openInput(ev)` despacha o
`needs_input` pelo `kind`. Armadilhas já vividas: `replaceChildren` não aceita array (embrulhe num `div`);
ao selecionar uma caixa que re-renderiza, capture o ponteiro DEPOIS de renderizar; confira sintaxe com
`node --check web/app.js`. Elemento escondido com `position: absolute` precisa de um ancestral posicionado (o checkbox do
`.switch` sem isso fazia o documento rolar ao ganhar foco e a tela ficava preta); `html, body` têm `overflow: hidden`.
**Ajustes** (`mountSettings`) são abas — *Geral*, *ComfyUI*, *Clef*, *Redator e agente*, *Addons* (`S.settingsTab`; a primeira vez abre a aba do
que está faltando) — e um só botão **Salvar ajustes** (barra fixa `.savebar`, escondida em *Geral* e *Addons*, que gravam na hora). Cartão novo
entra em `panels.<aba>` com `section(título, subtítulo, ...)`. Elemento com `hidden` precisa de `[hidden] { display: none !important }`
(já está no CSS): `.stack` e `.card` têm `display` e anulavam o atributo.
Ler `textContent` de volta devolve o texto JÁ traduzido: não compare com texto em português.

## 8. Recursos construídos nesta fase (contexto)

- **Vários objetos**: caixas vermelhas múltiplas (`replace_redbox`, só objetos iguais, × para apagar), caixas verdes
  numeradas com a imagem de cada uma (`transfer_multi`, o Clef decide quantidade, destino e papéis), vários
  recortes → várias camadas, rotação de camadas.
- **Velocidade**: `_shrink` (2 MP) e `resolution: 1024` em `transfer_multi` (com `resolution: 0` o ComfyUI usa o tamanho
  original de cada referência; fotos de 12 MP levavam minutos).
- **Agente de casos de uso** (`agent.enabled`, desligado por padrão; precisa do LLM em `settings.writer`): se o Clef
  responde `gap` à pergunta "o plano cobre o pedido todo?" (confiança ≥ `agent.gap_min`) ou o usuário escolhe "Agente"
  no seletor, o LLM conversa em rodadas (`show_workflow`, `show_recipe`, `list_loras`, `ask_clef`, `draft`, `finish`,
  `give_up`), valida com `check_recipe`, o usuário confirma (`agent.confirm`) e o caso fica em
  `custom/generated_recipes.yaml` com `generated.status` `trial` (o mais antigo sai passando de `agent.max_trial`)
  ou `kept` (botão "Manter"; limite `agent.max_generated`). Travas: máximo de rodadas e de consultas ao Clef; id
  sempre inédito; "igual" só com a mesma assinatura exata de passos (`recipe_signature`), um passo a mais já é outro
  caso (similaridade só vira aviso).
- **Repetição e fusão** (tela Casos de uso): `POST /api/recipes/similarity` (Clef dá a nota do par: `same`/`overlap`/
  `different`, combinada com `recipe_overlap`), `POST /api/recipes/merge` (LLM propõe um caso mais geral, sem gravar),
  `POST /api/recipes/merge/apply` (grava, desativa ou apaga os originais), `POST /api/recipes/{id}/keep`.
- **Melhorar a foto (um preset, quatro opções)**: `edit`/`melhorar` substituiu os presets `restaurar` e
  `restaurar_borrada`, que disputavam o mesmo pedido com o `livre` ("Make the image quality better" dava
  **0,933** para restaurar e **0,011** para livre com o Clef real). A variável `modo` — `sutil` (melhora e mantém
  o tamanho, a mesma pilha do `livre`), `restaurar` (LoRA restore, redesenha em 2 MP), `focado` (fora de foco,
  restore 0,5) e `antiga` (riscos/cor desbotada) — é UMA pergunta com critérios curtos e observáveis. O `melhorar`
  **não** tem `requires: [restore]`: o `sutil` não precisa da LoRA, então o preset aparece sempre (as opções que a
  usam perdem a LoRA se ela estiver escondida). `livre` continua sendo o editor genérico de qualquer mudança de
  conteúdo e `exposicao` a correção de luz — sem eles, "deixe o cabelo ruivo" cairia num prompt de qualidade.
- **Medidas da foto no `state`**: `pickflow/quality.py` mede cada anexo (megapixels + nitidez por variância do mapa
  de bordas a 768 px, só PIL) e `ImageRef.quality` leva a frase em inglês ("640x480, 0.3 MP, soft focus") para o
  `attached_images` de TODAS as perguntas. É o que permite o Clef escolher `sutil` × `restaurar` com informação.
  Limiares calibrados em 241 fotos reais dele: `SOFT` = 350 (p10), `CRISP` = 1100 (mediana). Custo mediano 46 ms.
- **Aprendizado com o feedback (`clef.learn`)**: cada execução ganha a linha "Foi isso que você queria?" com quatro
  respostas — `Era isso`, `Não era isso`, `Era outro workflow` (+ a rota certa) e `Era outro prompt` (+ o prompt
  certo, quando a rota estava certa) — e o download conta como positivo fraco. Tudo vai para `data/feedback.jsonl`
  (`pickflow/feedback.py`). Cada veredito fala só do que a pessoa indicou: `up` credita rota e prompt; `down` mexe só
  na rota (não se sabe de quem era o problema); `other` derruba a rota e sobe a apontada; `preset` **confirma a rota**
  e troca o prompt. Rota forçada na tela (`origin="user"`) ou feita pelo agente não ensina a rota ao histórico.
  Três usos no roteador: (1) `similar_requests` no `state` com os casos parecidos que deram certo OU que foram
  corrigidos com `other` — vindos de `FeedbackStore.examples`, que **funde** os casos: agrupa pelo destino (rota
  aprovada ou a apontada), conta a evidência (`similar_past_requests`), descarta texto repetido e distribui em
  rodadas (uma linha por destino; a segunda amostra do mesmo destino só ocupa a vaga que sobrar —
  `learn.examples_per_flow`, 1). Sem isso, três repetições do mesmo pedido gastavam as três vagas e um caso novo
  nunca entrava; (`learn.examples`, 3); (2) correção Beta-Binomial **semeada
  pela probabilidade do Clef** para a rota e para o prompt (`learn.weight`, 0,5 — com 0,3 nunca virava uma decisão
  0,7 × 0,3, por isso o padrão subiu); (3) `_maybe_ask` pergunta na tela quando a confiança fica abaixo de
  `learn.ask_preset_below` (0,5) — o `__auto__` do modal mantém a resposta do Clef; **no modo `mock` não pergunta**
  (a resposta é sintética e a janela travaria os testes). A rota julgada é a que RODOU: `PlanResult.route` (do
  roteador, já com a 2ª análise e a correção do histórico) e o runner atualiza depois do modal de dúvida e do agente
  — procurar a rota nas decisões deixava de fora justamente essas trocas. Sinais implícitos: `_implicit_feedback`
  compara o pedido novo com o anterior (`similarity` ≥ 0,8): rota diferente = `redo` (erro), mesma rota = `same`
  (ajuste, não conta), outro assunto = nada. O evento `done` leva `run` e `decision` (`route`, `origin`, confiança,
  presets, pedido) e `GET /api/feedback` devolve `runs` (execução → voto) para a tela marcar os botões já
  respondidos depois de recarregar.
- **Vários modelos de imagem (preparado, só o Qwen existe)**: `settings.model` (padrão `qwen21`), seletor "Modelo: …" no chat
  (`GET /api/models`; só aparece com 2+ opções), `model` no corpo de `POST /api/conversations/{id}/messages` (desconhecido ou
  não disponível cai no padrão) e `Pipeline.start/_turn(model=...)`. O `_turn` monta `self.view = store.for_model(model)` e o
  `Router` usa essa visão (o `lock` garante um turno por vez); o agente também, e o caso de uso que ele cria leva a tag do
  modelo. `Router._resolve` troca o workflow de um passo de caso de uso pelo equivalente (`implements`) do modelo. Prompts:
  sem tag valem para o modelo do workflow dono do arquivo; um workflow com `implements` herda os presets do alvo que tenham
  `models` explícito com o modelo. `Store.prompts/loras` seguem SEM filtro (as telas de edição mostram tudo). Nada em
  `defaults/` foi marcado: não há workflow de outro modelo, então a tela e o Clef ficam idênticos. `families.yaml` traz o
  `match` do MOSS (`*moss*`, palpite: ajuste com o nome real do arquivo).
- **Codificador de texto escolhido em Ajustes** (`encoders.py`): os workflows de editar/transferir/pose/3D/colar/fundo carregam
  `qwen3vl_8b_fp8_heretic` e os `troca_*` (AusBoss) `qwen3vl_8b_int8_convrot`, ambos num `CLIPLoader`. `Pipeline._run_step` chama
  `encoders.apply` no grafo já convertido (inclui os `CLIPLoader` dentro de subgraph, que o conversor achata); com a versão
  escolhida fora do ComfyUI nada muda e um `status` avisa. O assistente (`SetupService._files`) marca o outro arquivo como
  `unused` e não o conta como pendente. O CLIP PE do `t2i` (`qwen3.5_9b_..._pe_t2i`) não é tocado.
- **Servidor do Clef: bf16 e int4, Windows e Linux** (`clef_server/`): `--quant` aceita `bf16` (padrão; `none` é apelido) e `int4`
  (bitsandbytes nf4; **o int8 saiu**: nunca foi medido e a bitsandbytes na CPU não o garante). `check_setup(device, quant)` roda antes de
  baixar ~19 GB e na partida, e diz o motivo (int4 precisa de CUDA, torch sem CUDA, bitsandbytes ausente). As estimativas são as medidas
  no clef-flash: bf16 17,8 GB (pico ~26), int4 7,9 GB (pico 16,4 = pesos + 8,5 de ativações). `/health` devolve `quant` `""` (bf16) ou
  `int4`. `start_clef.py` é um único launcher (sem um .bat por quantização): `--quant` pula a pergunta, a última escolha vira o Enter
  (`.last_quant`, ignorado pelo git), sem placa NVIDIA só oferece bf16 na CPU. **Testado** com um `joint_schema_model` de mentira e uma
  conversa HTTP real com o `ClefClient` (`tests/test_clef_server.py`); **não testado** com os pesos reais neste servidor (o servidor de
  referência que o usuário roda à parte é outro, mais completo; a assinatura de `load_release_model` foi conferida nos pesos dele) nem em Linux de verdade.
- **Primeira experiência**: o `README.md` é o caminho curto (~130 linhas); o resto está em `docs/`. O cartão *Antes de começar* tem
  um botão **Usar o modo de teste** quando o Clef não responde. `start.bat`/`start.sh` marcam `.venv/.ready` só depois do `pip install`
  (uma instalação interrompida não fica "pronta"). Linux: o `.gitattributes` força LF nos `.sh`; **o bit de execução precisa ser posto
  no commit** (`git update-index --chmod=+x start.sh install_nodes.sh clef_server/start_clef.sh`), senão `./start.sh` falha (`bash start.sh`
  funciona). No Linux as grafias dos arquivos contam: `tests`/script de conferência provaram que todo `file:` dos registros bate com o
  nome exato em `defaults/workflows/`.
- **Camada 3 do aprendizado (decidida, NÃO implementada)**: rodar um "harness2-lite" só se as camadas 0-2 mostrarem
  resultado — tratar `custom/clef_prompt.yaml` e os `when` dos prompts como superfície mutável, com um replay dos
  pedidos já rotulados (as respostas do Clef podem ser cacheadas, custo ~zero), o LLM do redator como *improver* e a
  concordância com os rótulos como fitness. Veio de `google-research/harness2` (Apache-2.0, arXiv 2610.14224,
  *Propose–Probe–Compose*); o repo original **não** serve como drop-in: ele otimiza harness de agente com tool loop,
  dispensa ground truth de propósito (aqui há rótulos) e exige Linux + Vertex AI.

- **3D (mudar o ângulo)**: workflows `ambiente_3d` (rota; TripoSplat com a foto inteira) e `pessoa_3d` (interno; BiRefNet
  recorta) usam a MESMA cópia `PickFlow 3D - Gerar Splat (TripoSplat).json` (o `set` muda o `switch` do subgraph);
  `anyangle` (interno) usa `PickFlow 3D - AnyAngle (mudar camera).json`. Caso de uso `pessoa_angulo` = pessoa_3d -> anyangle.
  `pessoa_angulo_troca` (2 imagens, papéis person/reference) acrescenta o `troca_pessoa` (variável `corpo` pelo Clef;
  o `request` do passo pede só a cabeça quando a referência é um rosto) para recuperar a identidade que o splat perde.
  Ordem testada no ComfyUI real: image1 = original, image2 = render, prompt "Change the camera angle from <image1> to
  <image2>." (a do card do autor devolvia quase a original). No TripoSplat a frente da foto fica em +X (yaw 90° no
  CreateCameraInfo); o `splat.js` gira o splat para ela ficar diante da câmera inicial.
  O `splat.js` navega como o Blender, sem OrbitControls: modo `nav` (pivô, botão do meio gira, Shift arrasta, Ctrl/roda
  zoom na direção do mouse, duplo clique escolhe o pivô por raycast no splat, teclado numérico/fileira de números) e
  `walk` (W A S D seguradas, Shift+` alterna); `setFov`; a interface chama `nudge(...)`.
- **Outpaint**: LoRA `outpaint` (auto) e prompt `expandir` no `edit` (variável `expand`: lados, tudo, cima/baixo, `choose` = escolher
  na tela, `gray` = só completar o cinza que a imagem já tem). Com `expand_edit: true` no prompt, qualquer opção que aumenta a tela
  (ou `choose`, `ask_expand`) abre antes a tela "Ampliar a imagem" (`needs_input` kind `outpaint`, `outpaintModal`): a foto sobre o
  cinza, bordas arrastáveis, campos em %, proporções prontas, tamanho final e aviso acima de 4 MP; a sugestão do Clef vem
  pré-preenchida e cancelar encerra a execução. `gray` e a repassada de `_verify_gray` nunca abrem a tela.
  **`gray` tem texto e resolução próprios**: o render do 3D sai macio (poucos pontos) e a instrução da LoRA
  ("keeping the existing composition unchanged") congelava essa qualidade — a opção acrescenta "fill the gray AND
  redraw the whole image with more detail…" e sobe o `resolution` para **1536** (~2,2 MP; era `0`, ou seja, o tamanho
  do render, ~1 MP). É `settings` de opção (o `pvars.render` mescla). Nas opções que **aumentam a tela** o
  `resolution` tem de continuar `0`: o `paste_back` depende do tamanho exato do canvas com cinza.
- **Memória no meio do turno (o caso de uso de 4 passos)**: `_turn` libera a VRAM do ComfyUI (`_make_room_for_clef`)
  **antes de cada `Router.bind`**, não só no começo do turno. Sem isso, num caso de uso de vários passos o Clef precisa
  recarregar no meio do turno enquanto o Qwen ainda está na placa, o modelo **vaza para a memória compartilhada** e cada
  decisão passa de 0,5 s para 30 s (medido: 33 s com 4 imagens, pico de VRAM 25,8 GB > 24 GB, RSS do Clef 16,4 GB). Foi
  assim que um pedido estourou o `clef.timeout_s` (120 s) e o erro apareceu como «Não consegui falar com o Clef em X: »
  vazio — o timeout do httpx não traz mensagem, e agora `clef.py` diz o que houve e o que ajustar. O que resolve de
  verdade é o Clef precisar de menos espaço para decidir — veja o item "4 bits" abaixo (o `--quant int4` do servidor de
  referência equivale ao `--load-in-4bit` do servidor do Cloudflare).
- **4 bits (medido — e a conclusão é contraintuitiva)**: `--load-in-4bit` (bitsandbytes, nf4, mesma pilha de pesos, sem
  baixar outro modelo) deixa o `clef-flash` em **7,9 GB** de VRAM em vez de 17,75 GB, com as mesmas rotas: num A/B com o
  roteador real, 13 pedidos, oito cenários deram **8/8 rotas iguais** e 27/28 perguntas (a única troca foi 0,227 × 0,236,
  abaixo do `then_min`). Decidir com 2 imagens cai de 23,9 s para 3,0 s (5-12×).
  **Mas decidir dispara as ativações**: o pico sobe de 7,9 para **16,4 GB** (em bf16 chega a 25,8 GB), e 16,4 + 15,5 do
  Qwen não cabem em 24 GB. Então o Clef **continua sendo descarregado antes de gerar** — é a resposta certa, não um
  defeito; manter os dois na placa (o que eu supus primeiro) enche a VRAM, o modelo vaza para a memória compartilhada e
  cada decisão passa de 30 s. O `/health` informa três números: `quant`, `vram_expected_gb` (pesos, para carregar) e
  **`vram_work_gb`** (pesos + ativações, medido pelo pico ou estimado em +8,5 GB); `memory.clef_vram_need` usa o de
  trabalho e `should_free_comfy`/`should_unload` fazem a conta com ele. Conta pronta, com o motivo, em **Ajustes > Clef**
  (`GET /api/clef/memory`). O que reduziria mesmo as recargas é planejar todos os passos antes de gerar (um load por
  turno); não feito porque `on_partial`/`_on_pose` dependem do resultado do passo anterior.
  `causal_conv1d` não tem wheel para Windows: o aviso dele no log é esperado. `flash-linear-attention` + `triton-windows`
  funcionam e somam mais ~23% (o aviso do `chunk_gated_delta_rule` some).
- **Rota própria para o Outpaint** (`expandir_imagem`, caso de uso de 1 passo: `edit` + prompt `expandir`): o prompt `expandir`
  só existia dentro do `edit`; com "Outpaint this image" o Clef real deu a rota a `pessoa_angulo` (0,34) e `background_removal`
  (0,44) e nunca chegou ao `edit`. Um caso de uso é uma opção de rota com descrição própria e fixa o prompt (a variável `expand`
  e a tela "Ampliar a imagem" seguem iguais). A descrição do `edit` não cita mais outpaint, para não disputar o pedido.
  LoRA: a v2 da ausboss (1-2 MP, instalada); a v1 (`qwen-image-2.1-outpaint.safetensors`) serve melhor para zoom-out grande a ~1 MP.
- **Fusão que completa o corpo**: `colar_fundir`/`pessoa` tem a variável `corpo` (`inteiro` | `completar`); o Clef olha a
  colagem (a última imagem do `bind`) e o pedido. `completar` leva `{request}` (pose e ação) e manda desenhar o que falta.
- **Fusão e LoRAs fixas**: o arquivo do `colar_fundir` tem pysj666 (nó 11) e Consistency (nó 13) fixas; `knobs` (`consistency` com
  `bypass`, `pysj`, `pysj_clip`) deixam a opção `completar` desligar a Consistency e baixar a pysj666 a 0,5. Testado: com a
  Consistency em 0,6 a pessoa substituída ficava por baixo da colada. Texto genérico sozinho é pouco confiável para
  "no lugar de fulano" (às vezes some outro personagem); o redator com visão, que descreve quem sai, funcionou melhor.
- **Transferir × colar (testado com gerações reais)**: `Router._paste_intent` — com 2 imagens e rota em `PASTE_FAMILY`, pergunta
  `paste_intent` (outra | trocar | substituir | segurar | acrescentar; 28/29 no Clef real, antes de `trocar`) e move a rota: trocar (o pedido diz "face and body", "body swap", "head swap") → `troca_pessoa` (`SWAP_ROUTE`; a porta `_engine_gate` ainda decide se é uma exceção que vai ao `edit`; o `then` nunca repete a rota que o Clef tinha ao responder); substituir/segurar →
  `transfer_object` (com `vars.relacao`), acrescentar → Colar e fundir. `transfer_object` tem a variável `relacao`
  (adicionar | substituir) e o preset `pessoa` aceita pose do pedido. Recorte que encosta na borda (`storage.cut_edges`, faixa de
  2%) + `on_partial` no registro do recorte → a fusão recebe `corpo: completar`. Entradas com alfa vão achatadas em branco
  (`flatten_alpha`), salvo camada posicionada, máscara ou `keep_alpha` na entrada.
- **Mudar a pose** (`mudar_pose`, 1 imagem): `pose_detectar` (registro com `pose: {nodes: [lote, inteira]}`: `SAM3Segment` em modo *Separate* dá uma imagem por pessoa e o DWPreprocessor roda no lote (um quadro do `openpose_json` por pessoa); outro DWPreprocessor na foto inteira é a reserva; `pose.combine` junta: vale o do lote, sem deduplicar pessoas coladas, e só entram da foto inteira as que não caem em cima de uma já achada. Testado: na foto de duas pessoas se beijando o DWPose sozinho dava UM esqueleto misturado; com o SAM 3 saíram dois certos) -> `_pose_choose` abre `needs_input` kind `pose` (`poseModal`: SVG, ossos de tamanho fixo, filhos acompanham, Alt/Shift) -> devolve o mapa de pose (`pose.draw`, de todas as pessoas) como imagem, com `pose_text`; `_on_pose` troca `{pose_change}` no `request` dos passos seguintes. **Mãos:** `parse` guarda, depois dos 18 pontos do corpo, 21 pontos de cada mão (`HAND_L`, `HAND_R`; o DWPreprocessor roda com `detect_hand` ligado); a tela e `clean` só veem os 18 (`[:BODY]`), `with_hands` leva a mão do detector junto com o antebraço (ou usa `HAND_TEMPLATE`, mão aberta, se o braço mexeu e o detector não viu a mão) e `draw` pinta os dedos no mapa; sem isso o Union só tem o ponto do punho e desenha uma luva sem dedos (testado: mão levantada saía lisa, agora tem 5 dedos). **Pixels originais:** o Union redesenha a foto toda a ~1 MP e as mãos de quem não mexeu saíam borradas; `pose_blend: true` no registro faz o runner aplicar `compose.blend_masked` com `pose.change_mask` (ossos que mexeram, lugar antigo e novo, mão/pé e cabeça, borda suavizada) e o resto volta a ser o pixel da original. `pose.describe` cobre braços/pernas (giro > 12° ou tamanho > 20%), cabeça, tronco e deslocamento do corpo todo, e dá uma frase geral para ajuste pequeno: a tela conta qualquer ponto mexido (`changed`, 0,4% da foto) e o fluxo só recusa com "Nenhuma pose foi alterada" quando NADA mexeu (antes recusava tudo que não fosse um membro girando muito). Mover o corpo todo (arrastar o pescoço) não apaga o corpo antigo: só descreve. **Membro antigo:** o ControlNet só desenha o membro novo e o antigo ficava duplicado; `_pose_erase` roda `replace_redbox`/`remover` (nested `_run_step` com `forced={"red": caixas}`) sobre `pose.erase_boxes` (um quadrado por membro que mexeu, mão/pé incluídos) com o texto de `pose.erase_text`, e guarda o resultado em `run.pose_reference`; `pose_aplicar` (registro `pose_reference: image_1`) usa essa foto como referência. **Descartado em testes reais:** máscara de inpaint do Union (deixa o cinza na imagem), referência com o membro em cinza + Union (cinza fica; só funcionou com a semente 43), e Outpaint em cima do cinza (recria o braço, 3/3, porque a cena "pede" um). `pose_aplicar` = ControlNet Union (`QwenImage21UnionApply`, modo Pose) com a foto como referência. **Testado no ComfyUI real:** com o mapa e o prompt genérico quase nada mudava; com a frase de `pose.describe` o braço levantou e os outros ficaram iguais. Força 1,0 (1,6 gerava manchas). Corpo só (sem mãos/rosto). `min_area` 0,01 corta crianças minúsculas do fundo.
- **Trocar pessoa e roupa (AusBoss), com reserva**: workflows `troca_pessoa` (Person Swap v10) e `troca_roupa` (Outfit Swap v11),
  cópias em `defaults/workflows/PickFlow Troca - *.json` sem notas, comparador, cronômetro, `ShowText` nem `properties.models`
  (o assistente do PickFlow é quem baixa; sem isso `used_by` pedia LoRAs que o fluxo não carrega) e sem pasta nos nomes das
  LoRAs (o `AUSBOSS_NODES_LoraLoader` acha pelo nome em qualquer subpasta; os `widgets_values_named` também precisam ser limpos:
  o conversor prefere os nomeados). O texto do prompt vai para o nó `AUSBOSS_NODES_Text` ("Quem trocar"/"Quem vestir": variável
  `quem`, 10 opções; `corpo` liga `whole_body`, o `PrimitiveBoolean` 5). `modes`: `turbo` (LoRA viggle, 6 passos na pessoa, 8 na
  roupa) e `quality` (sem a viggle, 25 passos), trocados com `set` no widget `loras` (uma string JSON) e `knobs.steps`. A 2ª
  personagem do Person Swap NÃO foi ligada (o nó-subgraph 14 em bypass não é achatado como passagem pelo conversor; mais de uma
  pessoa vai para a reserva). O `ImageCropRotatePad` da roupa só serve de porta de entrada (foto inteira): `converter.is_loader`
  trata como carregador qualquer nó com combo `image` de upload, e o doctor aceita qualquer nó `output_node` como saída
  (`AUSBOSS_NODES_SaveImage`). `Reroute` não vem mais no `object_info` do ComfyUI novo: o conversor checa a passagem ANTES de
  reclamar de nó ausente. **Reserva**: o `edit` (prompts `trocar_pessoas` e `trocar_roupa`, intocados) fica para as exceções que
  o usuário observou (rosto ocupado com uma lata, corpo cortado) e para só o corpo, várias pessoas/lugar e tatuagens;
  `settings.clef.engine_main_weight` (1,5) pesa a favor do fluxo novo e `engine_min` (0,5) é o mínimo da exceção. As descrições
  do Clef (`instructions`, `edit`, `troca_*`) foram reescritas para o `edit` não competir na rota. Casos de uso `trocar_pessoa_e_fundo`
  e `pessoa_angulo_troca` fixam `troca_pessoa` (sem porta); o `custom/clef_prompt.yaml` do usuário tem cópias deles e foi atualizado
  à parte. Testado com gerações reais (fotos sintéticas do próprio t2i): corpo inteiro, só a cabeça, Qualidade, roupa Rápido e
  Qualidade; na foto com a lata na frente do rosto o editor antigo seguiu melhor a referência (uma amostra).
- **Vestir a pessoa e levar para outro lugar** (`vestir_e_levar`, caso de uso de 3 imagens): pessoa + **roupa sozinha** +
  lugar. Passos: `troca_roupa` (papéis `person`/`outfit`) → `colar_recortar` (pessoa) → `colar_posicionar`
  (`role:background` + `previous`) → `colar_fundir` (`pessoa`). Nasceu de um erro real: "Make an image of <image1> woman
  wearing the <image2> clothes… at <image3>" (imagem 2 = a peça, sem ninguém vestindo) caía em `trocar_pessoa_e_fundo`,
  que trata a imagem 2 como a CENA — a troca de pessoa consumia a roupa como se fosse gente e o resultado saía a
  própria foto da roupa. Para o Clef acertar: `CONTENT_KINDS` ganhou `garment` ("clothing or an outfit ON ITS OWN, with
  nobody wearing it"); as descrições de `troca_roupa` e do caso novo dizem que a peça pode estar sozinha; e o
  `trocar_pessoa_e_fundo` (inclusive a cópia do `custom/clef_prompt.yaml` do usuário, que é a que vale) exclui
  explicitamente "vestir a mesma pessoa". Id inédito é somado ao `custom/` de quem já personalizou, então o caso novo
  aparece sem ninguém apagar nada.
- **Vestir com a roupa da referência e DEPOIS trocar a pessoa** (`vestir_e_trocar`, 2 imagens): `troca_roupa`
  (image_1 = base, image_2 = referência, `vars.quem: person`) → `troca_pessoa` (image_1 = `previous` — a base já
  vestida —, image_2 = a mesma referência, `corpo: both`). O person swap copia a pessoa inteira e **já traz a roupa
  dela**, mas o usuário observou que a roupa sai melhor quando é aplicada primeiro e o swap vem por cima; por isso o
  caso de uso existe. **Só vale quando o pedido cita a roupa** ("com a roupa dela", "wearing her clothes"); sem isso, e
  sempre no swap só de rosto, continua `troca_pessoa` — as descrições dos dois dizem isso explicitamente (o usuário quer
  poder usar a roupa da imagem 3 com o swap na pessoa da imagem 1).
- **Trocar a pessoa, ficar com a roupa da cena e levar para outro lugar** (`trocar_pessoa_roupa_e_fundo`, 3 imagens): `troca_pessoa`
  (cena = pose e roupa, referência = só rosto e corpo) → `troca_roupa` (`image_1: previous`, `image_2: role:scene`: o swap
  traz a roupa da pessoa de referência, então a roupa da cena é vestida DEPOIS) → `colar_recortar` (pessoa) → `colar_posicionar`
  (`role:background` + `previous`) → `colar_fundir` (`pessoa`). Só vale quando o pedido cita a roupa da cena; sem isso é
  `trocar_pessoa_e_fundo`, e as descrições dos dois se apontam. O `custom/clef_prompt.yaml` do usuário tem cópia do
  `trocar_pessoa_e_fundo` (a dele já recorta e funde): a frase de exclusão foi acrescentada lá à parte; o caso novo vem dos defaults.
- **Cancelar vale na hora** (`Pipeline.cancel`): antes só levantava uma bandeira, e as esperas longas — a chamada ao
  Clef (que pode estar carregando o modelo, ~30 s) e a fila do ComfyUI — não olhavam para ela, então o botão Cancelar
  não fazia nada até a resposta chegar. Agora `cancel()` derruba a **tarefa** do run (`Run.task`): `_guarded` pega o
  `CancelledError`, manda `/interrupt` ao ComfyUI quando o run está em `phase "steps"` (senão ele continuaria
  desenhando) e, em `phase "clef"`, explica que a carga do modelo no servidor do Clef não pode ser interrompida pela
  metade. Para liberar a memória depois disso existe **Ajustes > Clef > Descarregar o Clef agora**
  (`POST /api/clef/unload`).
- **Colar com papéis**: `colar_e_fundir`/`colar_sem_fundir` usam os papéis `background` e `subject` (a ordem de envio não
  importa; o "invertido" saiu). O recorte (`colar_recortar`) tem modos `pessoa` (SAM3+RMBG), `objeto` (SAM3 pelo nome,
  `max_segments: 1`) e `fundo` (RMBG); o texto do SAM3 vem das variáveis `alvo` (nome em inglês, que fixa o modo) e `cor`.

## 9. Testes

`python -m pytest -q`. Peças de teste reaproveitáveis:
- `tests/fake_comfy.py` (ComfyUI de mentira com `tests/object_info.json`; o SaveGLB devolve um `.spz` falso; para uma LoRA nova aparecer como instalada,
  acrescente o arquivo nas listas `lora_name` desse JSON e em `tests/make_object_info.py`);
- fixtures `project` (cópia de `defaults/`), `comfy`, `api` (TestClient + pipeline) em `conftest.py`/`test_e2e.py`;
  helpers `upload`, `start(route=...)`, `wait_event`, `_answer`, `_chain` (LoRAs do grafo enviado);
- `Stub` de Clef que escolhe a opção pela id da pergunta (`preset`, `var_<key>`, `route`, `then`, `q` do agente,
  `lora_<id>`) — troque o Clef do pipeline com `monkeypatch.setattr("pickflow.runner.make_clef", ...)`;
- `FakeLLM` roteirizado (`tests/test_agent.py`) — troque com `api.pipe.llm = ...`;
- o `MockClef` dá respostas quase uniformes: não use para testar decisões.

Contagens fixas que quebram ao acrescentar coisas padrão: nº de workflows em `/api/status`, nº de prompts de `edit`
(`test_e2e`), tamanho do catálogo (`test_setup`). Atualize o número quando mudar de propósito.

**Ver a interface sem GPU**: `python tests/demo_server.py` (porta 7861; variáveis `DEMO_RGBA=1`, `DEMO_ADDON=1`,
`DEMO_AGENT=1`, `DEMO_SPLAT=<arquivo.spz>` para o 3D de verdade) e Playwright (Chromium já instalado; no Windows passe
`executable_path` do `ms-playwright/chromium-*/chrome-win64/chrome.exe` e `--use-angle=swiftshader` para o WebGL). Para parar:
`pkill -f "[d]emo_server.py"` **em uma chamada separada** (senão o `pkill` mata o próprio shell; no Windows, PowerShell
`Stop-Process` no processo cuja linha de comando tem `demo_server`). O demo copia `web/`: reinicie depois de mexer na interface. Um modal pendente de uma execução anterior bloqueia o clique seguinte:
reinicie o demo.

## 10. Como fazer…

- **Workflow novo**: `.json` em `custom/workflows/` (ou `defaults/` se for padrão) + registro + prompts + descrição em
  `clef_prompt.yaml`; ou importe pela interface. Rode Ajustes > ComfyUI > Verificar workflows no ComfyUI real.
- **Prompt novo num workflow existente**: acrescente um preset em `defaults/prompts/<id>.yaml` (com `when` claro para
  o Clef) e atualize a contagem nos testes.
- **LoRA nova**: `defaults/loras.yaml` (descrição que o Clef entende, `conflicts`, `settings`) + `defaults/models.yaml`
  (para o assistente instalar) + linha na tabela de LoRAs de `docs/installation.md` + preset que a use, se couber. Cuidado com `adjust` vs
  `strength` explícito.
- **Caso de uso novo**: `recipes` em `defaults/clef_prompt.yaml`, com `images`, `roles` e `description` bem escritos.
- **Parâmetro de desenho novo**: `params` no registro; coleta em `_collect_drawings`, normalização em `_norm_boxes`,
  tokens em `_draw_tokens`, tela em `drawModal`/`boxEditor`.
- **Mudança que o usuário só quer para si**: addon ou `custom/`, nunca `defaults/`.

## 11. Antes de entregar

1. `python -m pytest -q` verde (e `node --check web/app.js` se mexeu na interface).
2. Mexeu na interface? Suba o demo e olhe capturas de tela.
3. Atualize `docs/` (o assunto certo; o `README.md` só muda se o caminho curto mudou), esta página se a arquitetura mudou e a skill se mudou algo de addons.
4. Diga ao usuário o que não foi testado no ComfyUI/Clef reais e o que reiniciar (mudanças em `.py` e `web/` exigem
   reiniciar o PickFlow; registros, prompts, LoRAs e workflows valem na hora).
