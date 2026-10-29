# PickFlow

A chat for **ComfyUI**: you write what you want (and attach photos, if needed), the **Clef** model decides which of your
workflows to run — or a sequence of them — and PickFlow runs it all on your ComfyUI.

```
 You ──► PickFlow (this folder, port 7860) ──► Clef    (decides and returns probabilities)
                      │
                      └─────────────────────► ComfyUI (runs the workflow through the HTTP API)
```

> **Built for Qwen-Image 2.1.** The workflows that ship with the project use the Qwen-Image 2.1 models and nodes.
> PickFlow also works with other models, but you bring the workflows ([how](docs/customizing.md)).

PickFlow is **not a ComfyUI plugin**: it is a separate program (Python + a web page) that talks to ComfyUI over HTTP.
It does not change your workflows or your ComfyUI install, except for the custom nodes you choose to install.

## What it can do

Edit images by text · swap **person, head or clothing** · move, swap, transfer and remove objects · paste and blend
a person or object into a scene · remove the background · generate from scratch · **expand a photo** (outpaint) · change the **pose** · change the
**camera angle** in 3D · improve or restore a photo. Longer requests become **use cases**: sequences such as
*swap the person → put on the clothes → take her to another place*. Full list in [docs/workflows.md](docs/workflows.md).

## What you need

