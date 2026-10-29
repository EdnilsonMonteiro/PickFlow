import { app } from "/scripts/app.js";

const COLORS = { red: "#ff0000", green: "#00d200", blue: "#0078ff", yellow: "#ffdc00" };
const LABELS = { src: "1", tgt: "2", box: "" };

function getWidget(node, name) {
  return node.widgets ? node.widgets.find((w) => w.name === name) : null;
}
function getVal(node, name) {
  const w = getWidget(node, name);
  return w ? Number(w.value) : 0;
}
function setVal(node, name, v) {
  const w = getWidget(node, name);
  if (w) { w.value = v; if (typeof w.callback === "function") { try { w.callback(v); } catch (e) {} } }
}
function clamp01(v) { return Math.max(0, Math.min(1, v)); }

function upstreamFilename(node) {
  const inp = (node.inputs || []).find((i) => i.name === "image");
  if (!inp || inp.link == null) return null;
  const graph = app.graph;
  const link = graph.links ? graph.links[inp.link] : null;
  if (!link) return null;
  const origin = (graph.getNodeById && graph.getNodeById(link.origin_id)) || (graph._nodes_by_id && graph._nodes_by_id[link.origin_id]);
  if (!origin) return null;
  const w = (origin.widgets || []).find((w) => w.name === "image");
  if (w && typeof w.value === "string" && w.value) return w.value;
  return null;
}
function viewUrl(fn) {
  let sub = "", name = fn;
  const i = fn.lastIndexOf("/");
  if (i >= 0) { sub = fn.substring(0, i); name = fn.substring(i + 1); }
  return "/view?filename=" + encodeURIComponent(name) + "&type=input&subfolder=" + encodeURIComponent(sub);
}

