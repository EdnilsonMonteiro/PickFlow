# Workflows that ship with PickFlow

What each workflow does and how LoRAs come into play. The files live in `defaults/workflows/` (and `defaults/registry/` tells PickFlow which nodes receive images, prompt and output).

| Workflow | What it does | Images | Drawing |
|---|---|---|---|
| **Edit image** — Turbo / Quality | **the single workflow for editing people and images**: free editing, change light or style, **transfer tattoos**, **turn the point of view** of an object, with up to **8 images** (the ones you do not send are removed from the graph). The Clef picks the prompt **and the LoRAs** from the request (see *LoRAs* and *One prompt, several cases*) | 1–8 | — |
| **Swap person (body and head)** — Fast / Quality | **the default person-swap flow** (AusBoss's *Person Swap*): the person in the scene becomes the one in the 2nd image, **body and head** or **head only**; it keeps the face and the rest of the scene and **says WHO to swap** when there are several people ("the woman on the left", "the man on the right"). In exceptions (busy face, cut-off body, body only, several people) the Clef uses the classic editor: see *Default flow and fallback* | 2 | — |
| **Swap clothing** — Fast / Quality | **the default clothing-swap flow** (AusBoss's *Outfit Swap*): a vision model describes the clothing in the 2nd image and dresses the person in the 1st, without touching face, hair, hands, pose or background; it also says WHO to dress. Tattoos stay with the editor | 2 | — |
| Move or swap objects — Turbo / Quality | **move** an object to an empty destination, or **swap** two objects' places in the same image. Two prompts of the same workflow; the Clef picks from your request. **Checked by pixels:** after a *move*, PickFlow looks at whether the red box changed; if the object also stayed there (two copies), it runs a second pass that erases the copy | 1 | red box (object) and green box (destination, or the other object) |
| Paste and blend (use case, 3 steps) | pastes a person or object (2nd image) into a scene (1st image): **it cuts out only the person or object the request mentioned** (the Clef picks the name from a list, such as chair, sofa, dog or vase, and the color when the request gives one; SAM3 looks for "beige chair" and takes only that thing, even with others around; for a product photo on a plain background it removes the whole background), with a transparent background. **The order of the images does not matter**: the Clef decides by the roles *Scene* and *Person or object to paste* (person: SAM3 and RMBG run together and PickFlow adds the masks, so arms, hands, feet and bare skin that SAM3 would leave out come back, without taking a chair or touching objects along); you check the cutout and, if it is not good, **draw a box on the original** (and/or switch person/object) to redo it; then you **drag, resize and rotate** it in the chat (part of it may go outside the image; the corners keep the proportion, the side-middle handles stretch and the **dot above the middle of the top edge rotates**; Shift snaps to 15-degree steps). **The blend completes the body when it is missing**: the Clef looks at the collage and the request and, if the pasted person came cut off (waist up only, no legs or hands) or the request gives a pose or action ("sitting on the chair, holding a beer"), the Blend step becomes a full edit (it draws what is missing, the pose and the interaction with the furniture, keeping face and clothes); if the person is already whole, it only blends. With **several boxes** in the cutout you cut **several objects at once**, place each one and finally **blend** perspective, light and shadow. There are also the variants *person first* and *paste only* | 2 | review the cutout (box to redo it) and place the person |
| Add, replace or remove in an area | **add** a new object in an empty spot, **replace** what is inside the square or **remove** it. It can have **several red squares**, only for **identical** objects (click a square and use the **×** in its corner to remove it; for different objects, one edit at a time). The Clef picks the prompt from your request | 1 | red squares |
| Transfer an object between images — Turbo / Quality | takes an object or person from one image to another | 2 | red and green boxes |
| Transfer **several** objects — Turbo / Quality | takes 2 to 4 objects, from different images, to a destination image: the Clef counts how many the request sends, decides which image is the destination and which are objects; on screen you draw **one green box per object** and choose, in each one, **which uploaded image goes there** (1, 2, 3… in the order you attached them), with an optional red box on each object | 3–5 | numbered green boxes (each with its image) and red ones |
| Remove background | leaves only the subject (PNG) | 1 | — |
| **Scene in 3D** (change the angle) | the photo of a room, living room or place becomes a **3D model (Gaussian Splat, TripoSplat)** that shows up in the conversation: you **rotate the camera with the mouse** (sideways, up, down, zoom in), **download the PNG of that angle**, download the `.spz`, and choose between keeping the render (the gray is what the photo did not show) or **filling the gray**: the render goes to Edit with the Outpaint LoRA (only filling the gray, without enlarging the canvas), and if more than 2% gray is left it runs again, up to 2 times | 1 | rotate the 3D |
| **Change the pose** (use case, 2 steps) | **SAM 3** separates each person in the photo (including **people stuck together or one in front of the other**, where DWPose alone mixes the bodies into a single skeleton) and **DWPose** finds the 18 body points of each one, in a batch; detection on the whole photo is the fallback for whoever SAM 3 missed. The **skeleton of each person** appears on a screen: you drag the points and the whole limb follows (Alt moves only the point, Shift stretches the bone, the neck takes the whole person; there are *Mirror*, *Show missing points* for cut-off bodies and *Restore*). Whoever you do not move stays as is. **The old limb is erased first**: PickFlow puts a red square over each arm or leg that moved by itself and runs the remove flow (the ControlNet only draws the limb in the new position, and the old one stayed duplicated), rebuilding body, clothes and background behind it. Then it draws the pose map of everybody and **writes in English what changed** ("the second person from the left: the arm on the left side of the picture: upper arm pointing up…"), because the map alone, with the reference photo, barely moves anyone (tested). The **Qwen-Image 2.1 Union ControlNet** (Pose mode) redraws the whole photo with the new pose, keeping identity, clothes, style and background; it takes ~3 min at 1 MP (erase + apply). **Hands:** DWPose also finds the fingers; the hand follows the arm you moved and is **drawn with five fingers on the pose map** (without that the ControlNet only has the wrist point and drew a "glove" without fingers; if the detector did not see the hand, an open template hand goes in). After generating, **only what moved comes from the model**: outside the arms and legs you moved (old and new place, with the hand or foot) the result goes back to the **pixels of the original photo**, so faces, clothes and the hands of whoever stayed neither lose sharpness nor get blurred. Only the body and hands are edited (the face stays as the model draws it) | 1 | model the pose |
| **Change the person's angle (3D)** (use case, 2 steps) | the person (without the background, cut out by BiRefNet) becomes 3D, you choose the angle in the same viewer and **AnyAngle** redraws the photo at that angle, keeping the person, clothes and scene | 1 | rotate the 3D |
| **Change the person's angle and fix the face (3D + swap)** (use case, 3 steps) | same as above and, at the end, **swaps the head (or the body) for the one in the 2nd image**, so the face does not come out degraded from the 3D. The Clef decides which is the photo and which is the reference and whether to swap only the head (reference with a face/head) or the body too | 2 | rotate the 3D |
| Generate an image from scratch — Turbo / Quality | creates from text (Turbo uses the same graph as Edit, with no images) | 0 | — |

**3D controls (like Blender).** **Navigate** mode, Blender's viewport around a central point: middle button rotates (the left one too, for touchpad users; Alt+left emulates the middle one), Shift+drag or the right button pans the view, Ctrl+drag and the wheel zoom (the wheel zooms toward the mouse), **double click** (or Alt+middle click) makes the clicked point the center of rotation, and the numeric keypad (or the number row) switches views: 1 front, 3 right, 7 top (Ctrl = opposite side), 2/4/6/8 rotate 15°, Shift+4/6 tilt, 9 the other side, 0 or Home the photo's camera, + and - zoom, "." centers on the middle of the scene. **Walk** mode (Blender's Walk, Shift+`): W A S D move while held, Q/E go down and up, Shift runs, Alt walks slowly, dragging looks around, the wheel changes the speed and Enter goes back to Navigate. The **Lens** changes the field of view; the shortcuts are listed under *Shortcuts* in the panel.

**Saved 3D.** Every generated 3D is kept in `data/splats/` with its source photo: **the same photo is not generated again** (TripoSplat takes about 30 s). On the 3D screen, *Save to the library* gives it a name and keeps it forever; of the unsaved ones only the 12 most recent stay. The **Saved 3D** button, next to *Attach image*, lists them all: *Open* (rotate and download the PNG/the `.spz`), *Use in the chat* (attaches the photo and already picks the 3D flow, without generating again) and *Delete*. A result that came from 3D also has an *Open the 3D* button.

**Improve the photo.** Requests to only improve, clean up, sharpen or restore a photo **without changing what is in it** go to the *Improve the photo* prompt of Edit, which decides between four options: *Improve without enlarging* (the photo is already 2 MP or more and the request is general: it adjusts color, contrast, light and fine detail and **keeps the size and framing**, with no restoration LoRA), *Restore* (small, phone, video-frame, compressed or low-sharpness photo: **redraws at about 2 MP** with the restoration LoRA), *Unblur* (out-of-focus or shaky photo, same LoRA at 0.5) and *Restore an old photo* (scratches, dust, grain and faded color). The last two only apply if the LoRA exists; the restoration LoRA turns off Consistency and realism. Changing the content (color, clothes, object, pose, style) is still the *User request* prompt, and fixing only the light or exposure is *Neutral exposure*.

**Expand the photo (Outpaint).** Requests to only expand or complete the image ("make it wider", "outpaint") have **their own route**: the use case *Expand the image (outpaint)* (1 image), which pins Edit's *Expand or fill gray areas* prompt. Before generating, the **Enlarge the image** screen opens: the photo appears over the gray (#808080) and you drag the edges, type the percentage of each side or pick a ratio (1:1, 4:3, 3:2, 16:9, 9:16). What the Clef understood from the request is already filled in, and the *Choose on screen* option starts at zero. The Outpaint LoRA fills only the gray; the original photo comes back identical. If the image already has gray to fill (a 3D render), the screen does not open.

The Outpaint prompt exists because, as just a prompt inside Edit, the real Clef did not even look at it ("Outpaint this image" fell on *Change the angle* and *Remove background*); now it appears in the chat's use-case list and the Clef sees it as a route option. With *Only fill the gray* it does not enlarge anything: it serves the gray holes of the 3D render — and in that case it **also improves quality**, because the 3D render comes out soft (few points): besides filling the gray, the model redraws the whole image with more detail, at **1536** (~2.2 MP) instead of the render's size (~1 MP). If it still looks soft, what controls that is the `resolution` of that option in `defaults/prompts/edit.yaml` (2048 = more detail and more time); when the canvas **grows**, that number must stay at `0`, otherwise the piece pasted back ends up in the wrong place.

**PickFlow learns from your feedback.** Under every result there is a *Was that what you wanted?* question; how the history feeds into the next decisions is in [clef.md](clef.md#pickflow-learns-from-your-feedback).

**Fast or Quality mode.** Next to the text field there is a mode selector (*automatic*, *fast*, *quality*)
that applies to the workflows that have both: what you pick rules every step of that request, including inside
use cases, and the mode question is no longer asked to the Clef. In *automatic* the Clef decides (Fast, unless
you ask for maximum quality). Each workflow also appears in the workflow selector with the mode pinned (*Name — Fast*,
*Name — Quality*). **Optional images you do not send are removed from the graph** (ComfyUI loads and processes nothing
for them), so they do not slow the workflow down. Workflows that need a box or a mask **pause** and open a
drawing on screen (the wait is up to 15 minutes).

**Generate from scratch (Quality)** and **Remove background** are ComfyUI's official models (with a *subgraph*). PickFlow
"flattens" the subgraph when converting, so they work like the others. In Generate from scratch the built-in prompt rewriter is
discarded: whoever writes the prompt is the Clef (or the optional writer in Settings).

## LoRAs: one workflow, several functions

Instead of one workflow per LoRA (body, head, several bodies…), **Edit** is a single one and the **LoRA chain is built on
every request**:

```
the workflow's fixed stack  →  LoRAs of the chosen prompt  →  LoRAs the Clef added
(realism, consistency)         (e.g. swap_people turns on bfs_body)   (per the request and the descriptions)
```

Each LoRA lives in the **LoRAs** tab (`defaults/loras.yaml` + `custom/loras.yaml`) with:

- **Description**: what the Clef reads to decide whether to turn it on. You can rewrite it.
- **Strength** and **text strength**; 0 turns it off.
- **Default prompt**: the instruction that always goes with the LoRA (e.g. `body_swap: …`); it can be one per mode.
- **Changes other LoRAs / workflow settings**: e.g. turning on the body swap drops the realism one to 0 and raises the resolution.
- **Group** (only one per group, e.g. *swap*), **minimum images**, and whether the **Clef may add it by itself**.
- **Conflicts**: ids of LoRAs that do not work together with this one (both ways). Turning one on turns the other off
  and the chat says so; a LoRA that only the Clef suggested never knocks out one that the chosen prompt already uses. E.g.: *Swap clothing or
  tattoo* × *Consistency*.

The limit of LoRAs the Clef adds per request is `clef.max_loras` (default 2). Each candidate LoRA becomes a question
(*use / do not use*) with its description, so the result appears in *How Clef decided*.

**Prompt assembly.** Without an LLM: PickFlow joins the text of the chosen prompt, the instructions of the LoRAs the Clef
added and your request. In the prompt text, `{loras}` marks where the instructions of **all** the active LoRAs go and
`{request}` where the request goes. With the **writer** turned on (Settings), an LLM of yours receives that draft, the LoRA descriptions
and the request, and returns the final prompt (*refine* mode; *translate* mode just turns the request into English). If the writer
fails, the draft is used.

**Modes and settings.** In `registry/<id>.yaml`, `modes:` switches file, outputs and chain per mode; `knobs:` names the
parameters that LoRAs and prompts may change (`resolution`, `scene_mp`, `steps`…, with `off` to ignore a node). Pinned route:
`edit@quality`. In use cases, each step accepts `mode:` and `loras:`.

**Adding your own LoRA.** *LoRAs* tab → **New LoRA** (or click a file that ComfyUI has and that is not described yet). Write the description; if you want, a default prompt and "the Clef may add it by itself". To make it always enter
a certain prompt, put the id in *LoRAs and settings of this prompt* (`id`, `id=0.6`, `id=0.6@turbo`). A LoRA of yours may have adult
content: it and its prompts stay only in `custom/`.

**Downloading a LoRA from a Hugging Face link.** *LoRAs* tab → **Download from Hugging Face**: paste the model page link
(or the link of a `.safetensors` file), choose the file and click download. It goes to ComfyUI's `models/loras/` and is already
registered in the library (write the description afterwards). Restricted repositories or ones with a license to accept ask for a key:
create a read token at <https://huggingface.co/settings/tokens>, accept the license on the model page and write
`HF_TOKEN=your_token` in a **`.env`** file in the PickFlow folder (there is a template in `.env.example`; git ignores `.env`).
Restart PickFlow. The key is only sent to Hugging Face.