| | What for | Note |
|---|---|---|
| An up-to-date **ComfyUI** | runs the workflows | Required. [How to update](https://docs.comfy.org/installation/update_comfyui). |
| **Python 3.10+** | runs PickFlow | The start scripts create the environment by themselves. |
| An **NVIDIA card** (16–24 GB of VRAM) | Qwen-Image 2.1 | The workflows were built on an RTX with 24 GB. |
| **Qwen-Image 2.1 models and LoRAs** | the workflows load them | The setup assistant downloads the ones you choose. |
| **Clef server** | picks the workflow | Ships in `clef_server/`. To just see the interface, there is a *test mode* without a model. |

## Getting started

**1. ComfyUI.** Install [ComfyUI Desktop](https://www.comfy.org/download) (simplest) or the
[manual version](https://github.com/comfyanonymous/ComfyUI), update it and leave it open.

**2. PickFlow.** Download and start it (the first time it creates the Python environment and installs the dependencies; then it opens
<http://127.0.0.1:7860>):

```
git clone <this repository's address> PickFlow
```

| System | How to start |
|---|---|
| Windows | double-click `start.bat` |
| Linux / macOS | `bash start.sh` |

**3. Assistant.** The chat screen shows **Before you start** while something is missing: click **Review and install**.
It finds the ComfyUI folder, shows what is missing (PickFlow nodes, LoRAs, models) and **installs only what you tick**,
checking the sha256 of every file. After copying nodes, restart ComfyUI. (Prefer doing it by hand? [docs/installation.md](docs/installation.md).)

**4. Clef.** In another terminal, start the Clef server that ships with the project. It **asks for the quantization** before starting:

| System | How to start |
|---|---|
| Windows | double-click `clef_server\start_clef.bat` |
| Linux / macOS | `bash clef_server/start_clef.sh` |

| Quantization | VRAM | For whom |
|---|---|---|
| **bf16** | ~17.8 GB (peak ~26 GB when deciding) | most faithful; the card is left only to the Clef while it decides |
| **int4** | ~7.9 GB (peak ~16 GB) | a 24 GB card that also runs the Qwen; decides much faster (NVIDIA only) |

The first request downloads the model (`Cloudflare/clef-flash`, ~19 GB). Already have a Clef server? Point the address at it in
**Settings → Clef**. Just want to see the interface? In **Settings → Clef → Mode**, choose *Test without the model*
(the Clef then picks by shared words). Details in [docs/clef.md](docs/clef.md).

**5. Check.** **Settings → ComfyUI → Test** shows *Connected* or the reason for the failure; **Check workflows** checks every
workflow against **your** ComfyUI. Do this once before first use.

### First request

Type in the text box, attach photos if needed and send. A few examples:

- `Change the shirt color to red` (1 photo)
- `Swap the face of the person in image 1 for the one in image 2` (2 photos)
- `Outpaint this image` (1 photo: opens a screen for you to choose how much to grow)
- `Draw an astronaut cat` (no photo)

Every answer shows **How Clef decided**, with the probabilities, and below it a *Was that what you wanted?* question: your
answers teach the Clef for the next requests. You can force a workflow or the mode (fast/quality) in the selectors
next to the text field. The interface opens in **English**; **Settings → General → Language** switches to Portuguese.

## If something goes wrong

| Symptom | What to do |
|---|---|
| **ComfyUI is down** | Open ComfyUI. In *Settings → ComfyUI → Test* PickFlow tries to find the port (Desktop usually uses 8000). |
| **Nodes not installed: DrawBox, ViggleTurbo…** | *Settings → ComfyUI → Open the setup assistant*, install and restart ComfyUI. |
| **`'x.safetensors' is not installed`** | The file still has to be downloaded, or it has another name. The assistant shows what is missing. |
| **Clef is not answering** | The server is not up or the address is wrong (*Settings → Clef*). Start `start_clef` or use test mode. |
| **The Clef is slow every time** | It reloads on every request because it does not fit next to the Qwen: start it in **int4**. |

More cases in [docs/troubleshooting.md](docs/troubleshooting.md).

## Documentation

| | |
|---|---|
| [Detailed installation](docs/installation.md) | manual install, third-party nodes, Linux and macOS |
| [Workflows](docs/workflows.md) | what each workflow does, LoRAs, 3D, outpaint, modes |
| [Use cases](docs/use-cases.md) | sequences, swap × paste, prompt variables, agent |
| [Clef](docs/clef.md) | how it decides, server, quantization, memory, learning |
| [Customizing](docs/customizing.md) | `custom/` files, your workflows, LoRAs, addons, prompt writer, other models |
| [Troubleshooting](docs/troubleshooting.md) | |
| [For code contributors](docs/development.md) | tests, translation, demo |

## Security

PickFlow only listens on `127.0.0.1` and refuses requests coming from other sites. **Do not expose the port to the network**: there is no login.

## Credits

- [Qwen-Image 2.1](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1) and the models for ComfyUI: Qwen and [Comfy-Org](https://huggingface.co/Comfy-Org/Qwen-Image-2.1).
- LoRAs: [Viggle](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) (turbo), [Alissonerdx](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) (BFS body/head swap),
  [prithivMLmods](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) (Natural Exposure),
  [ausboss](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) (Consistency), [flymy-ai](https://huggingface.co/flymy-ai/qwen-image-realism-lora) (realism).
- 3D: [TripoSplat](https://huggingface.co/VAST-AI/TripoSplat) (VAST-AI), [BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet); LoRAs [AnyAngle](https://huggingface.co/lilylilith/QI_2.1_AnyAngle) and [Outpaint](https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA). The viewer uses [three.js](https://threejs.org) and [Spark](https://sparkjs.dev) (MIT, in `web/vendor/`).
- Swap person and swap clothing: the *Qwen Image 2.1 Person Swap* and *Outfit Swap* workflows and the [ComfyUI-AusBoss](https://github.com/ausboss/ComfyUI-AusBoss) pack by [AusBoss](https://www.youtube.com/@ausbossdev) (MIT).
- Clef: [Cloudflare](https://huggingface.co/Cloudflare/clef-flash).

## License

PickFlow's code is distributed under the [MIT license](LICENSE). Third-party models, LoRAs and workflows have their
authors' licenses (several — Qwen Research — are **non-commercial use**): check each page before using them
(*License* column in [docs/installation.md](docs/installation.md) and `defaults/models.yaml`).
