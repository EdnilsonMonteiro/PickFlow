# Customizing: files, workflows, LoRAs, addons, writer and other models

Everything that is yours lives in `custom/` and `addons/` (outside git). The interface never writes to `defaults/`.

## How PickFlow organizes files

```
PickFlow/
├── defaults/            What ships in the repository. The app NEVER writes here.
│   ├── workflows/       the .json files of the workflows above
│   ├── registry/        the technical wiring of each one (image nodes, prompt, output)
│   ├── prompts/         prompt library of each workflow
│   ├── loras.yaml       LoRA library (description, strength, default prompt)
│   ├── models.yaml      LoRAs and models the assistant knows how to install
│   └── clef_prompt.yaml description of each workflow for the Clef + use cases
├── custom/              What is YOURS. Ignored by git: everything you edit in the interface goes here.
│   ├── settings.yaml
│   ├── loras.yaml       your LoRAs (and tweaks to the default ones)
│   ├── workflows/       your .json files (the NSFW/ subfolder, for example, also stays out of git)
│   ├── registry/  prompts/  clef_prompt.yaml  models.yaml
│   └── README.md
├── addons/              YOUR extensions (code, flows, screens). Ignored by git. See "Addons".
├── comfyui_nodes/       custom nodes to copy into ComfyUI (install_nodes)
├── clef_server/         the Clef server that ships with the project
├── pickflow/  web/      the program
└── data/                uploaded images, results and conversations (ignored by git)
```

Simple rules:

- **`custom/` beats `addons/`, which beats `defaults/`** (same id = yours replaces the default).
- When you edit a default through the interface, only the **difference** goes to `custom/`. That is why `git pull` keeps bringing
  the updates of the defaults you did not touch, and **Restore default** is just deleting your copy.
- The whole of `custom/` is ignored by git. You do not need to list your files in `.gitignore`.
- `.json` files are never changed by PickFlow: it converts an in-memory copy on every run.

## Mine or official? Where each thing goes

Everything you create or edit **through the interface** (use cases, LoRAs, prompts, descriptions, settings, agent cases, imported
workflows) is saved in `custom/`, and `custom/`, `addons/`, `data/` and `.env` stay **outside git**: none of it is published
by mistake, and whoever downloads the project starts with only what is in `defaults/`.

For something to be **official** (everyone who downloads it gets it), put it in `defaults/` by hand and commit; the interface
never writes there:

| What | Where it goes in `defaults/` |
|---|---|
| Use case | `clef_prompt.yaml`, `recipes` list (copy the block from `custom/clef_prompt.yaml`) |
| LoRA | `loras.yaml` + `models.yaml` (so the assistant can download it) + a row in the LoRA table of `docs/installation.md` |
| Prompt | `prompts/<workflow>.yaml` |
| Workflow | `workflows/*.json` + `registry/<id>.yaml` + `prompts/<id>.yaml` + description in `clef_prompt.yaml` |

After copying, **delete the item from `custom/`** (yours beats the default and would hide the official version) and run
`python -m pytest -q`: the `test_defaults_ship_no_personal_or_nsfw_text` test blocks personal or adult text in `defaults/`,
and new screen texts must go into `web/locales/en.json` (`python tests/uitexts.py` lists what is missing).
What the Clef reads (descriptions, `when`, questions) goes in English.

## Adding your own workflow

1. Save the workflow in ComfyUI (**Workflow → Save**) and copy the `.json` to `custom/workflows/` (subfolders are fine).
2. **Workflows and prompts → Add workflow.** Choose the file, check the image inputs, the prompt node and
   the output, and click **Add**.
3. Write the description the Clef will read (*what it does and when to use it*) and the prompts. Run **Check workflows**.

It is ready to use. Boxes, masks, nodes to discard and fixed values you add by hand in
`custom/registry/<id>.yaml` — see `defaults/registry/object_move.yaml` (boxes) and `defaults/registry/t2i.yaml`
(nodes inside a subgraph). Details in [`custom/README.md`](../custom/README.md).

> Workflows and prompts with adult content: the repository only ships neutral versions. Keep yours in `custom/workflows/`
> (or in a subfolder such as `custom/workflows/NSFW/`), and the prompts in `custom/prompts/`. A registry of yours in
> `custom/registry/<id>.yaml` with the same `id` as a default (and `file: NSFW/file.json`) makes that id use your
> workflow. Nothing under `custom/` goes into git.

## Text encoder (CLIP): fp8 or int8

The workflows load **Qwen3-VL 8B** as the text encoder, in two versions: **fp8 (heretic)** is the one used by the edit, transfer,
pose, 3D and paste workflows; **int8 (convrot)** is the one used by swap person and swap clothing (AusBoss). In **Settings >
ComfyUI > Text encoder (CLIP)** you choose: *Automatic* (each workflow keeps its own, as shipped) or one of the versions, which
then applies to every workflow (PickFlow swaps the file in the graph sent to ComfyUI; the `.json` files do not change). You can only
pick a version that ComfyUI has (the others appear disabled), and the install assistant stops asking for the one you did not
choose. Only the `CLIPLoader` nodes that already load one of these two versions are swapped: the PE encoder of *Generate an image from scratch* stays
as it is. The setting is `comfyui.text_encoder` (`auto`, `fp8` or `int8`) in `custom/settings.yaml`.

