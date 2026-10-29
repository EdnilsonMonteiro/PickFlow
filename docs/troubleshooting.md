# Troubleshooting

If you cannot find your case here, open **Settings → Clef** and **Settings → ComfyUI → Test**: almost every error says what is missing.

| Symptom | What to do |
|---|---|
| **ComfyUI is down** | Open ComfyUI. In *Settings → ComfyUI → Test* PickFlow tries to find the right port. |
| **Nodes not installed: DrawBox, ViggleTurbo…** | *Settings → ComfyUI → Open the setup assistant* (or `install_nodes`) and restart ComfyUI. Other missing nodes: update ComfyUI. |
| **`'x.safetensors' is not installed`** (yellow notice) | The file still has to be downloaded, or it has another name. The assistant shows what is missing and downloads it. |
| **Download with an error or "unavailable"** | The author changed or removed the file, or it requires a login. Open the *author's page* in the list and adjust the URL in `custom/models.yaml`. |
| **Value not in list / prompt outputs failed validation** | ComfyUI refused the graph: the error text names the node. Run *Check workflows*. |
| **It froze when running the Clef and the Qwen together** | VRAM/RAM is short. If the Clef is on the GPU: *Unload Clef before generating the image* on *Automatic* and start the Clef in **int4** (`start_clef`). If it is on the CPU (bf16 only, ~18 GB of RAM): check the free RAM with ComfyUI open, or move the Clef to the GPU in int4. |
| **The Clef is slow to answer every time** | It is being loaded on every request: in BF16 on the GPU (18 GB) it does not fit next to the Qwen on a 24 GB card. Start it in **int4** (`start_clef`) or use `--device cpu` if you have RAM to spare (the decision then takes 1 to 3 s, without reloading the model). |
| **"This Clef server cannot switch devices"** | The server in use has no `POST /v1/device`: start it with `--device cpu` or `--device cuda` (the one in `clef_server/` accepts it; `start_clef` already does this). The setting still applies to the memory policy. |
| **"Clef is not answering"** | The Clef server is not up or the address is wrong (*Settings → Clef*). Start `start_clef`, or use test mode just to see the interface. |
| **The workflow will not run unless I draw** | Normal: Move/Replace/Transfer pause for you to mark the area. |
| **I changed things and want the default back** | *Workflows and prompts* → **Restore default**. |

PickFlow only listens on `127.0.0.1` and refuses requests coming from other sites. **Do not expose the port to the network**: there is no login.
