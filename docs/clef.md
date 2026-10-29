# The Clef: how it decides, server, memory and learning

The Clef does not generate text: it returns **probabilities**. This page explains what PickFlow asks it, how to start the server and how memory is shared with ComfyUI.

## How the Clef decides

PickFlow turns every decision into a multiple-choice question:

1. **Route**: which workflow or use case (and, optionally, which one comes next). Above 26 options, it decides in two stages.
   The next step only enters with at least 50% confidence (`clef.then_min`), after giving "none" a 1.5× weight (`clef.then_none_weight`), and only among workflows that fit the uploaded
   images: it receives the result in place of one of the photos, so with a single photo "transfer between images" does not show up.
2. **Prompt** of the workflow and **which image goes into each input**.
   A workflow with `fallback` in its registry (*Swap person*, *Swap clothing*) gets **one extra question** right after the route,
   to decide between it and the fallback.
3. If the confidence in the route falls below *Ask you below* (0.45), the chat asks instead of risking it. The
   same goes for the step's prompt and for one of its options (such as *how to improve*): below
   *Settings → Clef → Learning → Ask on screen below* (0.5), PickFlow opens the choice with the two best options
   instead of risking it — closing the window (or *Follow the first option*) keeps the Clef's answer. This path is only
   used with the real Clef: in test mode it interrupts nothing.
4. In every question the Clef receives **each photo's measurements** (width and height, megapixels and a sharpness
   estimate), what is in it and similar requests that already worked. That is what makes "improve this photo" become *restore*
   on a small photo and *improve without enlarging* on a large, sharp one.

Every answer shows **How Clef decided**, with the probabilities. You can also force a workflow in the selector next to the
text field and **choose the generation mode** (automatic, fast or quality): the chosen mode applies to every
step of that request, in every workflow that has both modes, and the mode question is no longer asked to the Clef. In
*Automatic* (the default) the Clef decides, as before.

## The Clef server

The weights come from Cloudflare (`Cloudflare/clef-flash`, ~19 GB in BF16, and `Cloudflare/clef`, ~55 GB, Apache-2.0).
PickFlow talks to a local HTTP server:

- `POST /v1/systemone` — decides (accepts `images` as a data URL or base64; at most 4 images per request — with more
  attached images, PickFlow sends windows of 4 and combines the answers);
- `GET /health` — answers `{"loaded": bool, "device": "...", "ram_gb": N, "vram": {"allocated_gb": N}}` and, for the
  memory policy, **`quant`** (how the model was loaded: `""` for BF16, `int4`), **`vram_expected_gb`**
  (how much it weighs when loaded — valid while the model is unloaded) and **`vram_work_gb`**
  (how much it needs **to decide**: the weights plus the activations — this is the number that decides whether it fits next to
  ComfyUI);
- `POST /v1/device` — switches between `cpu` and `cuda` (unloads the model and reloads it on the next request);
- `POST /v1/unload` (or `/unload`) — takes the model out of memory.

### Starting the server that ships with PickFlow

Double-click `clef_server\start_clef.bat` (Windows) or run `bash clef_server/start_clef.sh` (Linux/macOS). It is **a single script**
(`start_clef.py`; the `.bat` and the `.sh` only find Python): the first time it creates the environment, installs the right torch for your
machine and the rest; **every time it asks for the quantization** and starts the server at `http://127.0.0.1:8321` (the address that
*Settings → Clef* already has). The model is downloaded from Hugging Face on the first request.

```
Card: NVIDIA GeForce RTX 3090 (24 GB of VRAM)
Clef quantization:
  1) bf16  most faithful · ~17.8 GB of VRAM (peak ~26 GB when deciding)
  2) int4  4 bits (nf4) · ~7.9 GB of VRAM (peak ~16 GB), decides much faster   <- recommended
Choose [1/2, Enter = int4]:
```

Enter accepts the last choice (or the recommended one: int4 on cards below 32 GB). To skip the question and pick the rest on the command line:

```
python clef_server/start_clef.py --quant int4              # or bf16
python clef_server/start_clef.py --quant bf16 --device cpu # no NVIDIA card: bf16 only, ~18 GB of RAM
python clef_server/start_clef.py --install-only            # only prepares the environment
```

Whatever is left on the command line goes to `server.py` (`--port`, `--model`, `--idle-unload`...). The server loads the model on the
first request and unloads it after 120 s idle (`--idle-unload`, 0 = never). **int4** uses `bitsandbytes` and only runs on an
NVIDIA card; at startup the server refuses, with the reason, a combination that does not work (int4 on the CPU, missing CUDA).

If you already have a Clef server, point *Server address* and *Path* at it in **Settings → Clef**.

