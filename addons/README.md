# addons/ — your own extensions

This folder is ignored by git (see `.gitignore`): what you create here stays only on your machine and `git pull`
never touches it. Each addon is a folder `addons/<name>/`. The file priority order is
**`custom/` > `addons/` > `defaults/`**.

Create a new addon from the template:

```
python -m pickflow addon new my-addon        # or: Settings > Addons > Create addon
```

Restart PickFlow. The addon shows up in **Settings > Addons** (where you also turn it on and off). Python code and screens only
apply after a restart; the addon's workflows, prompts and LoRAs apply immediately.

## What an addon can have

```
addons/my-addon/
├── addon.yaml          title, version, default config, own LLM (optional)
├── addon.py            register(ctx): hooks, actions, routes, LLM, data
├── registry/           technical wiring of new flows (same format as defaults/registry)
├── workflows/          ComfyUI .json files of those flows
├── prompts/            prompts of the flows (same format as defaults/prompts)
├── loras.yaml          new LoRAs
├── clef_prompt.yaml    description of the flows for the Clef, use cases (recipes), instructions_append
└── web/                main.js, style.css (loaded automatically): new interface
```

Everything is optional. Resources alone (no `addon.py`) are already a valid addon: a new ComfyUI flow, for example.

## addon.py: `register(ctx)`

| `ctx.…` | What for |
|---|---|
| `@ctx.hook("request")` | changes the request text before the Clef reads it (`def f(text, info)` → new text) |
| `@ctx.hook("prompt")` | changes each step's final prompt before ComfyUI (`info`: workflow, preset, mode, vars, loras, request) |
| `@ctx.hook("plan" \| "step_done" \| "run_done")` | observers (log, notices, history) |
| `@ctx.action("name")` | flow written in Python, without ComfyUI; wired by a registry with `kind: action` and `action: <addon>.name` |
| `ctx.llm` | the LLM from Settings: `await ctx.llm.ask(text, system=…, images=[png])`, `ask_json(…)`, `.configured` |
| `ctx.router` | your own FastAPI routes, under `/api/addons/<name>/…` |
| `ctx.config` | `addon.yaml > config` + `settings.yaml > addons.config.<name>` |
| `ctx.load_data()` / `ctx.save_data(d)` | addon data that survives restarts (`data/addons/<name>.json`) |
| `ctx.store`, `ctx.app`, `ctx.log(msg)` | access to the Store, to FastAPI and to the log |

Hooks can be `def` or `async def`. An error in a hook is logged and ignored; an addon that breaks while loading is
turned off and the error shows up in Settings > Addons.

### Actions (new flows in Python)

`registry/my_flow.yaml`:

```yaml
id: my_flow
kind: action
action: my-addon.my_action
title: My flow
images: [{key: image_1, label: Image, required: true}]
params: []        # boxes, positions, mask: the same types as the other flows; they arrive in call.drawings
```

`addon.py`:

```python
@ctx.action("my_action")
async def my_action(call):           # call: ActionCall
    im = call.pil("image_1")         # call.images["image_1"] = PNG bytes
    await call.status("Working…")                # message in the conversation
    choice = await call.ask("my_kind", title="Which?", options=["a", "b"])   # opens the screen registered in the JS
    return [im.rotate(90, expand=True)]        # PNG bytes, PIL.Image or a list
```

`call` also carries `request`, `vars`, `drawings`, `prompt`, `llm`, `settings`. For the Clef to choose the flow, describe it
in `clef_prompt.yaml > workflows > my_flow > description`.

## web/main.js: `window.PickFlow`

```js
const CF = window.PickFlow, h = CF.h;                      // h(tag, attrs, ...children) creates elements (no innerHTML)
CF.addButton({ label, title, onClick })                    // next to "Attach image"
CF.addResultAction({ label, onClick(image) })              // on every result image
CF.addView({ id, title, mount(container) })                // new screen in the menu
CF.addSettings({ id, title, mount(container) })            // block in Settings > Addons
CF.registerInput('my_kind', async (event) => value)        // screen requested by Python (call.ask); null cancels
CF.on('run:end', ({images}) => …)                          // 'ready', 'view', 'run:event', 'run:end'
CF.addonApi('my-addon', '/route', { method, body })        // calls the addon's routes
CF.config(name) / CF.saveConfig(name, obj)                 // the addon's configuration
CF.modal({ title, body, buttons }) · CF.toast(text) · CF.api(path, opts)
CF.setText(text) · CF.attach(file) · CF.send({ text })     // drive the conversation
CF.boxEditor({ img, color, multiple, … })                  // PickFlow's box editor, for your own screens
CF.state                                                   // conversation, attached images, settings, workflows (read-only)
CF.lang · CF.tr(text)                                      // interface language ('en' | 'pt') and translation through the catalog
```

Text coming from outside goes into the DOM with `textContent`/`h()`, never `innerHTML`. The interface opens in English: PickFlow translates
on screen what is in the `web/locales/en.json` catalog; for your addon's own texts, write directly in whatever language you like or
choose by `CF.lang` (what is not in the catalog shows up as written).

## Good practices

- Prefix ids (`my_addon_…`), CSS classes and routes with the addon's name.
- Personal content (prompts, LoRAs) stays here or in `custom/`, never in `defaults/`.
- `python -m pickflow addon new` and the **pickflow-addons** skill (`.claude/skills/pickflow-addons`) show the whole
  path; Settings > ComfyUI > Check workflows verifies that the addons' actions exist.
