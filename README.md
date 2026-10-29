# PickFlow

Um chat para o **ComfyUI**: você escreve o que quer (e anexa fotos, se precisar), o modelo **Clef** decide qual dos seus
workflows rodar — ou uma sequência deles — e o PickFlow executa tudo no seu ComfyUI.

> **Feito para o Qwen-Image 2.1.** Os workflows que acompanham o projeto usam os modelos e os nós do Qwen-Image 2.1
> (edição de imagens, troca de pessoa/cabeça, mover e transferir objetos, remover fundo, gerar do zero).
> Com outros modelos o PickFlow funciona, mas você mesmo precisa trazer os workflows (veja
> [Adicionar o seu workflow](#adicionar-o-seu-workflow)).

```
 Você ──► PickFlow (esta pasta, porta 7860) ──► Clef    (decide e devolve probabilidades)
                      │
                      └──────────────────────► ComfyUI (executa o workflow pela API HTTP)
```

O PickFlow **não é um plugin do ComfyUI**: é um programa à parte (Python + uma página web) que conversa com o ComfyUI
por HTTP. Ele não altera os seus workflows nem a instalação do ComfyUI, a não ser pelos custom nodes que você instalar
de propósito (passo 3).

---

## O que você precisa

| | Para quê | Observação |
|---|---|---|
| **ComfyUI** | roda os workflows | Obrigatório e **atualizado** (os nós `TextEncodeQwenImage21` e `QwenImage21Cache` são recentes). [Como atualizar](https://docs.comfy.org/installation/update_comfyui). |
| **Python 3.10+** | roda o PickFlow | `start.bat` / `start.sh` criam o ambiente sozinhos. |
| **Placa de vídeo NVIDIA** | o Qwen-Image 2.1 | Os workflows foram feitos e usados numa RTX com 16–24 GB de VRAM (modelo em int8). |
| **Modelos e LoRAs do Qwen-Image 2.1** | os workflows carregam | O [assistente](#3-deixar-o-comfyui-pronto-assistente) baixa os que você escolher. |
| **Servidor do Clef** | escolhe o workflow | Opcional para testar a interface (há um *modo de teste*), necessário para decisões de verdade. Veja [Clef](#o-servidor-do-clef). |

## Instalação

### 1. Instalar o ComfyUI (se ainda não tem)

O PickFlow só pede imagens ao ComfyUI, então **ele precisa estar instalado, atualizado e aberto**.
Instale o [ComfyUI Desktop](https://www.comfy.org/download) (mais simples) ou a
[versão manual](https://github.com/comfyanonymous/ComfyUI) e abra-o uma vez. O assistente do passo 3 confere isso e
mostra o que fazer se não achar.

### 2. Baixar e ligar o PickFlow

```
git clone <endereço deste repositório> PickFlow
```

Pode ficar em qualquer pasta. Com o ComfyUI aberto, rode `start.bat` (Windows) ou `./start.sh`. Na primeira vez ele cria
o ambiente Python e instala as dependências; depois abre <http://127.0.0.1:7860>.

### 3. Deixar o ComfyUI pronto (assistente)

Na primeira vez a tela de conversa mostra **Antes de começar**. Clique em **Ver e instalar** (ou, a qualquer hora,
**Ajustes → Nós, LoRAs e modelos → Abrir o assistente**). Ele:

1. **procura a pasta do ComfyUI** (pelo próprio ComfyUI em execução; se não achar, você informa a pasta) e, se não
   encontrar o ComfyUI, explica como instalar;
2. mostra o que **falta** em três grupos: **nós do PickFlow** (caixas vermelha/verde e Viggle Turbo), **LoRAs** e
   **modelos base** do Qwen-Image 2.1 (arquivos grandes, desmarcados por padrão);
3. **pergunta o que você quer instalar.** Nada é copiado nem baixado antes de você clicar em *Instalar*;
4. baixa direto para `models/loras/` (ou a pasta do tipo), confere o **sha256** de cada arquivo e mostra o progresso.
   Depois de copiar nós, **reinicie o ComfyUI**.

Clicou em *Agora não*? O aviso some e o assistente continua em *Ajustes*. Se o ComfyUI está em **outro computador**, o
assistente não instala nada nele: use a lista de arquivos e as pastas indicadas para copiar à mão.

A lista de arquivos está em [`defaults/models.yaml`](defaults/models.yaml); para acrescentar os seus, crie
`custom/models.yaml` com o mesmo formato.

<details>
<summary>Instalação manual (sem o assistente)</summary>

**Modelos** (pastas normais do ComfyUI):

| Arquivo | Pasta | Onde baixar |
|---|---|---|
| `qwen_image_2.1_int8_convrot.safetensors` | `models/diffusion_models/` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) |
| `qwen3vl_8b_fp8_heretic.safetensors` | `models/text_encoders/` | idem |
| `qwen_image_2.1_vae_bf16.safetensors` | `models/vae/` | idem |

**LoRAs** (`models/loras/`). Cada workflow usa alguns:

| Arquivo | Usado em | Licença | Onde baixar |
|---|---|---|---|
| `Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors` | todos os **Turbo** (6 passos) e *Mover ou trocar objetos (Quality)* | Qwen Research | [Viggle/Qwen-Image-2.1-viggle-turbo](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) |
| `bfs_body_swap_v1.0_qwen_2.1.safetensors` | trocar pessoas (corpo), transferir objeto (Turbo) | MIT | [Alissonerdx/BFS-Best-Face-Swap](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) |
| `bfs_head_v1.1_qwen_2.1.safetensors` | trocar pessoas (cabeça) | MIT | idem |
| `Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors` | quase todos | Qwen Research | [prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) |
| `qwen-image-2.1-consistency.safetensors` (a versão final; a de 2000 passos não é mais usada) | edição, trocar pessoas, substituir área | Qwen Research | [ausboss/Qwen-Image-2.1-Consistency-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) |
| `qwen-image-2.1-outfit-swap.safetensors` | trocar roupa ou tatuagem (não usar com a consistência) | ver a página | página no [catálogo](defaults/models.yaml) |
| `qwen-image-2.1-restore.safetensors` | restaurar ou melhorar a qualidade de uma foto (prompt *Melhorar a foto* e suas opções *Restaurar* e *Restaurar foto antiga*; não usar com a consistência) | Qwen Research | [ausboss/Qwen-Image-2.1-Restore-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Restore-LoRA) |
| `Qwen2.1_viewpoint_orbit.safetensors` | girar o ponto de vista | ver a página | página no [catálogo](defaults/models.yaml) |
| `qwen-image-2.1-outpaint-v2.safetensors` | expandir a foto e completar áreas cinza (prompt *Expandir ou completar áreas cinza*) | Qwen Research | [ausboss/Qwen-Image-2.1-Outpaint-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA) |
| `QI2.1_AnyAngle.safetensors` | mudar o ângulo da câmera com 3D (ambiente e pessoa) | Apache-2.0 | [lilylilith/QI_2.1_AnyAngle](https://huggingface.co/lilylilith/QI_2.1_AnyAngle) |
| `casual_snapshot_realism_qwen21.safetensors` | foto com cara de celular (opcional) | ver a página | só no tensor.art: baixe à mão e ponha em `models/loras/` |
| `flymy_realism.safetensors` | edição, trocar cabeça, mover/transferir objeto | Apache-2.0 | [flymy-ai/qwen-image-realism-lora](https://huggingface.co/flymy-ai/qwen-image-realism-lora) |
| `qwen-image-2.1纠正背景透视溶图v2.0.safetensors` (palavra de ativação `pysj666`) | *Colar e fundir* (perspectiva e luz) | ver a página | [RunningHubAI/rh-qwen-image-2.1v2.0-lora](https://huggingface.co/RunningHubAI/rh-qwen-image-2.1v2.0-lora) |

Pastas dentro de `models/loras/` são aceitas: o PickFlow acha o arquivo pelo nome.

**3D (mudar o ângulo da câmera):** o TripoSplat usa `triposplat_fp16.safetensors` (`models/diffusion_models/`),
`dino_v3_vit_h.safetensors` (`models/clip_vision/`), `triposplat_vae_decoder_fp16.safetensors` e `flux2-vae.safetensors`
(`models/vae/`), todos de [VAST-AI/TripoSplat](https://huggingface.co/VAST-AI/TripoSplat) (MIT), e o
`birefnet.safetensors` (`models/background_removal/`) de [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet) (MIT),
que recorta a pessoa. Os nós (`TripoSplat…`, `SplatToFile3D`, `SaveGLB`, `RemoveBackground`) vêm no ComfyUI atualizado.
O assistente baixa tudo isso.

**Nó de outro autor:** o recorte do *Colar e fundir* usa os nodes `SAM3Segment` (pessoas) e `RMBG` (objetos) do pacote [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG); o SAM3 baixa sozinho o modelo `sam3.pt` (~3,4 GB) para `models/sam3/` no primeiro uso. O PickFlow não o instala: o assistente mostra se falta; instale pelo ComfyUI Manager e reinicie o ComfyUI. (`Create Layered Image` e `Add Layer`, do mesmo fluxo, já vêm no ComfyUI atualizado.)

**Nós de outro autor para *Trocar pessoa* e *Trocar roupa*:** o pacote [ComfyUI-AusBoss](https://github.com/ausboss/ComfyUI-AusBoss) (`ausboss-nodes`, instale pelo ComfyUI Manager), que traz o carregador de LoRAs, o salvar imagem e os textos desses dois fluxos. Os dois também usam o codificador `qwen3vl_8b_int8_convrot.safetensors` (~9,4 GB, `models/text_encoders/`, está no assistente) e as LoRAs de trocar corpo/cabeça (BFS), a viggle (modo Rápido) e a de roupa, que já estavam na lista. O AusBoss acha a LoRA pelo nome em qualquer subpasta de `models/loras/`.

**Nós de outros autores para *Mudar a pose*:** [comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) (o `DWPreprocessor`, que baixa sozinho os modelos de pose), o [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG) (o `SAM3Segment`, o mesmo do recorte do *Colar e fundir*) e o pacote *Qwen Image 2.1 Fun ControlNet Union (T8)* (instale pelo ComfyUI Manager). O arquivo `Qwen-Image-2.1-Fun-Controlnet-Union-ComfyUI.safetensors` (~7 GB, `models/controlnet/`) está no assistente. O passo de apagar usa os nós *Caixas vermelha e verde* do PickFlow (`install_nodes`).

**Nós do PickFlow** (`comfyui_nodes/`): `install_nodes.bat` (Windows) ou `./install_nodes.sh` (Linux/macOS). O script procura o
`custom_nodes` do ComfyUI (ou aceite o caminho: `install_nodes.bat "D:\ComfyUI"`) e **não sobrescreve nada sem perguntar**.
Reinicie o ComfyUI depois.

</details>

Referência: [guia oficial do Qwen-Image 2.1 no ComfyUI](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1).

> **Licenças.** O código do PickFlow é MIT, mas os modelos e LoRAs **não**: cada autor tem a sua licença e vários
> (Qwen Research) são de **uso não comercial**. Confira a página de cada um antes de usar.

### 4. Conferir

1. **Ajustes → ComfyUI → Testar.** Mostra na hora *Conectado* (com versão e placa) ou o motivo da falha. Se o endereço estiver errado, o PickFlow tenta as portas comuns
   (8188, 8000–8003, 8199). O ComfyUI Desktop costuma usar a 8000.
2. **Ajustes → Verificar workflows.** Converte cada workflow para o formato da API do ComfyUI e confere nós, ligações,
   limites e modelos contra o **seu** ComfyUI. Cada linha vermelha diz o que falta. Faça isso uma vez antes do primeiro uso.
3. **Ajustes → Clef.** Aponte para o servidor do Clef ou use o **modo de teste** só para ver a interface funcionando.

A tela de conversa mostra um aviso *Antes de começar* enquanto o ComfyUI ou o Clef não respondem, ou faltam arquivos.

**Idioma.** A interface abre em **inglês**; *Settings → Language* troca para português (vale na hora, a página recarrega).
O idioma vale para as telas e para as mensagens do PickFlow. As descrições, os critérios e as instruções que o **Clef lê**
(descrição de workflow, "quando usar", instruções gerais, perguntas) estão em **inglês** nos arquivos de `defaults/` e nos
editores, e as perguntas montadas pelo código também; os nomes de tela (títulos, rótulos) seguem o idioma da interface.
Os pedidos podem ser escritos em qualquer um dos dois idiomas.

---

## Os workflows que acompanham

Ficam em `defaults/workflows/` (e `defaults/registry/` diz ao PickFlow quais nós recebem imagem, prompt e saída).

| Workflow | O que faz | Imagens | Desenhar |
|---|---|---|---|
| **Editar** — Turbo / Quality | **o workflow único de edição de pessoas e imagens**: edição livre, mudar luz ou estilo, **passar tatuagens**, **girar o ponto de vista** de um objeto, com até **8 imagens** (as que você não manda saem do grafo). O Clef escolhe o prompt **e as LoRAs** conforme o pedido (veja *LoRAs* e *Um prompt, vários casos*) | 1–8 | — |
| **Trocar pessoa** — Rápido / Qualidade | **o fluxo padrão de trocar a pessoa** (o *Person Swap* do AusBoss): a pessoa da cena vira a da 2ª imagem, **corpo e cabeça** ou **só a cabeça**; mantém o rosto e o resto da cena e **diz QUEM trocar** quando há mais gente ("a mulher da esquerda", "o homem da direita"). Em exceções (rosto ocupado, corpo cortado, só o corpo, várias pessoas) o Clef usa o editor antigo: veja *Fluxo padrão e reserva* | 2 | — |
| **Trocar roupa** — Rápido / Qualidade | **o fluxo padrão de trocar a roupa** (o *Outfit Swap* do AusBoss): um modelo de visão descreve a roupa da 2ª imagem e veste a pessoa da 1ª, sem mexer em rosto, cabelo, mãos, pose e fundo; também diz QUEM vestir. Tatuagens ficam com o editor | 2 | — |
| Mover ou trocar objetos — Turbo / Quality | **mover** um objeto para um destino vazio, ou **trocar** dois objetos de lugar entre si, na mesma imagem. São dois prompts do mesmo workflow; o Clef escolhe pelo seu pedido. **Conferido pelos pixels:** depois de *mover*, o PickFlow olha se a caixa vermelha mudou; se o objeto ficou lá também (dois iguais), roda uma segunda passada que apaga a cópia | 1 | caixa vermelha (objeto) e verde (destino, ou o outro objeto) |
| Colar e fundir (caso de uso, 3 passos) | cola uma pessoa ou objeto (2ª imagem) num cenário (1ª imagem): **recorta sozinho só a pessoa ou o objeto que o pedido citou** (o Clef escolhe o nome numa lista, como cadeira, sofá, cachorro ou vaso, e a cor quando o pedido diz; o SAM3 procura "beige chair" e pega só aquela coisa, mesmo com outras em volta; para uma foto de produto num fundo liso, tira o fundo inteiro), com fundo transparente. **A ordem das imagens não importa**: o Clef decide pelos papéis *Ambiente* e *Pessoa ou objeto a colar* (pessoa: o SAM3 e o RMBG rodam juntos e o PickFlow soma as máscaras, então braços, mãos, pés e pele à mostra que o SAM3 deixaria de fora voltam, sem levar cadeira ou objetos encostados); você confere o recorte e, se não ficou bom, **desenha uma caixa no original** (e/ou troca pessoa/objeto) para refazer; depois **arrasta, redimensiona e gira** no chat (pode deixar parte para fora da imagem; os cantos mantêm a proporção, as alças do meio dos lados esticam e a **bolinha acima do meio da borda superior gira**; Shift prende de 15 em 15 graus). A **fusão completa o corpo quando falta**: o Clef olha a colagem e o pedido e, se a pessoa colada veio cortada (só da cintura para cima, sem pernas ou mãos) ou o pedido manda uma pose ou ação ("sentada na cadeira, segurando uma cerveja"), a etapa Fundir vira uma edição completa (desenha o que falta, a pose e a interação com o móvel, mantendo rosto e roupa); se a pessoa já está inteira, só funde. Com **várias caixas** no recorte você recorta **vários objetos de uma vez** e posiciona cada um e por fim **funde** perspectiva, luz e sombra. Também há as variantes *a pessoa vem primeiro* e *só colar* | 2 | revisar o recorte (caixa para refazer) e posicionar a pessoa |
| Adicionar, substituir ou remover numa área | **adicionar** um objeto novo num lugar vazio, **substituir** o que está dentro do quadrado ou **remover**. Pode ter **vários quadrados vermelhos**, só para objetos **iguais** (clique num quadrado e use o **×** do canto para tirar; para objetos diferentes, uma edição de cada vez). O Clef escolhe o prompt pelo seu pedido | 1 | quadrados vermelhos |
| Transferir objeto entre imagens — Turbo / Quality | leva um objeto ou pessoa de uma imagem para outra | 2 | caixas vermelha e verde |
| Transferir **vários** objetos — Turbo / Quality | leva 2 a 4 objetos, de imagens diferentes, para uma imagem de destino: o Clef conta quantos o pedido manda, decide qual imagem é o destino e quais são objetos; na tela você desenha **uma caixa verde para cada objeto** e escolhe, em cada uma, **qual imagem enviada vai ali** (1, 2, 3… na ordem em que anexou), com uma caixa vermelha opcional em cada objeto | 3–5 | caixas verdes numeradas (cada uma com sua imagem) e vermelhas |
| Remover fundo | deixa só o sujeito (PNG) | 1 | — |
| **Ambiente em 3D** (mudar o ângulo) | a foto de um quarto, sala ou lugar vira um **modelo 3D (Gaussian Splat, TripoSplat)** que aparece na conversa: você **gira a câmera com o mouse** (para os lados, para cima, para baixo, aproxima), **baixa o PNG daquele ângulo**, baixa o `.spz` e escolhe entre ficar com o render (o cinza é o que a foto não mostrava) ou **completar o cinza**: o render segue para o Editar com a LoRA de Outpaint (só completar o cinza, sem aumentar a tela), e se sobrar mais de 2% de cinza ele roda de novo, até 2 vezes | 1 | girar o 3D |
| **Mudar a pose** (caso de uso, 2 passos) | o **SAM 3** separa cada pessoa da foto (inclusive **gente colada ou uma na frente da outra**, onde o DWPose sozinho mistura os corpos num esqueleto só) e o **DWPose** acha os 18 pontos do corpo de cada uma, num lote; a detecção na foto inteira fica de reserva para quem o SAM 3 não pegar. Aparece o **esqueleto de cada pessoa** numa tela: você arrasta os pontos e o membro todo acompanha (Alt mexe só no ponto, Shift estica o osso, o pescoço leva a pessoa inteira; há *Espelhar*, *Mostrar pontos que faltam* para corpos cortados e *Restaurar*). Quem você não mexer continua como está. **O membro antigo é apagado antes**: o PickFlow põe sozinho um quadrado vermelho sobre cada braço ou perna que mexeu e roda o fluxo de remover (o ControlNet só desenha o membro na posição nova, e o antigo ficava duplicado), reconstruindo corpo, roupa e fundo atrás. Depois desenha o mapa de pose de todos e **escreve em inglês o que mudou** ("the second person from the left: the arm on the left side of the picture: upper arm pointing up…"), porque o mapa sozinho, com a foto de referência, quase não move ninguém (testado). O **ControlNet Union do Qwen-Image 2.1** (modo Pose) redesenha a foto inteira com a nova pose, mantendo identidade, roupa, estilo e fundo; leva ~3 min a 1 MP (apagar + aplicar). **Mãos:** o DWPose também acha os dedos; a mão acompanha o braço que você mexeu e vai **desenhada com os cinco dedos no mapa de pose** (sem isso o ControlNet só tem o ponto do punho e desenhava uma "luva" sem dedos; se o detector não viu a mão, entra uma mão aberta de modelo). Depois de gerar, **só o que mexeu vem do modelo**: fora dos braços e pernas que você mexeu (lugar antigo e novo, com a mão ou o pé) o resultado volta a ser o **pixel da foto original**, então rostos, roupas e as mãos de quem ficou não perdem nitidez nem ficam borrados. Só o corpo e as mãos são editados (o rosto fica como o modelo desenhar) | 1 | modelar a pose |
| **Mudar o ângulo da pessoa (3D)** (caso de uso, 2 passos) | a pessoa (sem o fundo, recortada pelo BiRefNet) vira 3D, você escolhe o ângulo no mesmo visualizador e o **AnyAngle** refaz a foto nesse ângulo, mantendo a pessoa, a roupa e o cenário | 1 | girar o 3D |
| **Mudar o ângulo da pessoa e corrigir o rosto (3D + troca)** (caso de uso, 3 passos) | igual ao anterior e, no fim, **troca a cabeça (ou o corpo) pela da 2ª imagem**, para o rosto não sair degradado do 3D. O Clef decide quem é a foto e quem é a referência e se troca só a cabeça (referência com rosto/cabeça) ou também o corpo | 2 | girar o 3D |
| Gerar imagem do zero — Turbo / Quality | cria a partir de texto (o Turbo usa o mesmo grafo do Editar, sem imagens) | 0 | — |

**Controles do 3D (como no Blender).** Modo **Navegar**, o viewport do Blender em volta de um ponto central: botão do meio gira (o esquerdo também, para quem usa touchpad; Alt+esquerdo emula o do meio), Shift+arrastar ou o botão direito arrasta a vista, Ctrl+arrastar e a roda dão zoom (a roda aproxima na direção do mouse), **duplo clique** (ou Alt+clique do meio) faz do ponto clicado o centro do giro, e o teclado numérico (ou a fileira de números) troca as vistas: 1 frente, 3 direita, 7 cima (Ctrl = lado oposto), 2/4/6/8 giram 15°, Shift+4/6 inclinam, 9 o outro lado, 0 ou Home a câmera da foto, + e - zoom, "." centro no meio da cena. Modo **Andar** (o Walk do Blender, Shift+`): W A S D andam enquanto seguradas, Q/E descem e sobem, Shift corre, Alt anda devagar, arrastar olha em volta, a roda muda a velocidade e Enter volta a Navegar. A **Lente** muda o campo de visão; os atalhos ficam em *Atalhos* no painel.

**3D salvos.** Cada 3D gerado fica em `data/splats/` com a foto de origem: **a mesma foto não é gerada de novo** (o TripoSplat leva uns 30 s). Na tela do 3D, *Guardar na biblioteca* dá um nome e o mantém para sempre; os não guardados ficam só os 12 mais recentes. O botão **3D salvos**, ao lado de *Anexar imagem*, lista todos: *Abrir* (girar e baixar o PNG/o `.spz`), *Usar na conversa* (anexa a foto e já escolhe o fluxo 3D, sem gerar de novo) e *Apagar*. Um resultado que veio do 3D também tem o botão *Abrir o 3D*.

**Melhorar a foto.** Pedidos de só melhorar, limpar, deixar mais nítida ou restaurar uma foto **sem mudar o que está nela** vão para o prompt *Melhorar a foto* do Editar, que decide entre quatro opções: *Melhorar sem aumentar* (a foto já tem 2 MP ou mais e o pedido é geral: mexe na cor, no contraste, na luz e no detalhe fino e **mantém o tamanho e a moldura**, sem LoRA de restauração), *Restaurar* (foto pequena, de celular, quadro de vídeo, comprimida ou com pouca nitidez: **redesenha em cerca de 2 MP** com a LoRA de restauração), *Desembaçar* (foto fora de foco ou tremida, mesma LoRA em 0,5) e *Restaurar foto antiga* (riscos, poeira, grão e cor desbotada). As duas últimas só valem se a LoRA existir; a LoRA de restauração desliga a Consistency e o realismo. Mudar o conteúdo (cor, roupa, objeto, pose, estilo) continua sendo o prompt *Pedido do usuário*, e corrigir só a luz ou a exposição é o *Exposição neutra*.

**Expandir a foto (Outpaint).** O prompt *Expandir ou completar áreas cinza* do Editar usa a LoRA de Outpaint: o Clef escolhe para onde a foto cresce (lados, em volta, cima e baixo, um lado só) e o PickFlow aumenta a tela com cinza `#808080` (múltiplo de 32) antes de mandar; no fim cola a foto original de volta, com a borda suavizada, para o que já existia ficar idêntico. Com *Só completar o cinza* ele não aumenta nada: serve para os buracos cinza do render do 3D — e nesse caso **também melhora a qualidade**, porque o render do 3D sai macio (poucos pontos): além de completar o cinza, o modelo redesenha a imagem inteira com mais detalhe, em **1536** (~2,2 MP) em vez de no tamanho do render (~1 MP). Se ainda ficar mole, quem manda nisso é o `resolution` dessa opção em `defaults/prompts/edit.yaml` (2048 = mais detalhe e mais tempo); quando a tela **cresce**, esse número tem de ficar em `0`, senão o pedaço colado de volta sai no lugar errado.

**O PickFlow aprende com o seu retorno.** Embaixo de cada resultado aparece uma pergunta discreta com quatro
respostas: *Era isso*, *Não era isso*, *Era outro workflow* (abre a lista e você diz qual era o certo) e *Era outro
prompt* (o workflow estava certo e só o prompt não era o certo — assim o erro de um não suja o histórico do outro).
**Baixar a imagem conta como um "sim" fraco** e não atrapalha a linha, e um voto já dado continua marcado depois de
recarregar a página. Se você **repete o pedido com outras palavras e cai em outra rota**, o PickFlow entende que a
primeira geração não serviu e conta como erro; se repete **a mesma rota**, é ajuste fino e não conta (ninguém é
obrigado a opinar: o silêncio nunca vira acerto). Uma rota que **você** escolheu na mão não ensina o Clef — ele não
foi quem decidiu. O julgamento sempre fala da rota que **rodou**: se você respondeu o aviso "o Clef ficou em dúvida"
ou se o agente montou o caso, é essa a rota que entra no histórico.

Com esse histórico, três coisas mudam nas próximas decisões: pedidos parecidos que deram certo (ou que você
corrigiu com *era outro workflow*) entram como exemplo no que o Clef lê — e casos que apontam para o **mesmo**
fluxo viram **uma linha só, com a contagem** ("3 pedidos parecidos"), para as vagas não serem gastas repetindo a
mesma lição; quando o histórico discorda do Clef a
probabilidade dele é **corrigida** (o histórico vale até metade, ajustável em *Ajustes → Aprendizado com o seu
feedback*); e uma **medida da foto** (megapixels e nitidez, calculada no seu computador) entra em toda decisão, para o
Clef não ter de adivinhar se vale restaurar ou só melhorar sem aumentar. Tudo fica em `data/feedback.jsonl`, na sua
máquina; o `.gitignore` já ignora.

**Modo Rápido ou Qualidade.** Ao lado do campo de texto há um seletor de modo (*automático*, *rápido*, *qualidade*)
que vale para os workflows que têm os dois: o que você escolhe manda em todos os passos daquele pedido, inclusive nos
casos de uso, e a pergunta de modo deixa de ser feita ao Clef. Em *automático* quem decide é o Clef (Rápido, a não ser
que você peça máxima qualidade). Cada workflow também aparece no seletor de workflow com o modo fixado (*Nome — Rápido*,
*Nome — Qualidade*). **Imagens opcionais que você não manda saem do grafo** (o ComfyUI não carrega nem processa nada
para elas), então não deixam o workflow mais lento. Workflows que precisam de caixa ou máscara **pausam** e abrem um
desenho na tela (a espera é de até 15 minutos).

O **Gerar do zero (Quality)** e o **Remover fundo** são os modelos oficiais do ComfyUI (com *subgraph*). O PickFlow
"achata" o subgraph ao converter, então eles funcionam como os outros. No Gerar do zero o reescritor de prompt embutido é
descartado: quem escreve o prompt é o Clef (ou o redator opcional dos Ajustes).

---

## Como o PickFlow organiza os arquivos

```
PickFlow/
├── defaults/            O que vem no repositório. O app NUNCA escreve aqui.
│   ├── workflows/       os .json dos workflows acima
│   ├── registry/        ligação técnica de cada um (nós de imagem, prompt, saída)
│   ├── prompts/         biblioteca de prompts de cada workflow
│   ├── loras.yaml       biblioteca de LoRAs (descrição, força, prompt padrão)
│   ├── models.yaml      LoRAs e modelos que o assistente sabe instalar
│   └── clef_prompt.yaml descrição de cada workflow para o Clef + casos de uso
├── custom/              O que é SEU. Ignorado pelo git: tudo que você edita na interface vai para cá.
│   ├── settings.yaml
│   ├── loras.yaml       as suas LoRAs (e ajustes nas padrão)
│   ├── workflows/       seus .json (a subpasta NSFW/, por exemplo, também fica de fora do git)
│   ├── registry/  prompts/  clef_prompt.yaml  models.yaml
│   └── README.md
├── addons/              Extensões SUAS (código, fluxos, telas). Ignorado pelo git. Veja "Addons".
├── comfyui_nodes/       custom nodes para copiar para o ComfyUI (install_nodes)
├── clef_server/         servidor do Clef de referência
├── pickflow/  web/      o programa
└── data/                imagens enviadas, resultados e conversas (ignorado pelo git)
```

Regras simples:

- **`custom/` vale mais que `addons/`, que vale mais que `defaults/`** (mesmo id = o seu substitui o padrão).
- Ao editar um padrão pela interface, só a **diferença** vai para `custom/`. Por isso `git pull` continua trazendo
  as novidades dos padrões que você não mexeu, e **Restaurar padrão** é só apagar a sua cópia.
- `custom/` inteira é ignorada pelo git. Não precisa listar seus arquivos no `.gitignore`.
- Os arquivos `.json` nunca são alterados pelo PickFlow: ele converte uma cópia em memória a cada execução.

### LoRAs: um workflow, várias funções

Em vez de um workflow por LoRA (corpo, cabeça, vários corpos…), o **Editar** é um só e a **cadeia de LoRAs é montada a
cada pedido**:

```
pilha fixa do workflow  →  LoRAs do prompt escolhido  →  LoRAs que o Clef acrescentou
(realismo, consistência)   (ex.: trocar_pessoas liga bfs_body)   (conforme o pedido e as descrições)
```

Cada LoRA vive na aba **LoRAs** (`defaults/loras.yaml` + `custom/loras.yaml`) com:

- **Descrição**: é o que o Clef lê para decidir se liga. Você pode reescrever.
- **Força** e **força do texto**; 0 desliga.
- **Prompt padrão**: a instrução que sempre acompanha a LoRA (ex.: `body_swap: …`); pode ser um por modo.
- **Muda outras LoRAs / ajustes do workflow**: ex. ao ligar a troca de corpo, a de realismo cai a 0 e a resolução sobe.
- **Grupo** (só uma por grupo, ex.: *troca*), **mínimo de imagens**, e se o **Clef pode acrescentar sozinho**.
- **Conflitos**: ids de LoRAs que não funcionam junto com esta (vale nos dois sentidos). Ao ligar uma, a outra é desligada
  e o chat avisa; uma LoRA que só o Clef sugeriu nunca derruba uma que o prompt escolhido já usa. Ex.: *Trocar roupa ou
  tatuagem* × *Consistência*.

O limite de LoRAs que o Clef acrescenta por pedido é `clef.max_loras` (padrão 2). Cada LoRA candidata vira uma pergunta
(*usar / não usar*) com a descrição, então o resultado aparece em *Como o Clef decidiu*.

**Montagem do prompt.** Sem LLM: o PickFlow junta o texto do prompt escolhido, as instruções das LoRAs que o Clef
acrescentou e o seu pedido. No texto do prompt, `{loras}` marca onde entram as instruções de **todas** as LoRAs ligadas e
`{request}` onde entra o pedido. Com o **redator** ativado (Ajustes), uma LLM sua recebe esse rascunho, as descrições das
LoRAs e o pedido, e devolve o prompt final (modo *ajustar*; o modo *traduzir* só passa o pedido para inglês). Se o redator
falhar, vale o rascunho.

**Modos e ajustes.** Em `registry/<id>.yaml`, `modes:` troca arquivo, saídas e cadeia por modo; `knobs:` dá nome aos
parâmetros que LoRAs e prompts podem mudar (`resolution`, `scene_mp`, `steps`…, com `off` para ignorar um nó). Rota fixa:
`edit@quality`. Em casos de uso, cada passo aceita `mode:` e `loras:`.

**Acrescentar a sua LoRA.** Aba *LoRAs* → **Nova LoRA** (ou clique num arquivo que o ComfyUI tem e ainda não foi
descrito). Escreva a descrição; se quiser, um prompt padrão e “o Clef pode acrescentar sozinho”. Para ela entrar sempre
em certo prompt, ponha o id em *LoRAs e ajustes deste prompt* (`id`, `id=0.6`, `id=0.6@turbo`). Uma LoRA sua pode ter conteúdo
adulto: ela e seus prompts ficam só em `custom/`.

**Baixar uma LoRA pelo link do Hugging Face.** Aba *LoRAs* → **Baixar do Hugging Face**: cole o link da página do modelo
(ou de um arquivo `.safetensors`), escolha o arquivo e clique em baixar. Ele vai para `models/loras/` do ComfyUI e já fica
cadastrado na biblioteca (escreva a descrição depois). Repositórios restritos ou com licença a aceitar pedem uma chave:
crie um token de leitura em <https://huggingface.co/settings/tokens>, aceite a licença na página do modelo e escreva
`HF_TOKEN=seu_token` num arquivo **`.env`** na pasta do PickFlow (há um modelo em `.env.example`; o git ignora o `.env`).
Reinicie o PickFlow. A chave só é enviada ao Hugging Face.

### Fluxo padrão e reserva: trocar pessoa e trocar roupa

Há dois caminhos para trocar uma pessoa, e **um é o prioritário**: o **Trocar pessoa** (AusBoss) é o padrão; o **editor** (prompt
*Trocar pessoas*, com as LoRAs BFS) fica de **reserva** para o que o outro não faz bem. Depois de escolher a rota, o Clef responde
**uma pergunta** ("é o caso normal ou uma exceção?") e só nas exceções o pedido vai para o editor:

| Exceção | Quem fica com o pedido |
|---|---|
| **Rosto ocupado ou coberto**: bebendo de uma lata, comendo, uma mão ou objeto na frente do rosto | editor |
| **Corpo cortado** e o pedido troca o corpo (só a parte de baixo aparece, o corpo sai da foto) | editor. *Só a cabeça* com o corpo cortado fica no fluxo novo |
| Trocar **só o corpo** (o rosto da cena fica) | editor |
| **Várias pessoas** de uma vez, ou uma imagem extra com o **lugar** | editor |

A opção normal pesa a favor do fluxo novo (`clef.engine_main_weight`, 1,5) e a exceção só vence com pelo menos 50% de
confiança (`clef.engine_min`): na dúvida, fica o fluxo novo. Na *Trocar roupa* a única exceção é **tatuagem** (sozinha ou com a
roupa), que continua no editor. Quem escolhe o workflow no seletor ao lado do campo de texto não passa por essa pergunta. Para
mudar a regra, edite `fallback` em `registry/troca_pessoa.yaml` e `registry/troca_roupa.yaml` (a pergunta, as opções e o `when`
de cada uma, em inglês: é o que o Clef lê) numa cópia em `custom/registry/`.

As variáveis do prompt: **Quem trocar** (a pessoa, a mulher, o homem, e "da esquerda", "da direita" ou "do meio") vira a frase
que o fluxo usa para achar a pessoa; **O que trocar** (corpo e cabeça, ou só a cabeça). Não há campo de instruções extras. Os casos de
uso *Trocar a pessoa e o fundo* e *Mudar o ângulo da pessoa e corrigir o rosto* usam o fluxo novo. Pedidos de trocar pessoa com
2 imagens que o Clef mandou para o transferir objeto ("troque ela por ele, rosto e corpo") também passam a ele.

### Levar alguém ou algo de uma imagem para outra: transferir × colar

Duas imagens e "ponha isto naquela foto" pode ir por dois caminhos, e eles servem a coisas diferentes (testado com gerações
reais, 31 pedidos):

| O pedido | Caminho | Por quê |
|---|---|---|
| **No lugar de** alguém ou algo ("o coringa no lugar do cara de vermelho", "troque o cavalo pelo peão") | **Transferir objeto** | redesenha dentro da caixa verde e tira o que estava lá; a colagem deixava o antigo por baixo |
| Alguém **segura** ou usa um objeto da outra imagem ("faça o batman segurar a tesoura") | **Transferir objeto** | refaz a mão segurando; o editor geral inventava outra tesoura |
| **Acrescentar** uma pessoa num lugar, mesmo com pose nova ("o batman sentado no banco", "a Frieren na praia") | **Colar e fundir** | você escolhe lugar e tamanho, o rosto fica igual e a fusão completa o corpo e a pose |
| Pôr um **objeto** numa superfície ("a tesoura em cima da mesa") | **Colar e fundir** | fica idêntico; o transferir também funciona |
| Copiar estilo | **Editor** | |
| Trocar a **pessoa** (corpo e/ou rosto) ou a **roupa** | **Trocar pessoa** / **Trocar roupa** | com reserva no editor nas exceções (veja acima) |
| Trocar a pessoa **usando a roupa da imagem de referência** ("troque a pessoa pela da imagem 2 com a roupa dela") | **Vestir com a roupa da referência e trocar a pessoa** | veste a imagem base primeiro e faz o swap por cima: a roupa sai bem melhor que num swap só. Sem citar a roupa no pedido — e sempre no swap só de rosto — continua **Trocar pessoa** |

Como o Clef escolhe: com duas imagens, quando a rota cai entre transferir, colar, fundir ou o editor, ele responde uma
**pergunta curta** ("a coisa trazida substitui algo, é segurada, é acrescentada ou é outra edição?"), que acertou 28 de 29
frases nos testes (a escolha entre todas as rotas errava "no lugar de" e "segurar"). A resposta também diz ao transferir se
o que está na caixa verde **sai** ou **fica**. Na colagem, se o recorte da pessoa encosta na borda da foto (um busto, sem
pernas), a fusão passa sozinha a **completar o corpo**. Imagens com fundo transparente vão ao ComfyUI sobre branco (o ComfyUI
as deixaria pretas).

**Vestir com a roupa de outra imagem e ainda mudar de lugar.** Com 3 imagens — a pessoa, uma foto só da roupa (a peça num
cabide, na cama, foto de produto: **sem ninguém vestindo**) e o lugar — o caso de uso *Vestir a pessoa e levar para outro
lugar* veste a pessoa com aquela roupa e depois a coloca no ambiente escolhido (trocar a roupa → recortar → posicionar →
fundir). Ele existe porque esse pedido era confundido com *Trocar a pessoa e o fundo* (que troca a pessoa, não a roupa): a
análise das imagens agora distingue **roupa sozinha** de pessoa, e as duas descrições se excluem. Com 2 imagens, é
*Trocar a roupa* mesmo.

**Trocar a pessoa, ficar com a roupa da cena e mudar de lugar.** Com 3 imagens — a cena (pose **e roupa**), a pessoa de
referência (só rosto e corpo) e o lugar —, quando o pedido manda usar a roupa da cena ("troque a pessoa da imagem 2 para
fazer a pose da imagem 1, usando a roupa da imagem 1, tirando uma selfie no espelho da imagem 3"), o caso de uso *Trocar a
pessoa, manter a roupa da cena e levar para outro lugar* faz: trocar corpo e cabeça → trocar a roupa (a da cena como
referência, porque o swap traz a roupa da pessoa de referência) → recortar → posicionar → fundir. Sem citar a roupa no
pedido, continua *Trocar a pessoa e o fundo*; as duas descrições apontam uma para a outra.

### Como o Clef entende casos de várias imagens

1. **O que há em cada imagem.** Com 2 ou mais imagens, a primeira pergunta ao Clef é, para cada uma, *pessoa, lugar sem
   pessoas, **roupa sozinha** (a peça, sem ninguém vestindo), objeto ou outro*. O rótulo vai junto de todas as perguntas
   seguintes, então o usuário não precisa dizer “cena” ou “novo fundo” — e “vestir a roupa da imagem 2” não vira troca de
   pessoa só porque a frase diz “wearing”. (Desligue em `clef.analyze_images: false`.)
2. **Quantidade exata.** Cada caso de uso declara `images:` (ex.: *Colar e fundir* = 2, *Trocar a pessoa e o fundo* = 3);
   com outra quantidade ele nem é oferecido ao Clef. Isso tira a confusão entre os dois.
3. **Segunda olhada.** Se a rota ficar abaixo de `clef.refine_below` (0,6), o Clef compara só os 3 candidatos mais
   prováveis, já sabendo quantas imagens há e o que mostram. Só então, se ainda houver dúvida (`clef.ask_below`), pergunta a você.
4. **Papéis.** Um caso de uso pode declarar `roles:` (ex.: cena, pessoa, fundo); o Clef responde uma pergunta por papel
   (“qual imagem é a pessoa de referência?”), respeita “a pessoa da imagem 3”, “o ambiente é a imagem 1” e nunca usa a mesma
   imagem em dois papéis. Os passos usam `role:<papel>` nas entradas; sem certeza, vale a ordem de envio.

### Um prompt, vários casos (variáveis)

Um prompt pode declarar `vars`: perguntas de múltipla escolha que o Clef responde (ele não escreve texto; o PickFlow monta
o texto com a resposta, deixando a sua decisão explícita no prompt final). Há três tipos:

- `count` — quantas pessoas/itens. O prompt **Trocar pessoas** troca as primeiras N pessoas da imagem 1, **da esquerda
  para a direita**, pelas pessoas das imagens seguintes (imagem 2, 3, 4…). Se você manda 4 imagens, o Clef pode responder
  de 1 a 3; nunca mais do que as imagens que existem.
- `choice` — uma opção entre várias, cada uma com seu texto, LoRAs e ajustes. Em *Trocar pessoas*: **corpo e cabeça**
  (padrão), **só o corpo** ou **só a cabeça**, e **com ou sem imagem de lugar**. Em *Trocar roupa ou tatuagem*: roupa,
  tatuagens ou os dois.
- `angle` — giro de câmera para a LoRA de órbita (*Girar o ponto de vista*): lido do pedido (“90 graus para a direita”,
  “de costas”, “de cima”). Se o pedido não traz o ângulo, abre uma **tela com a imagem** para escolher lado, ângulo
  (45, 90, 135, 180) e altura. Funciona melhor com objetos.

No texto use `{chave}`; contagens também dão `{chave_map}` (quem vira quem), `{chave_refs}`, `{chave_next}` e `{chave_many}`
(o `many_text` da variável, que só entra com **mais de uma** pessoa). Por isso, com **uma pessoa** o prompt de *Trocar
pessoas* fica só com as instruções das LoRAs (`body_swap: …` e/ou `head_swap: …`, conforme o que trocar; em corpo e cabeça
as duas, com a LoRA de cabeça em 1); o mapa “1ª pessoa da esquerda…” e a frase “Replicate the exact pose…” só aparecem com
várias pessoas. Com
`slots: order` as imagens entram nas entradas na ordem em que foram enviadas. As variáveis são editadas no cartão do
prompt, em *Workflows e prompts*.

### Outpaint: acrescentar cinza e completar

Pedidos de só expandir ou completar a imagem ("aumente para os lados", "outpaint") têm **rota própria**: o caso de uso
*Expandir a imagem (outpaint)* (1 imagem), que fixa o prompt *Expandir ou completar áreas cinza* do Editar. Ele existe porque, só
como prompt dentro do Editar, o Clef real nem olhava para ele ("Outpaint this image" caía em *Mudar o ângulo* e *Remover
fundo*); agora aparece na lista de casos de uso do chat e o Clef o enxerga como uma opção de rota. Antes de gerar, abre a tela **Ampliar a imagem**: a foto aparece sobre o cinza (#808080) e você arrasta as bordas, digita a
porcentagem de cada lado ou escolhe uma proporção (1:1, 4:3, 3:2, 16:9, 9:16). O que o Clef entendeu do pedido já vem preenchido, e a
opção *Escolher na tela* começa zerada. A LoRA de Outpaint completa só o cinza; a foto original volta idêntica. Se a imagem já
tem cinza para completar (um render 3D), a tela não abre.

### Meu ou oficial? Onde cada coisa vai

Tudo que você cria ou edita **pela interface** (casos de uso, LoRAs, prompts, descrições, ajustes, casos do agente, workflows
importados) é gravado em `custom/`, e `custom/`, `addons/`, `data/` e `.env` ficam **fora do git**: nada disso é publicado
por engano, e quem baixa o projeto começa só com o que está em `defaults/`.

Para que algo seja **oficial** (todo mundo que baixar recebe), coloque-o em `defaults/` à mão e faça o commit; a interface
nunca escreve ali:

| O quê | Onde fica em `defaults/` |
|---|---|
| Caso de uso | `clef_prompt.yaml`, lista `recipes` (copie o bloco de `custom/clef_prompt.yaml`) |
| LoRA | `loras.yaml` + `models.yaml` (para o assistente baixar) + linha na tabela do README |
| Prompt | `prompts/<workflow>.yaml` |
| Workflow | `workflows/*.json` + `registry/<id>.yaml` + `prompts/<id>.yaml` + descrição em `clef_prompt.yaml` |

Depois de copiar, **apague o item de `custom/`** (o seu vale mais que o padrão e esconderia a versão oficial) e rode
`python -m pytest -q`: o teste `test_defaults_ship_no_personal_or_nsfw_text` barra texto pessoal ou adulto em `defaults/`,
e textos novos de tela precisam entrar em `web/locales/en.json` (`python tests/uitexts.py` lista o que falta).
O que o Clef lê (descrições, `when`, perguntas) vai em inglês.

### Adicionar o seu workflow

1. Salve o workflow no ComfyUI (**Workflow → Salvar**) e copie o `.json` para `custom/workflows/` (subpastas valem).
2. **Workflows e prompts → Adicionar workflow.** Escolha o arquivo, confira as entradas de imagem, o nó do prompt e
   a saída, e clique em **Adicionar**.
3. Escreva a descrição que o Clef vai ler (*o que faz e quando usar*) e os prompts. Rode **Verificar workflows**.

Fica pronto para usar. Caixas, máscaras, nós a descartar e valores fixos você acrescenta à mão em
`custom/registry/<id>.yaml` — veja `defaults/registry/object_move.yaml` (caixas) e `defaults/registry/t2i.yaml`
(nós dentro de subgraph). Detalhes em [`custom/README.md`](custom/README.md).

> Workflows e prompts com conteúdo adulto: o repositório só traz versões neutras. Guarde os seus em `custom/workflows/`
> (ou numa subpasta como `custom/workflows/NSFW/`), e os prompts em `custom/prompts/`. Um registro seu em
> `custom/registry/<id>.yaml` com o mesmo `id` de um padrão (e `file: NSFW/arquivo.json`) faz aquele id usar o seu
> workflow. Nada de `custom/` entra no git.

### Codificador de texto (CLIP): fp8 ou int8

Os workflows carregam o **Qwen3-VL 8B** como codificador de texto, em duas versões: o **fp8 (heretic)** é o dos workflows de
editar, transferir, pose, 3D e colar; o **int8 (convrot)** é o dos de trocar pessoa e trocar roupa (AusBoss). Em **Ajustes >
ComfyUI > Codificador de texto (CLIP)** você escolhe: *Automático* (cada workflow com o seu, como vem) ou uma das versões, que
passa a valer em todos os workflows (o PickFlow troca o arquivo no grafo enviado ao ComfyUI; os `.json` não mudam). Só dá para
escolher a versão que o ComfyUI tem (as outras aparecem desligadas), e o assistente de instalação deixa de pedir a que você
não escolheu. Só os `CLIPLoader` que já carregam uma dessas duas versões são trocados: o codificador PE do *Gerar imagem* fica
como está. A configuração é `comfyui.text_encoder` (`auto`, `fp8` ou `int8`) em `custom/settings.yaml`.

### Outros modelos de imagem (além do Qwen 2.1)

Tudo o que o PickFlow traz é do **Qwen-Image 2.1** e continua sendo o padrão. Para preparar outro modelo (o MOSS-Image, por
exemplo) sem tocar em nada do que existe, vale uma **tag `models`** (uma lista) em cada coisa; **sem a tag, vale só o Qwen**:

| Onde | Como |
|---|---|
| **Workflow** (`registry/<id>.yaml`) | `models: [moss_image]`. Um workflow que faz o papel de outro para esse modelo declara `implements: edit` |
| **Prompt** (um preset em `prompts/<id>.yaml`) | `models: [qwen21, moss_image]` para valer nos dois |
| **LoRA** (`loras.yaml`) e **caso de uso** (`recipes`) | `models: [qwen21, moss_image]`; um caso de uso que cita `edit` roda o `edit` do modelo escolhido (o que declara `implements: edit`) |
| Qualquer um | `models: ['*']` serve a todos (recortes, 3D e outros fluxos que não dependem do modelo de imagem) |

No chat aparece **Modelo: …** ao lado do seletor de workflow **só quando há mais de um** para escolher: o Qwen (sempre) e os
modelos que têm workflow no PickFlow **e** cujo arquivo está no ComfyUI (o PickFlow olha os carregadores do ComfyUI e as
pastas `models/diffusion_models`, `unet` e `checkpoints`). Escolher um modelo faz o Clef enxergar só o que serve a ele
(workflows, casos de uso, prompts e LoRAs). Sem workflow do MOSS no PickFlow, nada muda e o seletor nem aparece. O modelo
usado quando ninguém escolhe é `model` em `custom/settings.yaml` (`qwen21`).

O que reconhece o arquivo de cada modelo está em `defaults/families.yaml` (`match`: padrões do nome do arquivo; o do MOSS é
`*moss*`, ajuste em `custom/families.yaml` se o seu arquivo tiver outro nome). Um modelo novo é só uma família nova nesse
arquivo mais workflows com a tag dela.

### Addons: funcionalidades suas

Quer mudar a interface, ajudar o prompt com o seu LLM, criar um fluxo novo (por exemplo, girar a imagem) ou ligar uma
API sua? Crie um **addon** em `addons/<nome>/`. A pasta é ignorada pelo git, então você mexe à vontade e o `git pull`
nunca encosta nela.

```
python -m pickflow addon new meu-addon      # ou Ajustes > Addons > Criar addon; reinicie o PickFlow
```

Um addon pode trazer **workflows, registros, prompts e LoRAs** (mesmo formato de `defaults/`), **código Python**
(`addon.py`: ganchos para o pedido e o prompt, **ações** = fluxos feitos em Python sem ComfyUI, rotas, o **LLM** de
Ajustes, dados) e **interface** (`web/main.js` com `window.PickFlow`: botões, telas, janelas, ajustes). O modelo criado
já vem com um exemplo de cada. A referência completa está em [`addons/README.md`](addons/README.md), e a skill
`pickflow-addons` (`.claude/skills/`) ensina o Claude Code a criar e alterar addons para você.

### Agente: casos de uso que o próprio PickFlow cria

O Clef só escolhe entre fluxos e casos de uso que já existem (mais uma etapa extra depois). Um pedido como "deixe a
pessoa da imagem 2 colorida e com melhor qualidade, depois coloque-a no ambiente da imagem 1" precisa de uma sequência
própria, com a imagem 2 passando por uma etapa **antes** de entrar no recorte e na fusão. Para isso existe o agente
(**Ajustes > Agente de casos de uso**, desligado por padrão; usa o LLM do redator):

1. Depois de escolher a rota, o Clef responde uma pergunta extra: o plano cobre o pedido inteiro ou falta uma etapa?
   Só com "falta" (e confiança acima de `agent.gap_min`) o agente acorda. Também dá para chamá-lo na mão no seletor
   de workflow da conversa ("Agente: montar um caso de uso novo").
2. O LLM trabalha em rodadas, como um agente: consulta o catálogo (workflows, prompts, LoRAs, casos existentes), pergunta ao
   Clef o que depende de ver as imagens, manda rascunhos e recebe de volta os erros até entregar um caso válido.
3. O caso passa pelas mesmas regras da tela de Casos de uso (workflows, entradas, prompts, modos e LoRAs existentes, número
   de imagens, passos no máximo). Um passo pode ter **instrução própria** (`request`): "Colorize this photo...", em vez
   de receber o pedido inteiro.
4. Travas: limite de respostas do LLM e de consultas ao Clef por criação; caso com exatamente os mesmos passos de um existente é
   reaproveitado; o id é sempre inédito (nunca sobrescreve); você confirma antes de guardar (`agent.confirm`); os criados
   ficam **em teste** em `custom/generated_recipes.yaml` (fora do git) e o mais antigo sai passando de `agent.max_trial`, a não
   ser que você clique em **Manter**; ao chegar em `agent.max_generated` mantidos o agente para.

**Casos de uso repetidos.** Em Casos de uso, **Verificar repetições** pede ao Clef uma nota de 0 a 100% para cada par (mesmo
caso, parecidos ou diferentes, somada à comparação dos passos) e mostra os pares do mais ao menos repetido. **Fundir os
dois** (ou o grupo) pede ao LLM um único caso mais geral, em geral usando papéis no lugar da ordem fixa das imagens. Você
revisa a proposta; só depois de **Aplicar a fusão** algo é gravado, e os originais são desativados (ou apagados, se você
escolher).

### Onde você mexe

| O quê | Na interface | Fica em |
|---|---|---|
| O que o Clef sabe de cada workflow, **quais prompts ele pode usar**, casos de uso | *Workflows e prompts*, *Casos de uso* | `custom/clef_prompt.yaml` |
| Prompts de cada workflow | *Workflows e prompts* → cartões de prompt | `custom/prompts/<id>.yaml` |
| Ligações técnicas (nós, widgets, descartes) | só leitura | `custom/registry/<id>.yaml` |
| LoRAs: descrição, força, prompt padrão | aba *LoRAs* | `custom/loras.yaml` |
| Endereços, memória, redator | *Ajustes* | `custom/settings.yaml` |

Em *Workflows e prompts*, cada prompt tem **O Clef pode usar** (liga/desliga), **Padrão**, **Quando usar** (o que o Clef lê
para escolher entre os prompts) e o **texto**. `{request}` recebe o pedido do usuário; `<image1>`, `<image2>` ficam literais
para o Qwen. Marque *reescrever em inglês* para que o redator opcional (qualquer servidor compatível com a API da OpenAI)
trate o pedido antes.

Em *Casos de uso* você cria sequências fixas (ex.: trocar o corpo e depois levar a pessoa para um novo fundo), define de qual
imagem cada passo parte (`imagem N`, `resultado anterior`) e pode fixar o prompt de cada passo.

---

## Como o Clef decide

O Clef não gera texto: devolve **probabilidades**. Por isso o PickFlow transforma cada decisão numa pergunta de
múltipla escolha:

1. **Rota**: qual workflow ou caso de uso (e, opcionalmente, qual vem depois). Acima de 26 opções, decide em dois estágios.
   A etapa seguinte só entra com confiança de pelo menos 50% (`clef.then_min`), depois de dar 1,5× de peso a "nenhuma" (`clef.then_none_weight`), e só entre workflows que cabem nas imagens
   enviadas: ela recebe o resultado no lugar de uma das fotos, então com uma foto só não aparece "transferir entre imagens".
2. **Prompt** do workflow e **qual imagem vai em cada entrada**.
   Um workflow com `fallback` no registro (*Trocar pessoa*, *Trocar roupa*) ganha **uma pergunta a mais** logo depois da rota,
   para decidir entre ele e a reserva.
3. Se a confiança na rota fica abaixo de *Perguntar a você abaixo de* (0,45), o chat pergunta em vez de arriscar. O
   mesmo vale para o prompt do passo e para uma opção dele (como *como melhorar*): abaixo de
   *Ajustes → Aprendizado → Perguntar na tela abaixo de* (0,5), o PickFlow abre a escolha com as duas melhores opções
   em vez de arriscar — fechar a janela (ou *Seguir a primeira opção*) mantém a resposta do Clef. Esse caminho só é
   usado com o Clef de verdade: no modo de teste ele não interrompe nada.
4. Em toda pergunta o Clef recebe **as medidas de cada foto** (largura e altura, megapixels e uma estimativa de
   nitidez), o que há nela e os pedidos parecidos que já deram certo. É o que faz "melhore esta foto" virar *restaurar*
   numa foto pequena e *melhorar sem aumentar* numa foto grande e nítida.

Cada resposta mostra **Como o Clef decidiu**, com as probabilidades. Você também pode forçar um workflow no seletor ao lado
do campo de texto e **escolher o modo de geração** (automático, rápido ou qualidade): o modo escolhido vale para todos os
passos daquele pedido, em todo workflow que tenha os dois modos, e a pergunta de modo deixa de ser feita ao Clef. Em
*Automático* (o padrão) quem decide é o Clef, como antes.

### O servidor do Clef

Os pesos são da Cloudflare (`Cloudflare/clef-flash`, ~19 GB em BF16, e `Cloudflare/clef`, ~55 GB, Apache-2.0).
O PickFlow fala com um servidor HTTP local:

- `POST /v1/systemone` — decide (aceita `images` em data URL ou base64; no máximo 4 imagens por pedido — com mais
  imagens anexadas, o PickFlow manda janelas de 4 e combina as respostas);
- `GET /health` — responde `{"loaded": bool, "device": "...", "ram_gb": N, "vram": {"allocated_gb": N}}` e, para a
  política de memória, **`quant`** (como o modelo foi carregado: `""` em BF16, `int8`, `nf4`), **`vram_expected_gb`**
  (quanto ele pesa ao carregar, medido no último carregamento — vale com o modelo descarregado) e **`vram_work_gb`**
  (quanto ele precisa **para decidir**: os pesos mais as ativações — é este número que decide se cabe junto do
  ComfyUI);
- `POST /v1/device` — troca entre `cpu` e `cuda` (descarrega o modelo e recarrega no próximo pedido);
- `POST /v1/unload` (ou `/unload`) — tira o modelo da memória.

Em `clef_server/` há uma implementação de referência (carrega no primeiro pedido e descarrega depois de um tempo parado):

No Windows, **dê dois cliques em `clef_server\start_clef.bat`**: na primeira vez ele cria o ambiente, instala o torch com CUDA 12.8 e o resto, e sobe o servidor
(o modelo é baixado do Hugging Face no primeiro pedido). Ou, à mão:

```
cd clef_server
python -m venv .venv          # instale antes o torch com CUDA da sua placa
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python server.py --model Cloudflare/clef-flash --port 8321 --quant int8 --device cpu
```

Se você já tem um servidor do Clef, aponte *Endereço do servidor* e *Caminho* em **Ajustes → Clef**.

> **Onde rodar o Clef?** A coluna “para decidir” é a que importa para saber se ele cabe junto do Qwen: as ativações de
> uma decisão com imagens pesam tanto quanto os pesos em 4 bits (o `clef-flash` tem 9 bilhões de parâmetros).
>
> | Onde | Pesos | Para decidir | Quando usar |
> |---|---|---|---|
> | GPU, BF16 | 17,8 GB de VRAM | ~26 GB | placa com folga; em 24 GB tem de sair antes de cada imagem |
> | GPU, `--load-in-4bit` | **7,9 GB** | **~16 GB** | decisões 3 a 12× mais rápidas com 2+ imagens; ainda sai antes de gerar |
> | CPU (`--device cpu`) | ~18 GB de RAM (BF16), ~6 GB em int4 | + ativações na RAM | libera a placa toda para o Qwen; responde em 1 a 3 s |
>
> Rodar na CPU **exige** quantização em máquinas de 32 GB: o ComfyUI segura alguns GB mesmo parado. Veja a próxima seção.
>
> **O PickFlow não adivinha — ele pergunta**: `quant`, `vram_expected_gb` e `vram_work_gb` vêm do `/health` e é com eles
> que a conta é feita. Com 7,9 GB de pesos o Clef *parece* caber ao lado do Qwen, mas **ao decidir ele chega a 16,4 GB**;
> 16,4 + 15,5 do Qwen não cabem em 24 GB, então a resposta certa é descarregar antes de gerar — e é o que ele faz,
> explicando o porquê em **Ajustes → Clef** e na conversa. O ganho do 4 bits está na velocidade da decisão, não em
> manter os dois na placa.

### Redator de prompt (LLM opcional)

Em *Ajustes > Redator de prompt* você cadastra **vários provedores** (OpenAI, OpenRouter, Google Gemini, Anthropic, DeepSeek,
Groq, um servidor local ou qualquer endereço compatível com a API de chat da OpenAI) e, em cada um, **vários modelos**;
um seletor diz qual está em uso (o mesmo LLM serve ao agente de casos de uso e aos addons).

- **Chave de API:** cole no campo e clique em *Guardar chave*. Ela vai para o arquivo `.env` desta pasta (variável
  `LLM_API_KEY_<id do provedor>`; o git ignora o arquivo) e **nunca** para `custom/settings.yaml`; a tela só sabe se ela
  existe. Se preferir, escreva a linha no `.env` e reinicie. Servidor local não precisa de chave.
- **Testar** manda uma pergunta de uma palavra e mostra o tempo ou o motivo da falha (chave errada, endereço, modelo).
- **"Enxerga imagens"** (por modelo): o PickFlow manda junto as imagens do passo (reduzidas), e o redator descreve pelo que vê
  ("o homem de jaqueta vermelha à direita") em vez de depender de "imagem 1". Só marque em modelos com visão.
- O redator só age nos prompts marcados com `rewrite` (hoje, a **fusão que completa o corpo**). Se falhar, o PickFlow segue com
  o prompt montado sem ele e **avisa o motivo na conversa**.
- Quem já tinha um endereço e um modelo salvos continua funcionando: viram o provedor "Padrão" (chave em `LLM_API_KEY`).

### Memória: Clef e Qwen na mesma máquina

O PickFlow mede a memória (`/system_stats` do ComfyUI + `GET /health` do Clef) e decide sozinho. Há **duas políticas**,
e quem escolhe é onde o Clef roda (o servidor informa em `/health`; quando ele não informa, vale *Ajustes → Clef → Onde o
Clef roda*):

- **Clef na GPU** (VRAM): se `VRAM total − VRAM do Clef − folga` ficar menor que a VRAM que o workflow usa (padrão: 16 GB),
  o Clef é descarregado antes de gerar e recarrega no próximo pedido. É o que acontece com o `clef-flash` em BF16 numa placa
  de 24 GB: 17,8 GB + 16 GB não cabem. Também libera os modelos do ComfyUI se faltar VRAM para carregar o Clef de volta.
- **Clef na CPU** (RAM): não há disputa de placa, então a conta é de RAM —
  `RAM livre − RAM do Clef` contra o que o workflow usa (`need_ram_gb`, padrão 10 GB). Nesse modo o PickFlow **não** libera
  a VRAM do ComfyUI antes de decidir, porque isso só faria ele recarregar tudo do disco.

O modo *Automático* (padrão) aplica a política do lugar onde o Clef está; *Sempre* e *Nunca* forçam o comportamento. Cada
decisão aparece no chat. Um workflow pode ter a própria estimativa em `registry/<id>.yaml` (`mem: {vram_gb: 12, ram_gb: 8}`).

> **Conta honesta para 32 GB de RAM:** com o ComfyUI aberto sobram uns 13 GB livres, e o `clef-flash` em BF16 na CPU usa
> ~18 GB. Na prática, Clef na CPU em máquina de 32 GB só fecha com `--quant int8` (~9,5 GB) ou `int4` (~5,6 GB). Se o
> servidor não informar a RAM que ocupa, o PickFlow não tem como medir e a regra de RAM não dispara — prefira um servidor
> que responda `ram_gb` em `/health` (o de `clef_server/` responde).

---

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| **ComfyUI fora do ar** | Abra o ComfyUI. Em *Ajustes → ComfyUI → Testar* o PickFlow tenta achar a porta certa. |
| **Nós não instalados: DrawBox, ViggleTurbo…** | *Ajustes → Abrir o assistente* (ou `install_nodes`) e reinicie o ComfyUI. Outros nós ausentes: atualize o ComfyUI. |
| **`'x.safetensors' não está instalado`** (aviso amarelo) | Falta baixar o arquivo, ou ele tem outro nome. O assistente mostra o que falta e baixa. |
| **Download com erro ou "indisponível"** | O autor mudou ou removeu o arquivo, ou ele exige login. Abra a *página do autor* na lista e ajuste a URL em `custom/models.yaml`. |
| **Value not in list / prompt outputs failed validation** | O ComfyUI recusou o grafo: o texto do erro cita o nó. Rode *Verificar workflows*. |
| **Travou ao rodar o Clef e o Qwen juntos** | Falta VRAM/RAM. Se o Clef está na GPU: *Descarregar o Clef antes de gerar* em *Automático* e `--quant int8` no Clef. Se ele está na CPU: use `--quant int4` (BF16 na CPU pede ~18 GB de RAM) e confira a RAM livre com o ComfyUI aberto. |
| **O Clef demora a responder toda vez** | Ele está sendo carregado a cada pedido: em BF16 na GPU (18 GB) não cabe junto com o Qwen numa placa de 24 GB. Use `--quant int8`/`int4` na GPU ou `--device cpu` (a decisão passa a levar 1 a 3 s, sem recarregar o modelo). |
| **"Este servidor do Clef não sabe trocar de dispositivo"** | O servidor em uso não tem `POST /v1/device`: suba-o com `--device cpu` ou `--device cuda` (o de `clef_server/` aceita). O ajuste continua valendo para a política de memória. |
| **"Clef sem resposta"** | O servidor do Clef não está de pé ou o endereço está errado (*Ajustes → Clef*). |
| **O workflow não roda sem eu desenhar** | Normal: Mover/Substituir/Transferir pausam para você marcar a área. |
| **Mexi e quero voltar ao padrão** | *Workflows e prompts* → **Restaurar padrão**. |

O PickFlow só escuta em `127.0.0.1` e recusa pedidos vindos de outros sites. **Não exponha a porta na rede**: não há login.

## Para quem quer mexer no código

```
pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes usam um ComfyUI falso (`tests/fake_comfy.py`) com o `object_info` **real** (reduzido) de um ComfyUI com o Qwen-Image 2.1
(`tests/object_info.json`, gerado por `tests/make_object_info.py`), um Clef de teste e um servidor HTTP falso do Clef.
Provam a lógica de roteamento, a conversão para o formato de API (inclusive subgraphs), as camadas `defaults/` + `custom/`
e o fluxo completo, mas **não substituem** o botão *Verificar workflows* no seu ComfyUI real, nem testam o Clef com os pesos de verdade.

**Tradução da interface.** O texto-fonte é português (`web/app.js`, mensagens do servidor, nomes em `defaults/`);
`web/i18n.js` traduz o que aparece na tela com o catálogo `web/locales/en.json` (`strings` para texto fixo, `templates`
com `{}` para as partes variáveis). Campos editáveis e o que está em `translate="no"` (o pedido do usuário) não são
tocados. Texto novo de interface precisa de uma linha no catálogo: `python tests/uitexts.py` lista o que falta e o teste
`test_catalog_covers_every_interface_text` acusa.

`python tests/demo_server.py` sobe a interface com dados de mentira em <http://127.0.0.1:7861> (com `DEMO_ADDON=1`, já com um addon criado do modelo; com `DEMO_SPLAT=caminho/arquivo.spz`, o passo 3D devolve esse splat de verdade). Com `DEMO_SETUP=1` simula um ComfyUI novo (sem nós, LoRAs e modelos) e downloads lentos, para ver o assistente; com `DEMO_NO_COMFY=1`, um ComfyUI que não responde.

## Créditos

- [Qwen-Image 2.1](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1) e os modelos para ComfyUI: Qwen e [Comfy-Org](https://huggingface.co/Comfy-Org/Qwen-Image-2.1).
- LoRAs: [Viggle](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) (turbo), [Alissonerdx](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) (BFS body/head swap),
  [prithivMLmods](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) (Natural Exposure),
  [ausboss](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) (Consistency), [flymy-ai](https://huggingface.co/flymy-ai/qwen-image-realism-lora) (realism).
- 3D: [TripoSplat](https://huggingface.co/VAST-AI/TripoSplat) (VAST-AI), [BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet); LoRAs [AnyAngle](https://huggingface.co/lilylilith/QI_2.1_AnyAngle) e [Outpaint](https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA). O visualizador usa [three.js](https://threejs.org) e [Spark](https://sparkjs.dev) (MIT, em `web/vendor/`).
- Trocar pessoa e trocar roupa: os workflows *Qwen Image 2.1 Person Swap* e *Outfit Swap* e o pacote [ComfyUI-AusBoss](https://github.com/ausboss/ComfyUI-AusBoss) de [AusBoss](https://www.youtube.com/@ausbossdev) (MIT).
- Clef: [Cloudflare](https://huggingface.co/Cloudflare/clef-flash).

## Licença

O código do PickFlow é distribuído sob a [licença MIT](LICENSE). Os modelos, LoRAs e workflows de terceiros
mencionados acima têm as licenças dos seus autores (veja a coluna *Licença* e `defaults/models.yaml`).