function makeEditor(node, specs) {
  const canvas = document.createElement("canvas");
  canvas.width = 300; canvas.height = 210;
  canvas.style.width = "300px"; canvas.style.height = "210px";
  canvas.style.background = "#0b0b0b"; canvas.style.borderRadius = "6px"; canvas.style.cursor = "crosshair";
  canvas.title = specs.length > 1
    ? "Arraste = caixa VERMELHA (1). Shift+arraste = caixa VERDE (2). Arraste por dentro = mover; canto inf. dir. = redimensionar."
    : "Arraste para desenhar a caixa. Arraste por dentro = mover; canto inf. dir. = redimensionar.";
  const ctx = canvas.getContext("2d");
  let img = null, imgName = null, drag = null;
  let disp = { x: 0, y: 0, w: canvas.width, h: canvas.height };

  function boxOf(spec) {
    return { x: getVal(node, spec.key + "_x"), y: getVal(node, spec.key + "_y"), w: getVal(node, spec.key + "_w"), h: getVal(node, spec.key + "_h") };
  }
  function setBox(spec, b) {
    setVal(node, spec.key + "_x", clamp01(b.x));
    setVal(node, spec.key + "_y", clamp01(b.y));
    setVal(node, spec.key + "_w", Math.max(0.005, b.w));
    setVal(node, spec.key + "_h", Math.max(0.005, b.h));
  }
  function colorOf(spec) { const w = getWidget(node, spec.colorName); return COLORS[w ? w.value : ""] || COLORS[spec.fallback]; }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (img) {
      const ar = img.width / img.height;
      let w = canvas.width, h = w / ar;
      if (h > canvas.height) { h = canvas.height; w = h * ar; }
      disp = { x: (canvas.width - w) / 2, y: (canvas.height - h) / 2, w, h };
      ctx.drawImage(img, disp.x, disp.y, disp.w, disp.h);
    } else {
      disp = { x: 8, y: 8, w: canvas.width - 16, h: canvas.height - 16 };
      ctx.fillStyle = "#1b1b1b"; ctx.fillRect(disp.x, disp.y, disp.w, disp.h);
      ctx.fillStyle = "#8a8a8a"; ctx.font = "12px sans-serif";
      ctx.fillText("Conecte uma imagem no input 'image'", disp.x + 8, disp.y + 20);
    }
    ctx.lineWidth = 2; ctx.font = "bold 11px sans-serif";
    for (const spec of specs) {
      const b = boxOf(spec);
      const x = disp.x + b.x * disp.w, y = disp.y + b.y * disp.h, w = b.w * disp.w, h = b.h * disp.h;
      ctx.strokeStyle = colorOf(spec);
      const filledW = getWidget(node, "filled");
      if (filledW && filledW.value) { ctx.fillStyle = ctx.strokeStyle; ctx.fillRect(x, y, w, h); }
      ctx.strokeRect(x, y, w, h);
      const lab = LABELS[spec.key] || "";
      if (lab) { ctx.fillStyle = ctx.strokeStyle; ctx.fillText(lab, x + 3, y + 12); }
    }
  }

  function loadImage() {
    const fn = upstreamFilename(node);
    if (!fn) { img = null; imgName = null; draw(); return; }
    if (fn === imgName) return;
    imgName = fn;
    const im = new Image();
    im.onload = () => { img = im; draw(); };
    im.onerror = () => { img = null; draw(); };
    im.src = viewUrl(fn);
  }

  function toNorm(e) {
    const r = canvas.getBoundingClientRect();
    const cx = (e.clientX - r.left) * (canvas.width / r.width);
    const cy = (e.clientY - r.top) * (canvas.height / r.height);
    return { nx: clamp01((cx - disp.x) / disp.w), ny: clamp01((cy - disp.y) / disp.h) };
  }
  const hit = (b, nx, ny) => nx >= b.x && nx <= b.x + b.w && ny >= b.y && ny <= b.y + b.h;
  const nearCorner = (b, nx, ny) => Math.abs(nx - (b.x + b.w)) < 0.06 && Math.abs(ny - (b.y + b.h)) < 0.06;

  canvas.addEventListener("mousedown", (e) => {
    e.preventDefault(); e.stopPropagation();
    const { nx, ny } = toNorm(e);
    for (const spec of specs) { const b = boxOf(spec); if (nearCorner(b, nx, ny)) { drag = { spec, mode: "resize", b0: { ...b } }; return; } }
    for (const spec of specs) { const b = boxOf(spec); if (hit(b, nx, ny)) { drag = { spec, mode: "move", b0: { ...b }, ox: nx, oy: ny }; return; } }
    const spec = (specs.length > 1 && e.shiftKey) ? specs[1] : specs[0];
    drag = { spec, mode: "new", ax: nx, ay: ny };
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!drag) return;
    e.preventDefault();
    const { nx, ny } = toNorm(e);
    if (drag.mode === "new") {
      const b = { x: Math.min(nx, drag.ax), y: Math.min(ny, drag.ay), w: Math.abs(nx - drag.ax), h: Math.abs(ny - drag.ay) };
      if (b.w > 0.01 && b.h > 0.01) { setBox(drag.spec, b); draw(); }
    } else if (drag.mode === "move") {
      setBox(drag.spec, { ...drag.b0, x: clamp01(drag.b0.x + (nx - drag.ox)), y: clamp01(drag.b0.y + (ny - drag.oy)) });
      draw();
    } else if (drag.mode === "resize") {
      setBox(drag.spec, { ...drag.b0, w: Math.max(0.01, nx - drag.b0.x), h: Math.max(0.01, ny - drag.b0.y) });
      draw();
    }
  });
  const end = () => { if (drag) { drag = null; node.setDirtyCanvas(true, true); } };
  canvas.addEventListener("mouseup", end);
  canvas.addEventListener("mouseleave", end);
  canvas.addEventListener("wheel", (e) => e.stopPropagation());

  setInterval(() => { loadImage(); draw(); }, 1200);
  loadImage(); draw();
  return canvas;
}

const CONFIG = {
  MoveBoxes: [
    { key: "src", colorName: "src_color", fallback: "red" },
    { key: "tgt", colorName: "tgt_color", fallback: "green" },
  ],
  DrawBox: [
    { key: "box", colorName: "color", fallback: "red" },
  ],
};

app.registerExtension({
  name: "qwen.objectmover.boxEditors",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const specs = nodeData && CONFIG[nodeData.name];
    if (!specs) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated ? onCreated.apply(this, arguments) : undefined;
      try {
        this.boxesEditor = makeEditor(this, specs);
        this.addDOMWidget("boxes", "BOXES", this.boxesEditor, { serialize: false });
      } catch (e) {
        console.warn("[boxEditor] error", e);
      }
      return r;
    };
  },
});
