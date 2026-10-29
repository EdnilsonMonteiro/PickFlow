# custom/ — o que é seu

Esta pasta é ignorada pelo git (veja o `.gitignore`): tudo que você coloca ou edita aqui fica só na sua máquina
e **vale mais** do que o que vem em `defaults/`. Atualizar o repositório (`git pull`) nunca mexe nela.

```
custom/
├── settings.yaml        endereços do ComfyUI e do Clef, memória... (criado pela tela Ajustes)
├── clef_prompt.yaml     só o que você mudou no prompt do Clef (descrições, casos de uso)
├── workflows/           seus arquivos .json salvos no ComfyUI
│   └── NSFW/            (exemplo) subpastas valem; esta e tudo dentro dela também fica fora do git
├── registry/            ligação técnica dos SEUS workflows (nós de imagem, prompt, saída)
├── prompts/             seus prompts, por workflow
├── loras.yaml           suas LoRAs (descrição, força, prompt padrão) e ajustes nas padrão
└── models.yaml          (opcional) LoRAs e modelos extras para o assistente de instalação
```

## Adicionar uma LoRA sua

Aba **LoRAs → Nova LoRA** (ou edite `custom/loras.yaml`):

```yaml
loras:
- id: minha_lora
  title: Minha LoRA
  file: pasta\arquivo.safetensors     # como aparece em models/loras do ComfyUI
  strength: 0.7
  auto: true                           # o Clef pode ligar sozinho
  description: Quando o pedido for X, use esta LoRA.
  prompt: "instrução que sempre acompanha a LoRA"   # ou {turbo: ..., quality: ...}
  adjust: {natexp: 0}                  # força de outras LoRAs enquanto esta estiver ligada
  settings: {resolution: 1024}         # ajustes do workflow (nomes em knobs: do registro)
  conflicts: [consistency]             # não funciona junto com estas: ao ligar esta, as outras saem (vale nos dois lados)
```

Também dá para **baixar pelo link do Hugging Face** (aba LoRAs → *Baixar do Hugging Face*). Se o arquivo exigir chave,
escreva `HF_TOKEN=seu_token` no arquivo `.env` da pasta do PickFlow (modelo em `.env.example`; o git ignora o `.env`).

Mesmo `id` de `defaults/loras.yaml` = a sua vale mais; `disabled: true` esconde uma padrão. Para uma LoRA entrar sempre
num prompt, use o campo `loras:` do prompt em `custom/prompts/<id>.yaml`.

## Adicionar um workflow seu

1. No ComfyUI, salve o workflow (**Workflow → Salvar**) e copie o `.json` para `custom/workflows/`
   (pode usar subpastas).
2. No PickFlow, aba **Workflows e prompts → Adicionar workflow**. Escolha o arquivo, confira onde entram
   as imagens e o prompt e clique em **Adicionar**.

O PickFlow cria `custom/registry/<id>.yaml` e `custom/prompts/<id>.yaml`. Seu arquivo `.json` nunca é alterado.
Caixas e máscaras, nós a descartar e valores fixos são acrescentados à mão no registro; use os de
`defaults/registry/` como exemplo (`object_move.yaml` tem caixas, `t2i.yaml` tem nós dentro de subgraph).

## Usar uma versão sua de um padrão (por exemplo, com outro workflow)

Copie `defaults/registry/<id>.yaml` para `custom/registry/` (mesmo nome e mesmo `id`) e troque `file:` pelo seu
workflow, como `file: NSFW/Meu workflow.json`. Em `custom/prompts/<id>.yaml` ficam os seus prompts para aquele id
(comece copiando `defaults/prompts/<id>.yaml` e acrescente os seus). Tudo isso fica fora do git.
O arquivo de prompts em `custom/` **substitui** o padrão inteiro: depois de um `git pull`, compare com o padrão para ver
os prompts novos. O workflow `edit` tem 8 entradas de imagem (as sem imagem saem do grafo); ao trazer o seu, mantenha
as 8 e a numeração dos nós de `defaults/registry/edit.yaml`, ou o PickFlow não saberá onde pôr as imagens.

## Mudar um padrão sem perder as atualizações

- **Prompts e descrições:** edite pela interface. Só a diferença vai para `custom/`; o botão
  **Restaurar padrão** apaga a sua versão.
- **Trocar o arquivo ou os nós de um padrão:** copie `defaults/registry/<id>.yaml` para `custom/registry/`
  (mesmo nome, mesmo `id`) e edite a cópia. Se o seu workflow tem outro nome, ponha o `.json` em
  `custom/workflows/` e ajuste `file:` na cópia.

## Remover

**Remover da lista** (na própria tela) tira o workflow do PickFlow e apaga os seus registros e prompts.
O `.json` continua em `custom/workflows/`. Para sumir de vez, apague o arquivo.