## Other image models (besides Qwen 2.1)

Everything PickFlow ships is for **Qwen-Image 2.1**, which remains the default. To prepare another model (MOSS-Image, for
example) without touching anything that exists, use a **`models` tag** (a list) on each item; **without the tag, only Qwen applies**:

| Where | How |
|---|---|
| **Workflow** (`registry/<id>.yaml`) | `models: [moss_image]`. A workflow that stands in for another one for that model declares `implements: edit` |
| **Prompt** (a preset in `prompts/<id>.yaml`) | `models: [qwen21, moss_image]` to apply to both |
| **LoRA** (`loras.yaml`) and **use case** (`recipes`) | `models: [qwen21, moss_image]`; a use case that names `edit` runs the chosen model's `edit` (the one that declares `implements: edit`) |
| Any | `models: ['*']` serves all of them (cutouts, 3D and other flows that do not depend on the image model) |

In the chat **Model: …** appears next to the workflow selector **only when there is more than one** to choose from: Qwen (always) and the
models that have a workflow in PickFlow **and** whose file is in ComfyUI (PickFlow looks at ComfyUI's loaders and at the
`models/diffusion_models`, `unet` and `checkpoints` folders). Choosing a model makes the Clef see only what serves it
(workflows, use cases, prompts and LoRAs). Without a MOSS workflow in PickFlow, nothing changes and the selector does not even show up. The model
used when nobody chooses is `model` in `custom/settings.yaml` (`qwen21`).

What recognizes each model's file is in `defaults/families.yaml` (`match`: file name patterns; MOSS's is
`*moss*`, adjust it in `custom/families.yaml` if your file has another name). A new model is just a new family in that
file plus workflows carrying its tag.

## Addons: your own features

Want to change the interface, help the prompt with your LLM, create a new flow (for example, rotating the image) or plug in an
API of yours? Create an **addon** in `addons/<name>/`. The folder is ignored by git, so you can tinker freely and `git pull`
never touches it.

```
python -m pickflow addon new my-addon      # or Settings > Addons > Create addon; restart PickFlow
```

An addon can bring **workflows, registries, prompts and LoRAs** (same format as `defaults/`), **Python code**
(`addon.py`: hooks for the request and the prompt, **actions** = flows written in Python without ComfyUI, routes, the **LLM** from
Settings, data) and **interface** (`web/main.js` with `window.PickFlow`: buttons, screens, windows, settings). The generated template
already has an example of each. The full reference is in [`addons/README.md`](../addons/README.md), and the
`pickflow-addons` skill (`.claude/skills/`) teaches Claude Code to create and change addons for you.

## Where you change things

| What | In the interface | Lives in |
|---|---|---|
| What the Clef knows about each workflow, **which prompts it may use**, use cases | *Workflows and prompts*, *Use cases* | `custom/clef_prompt.yaml` |
| Prompts of each workflow | *Workflows and prompts* → prompt cards | `custom/prompts/<id>.yaml` |
| Technical wiring (nodes, widgets, discards) | read-only | `custom/registry/<id>.yaml` |
| LoRAs: description, strength, default prompt | *LoRAs* tab | `custom/loras.yaml` |
| Addresses, memory, writer | *Settings* | `custom/settings.yaml` |

In *Workflows and prompts*, each prompt has **Clef can use it** (on/off), **Default**, **When to use** (what the Clef reads
to choose between prompts) and the **text**. `{request}` receives the user's request; `<image1>`, `<image2>` stay literal
for the Qwen. Tick *rewrite in English* so the optional writer (any server compatible with the OpenAI API)
processes the request first.

In *Use cases* you create fixed sequences (e.g. swap the body and then take the person to a new background), set which
image each step starts from (`image N`, `previous result`) and can pin each step's prompt.

## Prompt writer (optional LLM)

In *Settings > Writer and agent* you register **several providers** (OpenAI, OpenRouter, Google Gemini, Anthropic, DeepSeek,
Groq, a local server or any address compatible with the OpenAI chat API) and, in each one, **several models**;
a selector says which one is in use (the same LLM serves the use-case agent and the addons).

- **API key:** paste it in the field and click *Save key*. It goes into the `.env` file of this folder (variable
  `LLM_API_KEY_<provider id>`; git ignores the file) and **never** into `custom/settings.yaml`; the screen only knows whether it
  exists. If you prefer, write the line in `.env` and restart. A local server needs no key.
- **Test** sends a one-word question and shows the time or the reason for failure (wrong key, address, model).
- **"Sees images"** (per model): PickFlow sends the step's images along (downscaled), and the writer describes by what it sees
  ("the man in the red jacket on the right") instead of relying on "image 1". Only tick it on models with vision.
- The writer only acts on prompts marked `rewrite` (today, the **blend that completes the body**). If it fails, PickFlow goes on with
  the assembled prompt without it and **says why in the conversation**.
- Anyone who already had an address and a model saved keeps working: they become the "Default" provider (key in `LLM_API_KEY`).