> **Where to run the Clef?** The "to decide" column is the one that matters for knowing whether it fits next to the Qwen: the activations of
> a decision with images weigh as much as the weights in 4 bits (`clef-flash` has 9 billion parameters).
>
> | Where | Weights | To decide | When to use |
> |---|---|---|---|
> | GPU, bf16 | 17.8 GB of VRAM | ~26 GB | a card with room to spare; on 24 GB it has to leave before every image |
> | GPU, **int4** | **7.9 GB** | **~16 GB** | decisions 3 to 12× faster with 2+ images; it still leaves before generating |
> | CPU (`--device cpu`), bf16 | ~18 GB of RAM | + activations in RAM | frees the whole card for the Qwen; needs RAM to spare |
>
> **PickFlow does not guess — it asks**: `quant`, `vram_expected_gb` and `vram_work_gb` come from `/health` and the math is
> done with them. With 7.9 GB of weights the Clef *seems* to fit next to the Qwen, but **when deciding it reaches 16.4 GB**;
> 16.4 + the Qwen's 15.5 do not fit in 24 GB, so the right answer is to unload before generating — and that is what it does,
> explaining why in **Settings → Clef** and in the conversation. The gain of int4 is the speed of the decision, not
> keeping both on the card.

The loading logic follows `load_release_model` from Cloudflare's repository; `tests/test_clef_server.py` exercises it with a fake
model (bf16, int4, impossible combinations and a real HTTP conversation with PickFlow's client), but **the real weights were only
loaded in the reference server**, not in this one.

## Memory: Clef and Qwen on the same machine

PickFlow measures memory (ComfyUI's `/system_stats` + the Clef's `GET /health`) and decides by itself. There are **two policies**,
and what chooses between them is where the Clef runs (the server reports it in `/health`; when it does not, *Settings → Clef → Where the
Clef runs* applies):

- **Clef on the GPU** (VRAM): if `total VRAM − Clef VRAM − margin` ends up smaller than the VRAM the workflow uses (default: 16 GB),
  the Clef is unloaded before generating and reloads on the next request. That is what happens with `clef-flash` in BF16 on a 24 GB
  card: 17.8 GB + 16 GB do not fit. It also frees ComfyUI's models if there is not enough VRAM to load the Clef back.
- **Clef on the CPU** (RAM): there is no card contention, so the math is about RAM —
  `free RAM − Clef RAM` against what the workflow uses (`need_ram_gb`, default 10 GB). In this mode PickFlow does **not** free
  ComfyUI's VRAM before deciding, because that would only make it reload everything from disk.

*Automatic* mode (the default) applies the policy of wherever the Clef is; *Always* and *Never* force the behavior. Every
decision appears in the chat. A workflow can have its own estimate in `registry/<id>.yaml` (`mem: {vram_gb: 12, ram_gb: 8}`).

> **Honest math for 32 GB of RAM:** with ComfyUI open about 13 GB are free, and `clef-flash` in BF16 on the CPU uses
> ~18 GB. In practice, on a 32 GB machine with an NVIDIA card, prefer the Clef on the GPU in **int4** (int4 does not run on the CPU). If the
> server does not report the RAM it uses, PickFlow cannot measure it and the RAM rule does not fire — prefer a server
> that answers `ram_gb` in `/health` (the one in `clef_server/` does).

## PickFlow learns from your feedback

Under every result a discreet question appears with four
answers: *That was it*, *That was not it*, *It was another workflow* (opens the list and you say which was the right one) and *It was another
prompt* (the workflow was right and only the prompt was not — so one's mistake does not dirty the other's history).
**Downloading the image counts as a weak "yes"** and does not get in the way, and a vote already given stays marked after
reloading the page. If you **repeat the request in other words and land on another route**, PickFlow understands that the
first generation did not do, and counts it as an error; if you repeat it on **the same route**, it is a fine adjustment and does not count (nobody is
obliged to give an opinion: silence never becomes a hit). A route that **you** picked by hand does not teach the Clef — it was not
the one that decided. The judgment always concerns the route that **ran**: if you answered the "the Clef was in doubt" notice
or the agent built the case, that is the route that goes into the history.

With this history, three things change in the next decisions: similar requests that worked (or that you
corrected with *it was another workflow*) go in as examples in what the Clef reads — and cases that point to the **same**
flow become **a single line, with the count** ("3 similar requests"), so the slots are not spent repeating the same
lesson; when the history disagrees with the Clef its
probability is **corrected** (the history counts for up to half, adjustable in *Settings → Clef → Learning from your
feedback*); and a **measurement of the photo** (megapixels and sharpness, computed on your computer) goes into every decision, so the
Clef does not have to guess whether it is worth restoring or just improving without enlarging. Everything is kept in `data/feedback.jsonl`, on your
machine; `.gitignore` already ignores it.
