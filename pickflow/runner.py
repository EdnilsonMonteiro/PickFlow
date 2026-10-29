"""Executa um turno do chat: planeja com o Clef, liga as imagens, roda no ComfyUI e encadeia."""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import math
import inspect
import io
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from . import llm as llmcfg
from . import quality as picquality
from . import writer
from .clef import ClefClient, ClefError, MockClef
from .comfy import ComfyClient, ComfyError
from . import encoders
from .families import DEFAULT as DEFAULT_MODEL
from .feedback import FeedbackStore, similarity
from .memory import Memory, add_clef, from_stats, should_free_comfy, should_unload
from .converter import Converter, ConversionError, node_id, widget_list
from . import pvars
from .loras import compose, describe_stack, resolve
from .router import Binding, Ctx, ImageRef, Router, RouterError, Source, Step, needs_drawing
from .cutout import merge_person_cutouts
from .storage import (ConvStore, ImageStore, NoTransparency, crop_fraction, cut_edges, cutout_state, flatten_alpha, mask_to_alpha_png,
                      to_png, trim_to_alpha)
from .addons import ActionCall, AddonManager, LLM
from .compose import (blend_masked, burn_boxes, clamp_frac, expand_spec, fit_megapixels, gray_fraction, pad_gray, paste_back,
                      place_layers, region_change)
from . import pose as poselib
from .splats import SplatStore, splat_key, view as splat_view
from .store import Store, StoreError
from .recipe_agent import RecipeAgent
from .router import PlanResult


INPUT_TIMEOUT_S = 900


class RunCancelled(Exception):
    pass


@dataclass
class Run:
    id: str
    conv_id: str
    events: list[dict] = field(default_factory=list)
    finished: bool = False
    cancelled: bool = False
    inputs: dict[str, asyncio.Future] = field(default_factory=dict)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    pose_reference: bytes | None = None  # "Mudar a pose": a foto sem os membros que mexeram (o remover apagou o lugar antigo)
    pose_source: bytes | None = None     # a foto original e a máscara de onde ela pode mudar: o resto do resultado volta a ser o pixel dela
    pose_mask: Image.Image | None = None
    task: asyncio.Task | None = None     # a tarefa desta execução: cancelar é derrubar isto (aborta o que está em espera)
    phase: str = ""                      # "clef" (decidindo, pode estar carregando o modelo) | "steps" (gerando)

    async def emit(self, ev: dict):
        async with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    async def finish(self):
        async with self.cond:
            self.finished = True
            self.cond.notify_all()

    async def stream(self):
        i = 0
        while True:
            async with self.cond:
                await self.cond.wait_for(lambda: i < len(self.events) or self.finished)
                batch = self.events[i:]
                i = len(self.events)
                done = self.finished
            for ev in batch:
                yield ev
            if done and i >= len(self.events):
                return


def make_clef(settings: dict):
    c = settings["clef"]
    if c.get("mode") == "mock":
        return MockClef()
    return ClefClient(c["url"], c.get("path", "/v1/systemone"), c.get("model", "clef"),
                      float(c.get("timeout_s", 120)), int(c.get("max_images", 4)), c.get("api_key", "") or "")


