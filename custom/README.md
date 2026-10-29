# custom/ — what is yours

This folder is ignored by git (see `.gitignore`): everything you put or edit here stays only on your machine
and **beats** what ships in `defaults/`. Updating the repository (`git pull`) never touches it.

```
custom/
├── settings.yaml        ComfyUI and Clef addresses, memory... (created by the Settings screen)
├── clef_prompt.yaml     only what you changed in the Clef prompt (descriptions, use cases)
├── workflows/           your .json files saved from ComfyUI
│   └── NSFW/            (example) subfolders are fine; this one and everything inside it also stays out of git
├── registry/            technical wiring of YOUR workflows (image nodes, prompt, output)
├── prompts/             your prompts, per workflow
├── loras.yaml           your LoRAs (description, strength, default prompt) and tweaks to the default ones
└── models.yaml          (optional) extra LoRAs and models for the install assistant
```

## Adding a LoRA of your own

**LoRAs tab → New LoRA** (or edit `custom/loras.yaml`):

```yaml
loras:
- id: my_lora
  title: My LoRA
  file: folder\file.safetensors       # as it appears in ComfyUI's models/loras
  strength: 0.7
  auto: true                           # the Clef may turn it on by itself
  description: When the request is X, use this LoRA.
  prompt: "instruction that always goes with the LoRA"   # or {turbo: ..., quality: ...}
  adjust: {natexp: 0}                  # strength of other LoRAs while this one is on
  settings: {resolution: 1024}         # workflow settings (names in the registry's knobs:)
  conflicts: [consistency]             # does not work together with these: turning this one on removes the others (both ways)
```

You can also **download it from a Hugging Face link** (LoRAs tab → *Download from Hugging Face*). If the file requires a key,
write `HF_TOKEN=your_token` in the `.env` file of the PickFlow folder (template in `.env.example`; git ignores `.env`).

Same `id` as in `defaults/loras.yaml` = yours wins; `disabled: true` hides a default. To make a LoRA always enter
a prompt, use the prompt's `loras:` field in `custom/prompts/<id>.yaml`.

## Adding a workflow of your own

1. In ComfyUI, save the workflow (**Workflow → Save**) and copy the `.json` into `custom/workflows/`
   (subfolders are fine).
2. In PickFlow, **Workflows and prompts → Add workflow** tab. Choose the file, check where the
   images and the prompt go in, and click **Add**.

PickFlow creates `custom/registry/<id>.yaml` and `custom/prompts/<id>.yaml`. Your `.json` file is never changed.
Boxes and masks, nodes to discard and fixed values are added by hand in the registry; use the ones in
`defaults/registry/` as examples (`object_move.yaml` has boxes, `t2i.yaml` has nodes inside a subgraph).

## Using your own version of a default (for example, with another workflow)

Copy `defaults/registry/<id>.yaml` to `custom/registry/` (same name and same `id`) and change `file:` to your
workflow, such as `file: NSFW/My workflow.json`. In `custom/prompts/<id>.yaml` go your prompts for that id
(start by copying `defaults/prompts/<id>.yaml` and add yours). All of this stays out of git.
The prompts file in `custom/` **replaces** the whole default: after a `git pull`, compare with the default to see
the new prompts. The `edit` workflow has 8 image inputs (the ones without an image are removed from the graph); when bringing yours, keep
the 8 and the node numbering of `defaults/registry/edit.yaml`, or PickFlow will not know where to put the images.

## Changing a default without losing updates

- **Prompts and descriptions:** edit through the interface. Only the difference goes to `custom/`; the
  **Restore default** button deletes your version.
- **Changing a default's file or nodes:** copy `defaults/registry/<id>.yaml` to `custom/registry/`
  (same name, same `id`) and edit the copy. If your workflow has another name, put the `.json` in
  `custom/workflows/` and adjust `file:` in the copy.

## Removing

**Remove from the list** (on the screen itself) takes the workflow out of PickFlow and deletes your registries and prompts.
The `.json` stays in `custom/workflows/`. To get rid of it for good, delete the file.
