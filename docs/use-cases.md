# Use cases and prompts with variables

Sequences of workflows, how the Clef chooses between swapping and pasting, and prompt variables.

## Default flow and fallback: swap person and swap clothing

There are two ways to swap a person, and **one has priority**: **Swap person** (AusBoss) is the default; the **editor** (the
*Swap people* prompt, with the BFS LoRAs) is the **fallback** for what the other does not do well. After choosing the route, the Clef answers
**one question** ("is this the normal case or an exception?") and only for exceptions does the request go to the editor:

| Exception | Who gets the request |
|---|---|
| **Busy or covered face**: drinking from a can, eating, a hand or object in front of the face | editor |
| **Cut-off body** and the request swaps the body (only the lower part shows, the body leaves the photo) | editor. *Head only* with a cut-off body stays in the new flow |
| Swapping **only the body** (the scene's face stays) | editor |
| **Several people** at once, or an extra image with the **place** | editor |

The normal option weighs in favor of the new flow (`clef.engine_main_weight`, 1.5) and the exception only wins with at least 50%
confidence (`clef.engine_min`): when in doubt, the new flow stays. In *Swap clothing* the only exception is **tattoo** (alone or with
clothing), which stays with the editor. Whoever picks the workflow in the selector next to the text field skips this question. To
change the rule, edit `fallback` in `registry/troca_pessoa.yaml` and `registry/troca_roupa.yaml` (the question, the options and each one's `when`,
in English: it is what the Clef reads) in a copy under `custom/registry/`.

The prompt variables: **Who to swap** (the person, the woman, the man, and "on the left", "on the right" or "in the middle") becomes the phrase
the flow uses to find the person; **What to swap** (body and head, or head only). There is no extra-instructions field. The use
cases *Swap the person and the background* and *Change the person's angle and fix the face* use the new flow. Person-swap requests with
2 images that the Clef sent to transfer-object ("swap her for him, face and body") also go to it now.

## Taking someone or something from one image to another: transfer × paste

Two images and "put this in that photo" can go two ways, and they serve different things (tested with real generations,
31 requests):

| The request | Route | Why |
|---|---|---|
| **In place of** someone or something ("the joker in place of the guy in red", "swap the horse for the pawn") | **Transfer object** | redraws inside the green box and removes what was there; pasting left the old one underneath |
| Someone **holds** or uses an object from the other image ("make batman hold the scissors") | **Transfer object** | redraws the hand holding it; the general editor invented another pair of scissors |
| **Adding** a person in a place, even with a new pose ("batman sitting on the bench", "Frieren on the beach") | **Paste and blend** | you choose place and size, the face stays identical and the blend completes the body and the pose |
| Putting an **object** on a surface ("the scissors on top of the table") | **Paste and blend** | it stays identical; transfer also works |
| Copying style | **Editor** | |
| Swapping the **person** (body and/or face) or the **clothing** | **Swap person** / **Swap clothing** | with the editor as fallback in exceptions (see above) |
| Swapping the person **using the clothes from the reference image** ("swap the person for the one in image 2 with her clothes") | **Dress with the reference outfit, then swap the person** | dresses the base image first and swaps on top: the clothing comes out much better than with a single swap. Without mentioning clothes in the request — and always in a face-only swap — it stays **Swap person** |

How the Clef chooses: with two images, when the route falls between transfer, paste, blend or the editor, it answers a
**short question** ("does the brought thing replace something, is it held, is it added or is it another edit?"), which got 28 of 29
phrases right in the tests (choosing among all the routes got "in place of" and "hold" wrong). The answer also tells transfer whether
what is in the green box **leaves** or **stays**. In pasting, if the person's cutout touches the photo's edge (a bust, no
legs), the blend automatically starts to **complete the body**. Images with a transparent background go to ComfyUI over white (ComfyUI
would turn them black).

**Dressing with the clothes from another image and also changing place.** With 3 images — the person, a photo of the clothes alone (the piece on a
hanger, on a bed, a product photo: **nobody wearing it**) and the place — the use case *Dress the person and take her to another
place* dresses the person in those clothes and then puts her in the chosen scene (swap clothing → cut out → place →
blend). It exists because that request was being confused with *Swap the person and the background* (which swaps the person, not the clothing): the
image analysis now tells **clothing alone** apart from a person, and the two descriptions exclude each other. With 2 images, it is
*Swap clothing* anyway.

**Swapping the person, keeping the scene's clothes and changing place.** With 3 images — the scene (pose **and clothes**), the reference
person (face and body only) and the place —, when the request says to use the scene's clothes ("swap the person from image 2 to
make the pose of image 1, using the clothes of image 1, taking a selfie in the mirror of image 3"), the use case *Swap the person, keep the scene
clothing and take her to another place* does: swap body and head → swap clothing (the scene's as the
reference, because the swap brings the reference person's clothes) → cut out → place → blend. Without mentioning the clothes in the
request, it stays *Swap the person and the background*; the two descriptions point at each other.

## How the Clef understands multi-image cases

1. **What is in each image.** With 2 or more images, the first question to the Clef is, for each one, *person, place without
   people, **clothing alone** (the piece, nobody wearing it), object or other*. The label goes along with all the following
   questions, so the user does not need to say "scene" or "new background" — and "wear the clothes from image 2" does not turn into a person swap
   just because the sentence says "wearing". (Turn it off with `clef.analyze_images: false`.)
2. **Exact count.** Each use case declares `images:` (e.g. *Paste and blend* = 2, *Swap the person and the background* = 3);
   with another count it is not even offered to the Clef. This removes the confusion between the two.
3. **Second look.** If the route falls below `clef.refine_below` (0.6), the Clef compares only the 3 most likely
   candidates, already knowing how many images there are and what they show. Only then, if there is still doubt (`clef.ask_below`), it asks you.
4. **Roles.** A use case may declare `roles:` (e.g. scene, person, background); the Clef answers one question per role
   ("which image is the reference person?"), respects "the person from image 3", "the scene is image 1" and never uses the same
   image in two roles. Steps use `role:<role>` in their inputs; when unsure, the upload order applies.

## One prompt, several cases (variables)

A prompt can declare `vars`: multiple-choice questions that the Clef answers (it does not write text; PickFlow builds
the text from the answer, making your decision explicit in the final prompt). There are three kinds:

- `count` — how many people/items. The **Swap people** prompt swaps the first N people of image 1, **from left
  to right**, for the people in the following images (image 2, 3, 4…). If you send 4 images, the Clef may answer
  from 1 to 3; never more than the images that exist.
- `choice` — one option among several, each with its own text, LoRAs and settings. In *Swap people*: **body and head**
  (default), **body only** or **head only**, and **with or without a place image**. In *Swap clothing or tattoo*: clothing,
  tattoos or both.
- `angle` — camera rotation for the orbit LoRA (*Turn the point of view*): read from the request ("90 degrees to the right",
  "from behind", "from above"). If the request does not give the angle, a **screen with the image** opens to choose side, angle
  (45, 90, 135, 180) and height. It works best with objects.

In the text use `{key}`; counts also give `{key_map}` (who becomes who), `{key_refs}`, `{key_next}` and `{key_many}`
(the variable's `many_text`, which only goes in with **more than one** person). That is why, with **one person**, the *Swap
people* prompt keeps only the LoRA instructions (`body_swap: …` and/or `head_swap: …`, depending on what is swapped; in body and head
both, with the head LoRA at 1); the "1st person from the left…" map and the "Replicate the exact pose…" sentence only appear with
several people. With
`slots: order` the images go into the inputs in the order they were sent. Variables are edited on the prompt's
card, in *Workflows and prompts*.

## Agent: use cases that PickFlow creates by itself

The Clef only chooses among flows and use cases that already exist (plus one extra step afterwards). A request such as "make the
person in image 2 colored and with better quality, then put her in the scene of image 1" needs its own
sequence, with image 2 going through a step **before** entering the cutout and the blend. That is what the agent is for
(**Settings > Writer and agent > Use case agent**, off by default; it uses the writer's LLM):

1. After choosing the route, the Clef answers an extra question: does the plan cover the whole request or is a step missing?
   Only on "missing" (and confidence above `agent.gap_min`) does the agent wake up. You can also call it by hand in the
   chat's workflow selector ("Agent: build a new use case").
2. The LLM works in rounds, like an agent: it consults the catalog (workflows, prompts, LoRAs, existing cases), asks the
   Clef what depends on seeing the images, sends drafts and gets the errors back until it delivers a valid case.
3. The case goes through the same rules as the Use cases screen (workflows, inputs, prompts, modes and existing LoRAs, number
   of images, maximum steps). A step may have its **own instruction** (`request`): "Colorize this photo...", instead
   of receiving the whole request.
4. Limits: cap on LLM answers and on Clef queries per creation; a case with exactly the same steps as an existing one is
   reused; the id is always new (it never overwrites); you confirm before saving (`agent.confirm`); the created ones
   stay **on trial** in `custom/generated_recipes.yaml` (outside git) and the oldest leaves once `agent.max_trial` is exceeded, unless
   you click **Keep**; on reaching `agent.max_generated` kept ones the agent stops.

**Repeated use cases.** In Use cases, **Check for repetition** asks the Clef for a 0 to 100% score for each pair (same
case, similar or different, added to the comparison of the steps) and shows the pairs from most to least repeated. **Merge
both** (or the group) asks the LLM for a single more general case, usually using roles instead of the fixed image order. You
review the proposal; only after **Apply the merge** is anything saved, and the originals are disabled (or deleted, if you
choose so).
