# Detailed installation

The short path is in the [README](../README.md). This page covers the setup assistant in depth, the manual install, and what changes on Linux and macOS.

## 1. Install ComfyUI (if you do not have it yet)

PickFlow only asks ComfyUI for images, so **ComfyUI must be installed, up to date and open**.
Install [ComfyUI Desktop](https://www.comfy.org/download) (simplest) or the
[manual version](https://github.com/comfyanonymous/ComfyUI) and open it once. The assistant in step 3 checks this and
tells you what to do if it cannot find it.

## 2. Download and start PickFlow

```
git clone <this repository's address> PickFlow
```

It can live in any folder. With ComfyUI open, run `start.bat` (Windows) or `bash start.sh` (Linux/macOS). The first time it creates
the Python environment and installs the dependencies; then it opens <http://127.0.0.1:7860>.

## 3. Get ComfyUI ready (the assistant)

The first time, the chat screen shows **Before you start**. Click **Review and install** (or, at any time,
**Settings → ComfyUI → Open the setup assistant**). It:

1. **finds the ComfyUI folder** (through the running ComfyUI itself; if it cannot, you give it the folder) and, if it
   cannot find ComfyUI at all, explains how to install it;
2. shows what is **missing** in three groups: **PickFlow nodes** (red/green boxes and Viggle Turbo), **LoRAs** and the
   Qwen-Image 2.1 **base models** (large files, unticked by default);
3. **asks what you want to install.** Nothing is copied or downloaded before you click *Install*;
4. downloads straight into `models/loras/` (or the matching folder), checks the **sha256** of every file and shows progress.
   After copying nodes, **restart ComfyUI**.

Clicked *Not now*? The notice goes away and the assistant stays available in *Settings*. If ComfyUI runs on **another computer**, the
assistant installs nothing on it: use the file list and the folders shown to copy things by hand.

The file list is in [`defaults/models.yaml`](../defaults/models.yaml); to add your own, create
`custom/models.yaml` in the same format.

<details>
<summary>Manual installation (without the assistant)</summary>

**Models** (regular ComfyUI folders):

| File | Folder | Where to download |
|---|---|---|
| `qwen_image_2.1_int8_convrot.safetensors` | `models/diffusion_models/` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) |
| `qwen3vl_8b_fp8_heretic.safetensors` | `models/text_encoders/` | same |
| `qwen_image_2.1_vae_bf16.safetensors` | `models/vae/` | same |

**LoRAs** (`models/loras/`). Each workflow uses some of them:

| File | Used in | License | Where to download |
|---|---|---|---|
| `Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors` | every **Turbo** (6 steps) and *Move or swap objects (Quality)* | Qwen Research | [Viggle/Qwen-Image-2.1-viggle-turbo](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) |
| `bfs_body_swap_v1.0_qwen_2.1.safetensors` | swap people (body), transfer object (Turbo) | MIT | [Alissonerdx/BFS-Best-Face-Swap](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) |
| `bfs_head_v1.1_qwen_2.1.safetensors` | swap people (head) | MIT | same |
| `Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors` | almost all | Qwen Research | [prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) |
| `qwen-image-2.1-consistency.safetensors` (the final version; the 2000-step one is no longer used) | editing, swap people, replace area | Qwen Research | [ausboss/Qwen-Image-2.1-Consistency-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) |
| `qwen-image-2.1-outfit-swap.safetensors` | swap clothing or tattoo (do not use with consistency) | see the page | page in the [catalog](../defaults/models.yaml) |
| `qwen-image-2.1-restore.safetensors` | restore or improve a photo's quality (the *Improve the photo* prompt and its *Restore* and *Restore an old photo* options; do not use with consistency) | Qwen Research | [ausboss/Qwen-Image-2.1-Restore-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Restore-LoRA) |
| `Qwen2.1_viewpoint_orbit.safetensors` | turn the point of view | see the page | page in the [catalog](../defaults/models.yaml) |
| `qwen-image-2.1-outpaint-v2.safetensors` | expand the photo and fill gray areas (the *Expand or fill gray areas* prompt) | Qwen Research | [ausboss/Qwen-Image-2.1-Outpaint-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA) |
| `QI2.1_AnyAngle.safetensors` | change the camera angle with 3D (scene and person) | Apache-2.0 | [lilylilith/QI_2.1_AnyAngle](https://huggingface.co/lilylilith/QI_2.1_AnyAngle) |
| `casual_snapshot_realism_qwen21.safetensors` | phone-photo look (optional) | see the page | tensor.art only: download by hand and put it in `models/loras/` |
| `flymy_realism.safetensors` | editing, swap head, move/transfer object | Apache-2.0 | [flymy-ai/qwen-image-realism-lora](https://huggingface.co/flymy-ai/qwen-image-realism-lora) |
| `qwen-image-2.1纠正背景透视溶图v2.0.safetensors` (trigger word `pysj666`) | *Paste and blend* (perspective and light) | see the page | [RunningHubAI/rh-qwen-image-2.1v2.0-lora](https://huggingface.co/RunningHubAI/rh-qwen-image-2.1v2.0-lora) |

Subfolders inside `models/loras/` are accepted: PickFlow finds the file by name.

**3D (changing the camera angle):** TripoSplat uses `triposplat_fp16.safetensors` (`models/diffusion_models/`),
`dino_v3_vit_h.safetensors` (`models/clip_vision/`), `triposplat_vae_decoder_fp16.safetensors` and `flux2-vae.safetensors`
(`models/vae/`), all from [VAST-AI/TripoSplat](https://huggingface.co/VAST-AI/TripoSplat) (MIT), and
`birefnet.safetensors` (`models/background_removal/`) from [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet) (MIT),
which cuts the person out. The nodes (`TripoSplat…`, `SplatToFile3D`, `SaveGLB`, `RemoveBackground`) come with an up-to-date ComfyUI.
The assistant downloads all of this.

**Third-party node:** the *Paste and blend* cutout uses the `SAM3Segment` (people) and `RMBG` (objects) nodes from the [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG) pack; SAM3 downloads its `sam3.pt` model (~3.4 GB) into `models/sam3/` by itself on first use. PickFlow does not install it: the assistant shows whether it is missing; install it with ComfyUI Manager and restart ComfyUI. (`Create Layered Image` and `Add Layer`, from the same flow, already come with an up-to-date ComfyUI.)

**Third-party nodes for *Swap person* and *Swap clothing*:** the [ComfyUI-AusBoss](https://github.com/ausboss/ComfyUI-AusBoss) pack (`ausboss-nodes`, install it with ComfyUI Manager), which provides the LoRA loader, the image saver and the text nodes of these two flows. Both also use the `qwen3vl_8b_int8_convrot.safetensors` encoder (~9.4 GB, `models/text_encoders/`, in the assistant) and the body/head swap LoRAs (BFS), viggle (Fast mode) and the clothing one, which were already on the list. AusBoss finds the LoRA by name in any subfolder of `models/loras/`.

**Third-party nodes for *Change the pose*:** [comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) (the `DWPreprocessor`, which downloads the pose models by itself), [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG) (the `SAM3Segment`, the same one used by the *Paste and blend* cutout) and the *Qwen Image 2.1 Fun ControlNet Union (T8)* pack (install it with ComfyUI Manager). The `Qwen-Image-2.1-Fun-Controlnet-Union-ComfyUI.safetensors` file (~7 GB, `models/controlnet/`) is in the assistant. The erase step uses PickFlow's *Red and green boxes* nodes (`install_nodes`).

**PickFlow nodes** (`comfyui_nodes/`): `install_nodes.bat` (Windows) or `bash install_nodes.sh` (Linux/macOS). The script looks for the
ComfyUI `custom_nodes` folder (or accepts the path: `install_nodes.bat "D:\ComfyUI"`) and **never overwrites anything without asking**.
Restart ComfyUI afterwards.

</details>

Reference: the [official Qwen-Image 2.1 guide for ComfyUI](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1).

> **Licenses.** PickFlow's code is MIT, but the models and LoRAs are **not**: each author has their own license and several
> (Qwen Research) are **non-commercial use**. Check each page before using them.

## 4. Check everything

1. **Settings → ComfyUI → Test.** Instantly shows *Connected* (with version and graphics card) or the reason for the failure. If the address is wrong, PickFlow tries the usual ports
   (8188, 8000–8003, 8199). ComfyUI Desktop usually uses 8000.
2. **Settings → ComfyUI → Check workflows.** Converts every workflow to the ComfyUI API format and checks nodes, links,
   limits and models against **your** ComfyUI. Each red line says what is missing. Do this once before first use.
3. **Settings → Clef.** Point it at the Clef server or use **test mode** just to see the interface working.

The chat screen shows a *Before you start* notice while ComfyUI or the Clef do not answer, or files are missing.

**Language.** The interface opens in **English**; *Settings → General → Language* switches to Portuguese (it applies immediately and the page reloads).
The language applies to the screens and to PickFlow's messages. The descriptions, criteria and instructions that the **Clef reads**
(workflow description, "when to use", general instructions, questions) are in **English** in the `defaults/` files and in the
editors, and so are the questions built by the code; screen names (titles, labels) follow the interface language.
Requests can be written in either language.

## Linux and macOS

Everything is the same as on Windows; only the start scripts change (PickFlow and the Clef server are plain Python):

| What | Windows | Linux / macOS |
|---|---|---|
| PickFlow | `start.bat` | `bash start.sh` |
| PickFlow nodes in ComfyUI | `install_nodes.bat` | `bash install_nodes.sh` |
| Clef server | `clef_server\start_clef.bat` | `bash clef_server/start_clef.sh` |

- You need Python 3.10+ with the `venv` module (on Debian/Ubuntu: `sudo apt install python3 python3-venv`).
- The assistant finds the ComfyUI folder through the running ComfyUI itself; if it cannot (it also looks in `~/ComfyUI`, for
  example), set the folder in **Settings → ComfyUI**.
- The Clef server installs torch with CUDA 12.8 by itself if it finds an NVIDIA card (`nvidia-smi`); without one, it installs the CPU
  build of torch and only bf16 works (~18 GB of RAM). macOS has neither CUDA nor `bitsandbytes`: only bf16 on the CPU.
- For `./start.sh` to work the file needs the execute permission (`chmod +x start.sh install_nodes.sh clef_server/start_clef.sh`);
  `bash start.sh` works without it.