class Pipeline:
    def __init__(self, store: Store, images: ImageStore, convs: ConvStore, addons: Any = None):
        self.store, self.images, self.convs = store, images, convs
        self.addons = addons or AddonManager(store)          # sem addons carregados, os ganchos não fazem nada
        self.llm = LLM(lambda: self.store.settings().get("writer", {}))      # o LLM de Ajustes (redator, agente, fusão)
        self.splats = SplatStore(store.data_dir)            # modelos 3D (data/splats)
        self.feedback = FeedbackStore(store.data_dir)       # feedback do usuário: casos parecidos e correção
        self.runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task] = set()
        self.lock = asyncio.Lock()
        self._comfy: ComfyClient | None = None
        self.view = store                                   # o Store visto pelo modelo do turno (`_turn` troca)

    # ------------------------------------------------------------------ comfy
    async def comfy(self) -> ComfyClient:
        s = self.store.settings()["comfyui"]
        if self._comfy is None or self._comfy.base_url != s["url"].rstrip("/"):
            self._comfy = ComfyClient(s["url"], self.store.data_dir, s.get("upload_subfolder", "pickflow"),
                                      float(s.get("timeout_s", 1800)))
        return self._comfy

    # -------------------------------------------------------------- run mgmt
    def start(self, conv_id: str, text: str, image_ids: list[str], route: str | None,
              mode: str | None = None, model: str | None = None) -> Run:
        run = Run(uuid.uuid4().hex[:12], conv_id)
        self.runs[run.id] = run
        task = asyncio.get_running_loop().create_task(self._guarded(run, text, image_ids, route, mode, model))
        run.task = task                                    # cancelar = derrubar esta tarefa
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def active_run(self, conv_id: str) -> str | None:
        for r in self.runs.values():
            if r.conv_id == conv_id and not r.finished:
                return r.id
        return None

    def cancel(self, run_id: str):
        """Cancela de verdade: derruba a tarefa, o que aborta **o que estiver em espera agora** — inclusive uma
        chamada ao Clef que pode estar levando 30 s só para carregar o modelo. Antes só uma bandeira era levantada,
        e essas esperas (Clef carregando, ComfyUI na fila) não olhavam para ela: o botão Cancelar ficava sem efeito
        até a resposta chegar."""
        run = self.runs.get(run_id)
        if not run or run.finished:
            return
        run.cancelled = True
        for f in run.inputs.values():
            if not f.done():
                f.cancel()
        if run.task is not None and not run.task.done():
            run.task.cancel()

    def answer(self, run_id: str, input_id: str, value: Any):
        run = self.runs.get(run_id)
        fut = run.inputs.get(input_id) if run else None
        if not fut or fut.done():
            raise KeyError(input_id)
        fut.set_result(value)

    async def _guarded(self, run: Run, text: str, image_ids: list[str], route: str | None, mode: str | None = None,
                       model: str | None = None):
        try:
            if self.lock.locked():
                await run.emit({"type": "status", "text": "Na fila: espero a execução anterior terminar…"})
            async with self.lock:
                await self._turn(run, text, image_ids, route, mode, model)
        except (asyncio.CancelledError, RunCancelled):
            if run.phase == "steps":
                try:
                    await (await self.comfy()).interrupt()          # sem isto o ComfyUI continuaria desenhando sozinho
                except Exception:  # noqa: BLE001
                    pass
            note = ""
            if run.phase == "clef":
                # a espera aqui foi abortada na hora; a carga do modelo roda no processo do servidor do Clef e não
                # pode ser interrompida pela metade (é um from_pretrained)
                note = (" O Clef termina de carregar o modelo no processo dele (não há como interromper uma carga pela"
                        " metade); para liberar a memória use «Descarregar o Clef agora» em Ajustes > Clef.")
            await run.emit({"type": "error", "message": "Execução cancelada." + note})
        except (RouterError, ClefError, ComfyError, ConversionError, StoreError, ValueError) as e:
            await run.emit({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            await run.emit({"type": "error", "message": f"Erro inesperado: {e.__class__.__name__}: {e}"})
        finally:
            self._persist(run)
            await run.finish()

    def _persist(self, run: Run):
        try:
            conv = self.convs.get(run.conv_id)
        except KeyError:
            return
        evs = [e for e in run.events if e["type"] not in ("progress",)]
        imgs = [i for e in evs if e["type"] == "done" for i in e.get("images", [])]
        conv["messages"].append({"role": "assistant", "events": evs, "images": imgs})
        self.convs.save(conv)

    # ------------------------------------------------------- feedback implícito
    @staticmethod
    def _route_of(plan: PlanResult) -> tuple[str, float, str]:
        """A rota final do plano, a confiança e quem a escolheu.

        O `plan.route` (vindo do roteador) é a rota que VAI RODAR: já passou pela 2ª análise e pela correção do
        histórico. Antes o runner procurava a rota nas decisões e perdia justamente essas duas trocas, então o
        julgamento ia para a rota que o Clef tinha dado primeiro. A confiança e a origem saem da última decisão que
        falou da rota. Origem `user` (a pessoa forçou) ou `agent` (o agente montou o caso) não ensinam a rota ao
        histórico: o Clef não escolheu ali.
        """
        route = (plan.route or "").partition("@")[0]
        conf, source = 0.0, ""
        for d in plan.decisions:
            if d.get("question") in ("route", "route_2", "route_learn", "route_intent", "route_fallback"):
                conf, source = float(d.get("confidence") or 0.0), str(d.get("source") or "")
                if not plan.route:                       # plano sem `route`: vale a ÚLTIMA decisão da rota
                    route = str(d.get("chosen") or "").partition("@")[0]
        origin = "user" if source in ("user", "único") else ("history" if source == "histórico" else "clef")
        return route, conf, origin

    @staticmethod
    def _prev_done(conv: dict) -> dict | None:
        """A última execução antes desta: o pedido, a rota e o id do run (para o feedback implícito).
        Só considera uma execução que chegou ao fim (`done` com `run`), senão não há o que comparar."""
        msgs = conv.get("messages") or []
        for i in range(len(msgs) - 1, -1, -1):
            m = msgs[i]
            if m.get("role") != "assistant":
                continue
            done = next((e for e in m.get("events", []) if e.get("type") == "done" and e.get("run")), None)
            if not done:
                continue
            text = ""
            for j in range(i - 1, -1, -1):
                if msgs[j].get("role") == "user":
                    text = msgs[j].get("text") or ""
                    break
            dec = done.get("decision") or {}
            return {"run": done["run"], "text": text, "route": dec.get("route") or "",
                    "preset": (dec.get("presets") or [""])[0], "confidence": float(dec.get("confidence") or 0.0)}
        return None

    async def _implicit_feedback(self, run: Run, text: str, conv: dict, route: str) -> None:
        """Sinal implícito, sem incomodar ninguém: pedido quase igual ao anterior e rota DIFERENTE é "não era
        isso" (conta como erro da rota anterior); mesma rota é ajuste fino e não conta. Pedido de outro assunto
        não diz nada. Ninguém é obrigado a opinar — o silêncio nunca vira acerto."""
        try:
            prev = self._prev_done(conv)
            if not prev or not (text or "").strip() or not (prev["text"] or "").strip():
                return
            if similarity(prev["text"], text) < 0.8:
                return
            verdict = "same" if prev["route"] and route == prev["route"] else "redo"
            self.feedback.add(run.conv_id, prev["run"], prev["text"], prev["route"], prev["preset"],
                              prev["confidence"], verdict, "", verdict)
        except Exception:  # noqa: BLE001 - aprender é um extra: nunca derruba o pedido
            pass

    async def _shrink(self, run: Run, users: list[ImageRef], max_mp: float) -> list[ImageRef]:
        """Fotos grandes (12 MP de celular) viram no máximo `max_mp` megapixels antes de tudo: com 'resolution: 0' o
        ComfyUI usaria o tamanho original em cada referência e na saída, e uma troca de poucos segundos levaria minutos."""
        out, cut = [], []
        for im in users:
            data, (w, h) = fit_megapixels(im.data, max_mp)
            if data is not im.data:
                cut.append(f"{im.width}x{im.height} → {w}x{h}")
                im = dataclasses.replace(im, data=data, width=w, height=h)
            out.append(im)
        if cut:
            await run.emit({"type": "status", "text": f"Reduzi imagem grande para até {max_mp:g} MP ({'; '.join(cut)}): "
                                                      "mais rápido e sem perder o que o modelo aproveita. Ajustes > ComfyUI muda o limite."})
        return out

    @staticmethod
    def _measure_images(users: list[ImageRef]) -> list[ImageRef]:
        """Mede tamanho e nitidez de cada foto (PIL, sem numpy). O Clef lê isso no `state` e decide com informação
        em vez de adivinhação — é o que separa "restaurar esta foto pequena" de "melhorar esta foto grande"."""
        out = []
        for im in users:
            try:
                out.append(dataclasses.replace(im, quality=picquality.describe(picquality.measure(im.data))))
            except Exception:  # noqa: BLE001 - medir é um extra: sem a medida o pedido segue igual
                out.append(im)
        return out

    async def ask_user(self, run: Run, payload: dict) -> Any:
        iid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        run.inputs[iid] = fut
        await run.emit({"type": "needs_input", "input_id": iid, **payload})
        try:
            return await asyncio.wait_for(fut, INPUT_TIMEOUT_S)
        except asyncio.CancelledError:
            raise RunCancelled() from None
        except asyncio.TimeoutError:
            raise RouterError("Ninguém respondeu à pergunta a tempo; a execução foi encerrada.") from None

    # ---------------------------------------------------------------- memória
    async def _measure(self, comfy, clef) -> Memory:
        try:
            mem = from_stats(await comfy.stats())
        except Exception:  # noqa: BLE001
            mem = Memory()
        try:
            mem = add_clef(mem, await clef.health())
        except Exception:  # noqa: BLE001
            pass
        if not mem.clef_device:                     # servidor que não informa o device: vale o que o usuário escolheu
            dev = str(self.store.settings()["clef"].get("device") or "").strip().lower()
            mem.clef_device = "cpu" if dev.startswith("cpu") else ("cuda" if dev.startswith(("gpu", "cuda")) else "")
        return mem

    async def _make_room_for_clef(self, run: Run, comfy, clef, settings: dict):
        """Antes de decidir: se o Clef não está carregado e o ComfyUI ocupa a VRAM, libera o ComfyUI."""
        c = settings["clef"]
        if not c.get("make_room_for_clef", True) or getattr(clef, "mode", "") != "systemone":
            return
        mem = await self._measure(comfy, clef)
        free, why = should_free_comfy(mem, float(c.get("clef_vram_gb", 20)), float(c.get("margin_gb", 2)))
        if free:
            await comfy.free()
            await run.emit({"type": "memory", "text": why})

    async def _clef_memory_before_generation(self, run: Run, comfy, clef, settings: dict, manifest: dict):
        """Depois de decidir (e antes de desenhar/gerar): descarrega o Clef se a memória não comporta os dois."""
        c = settings["clef"]
        policy = c.get("unload_policy", "auto")
        if policy == "never" or getattr(clef, "mode", "") != "systemone":
            return
        mem = await self._measure(comfy, clef)
        need = manifest.get("mem") or {}
        go, why = should_unload(policy, mem, float(need.get("vram_gb", c.get("need_vram_gb", 16))),
                                float(need.get("ram_gb", c.get("need_ram_gb", 10))), float(c.get("margin_gb", 2)))
        if go:
            await clef.unload()
        if why:
            await run.emit({"type": "memory", "text": why})

    # ------------------------------------------------------------------ turn
    async def _turn(self, run: Run, text: str, image_ids: list[str], route: str | None, mode: str | None = None,
                    model: str | None = None):
        settings = self.store.settings()
        self.view = self.store.for_model(model)          # o modelo de imagem deste turno (um turno por vez: veja `lock`)
        run.phase = "clef"                     # daqui até o plano: o Clef pode estar carregando o modelo (30 s+)
        conv = self.convs.get(run.conv_id)
        users = []
        for iid in image_ids:
            info = self.images.info(iid)
            users.append(ImageRef(info["id"], info["name"], self.images.load(iid), info["width"], info["height"]))
        users = await self._shrink(run, users, float(settings["comfyui"].get("max_input_mp", 2.0)))
        users = self._measure_images(users)
        history = self._history(conv)
        last = self._last_result(conv)
        if not users and last:
            users = [ImageRef(last["id"], last["name"], self.images.load(last["id"]), last["width"], last["height"])]
            users = self._measure_images(users)
            await run.emit({"type": "status", "text": "Sem imagem anexada: uso o último resultado da conversa."})
        text = await self.addons.request(text, {"images": len(users), "route": route})
        ctx = Ctx(text, users, history)
        clef = make_clef(settings)
        router = Router(self.view, clef, self.feedback)
        comfy = await self.comfy()
        if settings["comfyui"].get("free_vram_before_clef") and str(settings["clef"].get("device") or "").lower() != "cpu":
            await comfy.free()          # com o Clef na RAM não adianta: só faz o ComfyUI recarregar tudo do disco
        await self._make_room_for_clef(run, comfy, clef, settings)
        await run.emit({"type": "status", "text": "Clef está escolhendo o workflow…"})
        if route == "agent":                                    # pedido manual: o agente monta um caso de uso novo
            await router.describe_images(ctx)
            plan = PlanResult([], [], None, {"p": 1.0, "route": "agent", "route_name": "pedido manual", "manual": True},
                              route="agent")
        else:
            plan = await router.plan(ctx, route if route and route != "auto" else None)
        for d in plan.decisions:
            await run.emit({"type": "decision", **d})
        steps = plan.steps
        # A rota que VAI RODAR (é ela que o feedback julga). O `plan.route` já vem com a 2ª análise e com a
        # correção do histórico aplicadas; aqui só falta o que muda depois: o modal de dúvida e o agente.
        route_chosen, route_conf, route_origin = self._route_of(plan)
        if plan.gap:
            built, built_route = await self._agent_plan(run, router, ctx, plan)
            if built:
                steps, plan.ambiguous = built, None
                route_chosen, route_conf, route_origin = built_route, 1.0, "agent"
            elif route == "agent":
                raise RouterError("O agente não conseguiu montar um caso de uso para este pedido. Escolha um workflow "
                                  "na lista ou reformule o pedido.")
        if plan.ambiguous:
            pick = await self.ask_user(run, {"kind": "choose", "title": "O Clef ficou em dúvida. Qual workflow usar?",
                                             "options": plan.ambiguous})
            if pick and pick != "__auto__":
                steps = router.steps_from_route(pick)
                route_chosen, route_origin = pick.partition("@")[0], "user"      # escolha da pessoa, não do Clef
        await self._implicit_feedback(run, text, conv, route_chosen)
        run.phase = "steps"                    # acabou a decisão: daqui para frente é ComfyUI
        await run.emit({"type": "plan", "steps": [
            {"workflow": s.workflow, "title": self.store.manifest(s.workflow).get("title", s.workflow)} for s in steps]})
        await self.addons.observe("plan", [s.workflow for s in steps], {"request": text})

        previous: dict | None = None
        prev_all: list[dict] = []                   # todos os resultados do passo anterior (vários recortes)
        final: list[dict] = []
        presets: list[str] = []                     # "workflow/prompt" de cada passo (vai no evento `done`: feedback)
        extra: list[Source] = []                    # fontes que um passo acrescenta (a foto de onde saiu o 3D: "origin")
        idx = -1
        while idx + 1 < len(steps):                 # o passo 3D pode acrescentar ou tirar o passo seguinte
            idx += 1
            step = steps[idx]
            if run.cancelled:
                raise RunCancelled()
            sources = [Source(f"user_{i + 1}", f"Imagem {i + 1} enviada pelo usuário ({im.name})", im.data,
                              f"/files/{self.images.path(im.id).name}") for i, im in enumerate(users)]
            if previous:
                sources.append(Source("previous", "Resultado do passo anterior", self.images.load(previous["id"]),
                                      previous["url"]))
            sources += [x for x in extra if x.key not in {s.key for s in sources}]
            m = self.store.manifest(step.workflow)
            if len(sources) < sum(1 for s in m.get("images", []) if s.get("required", True)):
                raise RouterError(f"O workflow '{m.get('title')}' precisa de mais imagens do que as disponíveis.")
            sctx = self._step_ctx(ctx, step)                # passo com instrução própria: o Clef e o prompt leem ela
            # O Clef pode precisar CARREGAR aqui no meio do turno: o passo anterior acabou de gerar com o Qwen na
            # placa. Sem liberar a VRAM antes, o modelo do Clef vaza para a memória compartilhada, cada decisão passa
            # de 30 s e o pedido pode estourar o tempo máximo (aconteceu num caso de uso de 4 passos com o Clef em
            # 17,7 GB ao lado do Qwen). `_make_room_for_clef` não faz nada quando o Clef já está carregado.
            await self._make_room_for_clef(run, comfy, clef, settings)
            binding = await router.bind(sctx, step, sources, mode=mode, ask=lambda p: self.ask_user(run, p))
            for d in binding.decisions:
                await run.emit({"type": "decision", "step": idx, **d})
            presets.append(f"{step.workflow}/{binding.preset_id}")
            await self._clef_memory_before_generation(run, comfy, clef, settings, m)
            result = await self._run_step(run, idx, step, m, binding, sources, sctx, settings, prev_all=prev_all)
            if self.store.manifest(step.workflow, binding.mode).get("review") == "cutout":
                result = await self._review_cutout(run, idx, step, m, binding, sources, sctx, settings, result)
            await self._on_partial(run, m, binding, steps, idx, result)
            self._on_pose(steps, idx, result)
            if m.get("splat"):
                src = self._first_source(m, binding, sources)
                if src:
                    extra = [Source("origin", "Foto usada no 3D", src.data, src.url)]
                if self._splat_then(steps, idx, m, result):
                    await run.emit({"type": "plan", "steps": [
                        {"workflow": s.workflow, "title": self.store.manifest(s.workflow).get("title", s.workflow)} for s in steps]})
            previous = result[0]
            prev_all = result
            final = result
            await self.addons.observe("step_done", result, {"workflow": step.workflow, "index": idx})
        if settings["comfyui"].get("free_vram_after_run"):
            await comfy.free()
        await run.emit({"type": "done", "images": final, "run": run.id,
                        "decision": {"route": route_chosen, "confidence": route_conf, "origin": route_origin,
                                     "presets": presets, "request": text}})
        await self.addons.observe("run_done", final, {"request": text})

    async def _on_partial(self, run: Run, m: dict, binding: Binding, steps: list[Step], idx: int, result: list[dict]):
        """`on_partial: {var, value, modes}` no registro de um recorte: se ele saiu cortado (encosta na borda de baixo ou
        dos lados da foto: um busto, uma pessoa sem pernas), os passos seguintes recebem a variável fixada (a fusão
        passa a completar o corpo). Medir o recorte é mais confiável que perguntar ao Clef olhando a colagem."""
        op = self.store.manifest(m["id"], binding.mode).get("on_partial") if m.get("id") else None
        if not op or (op.get("modes") and binding.mode not in op["modes"]):
            return
        edges = sorted({e for r in result for e in (r.get("cut") or []) if e in ("bottom", "left", "right")})
        if not edges:
            return
        for st in steps[idx + 1:]:
            if op["var"] not in (st.vars or {}):
                st.vars = {**(st.vars or {}), op["var"]: op["value"]}
        names = {"bottom": "inferior", "left": "esquerda", "right": "direita"}
        await run.emit({"type": "status", "text": "O recorte encosta na borda " + " e ".join(names[e] for e in edges if e in names)
                        + " da foto (a pessoa está cortada): a fusão vai completar o corpo e a pose."})

    @staticmethod
    def _on_pose(steps: list[Step], idx: int, result: list[dict]) -> None:
        """O passo da pose devolve, junto do mapa, a frase que descreve o que a pessoa mexeu: ela entra no `request` dos passos
        seguintes no lugar de `{pose_change}` (o ControlNet sozinho quase não move ninguém: o texto diz quem muda)."""
        text = (result[0].get("pose_text") if result else None) or None
        if text is None:
            return
        for st in steps[idx + 1:]:
            if st.request and "{pose_change}" in st.request:
                st.request = st.request.replace("{pose_change}", text)

    async def _pose_choose(self, run: Run, idx: int, m: dict, src: Source | None, outputs: dict, ctx: Ctx, settings: dict) -> list[dict]:
        """Mostra o esqueleto de cada pessoa da foto (pontos do DWPose) e deixa a pessoa montar a nova pose. Devolve o mapa de
        pose (todas as pessoas: as que mudaram e as que ficam como estão) como imagem, com a frase do que mudou."""
        if src is None:
            raise RouterError("O passo da pose precisa de uma foto.")
        nodes = [str(n) for n in (m.get("pose") or {}).get("nodes") or [(m.get("pose") or {}).get("node")]]
        w, h = Image.open(io.BytesIO(src.data)).size

        def read(n: str, frames: bool):
            raw = (outputs.get(n) or {}).get("openpose_json") or []
            if not raw:
                return []
            return poselib.parse_frames(raw[0], w, h) if frames else poselib.parse(raw[0], w, h)

        try:
            # 1º nó: um quadro por pessoa que o SAM 3 separou; 2º (se houver): a foto inteira, de reserva
            people = poselib.combine(read(nodes[0], True), read(nodes[1], False) if len(nodes) > 1 else [])
        except (ValueError, TypeError):
            people = []
        if not people:
            raise ComfyError("Não achei nenhuma pessoa nesta foto. Use uma foto em que o corpo (ou parte dele) apareça.")
        value = await self.ask_user(run, {"kind": "pose", "title": "Modele a pose", "image_url": src.url, "size": [w, h],
                                          "people": [p[:poselib.BODY] for p in people]})        # a tela mexe no corpo; as mãos vão atrás do braço
        if not isinstance(value, dict) or value.get("people") is None:
            raise RunCancelled()
        try:
            edited = poselib.clean(value["people"])
        except ValueError as e:
            raise RouterError(f"Pose inválida: {e}") from e
        if len(edited) != len(people):
            raise RouterError("Pose inválida: o número de pessoas mudou.")
        text = poselib.describe(people, edited, w, h)
        if not text or not any(poselib.changed(o, e) for o, e in zip(people, edited)):
            raise RouterError("Nenhuma pose foi alterada: mexa nos pontos de pelo menos uma pessoa e confirme.")
        run.pose_reference = await self._pose_erase(run, idx, src, poselib.erase_boxes(people, edited, w, h),
                                                    poselib.erase_text(people, edited, w, h), ctx, settings)
        full = poselib.with_hands(people, edited, w, h)
        run.pose_source, run.pose_mask = flatten_alpha(to_png(src.data)), poselib.change_mask(people, full, w, h)       # a mesma foto que o modelo viu (PNG com alfa vai achatado em branco)
        res = {**self.images.save(poselib.draw(full, w, h), "mapa_de_pose.png"), "pose_text": text}
        await run.emit({"type": "step_done", "index": idx, "title": m.get("title"), "images": [res], "extras": []})
        return [res]

    async def _pose_erase(self, run: Run, idx: int, src: Source, boxes: list[list[float]], what: str, ctx: Ctx, settings: dict) -> bytes:
        """O ControlNet só desenha o membro na posição nova; o antigo continua na foto de referência e aparece duplicado (testado:
        o braço que abraçava o parceiro ficava). Gerar por cima (máscara do Union, ou o Outpaint em cima de cinza) não resolve: o
        modelo recria o braço, porque a cena "pede" um. O que obedece a "tirar" é o fluxo de remover no quadrado vermelho do PickFlow:
        põe um quadrado sobre cada membro que mexeu e remove, reconstruindo o corpo, a roupa e o fundo atrás."""
        known = self.store.manifests()
        if not boxes or not what or "replace_redbox" not in known:
            return src.data
        await run.emit({"type": "status", "text": "Apagando o lugar antigo dos membros que mexeram…"})
        m2 = known["replace_redbox"]
        modes = m2.get("modes") or {}
        mode = next(iter(modes)) if modes else None
        step2 = Step("replace_redbox", "remover", {"image_1": "previous"}, mode, None, None, what)
        bind2 = Binding("remover", {"image_1": "previous"}, [], mode, [], {})
        res = await self._run_step(run, idx, step2, m2, bind2, [Source("previous", "Foto com os membros antigos", src.data, src.url)],
                                   self._step_ctx(ctx, step2), settings, forced={"red": [{"b": b, "ref": None} for b in boxes]})
        return self.images.load(res[0]["id"])

    @staticmethod
    def _first_source(m: dict, binding: Binding, sources: list[Source]) -> Source | None:
        by_key = {s.key: s for s in sources}
        key = next((binding.slots.get(sl["key"]) for sl in m.get("images", []) if binding.slots.get(sl["key"])), None)
        return by_key.get(key) if key else None

    @staticmethod
    def _splat_then(steps: list[Step], idx: int, m: dict, result: list[dict]) -> bool:
        """Depois do 3D a pessoa escolheu: só o render (o passo que gera a foto sai, se houver) ou a foto completa
        no ângulo (o passo `splat.then` entra logo depois, se ainda não estiver lá). Devolve se o plano mudou."""
        then = (m.get("splat") or {}).get("then")
        if isinstance(then, str):
            then = {"workflow": then}
        if not then or not then.get("workflow"):
            return False
        want = bool(result and result[0].get("then"))
        nxt = steps[idx + 1] if idx + 1 < len(steps) else None
        if want and (nxt is None or nxt.workflow != then["workflow"]):
            steps.insert(idx + 1, Step(then["workflow"], prompt=then.get("prompt"), inputs=dict(then.get("inputs") or {}),
                                       mode=then.get("mode"), loras=list(then.get("loras") or []),
                                       vars=dict(then["vars"]) if then.get("vars") else None, request=then.get("request")))
            return True
        if not want and nxt is not None and nxt.workflow == then["workflow"]:
            steps.pop(idx + 1)
            return True
        return False

    @staticmethod
    def _step_ctx(ctx: Ctx, step: Step) -> Ctx:
        if not step.request:
            return ctx
        return dataclasses.replace(ctx, request=step.request.replace("{request}", ctx.request).strip())

    # ----------------------------------------------------------------- agente
    async def _agent_plan(self, run: Run, router: Router, ctx: Ctx,
                          plan: PlanResult) -> tuple[list[Step] | None, str]:
        """O Clef disse que nenhuma rota cobre o pedido todo: o agente (LLM + Clef) monta um caso de uso novo.
        Devolve `(passos, rota)` — a rota é o `recipe__<id>` que vai rodar (para o feedback saber o que julgar) —
        ou `(None, "")` para seguir com o plano do Clef."""
        cfg = self.store.settings().get("agent") or {}
        manual = bool(plan.gap.get("manual"))
        if not cfg.get("enabled") and not manual:
            return None, ""
        kept = [g for g in self.store.generated_recipes() if (g.get("generated") or {}).get("status") == "kept"]
        if len(kept) >= int(cfg.get("max_generated", 20)):
            await run.emit({"type": "status", "text": f"O agente não cria mais casos de uso: já há {len(kept)} mantidos "
                                                      "(limite em Ajustes > Agente). Apague ou funda alguns em Casos de uso."})
            return None, ""
        await run.emit({"type": "status", "text": (
            "Pedido manual ao agente: ele vai montar um caso de uso." if manual else
            f"O Clef achou que '{plan.gap['route_name']}' não cobre o pedido todo. O agente vai montar um caso de uso novo…")})

        async def say(text: str):
            await run.emit({"type": "status", "text": text})

        built = await RecipeAgent(self.view, self.llm).build(ctx, router, plan.gap, say)
        if built.reuse:
            await run.emit({"type": "status", "text": f"O agente viu que já existe um caso de uso igual ({built.reuse}) e vai usá-lo "
                                                      "em vez de criar outro."})
            route = "recipe__" + built.reuse
            steps = router.steps_from_route(route)
            decisions: list[dict] = []
            await router._roles_for(ctx, route, steps, decisions)
            for d in decisions:
                await run.emit({"type": "decision", **d})
            return steps, route
        if not built.recipe:
            await run.emit({"type": "status", "text": f"O agente não montou um caso novo ({built.reason}). "
                                                      "Sigo com o plano do Clef."})
            return None, ""
        rec = built.recipe
        answer = "save"
        if cfg.get("confirm", True):
            answer = await self.ask_user(run, {
                "kind": "agent_recipe", "title": "O agente montou um caso de uso novo. Usar?",
                "recipe": {"name": rec["name"], "description": rec["description"],
                           "steps": [{"workflow": s["workflow"], "title": self.store.manifest(s["workflow"]).get("title", s["workflow"]),
                                      "prompt": s.get("prompt"), "mode": s.get("mode"), "request": s.get("request"),
                                      "inputs": s.get("inputs") or {}} for s in rec["steps"]],
                           "warnings": built.warnings, "similar": built.similar}})
            if answer is None:
                raise RunCancelled()
        if answer == "skip" or answer == "__auto__":
            await run.emit({"type": "status", "text": "Certo, sigo com o plano do Clef."})
            return None, ""
        saved = answer != "once"
        if saved:
            if self.view.model != DEFAULT_MODEL:            # o agente só enxergou os workflows deste modelo
                rec = {**rec, "models": [self.view.model]}
            rec = self.store.add_generated(rec, ctx.request, "trial", int(cfg.get("max_trial", 5)))
        steps = router.steps_from_recipe(rec)
        decisions = []
        if rec.get("roles"):
            await router.describe_images(ctx)
            await router.resolve_roles(ctx, rec, steps, decisions)
        for d in decisions:
            await run.emit({"type": "decision", **d})
        await run.emit({"type": "decision", "question": "agent", "label": "Caso de uso criado pelo agente", "source": "agente",
                        "chosen": rec["id"], "chosen_name": rec["name"] + (" (guardado em teste)" if saved else " (só desta vez)"),
                        "confidence": 1.0, "options": [{"id": rec["id"], "name": rec["name"], "p": 1.0}]})
        return steps, "recipe__" + str(rec["id"])

    # ------------------------------------------------------------------ step
    async def _review_cutout(self, run: Run, idx: int, step: Step, m: dict, binding: Binding, sources: list[Source],
                             ctx: Ctx, settings: dict, result: list[dict]) -> list[dict]:
        """Mostra o(s) recorte(s) e deixa o usuário aceitar ou refazer: desenhando uma ou VÁRIAS caixas no original
        (cada caixa vira um recorte, com seu tipo: pessoa ou objeto) e/ou trocando entre pessoa e objeto.
        Sem resposta, a execução é cancelada."""
        by_key = {s.key: s for s in sources}
        first = next((binding.slots.get(sl["key"]) for sl in m.get("images", []) if binding.slots.get(sl["key"])), None)
        modes = [{"id": k, "title": (v or {}).get("title") or k} for k, v in (m.get("modes") or {}).items()]
        boxes: list[dict] = []
        for _attempt in range(8):
            bad = next((r for r in result if r.get("state") in ("empty", "opaque")), None)
            value = await self.ask_user(run, {
                "kind": "cutout", "title": "O recorte ficou bom?", "original_url": by_key[first].url if first else "",
                "result_url": result[0]["url"], "warning": (bad or {}).get("warning", ""), "mode": binding.mode,
                "modes": modes, "boxes": boxes, "box": boxes[0]["b"] if boxes else None,
                "results": [{"url": r["url"], "warning": r.get("warning", ""), "mode": r.get("mode")} for r in result]})
            if value is None:
                raise RunCancelled()
            if not isinstance(value, dict) or value.get("action") != "redo":
                if bad:
                    raise ComfyError(f"{bad.get('warning')} Refaça marcando uma caixa ou trocando entre pessoa e objeto, "
                                     "e confira se o node está com background = Alpha.")
                return result
            mode = value.get("mode") if value.get("mode") in (m.get("modes") or {}) else binding.mode
            boxes = self._cutout_boxes(value, mode, m)
            binding = dataclasses.replace(binding, mode=mode, vars=self._vars_for_mode(step, binding, mode))
            await run.emit({"type": "status", "text": "Refazendo o recorte" + (
                f" dentro de {len(boxes)} caixas que você marcou." if len(boxes) > 1 else
                " dentro da caixa que você marcou." if boxes else ".")})
            if not boxes:
                result = await self._run_step(run, idx, step, m, dataclasses.replace(binding, mode=mode), sources, ctx, settings)
                continue
            result = []
            for bx in boxes:                                    # uma execução por caixa: cada objeto sai inteiro e separado
                one = await self._run_step(run, idx, step, m, dataclasses.replace(
                    binding, mode=bx["mode"], vars=self._vars_for_mode(step, binding, bx["mode"])), sources, ctx,
                                           settings, forced={"recorte": bx["b"]})
                result += [{**r, "mode": bx["mode"]} for r in one]
        return result

    def _vars_for_mode(self, step: Step, binding: Binding, mode: str | None) -> dict:
        """Trocou o tipo do recorte na tela (pessoa, objeto...): a variável cuja opção fixa outro modo passa para a
        opção desse modo (a que tem `fallback: true`, ou a única), para o texto do SAM3 combinar com o tipo."""
        out = dict(binding.vars)
        preset = next((p for p in self.view.prompts(step.workflow)["presets"] if p["id"] == binding.preset_id), {})
        for v in pvars.var_list(preset):
            opts = pvars.options(v)
            cur = next((o for o in opts if o["id"] == out.get(v["key"])), None)
            if not mode or not any(o.get("mode") for o in opts) or (cur and cur.get("mode") == mode):
                continue
            same = [o for o in opts if o.get("mode") == mode]
            pick = next((o for o in same if o.get("fallback")), same[0] if len(same) == 1 else None)
            if pick:
                out[v["key"]] = pick["id"]
        return out

    @staticmethod
    def _cutout_boxes(value: dict, default_mode: str | None, m: dict) -> list[dict]:
        """Caixas da tela de revisão: `boxes: [{b: [x, y, w, h], mode}]` (ou a `box` única de antes), até 8."""
        raw = value.get("boxes")
        if not isinstance(raw, list):
            raw = [{"b": value.get("box")}] if isinstance(value.get("box"), list) else []
        out = []
        for r in raw[:8]:
            b = r.get("b") if isinstance(r, dict) else r
            if not (isinstance(b, list) and len(b) == 4):
                continue
            try:
                x, y, w, h = [min(1.0, max(0.0, float(n))) for n in b]
            except (TypeError, ValueError):
                continue
            if w > 0.01 and h > 0.01:
                md = r.get("mode") if isinstance(r, dict) and r.get("mode") in (m.get("modes") or {}) else default_mode
                out.append({"b": [round(x, 4), round(y, 4), round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)], "mode": md})
        return out

    async def _run_step(self, run: Run, idx: int, step: Step, m: dict, binding: Binding,
                        sources: list[Source], ctx: Ctx, settings: dict, forced: dict | None = None,
                        prev_all: list[dict] | None = None) -> list[dict]:
        by_key = {s.key: s for s in sources}
        m = self.store.manifest(step.workflow, binding.mode)            # o modo escolhido (turbo, quality...)
        lib = {p["id"]: p for p in self.view.prompts(step.workflow)["presets"]}
        preset = lib[binding.preset_id]
        var_values: dict[str, Any] = dict(binding.vars)
        angle_var = pvars.has_angle(preset)
        if angle_var:                                   # giro de câmera: do pedido, ou o usuário escolhe na tela
            var_values[angle_var["key"]] = await self._resolve_angle(run, ctx.request, binding, by_key, m)
        preset = pvars.render(preset, var_values)
        loras_lib = self.store.loras()
        res = resolve(loras_lib, m, preset, binding.loras, binding.mode)
        stack = res["stack"]
        for note in res["notes"]:
            await run.emit({"type": "status", "text": note})
        drawings = await self._collect_drawings(run, m, binding, by_key, prev_all or [], skip=set(forced or {}))
        drawings.update(forced or {})
        review = m.get("review") == "cutout"
        tokens, notes = self._draw_tokens(m, binding, drawings)
        base_text = pvars._fill(preset["text"], tokens)
        if notes:
            base_text = base_text.rstrip() + "\n" + "\n".join(notes)
        request = ctx.request
        w = llmcfg.resolve(settings["writer"])
        use_llm = bool(w.get("enabled") and preset.get("rewrite"))
        if use_llm and not w.get("url"):
            await run.emit({"type": "status", "text": "O redator está ligado, mas nenhum provedor foi configurado (Ajustes > Redator de prompt)."})
            use_llm = False

        async def say(text: str):
            await run.emit({"type": "status", "text": text})

        if use_llm:
            await say(f"Redator: {w.get('provider_name') or w.get('provider')} / {w.get('model') or 'modelo padrão'} ajustando o prompt…")
        if use_llm and w.get("mode", "refine") == "translate":
            request = await writer.rewrite(request, w, say)
        prompt_text = compose(base_text, request, stack)
        if use_llm and w.get("mode", "refine") != "translate":
            step_images = [to_png(by_key[binding.slots[sl["key"]]].data) for sl in m.get("images", [])
                           if binding.slots.get(sl["key"]) and sl["key"] not in self._unused_refs(m, binding, drawings)]
            prompt_text = await writer.refine(prompt_text, ctx.request, describe_stack(stack, loras_lib), w, step_images,
                                              str(preset.get("writer_note") or ""), say)
        prompt_text = await self.addons.prompt(prompt_text, {"workflow": step.workflow, "preset": binding.preset_id,
                                                               "request": ctx.request, "mode": binding.mode,
                                                               "vars": var_values, "loras": [i["id"] for i in stack]})
        if m.get("kind") == "action":                          # fluxo em Python (addon): sem ComfyUI
            return await self._run_action(run, idx, step, m, binding, by_key, drawings, var_values, prompt_text, ctx, settings)
        splat = m.get("splat") or None
        splat_src, splat_k = None, ""
        if splat:                                               # 3D: a mesma foto não é gerada de novo
            splat_src = self._first_source(m, binding, list(by_key.values()))
            if splat_src is None:
                raise RouterError("O passo 3D precisa de uma foto.")
            splat_k = splat_key(splat_src.data, str(splat.get("kind") or "ambiente"))
            cached = self.splats.find(splat_k)
            if cached:
                await run.emit({"type": "step_start", "index": idx, "workflow": step.workflow, "title": m.get("title"),
                                "preset": binding.preset_id, "prompt": "", "mode": binding.mode, "vars": {}, "loras": [],
                                "inputs": {k: (by_key[v].label if v else None) for k, v in binding.slots.items()}})
                await run.emit({"type": "status", "text": "Esta foto já tinha um 3D gerado: uso o mesmo (sem rodar o TripoSplat)."})
                return await self._splat_choose(run, idx, step, m, splat_src, cached)
        expand = preset.get("expand") or None
        if preset.get("expand_edit") and (expand or preset.get("expand_ask")):
            expand = await self._ask_expand(run, m, binding, by_key, sources, expand) or None
        expanded: tuple | None = None                           # (original, caixa da foto na tela, tamanho da tela)
        placed: dict[str, tuple[bytes, tuple[int, int, int, int]]] = {}
        for p in m.get("params", []):                          # camada(s) posicionada(s) na tela: já no tamanho, giradas e cortadas no fundo
            if p.get("type") == "place" and p["key"] in drawings and binding.slots.get(p["image"]) and binding.slots.get(p["overlay"]):
                bg_img = Image.open(io.BytesIO(by_key[binding.slots[p["image"]]].data))
                layers = [d for _u, d in self._overlay_sources(binding, by_key, p, prev_all or [])]
                fracs = drawings[p["key"]]
                try:
                    placed[p["overlay"]] = place_layers(list(zip(layers, fracs)), bg_img.size)
                except ValueError as e:
                    raise RouterError(str(e)) from e

        comfy = await self.comfy()
        oi = await comfy.object_info()
        wf = self.store.workflow_json(step.workflow, binding.mode)
        stack = await self._available_loras(run, stack, oi)
        overrides: dict[tuple[Any, str], Any] = {}
        if m.get("prompt"):
            overrides[(node_id(m["prompt"]["node"]), m["prompt"].get("widget", "value"))] = prompt_text
        for c in m.get("set", []):
            v = c["value"]
            if v == "random":                       # sorteia a semente (útil em subgraphs com semente fixa)
                v = random.randrange(0, 2 ** 50)
            overrides[(node_id(c["node"]), c["widget"])] = v
        drop = {str(n) for n in m.get("drop", [])}
        keep: set = set()
        fallback: str | None = None
        cut_src: bytes | None = None
        skip = self._unused_refs(m, binding, drawings)
        if skip:
            names = sorted(f"imagem {self._src_number(binding.slots[k])}" for k in skip)
            await run.emit({"type": "status", "text": f"Sem caixa verde, a {', '.join(names)} não vai ao ComfyUI (menos tempo)."})
        for slot in m.get("images", []):
            src_key = binding.slots.get(slot["key"])
            node = node_id(slot["node"])
            if src_key is None or slot["key"] in skip:
                drop.add(str(node))
                continue
            data = to_png(by_key[src_key].data)
            if not slot.get("keep_alpha") and not slot.get("mask") and slot["key"] not in placed:
                data = flatten_alpha(data)                   # o ComfyUI deixaria o fundo transparente preto
            if slot["key"] in placed:
                data = placed[slot["key"]][0]
            for p in m.get("params", []):                    # recorte escolhido na tela (opcional)
                if p.get("type") == "crop" and p.get("image") == slot["key"] and p["key"] in drawings:
                    data = crop_fraction(data, drawings[p["key"]])
            for p in m.get("params", []):                    # caixas coloridas: as que o nó do ComfyUI não desenha vão queimadas na imagem
                if p.get("type") == "box" and p.get("image") == slot["key"] and p["key"] in drawings:
                    extra = self._boxes(drawings[p["key"]])
                    extra = extra[1:] if p.get("node") else extra       # a 1ª caixa o nó desenha (como sempre)
                    if extra:
                        data = burn_boxes(data, extra, p.get("color", "red"), int(p.get("thickness", 8)),
                                          bool(p.get("filled", False)), bool(p.get("numbered")))
            if slot.get("mask"):
                mask = drawings.get("mask_" + slot["key"])
                if mask is None:
                    raise RouterError("Faltou a máscara para o workflow de inpaint.")
                data = mask_to_alpha_png(data, mask)
            if m.get("pose_reference") == slot["key"] and run.pose_reference:      # "Mudar a pose": a foto sem o membro antigo
                data = run.pose_reference
            if expand and expanded is None:                  # outpaint: a 1ª imagem ganha uma borda cinza para o modelo completar
                padded, box = pad_gray(data, expand)
                pim = Image.open(io.BytesIO(padded))
                expanded = (data, box, pim.size)
                data = padded
                await run.emit({"type": "status", "text": f"Aumentei a tela para {pim.size[0]}x{pim.size[1]} com cinza: "
                                                          "o modelo completa essa área."})
            cut_src = cut_src or data                        # a imagem de onde sai o recorte (já cortada pela caixa)
            uploaded = await comfy.upload(data, f"cf_{run.id}_{idx}_{slot['key']}.png")
            overrides[(node, "image")] = uploaded
            keep.add(str(node))
            fallback = fallback or uploaded
        overrides.update(res["overrides"])
        bypass = res["bypass"]
        for p in m.get("params", []):
            if p.get("type") == "box" and p["key"] in drawings and p.get("node") is not None:
                x, y, w, h = self._boxes(drawings[p["key"]])[0]["b"]
                for name, v in zip(p["widgets"], (x, y, w, h)):
                    overrides[(node_id(p["node"]), name)] = v
            elif p.get("type") == "place" and p["key"] in drawings and p["overlay"] in placed:
                for name, v in zip(p["widgets"], placed[p["overlay"]][1]):      # parte visível da(s) camada(s), em pixels do fundo
                    overrides[(node_id(p["node"]), name)] = int(v)

        await run.emit({"type": "step_start", "index": idx, "workflow": step.workflow, "title": m.get("title"),
                        "preset": binding.preset_id, "prompt": "" if splat else prompt_text, "mode": binding.mode,
                        "vars": {k: (pvars.angle_phrase(v) if isinstance(v, dict) else str(v)) for k, v in var_values.items()},
                        "loras": [{"title": i["title"], "strength": i["strength"], "origin": i["origin"]}
                                  for i in stack if i["origin"] != "base"],
                        "inputs": {k: (by_key[v].label if v else None) for k, v in binding.slots.items()}})
        conv = Converter(oi)
        try:
            api = conv.convert(wf, overrides, drop, fallback, keep, bypass, m.get("lora_chain"),
                               stack if m.get("lora_chain") else None)
        except ConversionError:
            oi = await comfy.object_info(refresh=True)
            api = Converter(oi).convert(wf, overrides, drop, fallback, keep, bypass, m.get("lora_chain"),
                                        stack if m.get("lora_chain") else None)
        swapped, why = encoders.apply(api, settings["comfyui"].get("text_encoder"), oi)      # o CLIP escolhido em Ajustes
        if why:
            await run.emit({"type": "status", "text": why})
        out_nodes = [str(n) for n in m.get("outputs", [])]
        missing = [n for n in out_nodes if n not in api]
        if missing:
            raise ConversionError(f"O nó de saída {', '.join(missing)} ficou fora do grafo (entrada obrigatória removida).")
        pid = await comfy.submit(api)

        async def tick(elapsed: float):
            if run.cancelled:
                return
            await run.emit({"type": "progress", "index": idx, "elapsed": round(elapsed, 1)})

        outputs = await comfy.wait(pid, tick, lambda: run.cancelled)
        if splat:
            meta = await self._save_splat(comfy, outputs, splat, splat_src, splat_k)
            return await self._splat_choose(run, idx, step, m, splat_src, meta)
        if m.get("pose"):
            return await self._pose_choose(run, idx, m, self._first_source(m, binding, list(by_key.values())), outputs, ctx, settings)
        results: list[dict] = []
        pics: list[tuple[str, bytes, str]] = []
        for n in out_nodes:
            for im in (outputs.get(n) or {}).get("images", []):
                pics.append((n, await comfy.view(im["filename"], im.get("subfolder", ""), im.get("type", "output")),
                             im["filename"]))
        if m.get("merge") == "person" and len(pics) >= 2 and cut_src:
            try:                                            # SAM3 + RMBG: o corpo inteiro, sem levar o que está ao redor
                pics = [(pics[0][0], merge_person_cutouts(cut_src, pics[0][1], pics[1][1]), pics[0][2])]
            except Exception as e:  # noqa: BLE001
                await run.emit({"type": "status", "text": f"Não consegui somar as máscaras ({e}); uso só a do SAM3."})
                pics = pics[:1]
        if m.get("pose_blend") and run.pose_mask is not None and run.pose_source:          # só o que mexeu vem do modelo
            await run.emit({"type": "status", "text": "Mantendo os pixels da foto original fora dos membros que mexeram."})
            pics = [(n, blend_masked(run.pose_source, d, run.pose_mask), f) for n, d, f in pics]
        if expanded is not None:                               # o que já existia volta idêntico; só a borda vem do modelo
            pics = [(n, paste_back(d, expanded[0], expanded[1], expanded[2]), f) for n, d, f in pics]
        for _n, data, fname in pics:
            info: dict = {}
            if m.get("trim_alpha"):
                state = cutout_state(data)
                edges = cut_edges(data) if state == "ok" else []
                try:
                    data = trim_to_alpha(data, strict=not review)
                except NoTransparency as e:
                    raise ComfyError(f"{e} Confira se o node está com background = Alpha.") from e
                if state != "ok":                       # com revisão, o usuário vê e decide (refazer com caixa ou trocar o tipo)
                    info = {"state": state, "warning": "Nada foi reconhecido nesta imagem." if state == "empty"
                            else "O fundo não foi removido (nada ficou transparente)."}
                elif edges:
                    info = {"cut": edges}                # encosta na borda da foto: a pessoa ou o objeto está cortado
            results.append({**self.images.save(data, fname), **info})
        if not results:
            raise ComfyError("O workflow terminou, mas não gerou nenhuma imagem no nó de saída.")
        if preset.get("verify") and not forced:
            fixed = await self._verify_step(run, idx, step, binding, by_key, ctx, settings, preset, m, drawings, results)
            if fixed is not None:
                return fixed
        extras = []
        for n, o in outputs.items():
            if n in out_nodes:
                continue
            for im in o.get("images", []):
                extras.append(self.images.save(await comfy.view(im["filename"], im.get("subfolder", ""),
                                                                im.get("type", "temp")), im["filename"]))
        await run.emit({"type": "step_done", "index": idx, "title": m.get("title"), "images": results,
                        "extras": extras})
        return results

    # ------------------------------------------------------------ conferência do resultado
    async def _verify_step(self, run: Run, idx: int, step: Step, binding: Binding, by_key: dict[str, Source], ctx: Ctx,
                           settings: dict, preset: dict, m: dict, drawings: dict, results: list[dict]) -> list[dict] | None:
        """`verify: {kind: gone, box, skip, min_change, fix: {workflow, prompt, request}}` num prompt. Num "mover objeto"
        o modelo às vezes deixa o objeto também no lugar de origem (ficam dois). Aqui confere, pelos pixels, se a caixa
        de origem mudou de verdade; se não mudou, roda uma segunda passada (o fluxo `fix`, p. ex. remover no quadrado
        vermelho) sobre o resultado. Devolve os resultados da segunda passada, ou None se estava tudo certo."""
        v = preset["verify"]
        if v.get("kind") == "gray" and len(results) == 1:
            return await self._verify_gray(run, idx, step, binding, ctx, settings, m, v, results)
        if v.get("kind") != "gone" or len(results) != 1 or v.get("box") not in drawings:
            return None
        p = next((x for x in m.get("params", []) if x.get("key") == v["box"]), None)
        src_key = binding.slots.get(p.get("image")) if p else None
        if not src_key or src_key not in by_key:
            return None
        box = self._boxes(drawings[v["box"]])[0]["b"]
        skip = [bx["b"] for bx in self._boxes(drawings[v["skip"]])] if v.get("skip") in drawings else []
        out = self.images.load(results[0]["id"])
        frac = region_change(by_key[src_key].data, out, box, skip)
        if frac is None or frac >= float(v.get("min_change", 0.08)):
            return None
        fix = v.get("fix") or {}
        known = self.store.manifests()
        if fix.get("workflow") not in known:
            return None
        await run.emit({"type": "status", "text": "O objeto continuou no lugar de origem (a caixa vermelha quase não mudou, "
                                                  f"{frac:.0%}): rodo uma segunda passada só para apagá-lo."})
        wf = fix["workflow"]
        modes = known[wf].get("modes") or {}
        mode = binding.mode if binding.mode in modes else (next(iter(modes)) if modes else None)
        step2 = Step(wf, fix.get("prompt"), {"image_1": "previous"}, mode, None, None, fix.get("request") or None)
        sources = [Source("previous", "Resultado da primeira passada", out, results[0]["url"])]
        bind2 = Binding(fix.get("prompt") or self.view.prompts(wf)["default"], {"image_1": "previous"}, [], mode, [], {})
        return await self._run_step(run, idx, step2, known[wf], bind2, sources, self._step_ctx(ctx, step2), settings,
                                    forced={"red": [{"b": box, "ref": None}]})

    async def _ask_expand(self, run: Run, m: dict, binding: Binding, by_key: dict[str, Source], sources: list[Source],
                          suggested: dict | None) -> dict:
        """Outpaint: mostra a foto e deixa a pessoa acrescentar o cinza em cada lado (fração do lado da foto), partindo da
        sugestão do Clef. Devolve `{left, right, top, bottom}`; cancelar encerra a execução."""
        src = self._first_source(m, binding, sources)
        if src is None:
            return suggested or {}
        w, h = Image.open(io.BytesIO(src.data)).size
        value = await self.ask_user(run, {"kind": "outpaint", "title": "Ampliar a imagem (outpaint)", "image_url": src.url,
                                          "size": [w, h], "expand": expand_spec(suggested or {})})
        if not isinstance(value, dict):
            raise RunCancelled()
        return expand_spec(value)

    async def _verify_gray(self, run: Run, idx: int, step: Step, binding: Binding, ctx: Ctx, settings: dict, m: dict,
                           v: dict, results: list[dict]) -> list[dict] | None:
        """`verify: {kind: gray, max_gray, tries}` (Outpaint): conta quanto do cinza #808080 sobrou e, passando de
        `max_gray`, roda o mesmo prompt de novo sobre o resultado (outra semente; o que já foi completado fica). Testado
        no ComfyUI real com renders de um quarto em 3D: a mesma imagem saía com 0,3% ou 19% de cinza conforme a semente."""
        limit, tries = float(v.get("max_gray", 0.02)), int(v.get("tries", 2))
        cur = results
        slot = next((s["key"] for s in m.get("images", []) if binding.slots.get(s["key"])), "image_1")
        for _ in range(tries):
            out = self.images.load(cur[0]["id"])
            frac = gray_fraction(out)
            if frac <= limit:
                break
            await run.emit({"type": "status", "text": f"Ainda sobrou cinza ({frac:.0%} da imagem): rodo de novo sobre o "
                                                      "resultado para completar o resto."})
            slots = {k: ("previous" if k == slot else None) for k in binding.slots}
            again = {**(binding.vars or {}), **(v.get("vars") or {})}     # ex.: expand: gray (não aumentar a tela de novo)
            bind2 = dataclasses.replace(binding, slots=slots, decisions=[], vars=again)
            step2 = dataclasses.replace(step, inputs={slot: "previous"}, loras=list(binding.loras), vars=again)
            sources = [Source("previous", "Resultado da primeira passada", out, cur[0]["url"])]
            cur = await self._run_step(run, idx, step2, m, bind2, sources, ctx, settings, forced={"_retry": True})
        return cur if cur is not results else None

    # ----------------------------------------------------------------- 3D (splat)
    async def _save_splat(self, comfy, outputs: dict, splat: dict, src: Source, key: str) -> dict:
        files = (outputs.get(str(splat.get("node"))) or {}).get("3d") or []
        if not files:
            raise ComfyError("O workflow terminou, mas não gerou o arquivo 3D (nó SaveGLB).")
        f = files[0]
        data = await comfy.view(f["filename"], f.get("subfolder", ""), f.get("type", "output"))
        w, h = Image.open(io.BytesIO(src.data)).size
        ext = "." + str(f["filename"]).rsplit(".", 1)[-1].lower()
        return self.splats.add(data, str(splat.get("kind") or "ambiente"), key,
                               {"url": src.url, "label": src.label, "width": w, "height": h}, ext=ext)

    async def _splat_choose(self, run: Run, idx: int, step: Step, m: dict, src: Source, meta: dict) -> list[dict]:
        """Mostra o 3D: a pessoa gira a câmera, pode baixar o PNG e guardar o 3D, e escolhe entre ficar com o render
        desse ângulo ou gerar a foto completa nele (o passo seguinte). O render volta como imagem do passo."""
        splat = m.get("splat") or {}
        then = splat.get("then")
        then_wf = then.get("workflow") if isinstance(then, dict) else then
        w, h = Image.open(io.BytesIO(src.data)).size
        k = math.sqrt(1_048_576 / max(1, w * h))                  # o render sai com ~1 MP, na proporção da foto
        tw, th = max(64, int(w * k) // 16 * 16), max(64, int(h * k) // 16 * 16)
        known = self.store.manifests()
        value = await self.ask_user(run, {
            "kind": "splat", "title": "Escolha o ângulo da pessoa" if splat.get("kind") == "pessoa" else "Escolha o ângulo da câmera",
            "mode": splat.get("kind") or "ambiente", "splat": splat_view(meta), "image_url": src.url,
            "aspect": round(w / max(1, h), 5), "size": [tw, th],
            "then_title": known[then_wf].get("title", then_wf) if then_wf in known else ""})
        if not isinstance(value, dict):
            raise RunCancelled()
        raw = str(value.get("image") or "")
        if not raw.startswith("data:image/png;base64,"):
            raise RouterError("Faltou a imagem do ângulo escolhido.")
        try:
            png = base64.b64decode(raw.split(",", 1)[1], validate=True)
            res = self.images.save(png, f"{step.workflow}_angulo.png")
        except (ValueError, TypeError) as e:
            raise RouterError("A imagem do ângulo escolhido veio corrompida.") from e
        v = value.get("view") if isinstance(value.get("view"), dict) else {}
        res.update({"then": bool(value.get("action") == "final" and then_wf in known), "splat": meta["id"],
                    "view": {k2: v[k2] for k2 in ("yaw", "pitch", "distance", "fov") if isinstance(v.get(k2), (int, float))}})
        await run.emit({"type": "step_done", "index": idx, "title": m.get("title"), "images": [res], "extras": []})
        return [res]

    @staticmethod
    def _unused_refs(m: dict, binding: Binding, drawings: dict) -> set[str]:
        """Entradas de objeto que nenhuma caixa usa. Só as do FIM da lista saem do grafo (sem buraco na numeração
        <image2>, <image3>…): cada imagem de referência a mais deixa o texto-encoder e a difusão bem mais lentos."""
        out: set[str] = set()
        for p in m.get("params", []):
            if p.get("type") != "box" or not p.get("refs") or p["key"] not in drawings:
                continue
            used = {bx.get("ref") for bx in Pipeline._boxes(drawings[p["key"]])}
            for r in reversed(p["refs"]):
                src = binding.slots.get(r)
                if src in used:
                    break                                   # daqui para trás tudo fica (sem buraco na numeração)
                if src is not None:
                    out.add(r)
        return out

    # ----------------------------------------------------------------- ação (addon)
    async def _run_action(self, run: Run, idx: int, step: Step, m: dict, binding: Binding, by_key: dict[str, Source],
                          drawings: dict, var_values: dict, prompt_text: str, ctx: Ctx, settings: dict) -> list[dict]:
        """Fluxo `kind: action`: chama a função Python que um addon registrou com `@ctx.action(...)`.
        Ela recebe as imagens das entradas, o que o usuário desenhou e o prompt, e devolve imagens."""
        ref = str(m.get("action") or "")
        fn = self.addons.action(ref)
        if fn is None:
            raise RouterError(f"A ação '{ref}' não existe: confira se o addon está ligado e sem erro (Ajustes > Addons).")
        images = {sl["key"]: to_png(by_key[binding.slots[sl["key"]]].data)
                  for sl in m.get("images", []) if binding.slots.get(sl["key"])}

        async def status(text: str):
            await run.emit({"type": "status", "text": str(text)})

        async def ask(kind: str, **payload):
            if kind in ("draw", "angle", "cutout", "choose"):
                raise RouterError("Use um nome próprio para a tela do addon (os nomes draw, angle, cutout e choose são do PickFlow).")
            value = await self.ask_user(run, {"kind": kind, "title": payload.pop("title", m.get("title")), **payload})
            if value is None:
                raise RunCancelled()
            return value

        call = ActionCall(request=ctx.request, images=images, drawings=drawings, vars=var_values, prompt=prompt_text,
                          llm=LLM(lambda: self.store.settings().get("writer", {})), settings=settings, status=status, ask=ask)
        await run.emit({"type": "step_start", "index": idx, "workflow": step.workflow, "title": m.get("title"),
                        "preset": binding.preset_id, "prompt": prompt_text, "mode": binding.mode, "vars": {},
                        "loras": [], "inputs": {k: (by_key[v].label if v else None) for k, v in binding.slots.items()}})
        try:
            out = fn(call)
            if inspect.isawaitable(out):
                out = await out
        except (RouterError, ComfyError, RunCancelled):
            raise
        except Exception as e:  # noqa: BLE001 - erro do addon vira mensagem na conversa, não derruba o servidor
            raise RouterError(f"A ação '{ref}' falhou: {type(e).__name__}: {e}") from e
        if isinstance(out, dict) and "images" in out:
            if out.get("text"):
                await status(out["text"])
            out = out["images"]
        results = []
        for i, item in enumerate(out if isinstance(out, (list, tuple)) else [out]):
            data = self._action_bytes(item)
            results.append(self.images.save(data, f"{step.workflow}_{i + 1}.png"))
        if not results:
            raise RouterError(f"A ação '{ref}' não devolveu nenhuma imagem.")
        await run.emit({"type": "step_done", "index": idx, "title": m.get("title"), "images": results, "extras": []})
        return results

    @staticmethod
    def _action_bytes(item: Any) -> bytes:
        if isinstance(item, (bytes, bytearray)):
            return bytes(item)
        if hasattr(item, "save"):                                  # PIL.Image
            buf = io.BytesIO()
            item.save(buf, format="PNG")
            return buf.getvalue()
        raise RouterError("Uma ação deve devolver bytes PNG, uma PIL.Image ou uma lista delas.")

    # ----------------------------------------------------------------- ângulo
    async def _resolve_angle(self, run: Run, request: str, binding: Binding, by_key: dict[str, Source], m: dict) -> dict:
        """Giro de câmera (LoRA de órbita): lê do pedido; se faltar o lado ou o ângulo, abre a tela para escolher."""
        spec = pvars.parse_angle(request)
        if spec and spec["complete"]:
            return {"azimuth": spec["azimuth"], "elevation": spec["elevation"]}
        first = next((binding.slots.get(sl["key"]) for sl in m.get("images", []) if binding.slots.get(sl["key"])), None)
        value = await self.ask_user(run, {
            "kind": "angle", "title": "Para onde girar o ponto de vista?",
            "image_url": by_key[first].url if first else "",
            "azimuth": (spec or {}).get("azimuth", 90), "elevation": (spec or {}).get("elevation", "eye"),
            "steps": list(pvars.ANGLE_STEPS)})
        try:
            az = int((value or {}).get("azimuth"))
        except (TypeError, ValueError):
            raise RouterError("Faltou o ângulo do giro.") from None
        elev = (value or {}).get("elevation")
        return {"azimuth": max(-180, min(180, az)), "elevation": elev if elev in pvars.ELEVATIONS else "eye"}

    # ----------------------------------------------------------------- LoRAs
    async def _available_loras(self, run: Run, stack: list[dict], oi: dict) -> list[dict]:
        """Confere se cada LoRA da pilha está instalada (aceita arquivo em outra subpasta). Faltou uma do
        prompt escolhido: erro claro. Faltou uma de realismo ou uma que o Clef acrescentou: segue sem ela."""
        if not stack:
            return stack
        opts: list[str] = []
        for w in widget_list(oi.get("LoraLoader", {})):
            if w.name == "lora_name":
                opts = [o for o in w.options if isinstance(o, str)]
        if not opts:
            return stack                        # sem a lista do ComfyUI, deixa o ComfyUI reclamar
        base = lambda n: str(n).replace("\\", "/").rsplit("/", 1)[-1].lower()      # noqa: E731
        out = []
        for it in stack:
            hit = it["file"] if it["file"] in opts else next((o for o in opts if base(o) == base(it["file"])), None)
            if hit:
                out.append({**it, "file": hit})
            elif it["origin"] == "preset":
                raise RouterError(f"A LoRA '{it['title']}' ({it['file']}) não está instalada no ComfyUI. "
                                  "Instale pelo assistente da aba Ajustes ou troque o arquivo na aba LoRAs.")
            else:
                await run.emit({"type": "status", "text": f"LoRA '{it['title']}' não encontrada no ComfyUI: segui sem ela."})
        return out

    # -------------------------------------------------------------- drawings
    @staticmethod
    def _src_number(key: str) -> str:
        return key.split("_", 1)[1] if key.startswith("user_") else "anterior"

    def _overlay_sources(self, binding: Binding, by_key: dict[str, Source], p: dict,
                         prev_all: list[dict]) -> list[tuple[str, bytes]]:
        """As camadas que o usuário posiciona: a imagem da entrada de sobreposição e, quando o passo anterior
        entregou vários recortes, todos eles (cada um vira uma camada)."""
        key = binding.slots[p["overlay"]]
        src = by_key[key]
        out = [(src.url, src.data)]
        if key == "previous" and len(prev_all) > 1:
            out += [(r["url"], self.images.load(r["id"])) for r in prev_all[1:]]
        return out

    @staticmethod
    def _boxes(v: Any) -> list[dict]:
        """Uma caixa só ([x, y, w, h]) ou várias ([{b, ref}]) sempre como lista de {b, ref}."""
        if v and isinstance(v[0], dict):
            return list(v)
        return [{"b": list(v), "ref": None}]

    async def _collect_drawings(self, run: Run, m: dict, binding: Binding, by_key: dict[str, Source],
                                prev_all: list[dict] | None = None, skip: set | None = None) -> dict:
        tasks: list[dict] = []
        for p in m.get("params", []):
            key = binding.slots.get(p.get("image"))
            if not key or p.get("key") in (skip or ()):          # desenho já dado (`forced`): não pergunta de novo
                continue
            if p.get("type") == "box":
                t = {"key": p["key"], "kind": "box", "label": p["label"].replace("{n}", self._src_number(key)),
                     "color": p.get("color", "red"),
                     "image_url": by_key[key].url, "multiple": bool(p.get("multiple")),
                     "optional": bool(p.get("optional")), "hint": p.get("hint", ""), "numbered": bool(p.get("numbered"))}
                if p.get("refs"):                               # cada caixa diz qual imagem enviada ela representa
                    t["refs"] = [{"key": binding.slots[r], "n": self._src_number(binding.slots[r]),
                                  "url": by_key[binding.slots[r]].url} for r in p["refs"] if binding.slots.get(r)]
                tasks.append(t)
            elif p.get("type") == "crop" and p.get("ask") is not False:       # ask: false = só quando o usuário refaz o recorte
                tasks.append({"key": p["key"], "kind": "crop", "label": p["label"], "color": p.get("color", "white"),
                              "image_url": by_key[key].url, "optional": bool(p.get("optional"))})
            elif p.get("type") == "place" and binding.slots.get(p.get("overlay")):
                ovs = self._overlay_sources(binding, by_key, p, prev_all or [])
                sizes = [list(Image.open(io.BytesIO(d)).size) for _u, d in ovs]
                bw, bh = Image.open(io.BytesIO(by_key[key].data)).size
                tasks.append({"key": p["key"], "kind": "place", "label": p["label"], "image_url": by_key[key].url,
                              "overlay_url": ovs[0][0], "overlay_size": sizes[0], "image_size": [bw, bh],
                              "overlays": [{"url": u, "size": sz} for (u, _d), sz in zip(ovs, sizes)]})
        for s in m.get("images", []):
            if s.get("mask") and binding.slots.get(s["key"]):
                tasks.append({"key": "mask_" + s["key"], "kind": "mask", "label": s.get("draw_label", "Pinte a área a refazer"),
                              "color": "white", "image_url": by_key[binding.slots[s["key"]]].url})
        if not tasks:
            return {}
        value = await self.ask_user(run, {"kind": "draw", "title": m.get("title"), "tasks": tasks})
        out: dict[str, Any] = {}
        for t in tasks:
            v = (value or {}).get(t["key"])
            if t["kind"] in ("crop", "box") and (v is None or v == "skip") and t.get("optional"):
                continue                                          # sem seleção: usa a imagem inteira
            if v is None:
                raise RouterError(f"Faltou o desenho: {t['label']}")
            if t["kind"] == "place":
                # posição, tamanho e giro livres: pode passar das bordas (a parte de fora é cortada); uma lista por camada
                layers = v if v and isinstance(v[0], (list, tuple)) else [v]
                if len(layers) != len(t["overlays"]):
                    raise RouterError("Faltou posicionar uma das camadas.")
                try:
                    out[t["key"]] = [clamp_frac(x) for x in layers]
                except ValueError as e:
                    raise RouterError(str(e)) from None
                except TypeError:
                    raise RouterError("Posição inválida para a pessoa ou o objeto.") from None
            elif t["kind"] == "box":
                out[t["key"]] = self._norm_boxes(t, v)
            elif t["kind"] == "crop":
                x, y, w, h = [min(1.0, max(0.0, float(n))) for n in v]
                if w <= 0.002 or h <= 0.002:
                    raise RouterError(f"A caixa '{t['label']}' está vazia.")
                out[t["key"]] = [round(x, 4), round(y, 4), round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)]
            else:
                import base64
                b64 = v.split(",", 1)[-1]
                out[t["key"]] = base64.b64decode(b64)
        return out

    @staticmethod
    def _norm_boxes(t: dict, v: Any) -> list:
        """Caixa(s) da tela: limites 0-1, sem caixa vazia; com `refs`, cada caixa aponta para uma das imagens."""
        raw = [v] if (isinstance(v, (list, tuple)) and v and isinstance(v[0], (int, float))) else list(v or [])
        refs = {r["key"] for r in t.get("refs") or []}
        out = []
        for r in raw[:12 if t.get("multiple") else 1]:
            b = r.get("b") if isinstance(r, dict) else r
            try:
                x, y, w, h = [min(1.0, max(0.0, float(n))) for n in b]
            except (TypeError, ValueError):
                raise RouterError(f"Caixa inválida em '{t['label']}'.") from None
            if w <= 0.002 or h <= 0.002:
                raise RouterError(f"A caixa '{t['label']}' está vazia.")
            ref = r.get("ref") if isinstance(r, dict) else None
            if refs:
                if ref not in refs:
                    if len(refs) == 1:
                        ref = next(iter(refs))
                    else:
                        raise RouterError("Diga, em cada caixa, qual imagem vai ali.")
            out.append({"b": [round(x, 4), round(y, 4), round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)], "ref": ref})
        if not out:
            raise RouterError(f"Faltou o desenho: {t['label']}")
        return out if (t.get("multiple") or refs) else out[0]["b"]

    def _draw_tokens(self, m: dict, binding: Binding, drawings: dict) -> tuple[dict[str, str], list[str]]:
        """Palavras que dependem do que foi desenhado: `{chave_n}` (quantas caixas) e, nas caixas que apontam
        imagens, `{chave_map}` (qual objeto vai em cada caixa). `multi_prompt` do parâmetro entra no fim do prompt
        quando há mais de uma caixa."""
        tokens: dict[str, str] = {}
        notes: list[str] = []
        slot_of = {src: sl for sl, src in binding.slots.items() if src}
        drawn_slots = {p["image"] for p in m.get("params", []) if p.get("type") == "box" and p["key"] in drawings}
        for p in m.get("params", []):
            if p.get("type") != "box" or p["key"] not in drawings:
                continue
            boxes = self._boxes(drawings[p["key"]])
            tokens[f"{p['key']}_n"] = str(len(boxes))
            color = p.get("color", "red")
            if p.get("refs"):
                lines = []
                for i, bx in enumerate(boxes, 1):
                    sl = slot_of.get(bx.get("ref") or "", "image_2")
                    n = sl.split("_")[-1]
                    inside = " (the one inside its red box)" if sl in drawn_slots else ""
                    lines.append(f"{color.capitalize()} box {i}: put the object from <image{n}>{inside}.")
                tokens[f"{p['key']}_map"] = "\n".join(lines)
            if len(boxes) > 1 and p.get("multi_prompt"):
                notes.append(str(p["multi_prompt"]).replace("{n}", str(len(boxes))))
        return tokens, notes

    # --------------------------------------------------------------- history
    @staticmethod
    def _history(conv: dict) -> list[dict]:
        out = []
        for m in conv["messages"][-8:]:
            if m["role"] == "user":
                out.append({"role": "user", "text": m["text"][:300]})
            else:
                plan = next((e for e in m.get("events", []) if e["type"] == "plan"), None)
                out.append({"role": "assistant", "text": ("Executou: " + " -> ".join(s["title"] for s in plan["steps"]))
                            if plan else "Sem execução."})
        return out

    @staticmethod
    def _last_result(conv: dict) -> dict | None:
        for m in reversed(conv["messages"]):
            if m["role"] == "assistant" and m.get("images"):
                return m["images"][0]
        return None
