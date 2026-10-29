/* PickFlow: interface web (sem build). Todo conteúdo do usuário entra no DOM via textContent. */
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'open') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = (c) => {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else el.append(c.nodeType ? c : document.createTextNode(String(c)));
  };
  kids.forEach(add);
  return el;
}
// Tradução (web/i18n.js): a página é traduzida sozinha ao ser desenhada; `tr` serve para textos montados em pedaços
// (cada pedaço traduzido antes de juntar) e para o que vai num campo editável.
const tr = (s) => (window.I18N ? window.I18N.tr(s) : s);
const pct = (p) => Math.round(p * 100) + '%';
const clone = (o) => JSON.parse(JSON.stringify(o));
function slug(s) {
  return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'item';
}
function uniqueId(base, taken) {
  let id = base, n = 2;
  while (taken.has(id)) id = `${base}_${n++}`;
  return id;
}
let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, init);
  if (!r.ok) {
    let d = null;
    try { d = (await r.json()).detail; } catch (_) { /* sem corpo */ }
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join('; ') : `Erro ${r.status}`);
  }
  return r.json();
}
function switchEl(label, checked, onchange) {
  const input = h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) });
  return h('label', { class: 'switch' }, input, h('span', { class: 'track' }), h('span', null, label));
}
function field(label, control, hint) {
  return h('div', null, h('label', { class: 'f' }, label), control, hint ? h('div', { class: 'hint' }, hint) : null);
}

// -------------------------------------------------------------------- estado
const S = {
  view: 'chat', conv: null, convs: [], workflows: [], clefCfg: { instructions: '', recipes: [] },
  settings: null, status: null, tray: [], route: 'auto', mode: 'auto', live: null, answered: new Set(), openKeys: new Set(), C: null,
  fb: new Map(),          // run_id -> opinião já dada ("up", "down", "other"): o que já foi julgado aparece marcado
  models: [], model: '',  // modelos de imagem que o chat oferece (/api/models) e o escolhido; com um só, o seletor nem aparece
};
const BASE_MODEL = 'qwen21';                     // o que não tem a tag `models` é do Qwen-Image 2.1
const forModel = (x) => { const t = x.models && x.models.length ? x.models : [BASE_MODEL]; return t.includes('*') || t.includes(S.model || BASE_MODEL); };
const wfById = (id) => S.workflows.find((w) => w.id === id);
// Pontos de extensão que os addons preenchem (veja window.PickFlow mais abaixo).
const AX = { buttons: [], resultActions: [], inputs: new Map(), settings: [], listeners: new Map(), info: [] };
function AXemit(event, data) {
  for (const fn of AX.listeners.get(event) || []) { try { fn(data, PickFlow); } catch (e) { console.error(`[addon] erro em "${event}":`, e); } }
}
function addonButtonEl(b) {
  return h('button', { class: 'attach', type: 'button', title: b.title || '', onclick: () => { try { b.onClick(PickFlow); } catch (e) { toast(String(e.message || e)); } } }, b.label);
}

async function loadAll() {
  [S.workflows, S.clefCfg, S.settings, S.convs] = await Promise.all([
    api('/api/workflows'), api('/api/clef-config'), api('/api/settings'), api('/api/conversations')]);
  loadLoras();
  refreshStatus();
  await loadModels();
}
// Os modelos de imagem que têm workflow no PickFlow e estão no ComfyUI (o padrão sempre); com um só, o seletor some.
async function loadModels() {
  try {
    const r = await api('/api/models');
    S.models = r.models || [];
    if (!S.models.some((m) => m.id === S.model)) S.model = r.default || BASE_MODEL;
  } catch (_) { S.models = []; S.model = S.model || BASE_MODEL; }
}
async function loadLoras() {
  try { S.lorasInfo = await api('/api/loras'); S.loras = S.lorasInfo.loras; } catch (_) { S.loras = S.loras || []; }
}
// "bfs_body, flymy=0.5, consistency_2000=0.82/0.8@turbo" <-> [{id, strength, strength_clip, mode}]
function lorasToText(list) {
  return (list || []).map((it) => {
    if (typeof it === 'string') return it;
    let t = it.id;
    if (it.strength != null) t += '=' + it.strength + (it.strength_clip != null && it.strength_clip !== it.strength ? '/' + it.strength_clip : '');
    if (it.mode) t += '@' + it.mode;
    return t;
  }).join(', ');
}
function textToLoras(text) {
  return text.split(',').map((x) => x.trim()).filter(Boolean).map((tok) => {
    const m = tok.match(/^([a-z0-9_]+)(?:=([\d.]+)(?:\/([\d.]+))?)?(?:@([a-z0-9_]+))?$/i);
    if (!m) return tok;
    const o = { id: m[1] };
    if (m[2] != null) o.strength = Number(m[2]);
    if (m[3] != null) o.strength_clip = Number(m[3]);
    if (m[4]) o.mode = m[4];
    return Object.keys(o).length === 1 ? o.id : o;
  });
}
function settingsToText(obj) { return Object.entries(obj || {}).map(([k, v]) => `${k} = ${typeof v === 'string' && v === '' ? '""' : v === false ? 'off' : v}`).join('\n'); }
function textToSettings(text) {
  const out = {};
  text.split('\n').forEach((line) => {
    const m = line.match(/^\s*([a-z0-9_]+)\s*=\s*(.*?)\s*$/i);
    if (!m) return;
    let v = m[2];
    if (v === '""' || v === "''") v = '';
    else if (/^(off|false|não|nao)$/i.test(v)) v = 'off';
    else if (/^-?\d+(\.\d+)?$/.test(v)) v = Number(v);
    out[m[1]] = v;
  });
  return out;
}
async function refreshStatus() {
  try { S.status = await api('/api/status'); } catch (_) { S.status = null; }
  if (S.view === 'chat' && S.C) renderPills();
}

// ------------------------------------------------------------------- navegação
function go(view) {
  S.view = view;
  document.querySelectorAll('#nav button').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  const main = $('#main');
  main.replaceChildren();
  S.C = null;
  const fn = VIEWS[view] || mountChat;
  try { fn(main); } catch (e) { main.append(h('div', { class: 'page' }, h('div', { class: 'card' }, h('b', null, 'Esta tela falhou: '), String(e && e.message || e)))); console.error(e); }
  AXemit('view', { view });
}
const VIEWS = { chat: mountChat, workflows: mountWorkflows, loras: mountLoras, recipes: mountRecipes, settings: mountSettings };
$('#nav').addEventListener('click', (e) => { const b = e.target.closest('button[data-view]'); if (b) go(b.dataset.view); });

function renderConvs() {
  const ul = $('#convs');
  ul.replaceChildren(...S.convs.map((c) => h('li', { class: S.conv && S.conv.id === c.id ? 'on' : '' },
    h('button', { class: 't', title: c.title, onclick: () => openConv(c.id) }, c.title),
    h('button', { class: 'x', title: 'Apagar conversa', 'aria-label': `Apagar ${c.title}`, onclick: async () => {
      await api(`/api/conversations/${c.id}`, { method: 'DELETE' });
      if (S.conv && S.conv.id === c.id) S.conv = null;
      S.convs = await api('/api/conversations');
      renderConvs();
      if (S.view === 'chat') go('chat');
    } }, '×'))));
}
async function openConv(id) {
  S.conv = await api(`/api/conversations/${id}`);
  S.live = null;
  renderConvs();
  go('chat');
  reattach();
  await loadFeedbackMarks();
  if (S.view === 'chat') renderFeed();
}
// se a execução ainda está rodando no servidor (ex.: a página foi recarregada), volta a acompanhá-la
function reattach() {
  if (S.conv && S.conv.active_run && !S.live) {
    S.live = { events: [], runId: S.conv.active_run };
    streamRun(S.conv.active_run);
    renderFeed(true);
  }
}
$('#new-conv').addEventListener('click', async () => {
  S.conv = await api('/api/conversations', { method: 'POST' });
  S.convs = await api('/api/conversations');
  S.tray = [];
  renderConvs();
  go('chat');
});

// ===================================================================== CHAT
function mountChat(main) {
  const title = h('h1', null, S.conv ? S.conv.title : 'Nova conversa');
  const pills = h('div', { class: 'row' });
  const feed = h('div', { class: 'feed' }, h('div', { class: 'inner' }));
  const ta = h('textarea', { rows: 1, placeholder: 'Peça uma imagem ou anexe fotos e diga o que fazer', 'aria-label': 'Mensagem' });
  const tray = h('div', { class: 'tray' });
  const file = h('input', { type: 'file', accept: 'image/*', multiple: true, class: 'hidden', onchange: (e) => { addFiles(e.target.files); e.target.value = ''; } });
  const routeSel = h('select', { 'aria-label': 'Workflow', onchange: (e) => { S.route = e.target.value; } });
  const modelSel = h('select', { 'aria-label': 'Modelo de imagem', class: 'hidden',
    title: 'Modelo que gera as imagens. Só aparecem os que têm workflow no PickFlow e estão instalados no ComfyUI.',
    onchange: (e) => { S.model = e.target.value; fillRoutes(); } });
  // Modo de geração escolhido por você (como o "esforço" de um LLM): vale para os workflows que têm mais de um
  // modo e substitui a pergunta que antes era feita ao Clef.
  const modeSel = h('select', { 'aria-label': 'Modo de geração', title: 'Rápido ou Qualidade: vale para os workflows que têm os dois modos',
    onchange: (e) => { S.mode = e.target.value; } },
    h('option', { value: 'auto' }, 'Modo: automático'),
    h('option', { value: 'turbo' }, 'Modo: rápido'),
    h('option', { value: 'quality' }, 'Modo: qualidade'));
  modeSel.value = S.mode || 'auto';
  const send = h('button', { class: 'btn', onclick: sendMessage }, 'Enviar');
  const addonBar = h('span', { class: 'addon-bar' }, AX.buttons.filter((b) => b.where === 'composer').map(addonButtonEl));
  const cancel = h('button', { class: 'btn secondary hidden', onclick: cancelRun }, 'Cancelar');
  const composer = h('div', { class: 'composer' }, tray, ta,
    h('div', { class: 'bar2' }, h('button', { class: 'attach', onclick: () => file.click(), title: 'Anexar imagens' }, 'Anexar imagem'),
      h('button', { class: 'attach', onclick: () => splatLibraryModal(), title: 'Modelos 3D que você guardou' }, '3D salvos'),
      modelSel, routeSel, modeSel, addonBar, h('span', { class: 'grow' }), cancel, send), file);
  main.append(h('div', { class: 'page-head' }, title, pills),
    h('div', { class: 'chat' }, feed,
      h('div', { class: 'composer-wrap' }, composer,
        h('div', { class: 'hint-line' }, 'A ordem das miniaturas define as imagens 1, 2, 3… que os workflows recebem. Enter envia, Shift+Enter quebra a linha.'))));
  S.C = { title, pills, feed, ta, tray, routeSel, modelSel, modeSel, send, cancel, composer };
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(); } });
  ta.addEventListener('input', autosize);
  ta.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  ['dragenter', 'dragover'].forEach((ev) => composer.addEventListener(ev, (e) => { e.preventDefault(); composer.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => composer.addEventListener(ev, (e) => { e.preventDefault(); composer.classList.remove('drag'); }));
  composer.addEventListener('drop', (e) => addFiles([...e.dataTransfer.files].filter((f) => f.type.startsWith('image/'))));
  fillModels();
  fillRoutes();
  loadModels().then(() => { if (S.C && S.C.modelSel === modelSel) { fillModels(); fillRoutes(); } });
  renderPills();
  renderTray();
  renderFeed();
  ta.focus();
  loadFeedbackMarks().then(() => { if (S.C) renderFeed(); });
}
function autosize() { const t = S.C.ta; t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 220) + 'px'; }
function fillModels() {
  const sel = S.C.modelSel;
  sel.replaceChildren(...S.models.map((m) => h('option', { value: m.id }, `Modelo: ${m.title}`)));
  sel.value = S.model;
  sel.classList.toggle('hidden', S.models.length < 2);
}
function fillRoutes() {
  const sel = S.C.routeSel;
  const wf = S.workflows.filter((w) => w.enabled && !w.internal && forModel(w));       // passos internos só rodam dentro dos casos de uso
  const rc = S.clefCfg.recipes.filter((r) => r.enabled && forModel(r));
  sel.replaceChildren(h('option', { value: 'auto' }, 'Automático: o Clef escolhe'),
    h('optgroup', { label: 'Forçar um workflow' }, wf.flatMap((w) => w.modes.length > 1
      ? [h('option', { value: w.id }, w.title), ...w.modes.map((m) => h('option', { value: `${w.id}@${m.id}` }, `${tr(w.title)} (${tr(m.title).toLowerCase()})`))]
      : [h('option', { value: w.id }, w.title)])),
    rc.length ? h('optgroup', { label: 'Forçar um caso de uso' }, rc.map((r) => h('option', { value: 'recipe__' + r.id }, r.name))) : null,
    S.settings && S.settings.agent && S.settings.agent.enabled ? h('option', { value: 'agent' }, 'Agente: montar um caso de uso novo para este pedido') : null);
  sel.value = [...sel.options].some((o) => o.value === S.route) ? S.route : 'auto';
  S.route = sel.value;                       // trocar de modelo pode tirar da lista o workflow que estava forçado
}
// Aviso de primeiros passos: só aparece quando falta ligar algo (ComfyUI, Clef, arquivos) e some sozinho depois.
function setupCard() {
  const st = S.status;
  if (!st) return null;
  const items = [];
  const actions = [];
  if (!st.comfy.ok) {
    items.push(['O ComfyUI não está respondendo.', `O PickFlow envia os pedidos para o ComfyUI, então ele precisa estar instalado e aberto. Abra-o e confira o endereço em Ajustes${st.comfy_detected_url ? ` (achei um em ${st.comfy_detected_url})` : ''}. Se ainda não tem, o assistente mostra como instalar.`]);
    actions.push(h('button', { class: 'btn', onclick: () => setupModal() }, 'Abrir o assistente'));
  } else if (st.setup && st.setup.total > 0 && !st.setup.dismissed) {
    const p = st.setup, parts = [];
    if (p.nodes) parts.push(`${p.nodes} ${p.nodes === 1 ? 'nó' : 'nós'}`);
    if (p.loras) parts.push(`${p.loras} ${p.loras === 1 ? 'LoRA' : 'LoRAs'}`);
    if (p.models) parts.push(`${p.models} ${p.models === 1 ? 'modelo base' : 'modelos base'}`);
    items.push(['Faltam arquivos no ComfyUI.', `Os workflows usam ${parts.join(', ')} que ainda não estão instalados. Você escolhe o que baixar; nada é baixado sem a sua confirmação.`]);
    actions.push(h('button', { class: 'btn', onclick: () => setupModal() }, 'Ver e instalar'),
      h('button', { class: 'btn secondary', onclick: async () => {
        try { await api('/api/setup/dismiss', { method: 'POST', body: { value: true } }); } catch (e) { toast(e.message); }
        await refreshStatus(); renderFeed();
      } }, 'Agora não'));
  }
  if (!st.clef.ok) {
    items.push(['O Clef não está respondendo.', 'Ligue o servidor do Clef: dê dois cliques em clef_server/start_clef.bat (Windows) ou rode bash clef_server/start_clef.sh (Linux/macOS); na primeira vez ele instala tudo sozinho e pergunta bf16 ou int4. Só para ver a interface funcionando, use o modo de teste.']);
    actions.push(h('button', { class: 'btn secondary', onclick: useMockClef }, 'Usar o modo de teste'));
  } else if (st.clef.mode === 'mock') {
    items.push(['O Clef está em modo de teste.', 'Ele escolhe por palavras em comum, sem o modelo. Aponte para o servidor do Clef em Ajustes para decisões de verdade.']);
  }
  if (!items.length) return null;
  if (!actions.length) actions.push(h('button', { class: 'btn secondary', onclick: () => go('settings') }, 'Abrir Ajustes'));
  return h('div', { class: 'card setup' }, h('h3', null, 'Antes de começar'),
    h('ul', null, items.map(([a, b]) => h('li', null, h('b', null, a + ' '), b))),
    h('div', { class: 'row' }, actions));
}
// Primeira vez sem o servidor do Clef: um clique troca para o modo de teste (escolhe por palavras em comum, sem o modelo).
async function useMockClef() {
  try {
    S.settings = await api('/api/settings', { method: 'PUT', body: { clef: { mode: 'mock' } } });
    toast('Modo de teste ligado. Para decisões de verdade, ligue o Clef e mude em Ajustes > Clef.');
  } catch (e) { toast(e.message); }
  await refreshStatus();
  renderFeed();
}
function renderPills() {
  const st = S.status, p = S.C.pills;
  if (!S.conv && !S.live) renderFeed(false);
  if (!st) { p.replaceChildren(h('span', { class: 'pill bad' }, h('i'), 'Servidor sem resposta')); return; }
  const comfy = st.comfy.ok
    ? h('button', { class: 'pill ok', onclick: () => go('settings') }, h('i'), 'ComfyUI conectado')
    : h('button', { class: 'pill bad', onclick: () => go('settings'), title: st.comfy.error }, h('i'), 'ComfyUI fora do ar');
  const clef = st.clef.ok
    ? h('button', { class: 'pill ' + (st.clef.mode === 'mock' ? 'warn' : 'ok'), onclick: () => go('settings') }, h('i'),
      st.clef.mode === 'mock' ? 'Clef em modo de teste' : 'Clef conectado')
    : h('button', { class: 'pill bad', onclick: () => go('settings'), title: st.clef.error }, h('i'), 'Clef sem resposta');
  p.replaceChildren(comfy, clef);
}
function renderTray() {
  const t = S.C.tray;
  t.replaceChildren(...S.tray.map((im, i) => h('div', { class: 'thumb', title: im.name },
    h('img', { src: im.url, alt: im.name }), h('span', { class: 'n' }, String(i + 1)),
    h('button', { class: 'rm', 'aria-label': `Remover imagem ${i + 1}`, onclick: () => { S.tray.splice(i, 1); renderTray(); } }, '×'))));
}
async function addFiles(files) {
  for (const f of [...files]) {
    const fd = new FormData();
    fd.append('file', f, f.name || 'imagem.png');
    try { S.tray.push(await api('/api/upload', { method: 'POST', body: fd })); renderTray(); }
    catch (e) { toast(e.message); }
  }
}

function userMsgView(m) {
  return h('div', { class: 'msg-user' },
    (m.images || []).length ? h('div', { class: 'thumbs' }, m.images.map((im, i) => h('div', { class: 'thumb', title: im.name },
      h('img', { src: im.url, alt: im.name }), h('span', { class: 'n' }, String(i + 1))))) : null,
    m.text ? h('div', { class: 'bubble', translate: 'no' }, m.text) : null);
}

function summarize(events, live) {
  const o = { status: null, memory: [], decisions: [], plan: null, steps: [], done: null, error: null, pending: null };
  for (const e of events) {
    if (e.type === 'status') o.status = e.text;
    else if (e.type === 'decision') o.decisions.push(e);
    else if (e.type === 'memory') o.memory.push(e.text);
    else if (e.type === 'plan') {
      const old = o.steps;
      o.plan = e;
      o.steps = e.steps.map((s, i) => (old[i] && old[i].workflow === s.workflow ? old[i] : { ...s, state: 'wait', images: [], extras: [] }));
    }
    else if (e.type === 'step_start') { const s = o.steps[e.index]; if (s) Object.assign(s, e, { state: 'running' }); }
    else if (e.type === 'progress') { const s = o.steps[e.index]; if (s) s.elapsed = e.elapsed; }
    else if (e.type === 'step_done') { const s = o.steps[e.index]; if (s) Object.assign(s, { state: 'done', images: e.images, extras: e.extras || [] }); }
    else if (e.type === 'needs_input') o.pending = live && !S.answered.has(e.input_id) ? e : null;
    else if (e.type === 'done') o.done = e;
    else if (e.type === 'error') o.error = e;
  }
  if (o.error) o.steps.forEach((s) => { if (s.state === 'running') s.state = 'failed'; });
  return o;
}
function shortSource(label) {
  if (!label) return 'não usada';
  const m = label.match(/^Imagem (\d+)/);
  return m ? `imagem ${m[1]}` : /anterior/i.test(label) ? 'resultado anterior' : label;
}
function presetName(wid, pid) {
  const w = wfById(wid);
  const p = w && w.presets.find((x) => x.id === pid);
  return p ? p.name : pid;
}
// Respostas das variáveis do prompt (quantas pessoas, o que trocar...) como etiquetas legíveis.
function varTags(s, w) {
  const p = w && w.presets.find((x) => x.id === s.preset);
  if (!p || !p.vars || !s.vars) return [];
  return p.vars.filter((v) => v.key in s.vars).map((v) => {
    const raw = s.vars[v.key];
    const opt = (v.options || []).find((o) => o.id === raw);
    const val = v.kind === 'angle' ? raw.replace(/^<orbit>\s*/, '').replace(/\.$/, '') : opt ? opt.name || opt.id : raw;
    return h('span', { class: 'tag' }, (v.label || v.key) + ': ', h('b', null, val));
  });
}
// <details> que lembra se está aberto: o feed é redesenhado a cada evento e sem isso fecharia sozinho.
function keptDetails(key, cls, ...children) {
  const d = h('details', { class: cls }, ...children);
  if (S.openKeys.has(key)) d.open = true;
  d.addEventListener('toggle', () => { if (d.open) S.openKeys.add(key); else S.openKeys.delete(key); });
  return d;
}
function whyView(decisions, ns) {
  if (!decisions.length) return null;
  return keptDetails(`${ns}:why:${decisions[0].step === undefined ? 'plan' : 'steps'}`, 'why', h('summary', null, 'Como o Clef decidiu'),
    decisions.map((d) => h('div', { class: 'why-q' },
      h('div', { class: 'qh' }, h('span', null, d.label), ': ', h('span', null, d.chosen_name),
        d.source === 'user' ? ' (escolha sua)' : d.source === 'mock' ? ' (modo de teste)' : d.source === 'único' ? ' (única opção possível)' : ''),
      d.options.map((o, i) => h('div', { class: 'bar' + (o.id === d.chosen ? ' top' : '') },
        h('span', { class: 'nm', title: o.name }, o.name), h('span', { class: 'tr' }, h('i', { style: `width:${Math.round(o.p * 100)}%` })),
        h('span', { class: 'pc' }, pct(o.p)))))));
}
function resultsView(images, small, actions = true, run = '') {
  return h('div', { class: 'results' }, images.map((im) => h('figure', { class: 'result' + (small ? ' small' : ''), style: 'margin:0' },
    h('a', { href: im.url, target: '_blank', rel: 'noopener' }, h('img', { src: im.url, alt: im.name || 'Resultado', loading: 'lazy' })),
    actions ? h('div', { class: 'acts' },
      h('button', { class: 'ghost sm', onclick: () => { S.tray.push(im); if (S.C) renderTray(); toast('Adicionada às imagens da próxima mensagem'); } }, 'Usar como entrada'),
      h('a', { class: 'ghost sm', href: im.url, download: im.name || 'resultado.png', style: 'text-decoration:none',
        onclick: run ? () => sendFeedback(run, 'download', '', true) : null }, 'Baixar'),
      im.splat ? h('button', { class: 'ghost sm', title: 'Abrir de novo o modelo 3D de onde este ângulo saiu', onclick: () => openSplatById(im.splat) }, 'Abrir o 3D') : null,
      AX.resultActions.map((a) => h('button', { class: 'ghost sm', title: a.title || '', onclick: () => { try { a.onClick(im, PickFlow); } catch (e) { toast(String(e.message || e)); } } }, a.label))) : null)));
}
function stateText(o, s, i) {
  const t = s.state === 'running' ? (o.pending ? 'aguardando você' : `gerando${s.elapsed ? ' ' + Math.round(s.elapsed) + ' s' : '…'}`)
    : s.state === 'done' ? 'pronto' : s.state === 'failed' ? 'falhou' : 'na fila';
  return o.steps.length > 1 ? `etapa ${i + 1} de ${o.steps.length}: ${t}` : t;
}
function botView(events, live, ns) {
  const o = summarize(events, live);
  const frag = h('div', { class: 'msg-bot' });
  if (o.plan) {
    const strip = h('div', { class: 'strip' });
    o.steps.forEach((s, i) => {
      if (i) strip.append(h('div', { class: 'link' }));
      const w = wfById(s.workflow);
      const tags = [];
      if (s.preset) tags.push(h('span', { class: 'tag' }, 'prompt: ', h('b', null, presetName(s.workflow, s.preset))));
      if (s.mode && w && w.modes.length > 1) tags.push(h('span', { class: 'tag' }, 'modo: ', h('b', null, (w.modes.find((m) => m.id === s.mode) || {}).title || s.mode)));
      varTags(s, w).forEach((t) => tags.push(t));
      (s.loras || []).forEach((l) => tags.push(h('span', { class: 'tag', title: l.origin === 'clef' ? 'O Clef ligou esta LoRA pelo pedido' : 'LoRA do prompt escolhido' },
        'LoRA: ', h('b', null, l.title), ' ', String(l.strength))));
      Object.entries(s.inputs || {}).forEach(([k, v]) => {
        const slot = w && w.images.find((x) => x.key === k);
        tags.push(h('span', { class: 'tag' }, h('b', null, slot ? slot.label : k), ': ', shortSource(v)));
      });
      strip.append(h('div', { class: 'frame ' + s.state },
        h('div', { class: 'frame-head' }, h('h3', null, s.title), h('span', { class: 'state', 'data-step': String(i) }, stateText(o, s, i))),
        tags.length ? h('div', { class: 'meta' }, tags) : null,
        s.prompt ? keptDetails(`${ns}:prompt:${i}`, 'prompt', h('summary', null, 'Prompt enviado ao workflow'), h('pre', { class: 'promptbox' }, s.prompt)) : null,
        s.state === 'done' && i < o.steps.length - 1 ? resultsView(s.images, true, true) : null));
    });
    frag.append(whyView(o.decisions.filter((d) => d.step === undefined), ns), strip);
    const stepDecisions = o.decisions.filter((d) => d.step !== undefined);
    if (stepDecisions.length) frag.append(whyView(stepDecisions, ns));
    o.memory.forEach((t) => frag.append(h('div', { class: 'hint memo' }, t)));
  } else if (o.decisions.length) frag.append(whyView(o.decisions, ns));
  if (o.pending) frag.append(h('div', { class: 'notice wait' }, h('span', { class: 'grow' }, o.pending.kind === 'draw' ? 'O workflow precisa que você desenhe na imagem.' : o.pending.kind === 'angle' ? 'Escolha para onde girar o ponto de vista.' : o.pending.kind === 'cutout' ? 'Confira o recorte: use este ou refaça marcando uma caixa.' : o.pending.kind === 'splat' ? 'O 3D está pronto: escolha o ângulo da câmera.' : 'O Clef ficou em dúvida e espera sua escolha.'),
    h('button', { class: 'btn', onclick: () => openInput(o.pending) }, 'Abrir')));
  if (o.done) {
    frag.append(resultsView(o.done.images, false, true, o.done.run || ''));
    if (o.done.run) frag.append(feedbackView(o.done));
  }
  if (o.error) frag.append(h('div', { class: 'notice err', role: 'alert' }, h('span', { class: 'grow' }, o.error.message)));
  if (live && !o.done && !o.error && !o.pending && !(o.steps.some((s) => s.state === 'running'))) {
    frag.append(h('div', { class: 'notice' }, h('span', { class: 'spin' }), h('span', { class: 'grow' }, o.status || 'Trabalhando…')));
  }
  return frag;
}
const EXAMPLES = ['Troque o rosto da imagem 1 pelo da imagem 2', 'Gere uma foto de uma praia ao amanhecer', 'Mostre a pessoa de costas', 'Deixe a foto mais escura e com cara de filme'];
// Um redesenho entre o mousedown e o mouseup cancela o clique (o nó clicado deixa de existir).
// Por isso, com o botão do mouse pressionado, o redesenho espera o clique terminar.
let pressed = false, pendingStick = null;
addEventListener('pointerdown', () => { pressed = true; }, true);
addEventListener('pointerup', () => {
  pressed = false;
  if (pendingStick !== null) setTimeout(() => { const st = pendingStick; pendingStick = null; if (st !== null) renderFeed(st); }, 80);
}, true);
addEventListener('pointercancel', () => { pressed = false; }, true);
function renderFeed(stick) {
  if (!S.C) return;
  if (pressed) { pendingStick = !!(stick || pendingStick); return; }
  const feed = S.C.feed, inner = feed.firstChild;
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 160;
  const nodes = [];
  const msgs = S.conv ? S.conv.messages : [];
  msgs.forEach((m, i) => nodes.push(m.role === 'user' ? userMsgView(m) : botView(m.events || [], false, `m${i}`)));
  if (S.live) nodes.push(botView(S.live.events, true, 'live'));
  if (!nodes.length) {
    nodes.push(h('div', { class: 'empty' }, setupCard(), h('h2', null, 'O que vamos criar?'),
      h('p', null, 'Escreva o pedido e, se precisar, anexe imagens. O Clef escolhe o workflow do ComfyUI, liga as imagens certas e encadeia mais de um quando fizer sentido.'),
      h('div', { class: 'chips' }, EXAMPLES.map((t) => h('button', { class: 'chip', onclick: () => { S.C.ta.value = tr(t); autosize(); S.C.ta.focus(); } }, t)))));
  }
  inner.replaceChildren(...nodes);
  S.C.send.disabled = !!S.live;
  S.C.cancel.classList.toggle('hidden', !S.live);
  S.C.title.textContent = S.conv ? S.conv.title : 'Nova conversa';
  if (stick || nearBottom) requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
}

async function sendMessage() {
  if (S.live) return;
  const text = S.C.ta.value.trim();
  if (!text && !S.tray.length) return;
  try {
    if (!S.conv) { S.conv = await api('/api/conversations', { method: 'POST' }); }
    const images = S.tray.slice();
    const body = { text, image_ids: images.map((i) => i.id), route: S.route === 'auto' ? null : S.route,
      mode: S.mode === 'auto' ? null : S.mode, model: S.models.length > 1 ? S.model : null };
    S.conv.messages.push({ role: 'user', text, images });
    S.C.ta.value = ''; autosize(); S.tray = []; renderTray();
    S.live = { events: [{ type: 'status', text: 'Enviando…' }], runId: null };
    renderFeed(true);
    const { run_id } = await api(`/api/conversations/${S.conv.id}/messages`, { method: 'POST', body });
    S.live.runId = run_id;
    streamRun(run_id);
  } catch (e) {
    S.live = null;
    toast(e.message);
    if (S.conv) S.conv = await api(`/api/conversations/${S.conv.id}`).catch(() => S.conv);
    renderFeed(true);
  }
}
function streamRun(runId) {
  const es = new EventSource(`/api/runs/${runId}/events`);
  const live = S.live;
  let ended = false;
  es.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    live.events = live.events.filter((x) => !(x.type === 'status' && x.text === 'Enviando…'));
    if (e.type === 'progress') {  // chega a cada segundo: troca só o texto, sem redesenhar o feed
      const k = live.events.findIndex((x) => x.type === 'progress' && x.index === e.index);
      if (k >= 0) live.events[k] = e; else live.events.push(e);
      if (S.live === live && S.C) {
        const el = S.C.feed.querySelector(`.msg-bot:last-child [data-step="${e.index}"]`);
        const o = summarize(live.events, true);
        if (el && o.steps[e.index]) { el.textContent = stateText(o, o.steps[e.index], e.index); return; }
        renderFeed();
      }
      return;
    }
    live.events.push(e);
    AXemit('run:event', e);
    if (e.type === 'needs_input') openInput(e);
    if (S.live === live) renderFeed();
  };
  es.addEventListener('end', async () => {
    ended = true; es.close();
    const finalEv = [...live.events].reverse().find((x) => x.type === 'done');
    S.live = null;
    AXemit('run:end', { runId, images: finalEv ? finalEv.images : [] });
    S.conv = await api(`/api/conversations/${S.conv.id}`);
    const idx = S.conv.messages.length - 1;
    for (const k of [...S.openKeys]) if (k.startsWith('live:')) { S.openKeys.delete(k); S.openKeys.add(`m${idx}:` + k.slice(5)); }
    S.convs = await api('/api/conversations');
    renderConvs(); renderFeed(true);
  });
  es.onerror = () => {
    if (ended) return;
    es.close();
    live.events.push({ type: 'error', message: 'A conexão com o servidor caiu antes do fim da execução.' });
    S.live = null; renderFeed(true);
  };
}
async function cancelRun() { if (S.live && S.live.runId) await api(`/api/runs/${S.live.runId}/cancel`, { method: 'POST' }); }

const OPEN_INPUTS = new Set();
async function openInput(ev) {
  if (S.answered.has(ev.input_id) || OPEN_INPUTS.has(ev.input_id)) return;      // o mesmo pedido não abre duas telas
  OPEN_INPUTS.add(ev.input_id);
  const runId = S.live && S.live.runId;
  let value = null;
  try {
    if (ev.kind === 'draw') value = await drawModal(ev);
    else if (ev.kind === 'angle') value = await angleModal(ev);
    else if (ev.kind === 'outpaint') value = await outpaintModal(ev);
    else if (ev.kind === 'pose') value = await poseModal(ev);
    else if (ev.kind === 'cutout') value = await cutoutModal(ev);
    else if (ev.kind === 'agent_recipe') value = await agentRecipeModal(ev);
    else if (ev.kind === 'splat') value = await splatModal(ev);
    else if (AX.inputs.has(ev.kind)) value = await AX.inputs.get(ev.kind)(ev, PickFlow);
    else value = await choiceModal(ev);
  } finally { OPEN_INPUTS.delete(ev.input_id); }
  if (value === null) { await cancelRun(); return; }
  S.answered.add(ev.input_id);
  try { await api(`/api/runs/${runId}/input`, { method: 'POST', body: { input_id: ev.input_id, value } }); }
  catch (e) { toast(e.message); S.answered.delete(ev.input_id); }
  renderFeed();
}

// ----------------------------------------------------------------- modais
function openModal(content, onEsc) {
  const ov = h('div', { class: 'overlay', role: 'dialog', 'aria-modal': 'true' }, content);
  const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onEsc && onEsc(); } };
  document.addEventListener('keydown', key, true);
  $('#modal-root').append(ov);
  const first = ov.querySelector('button, input'); if (first) first.focus();
  return () => { document.removeEventListener('keydown', key, true); ov.remove(); };
}
function choiceModal(ev) {
  return new Promise((resolve) => {
    let close;
    const pick = (v) => { close(); resolve(v); };
    close = openModal(h('div', { class: 'modal' },
      h('header', null, h('h2', null, ev.title), h('p', null, 'A porcentagem é a confiança do Clef em cada opção.')),
      h('div', { class: 'body' }, h('div', { class: 'choice-list' }, ev.options.map((o) => h('button', { class: 'choice', onclick: () => pick(o.id) },
        h('span', null, o.name), h('span', { class: 'p' }, pct(o.p)))))),
      h('footer', null, h('span', { class: 'grow' }), h('button', { class: 'btn secondary', onclick: () => pick('__auto__') }, 'Seguir a primeira opção do Clef'))), () => pick('__auto__'));
  });
}
// ===================================================================== FEEDBACK
// Opinião sobre a execução: alimenta o histórico que corrige as próximas decisões do Clef (pickflow/feedback.py).
// "Era isso" e "não era isso" falam da ROTA; "era outro workflow" ensina qual era o certo; "era outro prompt" diz
// que a rota estava certa e o prompt não (sem misturar o erro de um com o histórico do outro); baixar a imagem conta
// como um positivo fraco (sem perguntar nada).
const FB_LABEL = { up: 'Você disse: era isso.', down: 'Você disse: não era isso.',
  other: 'Você disse: era outro workflow.', preset: 'Você disse: o prompt era outro.',
  download: 'Contado como positivo: você baixou a imagem.' };
// O servidor guarda os votos; ao abrir a conversa, marca os botões já respondidos (antes a marcação morria com a
// página). Só preenche o que ainda não está na memória da página, para não desfazer um voto recém-enviado.
async function loadFeedbackMarks() {
  try {
    const r = await api('/api/feedback');
    for (const [run, verdict] of Object.entries((r && r.runs) || {})) if (!S.fb.has(run)) S.fb.set(run, verdict);
  } catch (e) { /* sem histórico: os botões ficam só sem marcação */ }
}
function feedbackView(done) {
  const run = done.run || '';
  const given = S.fb.get(run) || '';
  const presets = (done.decision && done.decision.presets) || [];
  const btn = (label, verdict, onclick, title) => h('button', {
    class: 'ghost sm' + (given === verdict ? ' on' : ''), title: title || '', onclick }, label);
  return h('div', { class: 'feedback' },
    h('span', { class: 'grow' }, given ? (FB_LABEL[given] || '') : 'Foi isso que você queria?'),
    btn('Era isso', 'up', () => sendFeedback(run, 'up'), 'A rota e o prompt estavam certos'),
    btn('Não era isso', 'down', () => sendFeedback(run, 'down'), 'A geração não era o que você pediu'),
    btn('Era outro workflow', 'other', () => otherRouteModal(run), 'O Clef escolheu o workflow errado'),
    btn('Era outro prompt', 'preset', () => otherPresetModal(run, presets),
      'O workflow estava certo, mas o prompt não era esse'));
}
async function sendFeedback(run, verdict, picked = '', quiet = false) {
  if (!S.conv || !run) return;
  const before = S.fb.get(run) || '';
  if (!quiet) { S.fb.set(run, verdict); renderFeed(); }
  try {
    await api(`/api/conversations/${S.conv.id}/feedback`, { method: 'POST', body: { run_id: run, verdict, picked } });
    if (!quiet) toast('Obrigado: isso ensina o Clef nos próximos pedidos.');
  } catch (e) {
    if (!quiet) { if (before) S.fb.set(run, before); else S.fb.delete(run); renderFeed(); toast(e.message); }
  }
}
// "Era outro workflow": escolhe a rota certa e manda junto com a opinião (vira exemplo para pedidos parecidos).
function otherRouteModal(run) {
  const wf = S.workflows.filter((w) => w.enabled && !w.internal);
  const rc = S.clefCfg.recipes.filter((r) => r.enabled);
  const sel = h('select', { 'aria-label': 'Workflow' },
    h('option', { value: '' }, 'Escolha o workflow certo'),
    h('optgroup', { label: 'Workflows' }, wf.map((w) => h('option', { value: w.id }, w.title))),
    rc.length ? h('optgroup', { label: 'Casos de uso' }, rc.map((r) => h('option', { value: 'recipe__' + r.id }, r.name))) : null);
  let close;
  const shut = () => { if (close) close(); };
  close = openModal(h('div', { class: 'modal' },
    h('header', null, h('h2', null, 'Qual workflow você queria?'),
      h('p', null, 'O pedido passa a valer como exemplo para pedidos parecidos.')),
    h('div', { class: 'body' }, h('div', { class: 'row' }, h('div', { class: 'grow' }, sel))),
    h('footer', null, h('span', { class: 'grow' }),
      h('button', { class: 'btn secondary', onclick: shut }, 'Cancelar'),
      h('button', { class: 'btn', onclick: () => {
        const v = sel.value;
        if (!v) { toast('Escolha o workflow certo.'); return; }
        shut(); sendFeedback(run, 'other', v);
      } }, 'Enviar'))), shut);
}
// "Era outro prompt": a rota estava certa e o prompt não. Lista os prompts dos workflows deste passo (o valor vai
// como "workflow/prompt", a mesma chave que o histórico usa).
function otherPresetModal(run, presets) {
  const wids = [...new Set((presets || []).map((p) => String(p).split('/')[0]))].filter(Boolean);
  const opts = [];
  wids.forEach((wid) => {
    const w = wfById(wid);
    if (!w) return;
    (w.presets || []).forEach((p) => opts.push({ v: `${wid}/${p.id}`, label: `${tr(w.title)}: ${tr(p.name)}` }));
  });
  if (!opts.length) { toast('Não consegui listar os prompts deste workflow.'); return; }
  const sel = h('select', { 'aria-label': 'Prompt' },
    h('option', { value: '' }, 'Escolha o prompt certo'), opts.map((o) => h('option', { value: o.v }, o.label)));
  let close;
  const shut = () => { if (close) close(); };
  close = openModal(h('div', { class: 'modal' },
    h('header', null, h('h2', null, 'Qual prompt era o certo?'),
      h('p', null, 'O workflow fica confirmado e só o prompt entra no histórico.')),
    h('div', { class: 'body' }, h('div', { class: 'row' }, h('div', { class: 'grow' }, sel))),
    h('footer', null, h('span', { class: 'grow' }),
      h('button', { class: 'btn secondary', onclick: shut }, 'Cancelar'),
      h('button', { class: 'btn', onclick: () => {
        const v = sel.value;
        if (!v) { toast('Escolha o prompt certo.'); return; }
        shut(); sendFeedback(run, 'preset', v);
      } }, 'Enviar'))), shut);
}
// Passos de um caso de uso em texto simples (confirmação do agente e proposta de fusão).
function recipeSteps(rec) {
  const src = (v) => v === 'previous' ? 'resultado do passo anterior' : v === 'none' ? 'não usa'
    : String(v).startsWith('user:') ? `imagem ${String(v).slice(5)}` : String(v).startsWith('role:') ? `papel "${((rec.roles || []).find((r) => r.key === String(v).slice(5)) || {}).label || String(v).slice(5)}"` : v;
  return h('ol', { class: 'recipe-steps' }, (rec.steps || []).map((s) => {
    const w = wfById(s.workflow);
    const slots = Object.entries(s.inputs || {}).map(([k, v]) => {
      const sl = w && w.images.find((x) => x.key === k);
      return `${sl ? tr(sl.label) : k} ← ${tr(src(v))}`;
    });
    return h('li', null, h('b', null, s.title || (w && w.title) || s.workflow),
      s.prompt ? h('span', { class: 'tag soft' }, `prompt ${s.prompt}`) : null,
      s.mode ? h('span', { class: 'tag soft' }, s.mode) : null,
      slots.length ? h('div', { class: 'sm muted' }, slots.join(' · ')) : null,
      s.request ? h('div', { class: 'sm' }, 'Instrução deste passo: ', h('i', null, s.request)) : null);
  }));
}
// O agente montou um caso de uso: você decide se usa e guarda, se usa só agora, ou se segue o plano do Clef.
function agentRecipeModal(ev) {
  return new Promise((resolve) => {
    let close;
    const pick = (v) => { close(); resolve(v); };
    const r = ev.recipe;
    close = openModal(h('div', { class: 'modal' },
      h('header', null, h('h2', null, ev.title), h('p', null, 'Nenhum caso de uso existente cobria o pedido todo. Este foi montado agora com o Clef e o LLM.')),
      h('div', { class: 'body stack' },
        h('div', null, h('b', null, r.name), h('p', { class: 'sm', style: 'margin-top:4px' }, r.description)),
        recipeSteps(r),
        (r.warnings || []).length ? h('ul', { class: 'sm warnlist' }, r.warnings.map((w) => h('li', null, w))) : null,
        (r.similar || []).length ? h('p', { class: 'sm muted' }, tr('Parecido com: {}. Dá para fundir depois em Casos de uso.').replace('{}', r.similar.map((x) => `${tr(x.name)} (${pct(x.score)})`).join(', '))) : null),
      h('footer', null,
        h('button', { class: 'btn secondary', onclick: () => pick('skip') }, 'Seguir o plano do Clef'),
        h('span', { class: 'grow' }),
        h('button', { class: 'btn secondary', onclick: () => pick('once') }, 'Usar só desta vez'),
        h('button', { class: 'btn', onclick: () => pick('save') }, 'Usar e guardar em teste'))), () => pick('skip'));
  });
}
// Proposta de fusão feita pelo LLM: nada é gravado até você aprovar aqui.
function mergeModal(res) {
  return new Promise((resolve) => {
    let close, mode = 'disable';
    const r = res.recipe;
    const modeSel = h('select', { 'aria-label': 'O que fazer com os originais', onchange: (e) => { mode = e.target.value; } },
      h('option', { value: 'disable' }, 'Desativar os originais (dá para reativar)'), h('option', { value: 'delete' }, 'Apagar os originais'));
    close = openModal(h('div', { class: 'modal' },
      h('header', null, h('h2', null, 'Fundir em um caso de uso só'), h('p', null, res.reason || '')),
      h('div', { class: 'body stack' },
        h('div', null, h('b', null, r.name), h('p', { class: 'sm', style: 'margin-top:4px' }, r.description)),
        r.roles && r.roles.length ? h('p', { class: 'sm muted' }, tr('Papéis (o Clef decide quem é quem): {}.').replace('{}', r.roles.map((x) => tr(x.label)).join(', '))) : null,
        recipeSteps(r),
        res.notes ? h('p', { class: 'sm' }, h('b', null, 'O que mudou: '), res.notes) : null,
        (res.warnings || []).length ? h('ul', { class: 'sm warnlist' }, res.warnings.map((w) => h('li', null, w))) : null,
        field('Os casos de uso originais', modeSel)),
      h('footer', null, h('span', { class: 'grow' }), h('button', { class: 'btn secondary', onclick: () => { close(); resolve(null); } }, 'Cancelar'),
        h('button', { class: 'btn', onclick: () => { close(); resolve({ mode }); } }, 'Aplicar a fusão'))), () => { close(); resolve(null); });
  });
}
// Revisão do recorte automático: aceita, ou refaz (desenhando uma ou VÁRIAS caixas no original, cada uma com seu tipo:
// pessoa ou objeto). Cada caixa vira um recorte separado, que depois você posiciona um a um.
function cutoutModal(ev) {
  return new Promise((resolve) => {
    let close, mode = ev.mode, ed = null;
    const modes = ev.modes || [];
    const finish = (v) => { close(); resolve(v); };
    const note = h('p', { class: ev.warning ? 'msg err' : 'muted sm' }, ev.warning ||
      'Se pegou o fundo, ou deixou de fora parte da pessoa, desenhe uma caixa em volta de cada coisa que você quer e clique em Refazer. Várias caixas dão vários recortes.');
    const img = new Image();
    const left = h('div', { class: 'cutcol' }, h('p', { class: 'muted sm' }, 'Original: arraste para desenhar caixas'));
    const redo = h('button', { class: 'btn secondary' }, 'Refazer');
    const clear = h('button', { class: 'btn secondary', onclick: () => { ed && ed.clear(); sync(); } }, 'Limpar caixas');
    function sync() {
      const n = ed ? ed.count() : 0;
      redo.textContent = n > 1 ? `Refazer dentro das ${n} caixas` : n === 1 ? 'Refazer dentro da caixa' : 'Refazer';
    }
    img.onload = () => {
      ed = boxEditor({ img, color: '#2B3FE0', multiple: true, numbered: true, max: 8, maxW: 360, maxH: 420,
        options: modes.map((m) => ({ id: m.id, label: m.title })), optKey: 'mode', optLabel: (n) => `A caixa ${n} é:`,
        defaultOpt: () => mode, boxes: (ev.boxes || []).map((x) => ({ b: x.b, opt: x.mode })), onChange: sync });
      left.append(ed.el, ed.side); sync();
    };
    img.src = ev.original_url;
    const results = ev.results && ev.results.length ? ev.results : [{ url: ev.result_url, warning: ev.warning }];
    const right = h('div', { class: 'cutcol' }, h('p', { class: 'muted sm' }, results.length > 1 ? `${results.length} recortes (o xadrez é a parte transparente)` : 'Recorte (o xadrez é a parte transparente)'),
      h('div', { class: 'cutlist' }, results.map((r, i) => h('div', { class: 'checker' }, h('img', { src: r.url, alt: `Recorte ${i + 1}` })))));
    const radios = modes.length > 1 ? h('div', { class: 'tools' }, h('span', null, 'Caixas novas e refazer sem caixa recortam:'), ...modes.map((m) =>
      h('label', { class: 'radio' }, h('input', { type: 'radio', name: 'cutmode', value: m.id, checked: m.id === mode, onchange: () => { mode = m.id; } }), ' ' + m.title))) : null;
    const accept = h('button', { class: 'btn', disabled: !!ev.warning, title: ev.warning ? 'Este recorte não serve: refaça' : '', onclick: () => finish({ action: 'accept' }) },
      results.length > 1 ? 'Usar estes recortes' : 'Usar este recorte');
    redo.addEventListener('click', () => finish({ action: 'redo', mode, boxes: ed ? ed.get().map((x) => ({ b: x.b, mode: x.opt || mode })) : [] }));
    close = openModal(h('div', { class: 'modal wide' }, h('header', null, h('h2', null, ev.title), note),
      h('div', { class: 'body' }, h('div', { class: 'cutrow' }, left, right), radios),
      h('footer', null, h('span', { class: 'grow' }), h('button', { class: 'btn secondary', onclick: () => finish(null) }, 'Cancelar execução'), clear, redo, accept)), () => finish(null));
    sync();
  });
}
// Giro de câmera: vista de cima de um círculo com o objeto no centro; a câmera começa em frente (embaixo).
function angleModal(ev) {
  return new Promise((resolve) => {
    let az = Number(ev.azimuth) || 0, elev = ev.elevation || 'eye', close;
    const steps = ev.steps || [45, 90, 135, 180];
    const R = 74, C = 100;
    const pos = (deg) => { const a = (deg * Math.PI) / 180; return [C + R * Math.sin(a), C + R * Math.cos(a)]; };   // 0° = frente (embaixo); + = direita
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 200 200'); svg.setAttribute('class', 'orbit');
    const label = h('div', { class: 'orbit-label' });
    const mk = (tag, attrs, txt) => { const e = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); if (txt) e.textContent = txt; return e; };
    const draw = () => {
      svg.replaceChildren(mk('circle', { cx: C, cy: C, r: R, class: 'ring' }), mk('circle', { cx: C, cy: C, r: 14, class: 'obj' }));
      const all = [0, ...steps, ...steps.filter((x) => x < 180).map((x) => -x)];
      all.forEach((d) => {
        const [x, y] = pos(d);
        const g = mk('g', { class: 'stop' + (Math.round(az) === d || (Math.abs(az) === 180 && Math.abs(d) === 180) ? ' on' : ''), tabindex: '0', role: 'button', 'aria-label': d === 0 ? 'Manter a frente' : `${Math.abs(d)} graus para a ${d > 0 ? 'direita' : 'esquerda'}` });
        g.append(mk('circle', { cx: x, cy: y, r: 11 }), mk('text', { x, y: y + 4, 'text-anchor': 'middle' }, d === 0 ? '0' : String(Math.abs(d))));
        g.addEventListener('click', () => { az = d; draw(); upd(); });
        g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); az = d; draw(); upd(); } });
        svg.append(g);
      });
      svg.append(mk('text', { x: C, y: 196, 'text-anchor': 'middle', class: 'cap' }, 'frente (como está a foto)'));
      const [cx, cy] = pos(az); svg.append(mk('line', { x1: C, y1: C, x2: cx, y2: cy, class: 'ray' }));
    };
    const upd = () => {
      const side = az === 0 ? 'sem girar' : Math.abs(az) === 180 ? 'por trás (180°)' : `${Math.abs(az)}° para a ${az > 0 ? 'direita' : 'esquerda'}`;
      label.textContent = `${tr(side)}, ${tr({ low: 'de baixo', eye: 'na altura dos olhos', high: 'de cima' }[elev])}`;
      elevBtns.forEach(([k, b]) => b.classList.toggle('on', k === elev));
    };
    const elevBtns = [['low', 'De baixo'], ['eye', 'Na altura'], ['high', 'De cima']].map(([k, t]) =>
      [k, h('button', { class: 'chip', type: 'button', onclick: () => { elev = k; upd(); } }, t)]);
    draw();
    const ok = () => { close(); resolve({ azimuth: az, elevation: elev }); };
    const cancel = () => { close(); resolve(null); };
    close = openModal(h('div', { class: 'modal' },
      h('header', null, h('h2', null, ev.title || 'Girar o ponto de vista'),
        h('p', null, 'Clique no lado para onde a câmera deve ir. Direita e esquerda são vistas da imagem, de frente para ela.')),
      h('div', { class: 'body angle-body' },
        ev.image_url ? h('img', { src: ev.image_url, alt: 'Imagem a girar', class: 'angle-img' }) : null,
        h('div', { class: 'stack' }, svg, label, h('div', { class: 'chips' }, elevBtns.map((x) => x[1])))),
      h('footer', null, h('button', { class: 'btn secondary', onclick: cancel }, 'Cancelar'), h('span', { class: 'grow' }), h('button', { class: 'btn', onclick: ok }, 'Girar assim'))), cancel);
    upd();
  });
}
// Mudar a pose: o esqueleto de cada pessoa (18 pontos do DWPose) sobre a foto. Arraste um ponto e o membro todo acompanha
// (Alt: só o ponto; Shift: estica o osso). Devolve { people } com os pontos em frações; quem não mexeu continua como estava.
const POSE_COLORS = ['#ff0000', '#ff5500', '#ffaa00', '#ffff00', '#aaff00', '#55ff00', '#00ff00', '#00ff55', '#00ffaa', '#00ffff',
  '#00aaff', '#0055ff', '#0000ff', '#5500ff', '#aa00ff', '#ff00ff', '#ff00aa', '#ff0055'];
const POSE_LIMBS = [[1, 2], [1, 5], [2, 3], [3, 4], [5, 6], [6, 7], [1, 8], [8, 9], [9, 10], [1, 11], [11, 12], [12, 13], [1, 0], [0, 14], [14, 16], [0, 15], [15, 17]];
const POSE_PARENT = { 0: 1, 2: 1, 3: 2, 4: 3, 5: 1, 6: 5, 7: 6, 8: 1, 9: 8, 10: 9, 11: 1, 12: 11, 13: 12, 14: 0, 15: 0, 16: 14, 17: 15 };
const POSE_MIRROR = [0, 1, 5, 6, 7, 2, 3, 4, 11, 12, 13, 8, 9, 10, 15, 14, 17, 16];
// onde fica cada ponto que o detector não viu, em unidades do tronco (pescoço-quadril), partindo do ponto "pai"
const POSE_GUESS = { 0: [0, -0.4], 2: [-0.28, 0.04], 5: [0.28, 0.04], 3: [0, 0.6], 6: [0, 0.6], 4: [0, 0.5], 7: [0, 0.5], 8: [-0.14, 1],
  11: [0.14, 1], 9: [0, 0.9], 12: [0, 0.9], 10: [0, 0.9], 13: [0, 0.9], 14: [-0.05, -0.05], 15: [0.05, -0.05], 16: [-0.06, 0.02], 17: [0.06, 0.02] };
function poseKids(i) {
  const out = [i];
  Object.entries(POSE_PARENT).forEach(([c, p]) => { if (p === i) out.push(...poseKids(Number(c))); });
  return out;
}
function poseModal(ev) {
  return new Promise((resolve) => {
    const [W, H] = ev.size, SVG = 'http://www.w3.org/2000/svg';
    const orig = ev.people.map((p) => p.map((q) => q.slice()));
    const cur = ev.people.map((p) => p.map((q) => q.slice()));
    const vis = (p) => p[2] >= 0.3;
    const clamp01 = (v) => Math.max(0, Math.min(1, v));
    const same = (a, b) => a.every((p, i) => vis(p) === vis(b[i]) && (!vis(p) || (Math.abs(p[0] - b[i][0]) < 0.004 && Math.abs(p[1] - b[i][1]) < 0.004)));
    let sel = 0, close;
    const sc = Math.min(1, Math.min(560, innerWidth - 420) / W, Math.max(260, innerHeight * 0.6) / H);
    const SW = Math.round(W * sc), SH = Math.round(H * sc);
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('width', SW); svg.setAttribute('height', SH); svg.setAttribute('class', 'pose-svg');
    const stage = h('div', { class: 'pose-stage', style: `width:${SW}px;height:${SH}px` }, h('img', { src: ev.image_url, alt: '', draggable: 'false' }), svg);
    const chips = h('div', { class: 'chips' });
    const status = h('div', { class: 'sm muted' });
    const apply = h('button', { class: 'btn', disabled: true, onclick: () => { close(); resolve({ people: cur }); } }, 'Aplicar a pose');
    const mk = (tag, attrs) => { const e = document.createElementNS(SVG, tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); return e; };
    const px = (p) => [p[0] * SW, p[1] * SH];

    function render() {
      svg.replaceChildren();
      const order = cur.map((_, k) => k).sort((a, b) => (a === sel) - (b === sel));        // a selecionada por cima
      order.forEach((k) => {
        const P = cur[k], on = k === sel, g = mk('g', { opacity: on ? 1 : 0.4 });
        POSE_LIMBS.forEach(([a, b], n) => {
          if (!vis(P[a]) || !vis(P[b])) return;
          const [x1, y1] = px(P[a]), [x2, y2] = px(P[b]);
          g.append(mk('line', { x1, y1, x2, y2, stroke: POSE_COLORS[n], 'stroke-width': on ? 5 : 3, 'stroke-linecap': 'round' }));
        });
        P.forEach((p, i) => {
          if (!vis(p)) return;
          const [x, y] = px(p);
          const dot = mk('circle', { cx: x, cy: y, r: on ? 6 : 4, fill: POSE_COLORS[i], stroke: '#fff', 'stroke-width': on ? 1.5 : 0.8 });
          const hit = mk('circle', { cx: x, cy: y, r: 11, fill: 'transparent', class: 'pose-hit' });
          hit.addEventListener('pointerdown', (e) => drag(k, i, e, hit));
          g.append(dot, hit);
        });
        svg.append(g);
      });
      chips.replaceChildren(...cur.map((P, k) => h('button', { type: 'button', class: 'chip' + (k === sel ? ' on' : ''), onclick: () => { sel = k; render(); } },
        tr(`Pessoa ${k + 1}`) + (same(P, orig[k]) ? '' : ' ✎'))));
      const moved = cur.filter((P, k) => !same(P, orig[k])).length;
      apply.disabled = !moved;
      status.textContent = moved ? tr(`${moved} pessoa(s) com a pose alterada.`) : tr('Arraste os pontos de quem você quer mudar de pose.');
    }
    function drag(k, i, e, el) {
      e.preventDefault(); sel = k; render();
      const P = cur[k], start = P.map((p) => p.slice());
      const par = POSE_PARENT[i];
      const px2 = (p) => [p[0] * W, p[1] * H];
      const len = par !== undefined && vis(start[par]) ? Math.hypot(...px2(start[i]).map((v, n) => v - px2(start[par])[n])) : null;
      const kids = poseKids(i).filter((j) => vis(start[j]));
      const r = svg.getBoundingClientRect();
      const mv = (e2) => {
        let nx = clamp01((e2.clientX - r.left) / SW), ny = clamp01((e2.clientY - r.top) / SH);
        if (len && !e2.shiftKey) {                                   // o osso mantém o tamanho: o ponto gira em volta do pai
          const [ax, ay] = px2(start[par]), dx = nx * W - ax, dy = ny * H - ay, d = Math.hypot(dx, dy) || 1;
          nx = clamp01((ax + (dx * len) / d) / W); ny = clamp01((ay + (dy * len) / d) / H);
        }
        const dx = nx - start[i][0], dy = ny - start[i][1];
        (e2.altKey ? [i] : kids).forEach((j) => { P[j][0] = clamp01(start[j][0] + dx); P[j][1] = clamp01(start[j][1] + dy); });
        render();
      };
      const end = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', end); };
      window.addEventListener('pointermove', mv); window.addEventListener('pointerup', end);
    }
    function mirror() {
      const P = cur[sel]; if (!vis(P[1])) return;
      const cx = P[1][0], old = P.map((p) => p.slice());
      POSE_MIRROR.forEach((from, i) => { P[i] = [clamp01(2 * cx - old[from][0]), old[from][1], old[from][2]]; });
      render();
    }
    function showMissing() {
      const P = cur[sel], torso = vis(P[1]) && vis(P[8]) && vis(P[11]) ? Math.hypot((P[1][0] - (P[8][0] + P[11][0]) / 2) * W, (P[1][1] - (P[8][1] + P[11][1]) / 2) * H)
        : (vis(P[2]) && vis(P[5]) ? Math.hypot((P[2][0] - P[5][0]) * W, (P[2][1] - P[5][1]) * H) * 1.5 : H * 0.25);
      [0, 2, 5, 3, 6, 4, 7, 8, 11, 9, 12, 10, 13, 14, 15, 16, 17].forEach((i) => {
        if (vis(P[i])) return;
        const par = POSE_PARENT[i], base = par !== undefined ? P[par] : null;
        if (!base || !vis(base)) return;
        P[i] = [clamp01(base[0] + (POSE_GUESS[i][0] * torso) / W), clamp01(base[1] + (POSE_GUESS[i][1] * torso) / H), 1];
      });
      render();
    }
    const reset = (k) => { orig[k].forEach((p, i) => { cur[k][i] = p.slice(); }); render(); };
    const cancel = () => { close(); resolve(null); };
    close = openModal(h('div', { class: 'modal wide' },
      h('header', null, h('h2', null, ev.title || 'Modele a pose'),
        h('p', null, 'Cada pessoa da foto aparece como um esqueleto. Arraste os pontos para montar a nova pose: o membro todo acompanha. Quem você não mexer continua como está.')),
      h('div', { class: 'body' }, h('div', { class: 'pose-row' }, h('div', { class: 'pose-wrap' }, stage),
        h('div', { class: 'stack pose-side' }, h('b', null, 'Pessoa'), chips,
          h('div', { class: 'row wrap' },
            h('button', { class: 'chip', type: 'button', onclick: mirror, title: 'Vira a pose desta pessoa para o outro lado' }, 'Espelhar'),
            h('button', { class: 'chip', type: 'button', onclick: showMissing, title: 'Acrescenta os pontos que o detector não viu (cortados pela borda da foto)' }, 'Mostrar pontos que faltam'),
            h('button', { class: 'chip', type: 'button', onclick: () => reset(sel) }, 'Restaurar esta pessoa')),
          h('p', { class: 'sm muted' }, 'Alt + arrastar mexe só no ponto. Shift + arrastar estica o osso. Arrastar o pescoço leva a pessoa inteira.'),
          status))),
      h('footer', null, h('button', { class: 'btn secondary', onclick: cancel }, 'Cancelar execução'), h('span', { class: 'grow' }),
        h('button', { class: 'btn secondary', onclick: () => { cur.forEach((_, k) => reset(k)); } }, 'Restaurar tudo'), apply)), cancel);
    render();
  });
}

// Outpaint: a foto no meio de uma tela cinza (#808080) que a LoRA de Outpaint completa. A pessoa arrasta as bordas ou digita
// quanto acrescentar em cada lado (em % do lado da foto), ou usa uma proporção pronta. Devolve {left, right, top, bottom}.
function outpaintModal(ev) {
  return new Promise((resolve) => {
    const [W, H] = ev.size, SIDES = ['left', 'right', 'top', 'bottom'];
    const LABEL = { left: 'Esquerda (%)', right: 'Direita (%)', top: 'Cima (%)', bottom: 'Baixo (%)' };
    const m = { left: 0, right: 0, top: 0, bottom: 0, ...(ev.expand || {}) };
    const clamp = (v) => Math.max(0, Math.min(1, Math.round(v * 100) / 100));
    SIDES.forEach((k) => { m[k] = clamp(m[k]); });
    const up32 = (n) => Math.ceil(n / 32) * 32;
    const img = h('img', { src: ev.image_url, alt: '', draggable: 'false' });
    const stage = h('div', { class: 'op-stage' }, img);
    const info = h('div', { class: 'sm' });
    const fields = {};
    let s = 1, close;
    const fit = () => Math.min(1, Math.min(560, innerWidth - 140) / (W * (1 + m.left + m.right)),
      Math.max(200, innerHeight * 0.48) / (H * (1 + m.top + m.bottom)));
    function layout(syncFields = true) {
      stage.style.width = W * (1 + m.left + m.right) * s + 'px';
      stage.style.height = H * (1 + m.top + m.bottom) * s + 'px';
      Object.assign(img.style, { left: W * m.left * s + 'px', top: H * m.top * s + 'px', width: W * s + 'px', height: H * s + 'px' });
      if (syncFields) SIDES.forEach((k) => { fields[k].value = String(Math.round(m[k] * 100)); });
      const cw = up32(W * (1 + m.left + m.right)), ch = up32(H * (1 + m.top + m.bottom)), mp = (cw * ch) / 1e6;
      info.className = 'sm ' + (mp > 4 ? 'badc' : 'muted');
      info.textContent = tr('Tamanho final: {}×{} px ({} MP)').replace('{}', cw).replace('{}', ch).replace('{}', mp.toFixed(1))
        + (mp > 4 ? ' ' + tr('Acima de 4 MP o ComfyUI fica bem mais lento.') : '');
    }
    const refit = () => { s = fit(); layout(); };
    SIDES.forEach((k) => {
      const horiz = k === 'left' || k === 'right';
      const grip = h('div', { class: 'op-grip ' + k, title: 'Arraste para acrescentar cinza deste lado', role: 'presentation' });
      grip.addEventListener('pointerdown', (e) => {
        e.preventDefault(); grip.setPointerCapture(e.pointerId);
        const x0 = e.clientX, y0 = e.clientY, v0 = m[k], s0 = s;
        const mv = (e2) => {
          const d = horiz ? (k === 'left' ? x0 - e2.clientX : e2.clientX - x0) : (k === 'top' ? y0 - e2.clientY : e2.clientY - y0);
          m[k] = clamp(v0 + d / ((horiz ? W : H) * s0));
          layout();
        };
        const end = () => { grip.removeEventListener('pointermove', mv); grip.removeEventListener('pointerup', end); grip.removeEventListener('pointercancel', end); refit(); };
        grip.addEventListener('pointermove', mv); grip.addEventListener('pointerup', end); grip.addEventListener('pointercancel', end);
      });
      stage.append(grip);
      fields[k] = h('input', { type: 'number', min: 0, max: 100, step: 5, 'aria-label': LABEL[k],
        oninput: (e) => { m[k] = clamp(Number(e.target.value || 0) / 100); s = fit(); layout(false); } });
    });
    // proporção pronta: acrescenta o cinza necessário, dividido igualmente nos dois lados
    const ratio = (r) => {
      const ew = W * (1 + m.left + m.right), eh = H * (1 + m.top + m.bottom);
      if (ew / eh < r) { const x = (eh * r - ew) / 2 / W; m.left = clamp(m.left + x); m.right = clamp(m.right + x); }
      else { const y = (ew / r - eh) / 2 / H; m.top = clamp(m.top + y); m.bottom = clamp(m.bottom + y); }
      refit();
    };
    const add = (f) => { SIDES.forEach((k) => { m[k] = clamp(m[k] + f); }); refit(); };
    const chip = (label, fn) => h('button', { class: 'chip', type: 'button', onclick: fn }, label);
    const side = (k) => h('label', { class: 'op-field' }, h('span', { class: 'sm' }, LABEL[k]), fields[k]);
    const ok = () => { close(); resolve({ left: m.left, right: m.right, top: m.top, bottom: m.bottom }); };
    const cancel = () => { close(); resolve(null); };
    close = openModal(h('div', { class: 'modal wide' },
      h('header', null, h('h2', null, ev.title || 'Ampliar a imagem (outpaint)'),
        h('p', null, 'Arraste as bordas ou digite quanto acrescentar em cada lado. O cinza é o que a LoRA de Outpaint vai completar com a continuação da cena; a foto original fica idêntica.')),
      h('div', { class: 'body stack' }, h('div', { class: 'op-wrap' }, stage),
        h('div', { class: 'op-fields' }, SIDES.map(side)),
        h('div', { class: 'chips' }, h('span', { class: 'sm muted' }, 'Acrescentar:'),
          chip('+10% em volta', () => add(0.1)), chip('+25% em volta', () => add(0.25)),
          h('span', { class: 'sm muted' }, 'Proporção:'),
          [['1:1', 1], ['4:3', 4 / 3], ['3:2', 3 / 2], ['16:9', 16 / 9], ['9:16', 9 / 16]].map(([t, r]) => chip(t, () => ratio(r)))),
        info),
      h('footer', null, h('button', { class: 'btn secondary', onclick: cancel }, 'Cancelar execução'), h('span', { class: 'grow' }),
        h('button', { class: 'btn secondary', onclick: () => { SIDES.forEach((k) => { m[k] = 0; }); refit(); } }, 'Zerar'),
        h('button', { class: 'btn', onclick: ok }, 'Completar com Outpaint'))), cancel);
    refit();
  });
}

// ------------------------------------------------------------- 3D (Gaussian Splat)
// O visualizador (web/splat.js: three.js + Spark) só é carregado quando um 3D é aberto.
let splatMod = null;
async function loadSplatModule() {
  if (!splatMod) splatMod = await import('/static/splat.js');
  return splatMod;
}
function dataUrlDownload(url, name) {
  const a = h('a', { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
}
// Palco do 3D: canvas + mouse/teclado como no Blender + botões. Dois modos: "Navegar" (o viewport do Blender: girar em volta
// de um ponto, arrastar a vista, zoom, vistas do teclado numérico) e "Andar" (o Walk do Blender: W A S D, olhar com o mouse).
// opts: url, aspect, size [w, h], image_url. Devolve { el, side, snapshot(), view(), dispose() }; antes de carregar,
// snapshot() devolve null.
const SPLAT_KEYS = {
  nav: [['Botão do meio (ou esquerdo)', 'girar em volta do ponto central'], ['Shift + arrastar, ou botão direito', 'arrastar a vista'],
    ['Ctrl + arrastar', 'zoom'], ['Roda', 'zoom na direção do mouse'], ['Duplo clique (ou Alt + clique do meio)', 'o ponto clicado vira o centro do giro'],
    ['Alt + esquerdo', 'igual ao botão do meio (mouse sem botão do meio)'],
    ['1 / 3 / 7 (Ctrl: lado oposto)', 'frente, direita, cima'], ['2 4 6 8 · setas', 'girar 15°'], ['Shift + 4 / 6', 'inclinar'],
    ['9', 'o outro lado'], ['0 ou Home', 'câmera da foto'], ['+ / −', 'zoom'], ['.', 'centro do giro no meio da cena'], ['Shift + `', 'modo Andar']],
  walk: [['W A S D (segurando)', 'andar'], ['Q / E', 'descer / subir'], ['Shift / Alt', 'mais rápido / mais devagar'],
    ['Arrastar', 'olhar em volta'], ['Botão direito + arrastar', 'arrastar a vista'], ['Roda', 'velocidade'], ['Enter ou Shift + `', 'voltar a Navegar']],
};
function splatStage(opts) {
  const box = h('div', { class: 'splat-box' });
  const veil = h('div', { class: 'splat-veil' }, h('span', { class: 'spin' }), ' Carregando o 3D…');
  const wrap = h('div', { class: 'splat-wrap', style: `aspect-ratio:${opts.aspect || 1}` }, box, veil);
  const readout = h('div', { class: 'splat-readout', 'aria-live': 'polite' }, 'Ângulo da foto');
  const keys = h('dl', { class: 'splat-keys' });
  let v = null, raf = 0, mode = 'nav';
  const deg = (n, pos, neg) => `${Math.abs(n)}° ${tr(n > 0 ? pos : neg)}`;
  const upd = () => {
    if (!v || raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const c = v.view();
      if (c.mode !== mode) setMode(c.mode, false);                      // Shift+` no próprio 3D troca o modo
      if (mode === 'nav') {
        const side = Math.abs(c.yaw) < 2 ? tr('de frente') : deg(c.yaw, 'para a direita', 'para a esquerda');
        const up = Math.abs(c.pitch) < 2 ? tr('na altura da foto') : deg(c.pitch, 'de cima', 'de baixo');
        readout.textContent = `${side}, ${up}` + (Math.abs(c.roll) >= 2 ? tr(`, inclinada ${Math.abs(c.roll)}°`) : '');
      } else {
        const parts = [Math.abs(c.look.yaw) < 2 ? tr('olhando para a frente') : tr('olhando') + ' ' + deg(c.look.yaw, 'para a direita', 'para a esquerda')];
        if (Math.abs(c.look.pitch) >= 2) parts.push(deg(c.look.pitch, 'para cima', 'para baixo'));
        readout.textContent = tr(`Andando (velocidade ${c.speed}): {}`).replace('{}', parts.join(', '));
      }
      if (fov.value !== String(c.fov)) fov.value = String(c.fov);
      fovOut.textContent = `${c.fov}°`;
    });
  };
  const nb = (label, title, what) => h('button', { class: 'chip', type: 'button', title, 'aria-label': title, onclick: () => { if (v) { v.nudge(what); upd(); v.canvas.focus(); } } }, label);
  const padNav = h('div', { class: 'splat-pad' },
    h('div', { class: 'splat-views' }, nb('Frente', 'Vista de frente (1)', 'view:front'), nb('Trás', 'Vista de trás (Ctrl+1)', 'view:back'),
      nb('Esquerda', 'Vista da esquerda (Ctrl+3)', 'view:left'), nb('Direita', 'Vista da direita (3)', 'view:right'),
      nb('Cima', 'Vista de cima (7)', 'view:top'), nb('Baixo', 'Vista de baixo (Ctrl+7)', 'view:bottom')),
    nb('◀', 'Girar para a esquerda (4)', 'left'), nb('▲', 'Girar para cima (8)', 'up'), nb('▼', 'Girar para baixo (2)', 'down'), nb('▶', 'Girar para a direita (6)', 'right'),
    nb('+', 'Aproximar (+)', 'in'), nb('−', 'Afastar (−)', 'out'), nb('⟲', 'Inclinar para a esquerda (Shift+4)', 'rollLeft'), nb('⟳', 'Inclinar para a direita (Shift+6)', 'rollRight'),
    nb('Outro lado', 'O mesmo ângulo, do outro lado (9)', 'opposite'), nb('Centro', 'Girar em volta do meio da cena (.)', 'center'));
  const padWalk = h('div', { class: 'splat-pad hidden' },
    nb('↶', 'Olhar para a esquerda', 'lookLeft'), nb('↑', 'Olhar para cima', 'lookUp'), nb('↓', 'Olhar para baixo', 'lookDown'), nb('↷', 'Olhar para a direita', 'lookRight'),
    nb('Avançar', 'Andar para a frente (W)', 'fwd'), nb('Recuar', 'Andar para trás (S)', 'back'), nb('⇠', 'Andar para a esquerda (A)', 'stepLeft'), nb('⇢', 'Andar para a direita (D)', 'stepRight'),
    nb('Subir', 'Subir (E)', 'rise'), nb('Descer', 'Descer (Q)', 'sink'));
  const modeBtns = [['nav', 'Navegar'], ['walk', 'Andar']].map(([id, label]) =>
    h('button', { class: 'seg' + (id === mode ? ' on' : ''), type: 'button', 'aria-pressed': String(id === mode), onclick: () => setMode(id) }, label));
  function setMode(id, tell = true) {
    mode = id;
    if (v && tell) { v.setMode(id); v.canvas.focus(); }
    modeBtns.forEach((b, i) => { const on = ['nav', 'walk'][i] === id; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); });
    padNav.classList.toggle('hidden', id !== 'nav');
    padWalk.classList.toggle('hidden', id !== 'walk');
    keys.replaceChildren(...SPLAT_KEYS[id].flatMap(([a, b]) => [h('dt', null, a), h('dd', null, b)]));
    if (v) v.canvas.setAttribute('aria-label', id === 'nav' ? 'Modelo 3D: arraste para girar em volta' : 'Modelo 3D: W A S D para andar, arraste para olhar');
    upd();
  }
  const fov = h('input', { type: 'range', min: '15', max: '100', step: '1', value: '35', 'aria-label': 'Lente (campo de visão)',
    oninput: (e) => { if (v) v.setFov(Number(e.target.value)); } });
  const fovOut = h('span', { class: 'sm muted' }, '35°');
  const side = h('div', { class: 'splat-side' },
    opts.image_url ? h('figure', { class: 'splat-orig' }, h('img', { src: opts.image_url, alt: 'Foto original' }), h('figcaption', null, 'Foto original')) : null,
    h('div', { class: 'segs', role: 'group', 'aria-label': 'Modo de navegação' }, modeBtns),
    readout, padNav, padWalk,
    h('label', { class: 'splat-fov' }, h('span', { class: 'sm' }, 'Lente'), fov, fovOut),
    h('button', { class: 'chip', type: 'button', title: 'Voltar à câmera da foto original (0 ou Home)', onclick: () => { if (v) { v.home(); upd(); } } }, 'Voltar ao ângulo da foto'),
    h('details', { class: 'splat-help' }, h('summary', null, 'Atalhos (como no Blender)'), keys,
      h('p', { class: 'muted sm' }, 'Clique no 3D antes de usar o teclado. O teclado numérico funciona também pela fileira de números. O cinza é o que o 3D não viu na foto.')));
  setMode('nav');
  let disposed = false;
  loadSplatModule().then((mod) => {
    if (disposed) return;
    v = mod.createViewer({ container: box, url: opts.url, aspect: opts.aspect, onChange: upd,
      onReady: () => { veil.remove(); upd(); if (opts.onReady) opts.onReady(); },
      onError: (msg) => { veil.textContent = `Não consegui abrir o 3D: ${msg}`; } });
    setMode(mode);
  }).catch((e) => { veil.textContent = `Não consegui carregar o visualizador 3D (${e.message || e}). O navegador precisa de WebGL2.`; });
  return {
    el: wrap, side,
    snapshot: () => (v ? v.snapshot(opts.size[0], opts.size[1]) : null),
    view: () => (v ? v.view() : {}),
    dispose: () => { disposed = true; if (v) v.dispose(); },
  };
}
function splatSaveRow(sp) {
  const name = h('input', { type: 'text', value: sp.name || '', placeholder: 'Nome (ex.: quarto do Batman)', 'aria-label': 'Nome do 3D' });
  const btn = h('button', { class: 'btn secondary', type: 'button' }, sp.saved ? 'Guardado na biblioteca' : 'Guardar na biblioteca');
  btn.addEventListener('click', async () => {
    try {
      const r = await api(`/api/splats/${sp.id}`, { method: 'PUT', body: { name: name.value.trim(), saved: true } });
      Object.assign(sp, r);
      btn.textContent = 'Guardado na biblioteca';
      toast('3D guardado: use de novo pelo botão "3D salvos" da conversa.');
    } catch (e) { toast(e.message); }
  });
  return h('div', { class: 'row splat-save' }, name, btn);
}
// needs_input kind 'splat': escolher o ângulo e decidir entre o render deste ângulo e a foto completa (passo seguinte).
function splatModal(ev) {
  return new Promise((resolve) => {
    let close;
    const sp = ev.splat;
    const person = ev.mode === 'pessoa';
    const stage = splatStage({ url: sp.url, aspect: ev.aspect, size: ev.size, image_url: ev.image_url, onReady: () => btns.forEach((b) => { b.disabled = false; }) });
    const finish = (v) => { stage.dispose(); close(); resolve(v); };
    const take = (action) => {
      const image = stage.snapshot();
      if (!image) { toast('O 3D ainda está carregando.'); return; }
      finish({ action, image, view: stage.view() });
    };
    const png = h('button', { class: 'btn secondary', disabled: true, onclick: () => { const u = stage.snapshot(); if (u) dataUrlDownload(u, 'angulo.png'); } }, 'Baixar PNG');
    const file = h('a', { class: 'btn secondary', href: sp.url, download: `${slug(sp.name || (person ? 'pessoa' : 'ambiente'))}.spz`, style: 'text-decoration:none' }, 'Baixar 3D (.spz)');
    // o passo seguinte é o principal: pessoa -> AnyAngle refaz a foto; ambiente -> Outpaint completa o cinza do render
    const render = h('button', { class: ev.then_title ? 'btn secondary' : 'btn', disabled: true, onclick: () => take('render') }, person ? 'Usar só o render' : 'Usar o render com o cinza');
    const final = ev.then_title ? h('button', { class: 'btn', disabled: true, title: ev.then_title, onclick: () => take('final') },
      person ? 'Gerar a foto neste ângulo' : 'Completar o cinza (Outpaint)') : null;
    const btns = [png, render, final].filter(Boolean);
    const help = person
      ? 'Gire a pessoa até o ângulo que você quer. Depois o AnyAngle refaz a foto nesse ângulo, mantendo a pessoa e o estilo.'
      : 'Gire a câmera até o ângulo que você quer. "Completar o cinza (Outpaint)" manda o render deste ângulo para o Editar com a LoRA de Outpaint, que preenche o cinza (o que a foto não mostrava) continuando o ambiente; "Usar o render com o cinza" entrega o render como está.';
    close = openModal(h('div', { class: 'modal wide splat-modal' },
      h('header', null, h('h2', null, ev.title), h('p', null, help)),
      h('div', { class: 'body' }, h('div', { class: 'splat-row' }, stage.el, h('div', { class: 'splat-col' }, stage.side, splatSaveRow(sp)))),
      h('footer', null, h('button', { class: 'btn secondary', onclick: () => finish(null) }, 'Cancelar execução'), h('span', { class: 'grow' }),
        file, png, render, final)), () => finish(null));
  });
}
// Visualizador avulso (biblioteca ou "Abrir o 3D" num resultado): girar, baixar o PNG e o .spz, guardar, usar na conversa.
function splatViewerModal(sp) {
  const src = sp.source || {};
  const aspect = src.width && src.height ? src.width / src.height : 1;
  const k = Math.sqrt(1048576 / Math.max(1, (src.width || 1024) * (src.height || 1024)));
  const size = [Math.max(64, Math.floor(((src.width || 1024) * k) / 16) * 16), Math.max(64, Math.floor(((src.height || 1024) * k) / 16) * 16)];
  const stage = splatStage({ url: sp.url, aspect, size, image_url: src.url, onReady: () => { png.disabled = false; } });
  let close;
  const done = () => { stage.dispose(); close(); };
  const png = h('button', { class: 'btn secondary', disabled: true, onclick: () => { const u = stage.snapshot(); if (u) dataUrlDownload(u, 'angulo.png'); } }, 'Baixar PNG');
  const use = src.url ? h('button', { class: 'btn', title: 'Anexa a foto original e já escolhe o fluxo 3D: ao enviar, o 3D guardado é reaproveitado', onclick: () => {
    useSplatInChat(sp); done();
  } }, 'Usar na conversa') : null;
  close = openModal(h('div', { class: 'modal wide splat-modal' },
    h('header', null, h('h2', null, sp.name || (sp.kind === 'pessoa' ? 'Pessoa em 3D' : 'Ambiente em 3D')),
      h('p', null, 'Gire com o mouse e baixe o PNG do ângulo. "Usar na conversa" anexa a foto de origem e escolhe o fluxo 3D, sem gerar o 3D de novo.')),
    h('div', { class: 'body' }, h('div', { class: 'splat-row' }, stage.el, h('div', { class: 'splat-col' }, stage.side, splatSaveRow(sp)))),
    h('footer', null, h('button', { class: 'btn secondary', onclick: done }, 'Fechar'), h('span', { class: 'grow' }),
      h('a', { class: 'btn secondary', href: sp.url, download: `${slug(sp.name || sp.kind || '3d')}.spz`, style: 'text-decoration:none' }, 'Baixar 3D (.spz)'), png, use)), done);
}
async function openSplatById(id) {
  try {
    const all = await api('/api/splats?all=true');
    const sp = all.find((x) => x.id === id);
    if (!sp) { toast('Este 3D não existe mais (os não guardados são apagados com o tempo).'); return; }
    splatViewerModal(sp);
  } catch (e) { toast(e.message); }
}
function useSplatInChat(sp) {
  const src = sp.source || {};
  const m = String(src.url || '').match(/\/files\/([^/]+)$/);
  if (!m) { toast('A foto de origem deste 3D não existe mais.'); return; }
  const id = m[1].replace(/\.[^.]+$/, '');
  if (S.view !== 'chat') go('chat');
  S.tray = [{ id, name: src.label || m[1], url: src.url, width: src.width, height: src.height }];
  S.route = sp.kind === 'pessoa' ? 'recipe__pessoa_angulo' : 'ambiente_3d';
  if (S.C) {
    renderTray(); fillRoutes();
    if (S.C.routeSel.value !== S.route) toast('O fluxo 3D está desligado em Workflows ou Casos de uso: o Clef vai escolher.');
    if (!S.C.ta.value.trim()) { S.C.ta.value = tr(sp.kind === 'pessoa' ? 'Mudar o ângulo da pessoa' : 'Mudar o ângulo do ambiente'); autosize(); }
    S.C.ta.focus();
  }
  toast('Foto anexada: envie para escolher o ângulo (o 3D guardado é reaproveitado).');
}
async function splatLibraryModal() {
  let list;
  try { list = await api('/api/splats?all=true'); } catch (e) { toast(e.message); return; }
  let close;
  const body = h('div', { class: 'body' });
  const draw = () => {
    const saved = list.filter((x) => x.saved), recent = list.filter((x) => !x.saved);
    const card = (sp) => h('div', { class: 'splat-card' },
      sp.source && sp.source.url ? h('img', { src: sp.source.url, alt: '', loading: 'lazy' }) : h('div', { class: 'splat-noimg' }, '3D'),
      h('div', { class: 'grow' }, h('b', null, sp.name || (sp.kind === 'pessoa' ? 'Pessoa' : 'Ambiente')),
        h('div', { class: 'sm muted' }, `${tr(sp.kind === 'pessoa' ? 'Pessoa' : 'Ambiente')} · ${new Date((sp.created || 0) * 1000).toLocaleString()} · ${fmtBytes(sp.size || 0)}`)),
      h('div', { class: 'row' },
        h('button', { class: 'ghost sm', onclick: () => { close(); splatViewerModal(sp); } }, 'Abrir'),
        h('button', { class: 'ghost sm', onclick: () => { close(); useSplatInChat(sp); } }, 'Usar na conversa'),
        h('button', { class: 'ghost sm', 'aria-label': `Apagar ${sp.name || 'este 3D'}`, onclick: async () => {
          if (!confirm('Apagar este modelo 3D? O arquivo .spz é removido.')) return;
          try { await api(`/api/splats/${sp.id}`, { method: 'DELETE' }); list = list.filter((x) => x.id !== sp.id); draw(); } catch (e) { toast(e.message); }
        } }, 'Apagar')));
    body.replaceChildren(h('div', null,
      h('h3', null, 'Guardados'),
      saved.length ? h('div', { class: 'splat-list' }, saved.map(card)) : h('p', { class: 'muted sm' }, 'Nenhum ainda. Na tela do 3D, clique em "Guardar na biblioteca".'),
      recent.length ? h('h3', null, 'Recentes (não guardados: os mais antigos saem sozinhos)') : null,
      recent.length ? h('div', { class: 'splat-list' }, recent.map(card)) : null));
  };
  draw();
  close = openModal(h('div', { class: 'modal' },
    h('header', null, h('h2', null, '3D salvos'), h('p', null, 'Modelos 3D gerados a partir das suas fotos. A mesma foto nunca é gerada duas vezes enquanto o 3D existir aqui.')),
    body, h('footer', null, h('span', { class: 'grow' }), h('button', { class: 'btn secondary', onclick: () => close() }, 'Fechar'))), () => close());
}
const BOX_COLORS = { red: '#E5392F', green: '#12A150', white: '#2B3FE0' };
// Editor de caixas sobre uma imagem. Arraste no fundo para desenhar; clique numa caixa para selecionar (aparece um × no
// canto superior esquerdo que a tira); arraste a caixa selecionada para mover e os cantos para mudar o tamanho.
// opts: img, color, multiple, numbered, options [{id, label, url}] + optKey ('ref', 'mode'...), boxes [{b, opt}],
//       max, maxW/maxH, onChange(boxes). Devolve { el, side, get(), count() }.
function fitImage(img, maxW, maxH) {
  const s = Math.min(1, maxW / img.naturalWidth, maxH / img.naturalHeight);
  return [Math.round(img.naturalWidth * s), Math.round(img.naturalHeight * s)];
}
function boxEditor(o) {
  const col = o.color || '#2B3FE0', max = o.multiple ? (o.max || 12) : 1, opts = o.options || [];
  const [W, H] = fitImage(o.img, o.maxW || Math.min(640, window.innerWidth - 80), o.maxH || Math.max(240, window.innerHeight * 0.52));
  let boxes = (o.boxes || []).map((b) => ({ b: b.b.slice(), opt: b.opt })), sel = -1;
  const layer = h('div', { class: 'boxlayer' });
  const stage = h('div', { class: 'stage boxed', style: `width:${W}px;height:${H}px;--bx:${col}` }, h('img', { src: o.img.src, alt: '' }), layer);
  const side = h('div', { class: 'boxside' });
  const optOf = (i) => opts.find((x) => x.id === boxes[i].opt);
  const changed = () => { o.onChange && o.onChange(get()); };
  const get = () => boxes.map((b) => ({ b: b.b.map((n) => Math.round(n * 10000) / 10000), opt: b.opt }));
  const defaultOpt = (i) => {
    if (!opts.length) return undefined;
    const used = new Set(boxes.map((b) => b.opt));
    return (o.defaultOpt && o.defaultOpt(i, boxes)) || (opts.find((x) => !used.has(x.id)) || opts[i % opts.length]).id;
  };
  const pos = (e) => { const r = stage.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
  const clamp = (b) => { b[2] = Math.min(b[2], 1); b[3] = Math.min(b[3], 1); b[0] = Math.min(Math.max(0, b[0]), 1 - b[2]); b[1] = Math.min(Math.max(0, b[1]), 1 - b[3]); return b; };
  function remove(i) { boxes.splice(i, 1); sel = Math.min(sel, boxes.length - 1); if (i < sel) sel--; if (!boxes.length) sel = -1; render(); changed(); }
  function render() {
    layer.replaceChildren();
    boxes.forEach((bx, i) => {
      const on = i === sel, op = optOf(i);
      const tag = [o.numbered || boxes.length > 1 ? String(i + 1) : '', op ? op.label : ''].filter(Boolean).join(' · ');
      const el = h('div', { class: 'bx' + (on ? ' on' : ''), tabindex: '0', role: 'button', 'aria-label': tr(`Caixa ${i + 1}`) + (op ? ', ' + tr(op.label) : ''),
        style: `left:${bx.b[0] * 100}%;top:${bx.b[1] * 100}%;width:${bx.b[2] * 100}%;height:${bx.b[3] * 100}%` }, tag ? h('span', { class: 'bxtag' }, tag) : null);
      el.addEventListener('pointerdown', (e) => {
        if (e.target.closest('.bxdel, .hd')) return;
        e.stopPropagation();
        const pid = e.pointerId, p0 = pos(e), b0 = bx.b.slice();
        if (sel !== i) { sel = i; render(); }                       // selecionar redesenha (aparecem o × e os cantos)
        const cur = layer.children[i];
        cur.setPointerCapture(pid); cur.focus({ preventScroll: true });
        let moved = false;
        const mv = (ev) => { const p = pos(ev); if (Math.abs(p[0] - p0[0]) + Math.abs(p[1] - p0[1]) > 0.004) moved = true;
          if (moved) { bx.b = clamp([b0[0] + p[0] - p0[0], b0[1] + p[1] - p0[1], b0[2], b0[3]]); live(cur, bx); } };
        const up = () => { cur.removeEventListener('pointermove', mv); cur.removeEventListener('pointerup', up); if (moved) changed(); };
        cur.addEventListener('pointermove', mv); cur.addEventListener('pointerup', up);
      });
      el.addEventListener('keydown', (e) => { if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); remove(i); } });
      if (on) {
        el.append(h('button', { class: 'bxdel', type: 'button', title: 'Tirar esta caixa', 'aria-label': `Tirar a caixa ${i + 1}`,
          onclick: (e) => { e.stopPropagation(); remove(i); } }, '×'));
        for (const c of ['ne', 'sw', 'se']) {
          const hd = h('span', { class: 'hd ' + c });
          hd.addEventListener('pointerdown', (e) => {
            e.stopPropagation(); hd.setPointerCapture(e.pointerId);
            const b0 = bx.b.slice(), ax = c.includes('w') ? b0[0] + b0[2] : b0[0], ay = c.includes('n') ? b0[1] + b0[3] : b0[1];
            const mv = (ev) => { const p = pos(ev); bx.b = [Math.min(ax, p[0]), Math.min(ay, p[1]), Math.max(0.02, Math.abs(p[0] - ax)), Math.max(0.02, Math.abs(p[1] - ay))]; live(el, bx); };
            const up = () => { hd.removeEventListener('pointermove', mv); hd.removeEventListener('pointerup', up); changed(); };
            hd.addEventListener('pointermove', mv); hd.addEventListener('pointerup', up);
          });
          el.append(hd);
        }
      }
      layer.append(el);
    });
    renderSide();
  }
  function live(el, bx) { Object.assign(el.style, { left: bx.b[0] * 100 + '%', top: bx.b[1] * 100 + '%', width: bx.b[2] * 100 + '%', height: bx.b[3] * 100 + '%' }); }
  function renderSide() {
    side.replaceChildren();
    if (!opts.length || sel < 0) return;
    side.append(h('div', { class: 'boxpick' }, h('span', { class: 'muted sm' }, o.optLabel ? o.optLabel(sel + 1) : `Caixa ${sel + 1}:`),
      opts.map((x) => h('button', { type: 'button', class: 'chip pickopt' + (boxes[sel].opt === x.id ? ' on' : ''), title: x.label,
        onclick: () => { boxes[sel].opt = x.id; render(); changed(); } }, x.url ? h('img', { src: x.url, alt: '' }) : null, x.label))));
  }
  stage.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.bx')) return;
    stage.setPointerCapture(e.pointerId);
    const p0 = pos(e); let b = null;
    const mv = (ev) => { const p = pos(ev); b = [Math.min(p0[0], p[0]), Math.min(p0[1], p[1]), Math.abs(p[0] - p0[0]), Math.abs(p[1] - p0[1])];
      let tmp = layer.querySelector('.bx.new'); if (!tmp) { tmp = h('div', { class: 'bx new' }); layer.append(tmp); } live(tmp, { b }); };
    const up = () => {
      stage.removeEventListener('pointermove', mv); stage.removeEventListener('pointerup', up);
      const tmp = layer.querySelector('.bx.new'); if (tmp) tmp.remove();
      if (b && b[2] > 0.02 && b[3] > 0.02) {
        if (boxes.length >= max) { if (max === 1) boxes = []; else { toast(`No máximo ${max} caixas.`); render(); return; } }
        boxes.push({ b, opt: defaultOpt(boxes.length) }); sel = boxes.length - 1; render(); changed();
      } else if (sel >= 0) { sel = -1; render(); }
    };
    stage.addEventListener('pointermove', mv); stage.addEventListener('pointerup', up);
  });
  stage.addEventListener('keydown', (e) => { if ((e.key === 'Delete' || e.key === 'Backspace') && sel >= 0) { e.preventDefault(); remove(sel); } });
  render();
  return { el: stage, side, get, count: () => boxes.length, clear: () => { boxes = []; sel = -1; render(); changed(); } };
}
function drawModal(ev) {
  return new Promise((resolve) => {
    const tasks = ev.tasks, values = {};
    let idx = 0, close;
    const finish = (v) => { close(); resolve(v); };
    const body = h('div', { class: 'body' });
    const head = h('header');
    const next = h('button', { class: 'btn', disabled: true }, 'Próximo');
    const counter = h('span', { class: 'grow' });
    close = openModal(h('div', { class: 'modal' }, head, body,
      h('footer', null, counter, h('button', { class: 'btn secondary', onclick: () => finish(null) }, 'Cancelar execução'), next)), () => finish(null));
    next.addEventListener('click', () => { if (idx < tasks.length - 1) { idx++; show(); } else finish(values); });
    function show() {
      const t = tasks[idx];
      head.replaceChildren(h('h2', null, ev.title), h('p', null, t.label));
      counter.textContent = tasks.length > 1 ? `Desenho ${idx + 1} de ${tasks.length}` : '';
      next.textContent = idx < tasks.length - 1 ? 'Próximo' : (t.kind === 'place' ? 'Usar esta posição' : 'Usar desenho');
      next.disabled = values[t.key] === undefined;
      body.replaceChildren(h('p', { class: 'muted sm' }, 'Carregando imagem…'));
      const img = new Image();
      img.onload = () => body.replaceChildren(...(t.kind === 'box' ? boxStage(t, img) : t.kind === 'crop' ? cropStage(t, img)
        : t.kind === 'place' ? placeStage(t, img) : maskStage(t, img)).filter(Boolean));
      img.src = t.image_url;
    }
    function fit(img) {
      const s = Math.min(1, Math.min(640, window.innerWidth - 80) / img.naturalWidth, Math.max(240, window.innerHeight * 0.52) / img.naturalHeight);
      return [Math.round(img.naturalWidth * s), Math.round(img.naturalHeight * s)];
    }
    function stageEl(img) {
      const [w, hgt] = fit(img);
      const cv = h('canvas', { width: w, height: hgt });
      const el = h('div', { class: 'stage', style: `width:${w}px;height:${hgt}px` }, h('img', { src: img.src, alt: '' }), cv);
      return { el, cv, w, h: hgt };
    }
    function pos(cv, e) {
      const r = cv.getBoundingClientRect();
      return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
    }
    function boxStage(t, img) {
      // Quadrado(s) colorido(s): arraste no fundo para desenhar; clique numa caixa para selecionar (× no canto superior
      // esquerdo tira ela). Com `refs`, cada caixa diz qual imagem enviada vai ali.
      const refs = (t.refs || []).map((r) => ({ id: r.key, label: `Imagem ${r.n}`, url: r.url }));
      const many = !!t.multiple || refs.length > 0, col = BOX_COLORS[t.color] || '#E5392F';
      const saved = values[t.key];
      const init = Array.isArray(saved) ? (Array.isArray(saved[0]) || typeof saved[0] === 'number' ? [{ b: saved, opt: undefined }] : saved.map((x) => ({ b: x.b, opt: x.ref }))) : [];
      const ed = boxEditor({ img, color: col, multiple: many, numbered: !!t.numbered || refs.length > 0, options: refs, boxes: init,
        optLabel: (n) => `A caixa ${n} recebe:`, onChange: (bs) => {
          if (!bs.length) delete values[t.key];
          else values[t.key] = many ? bs.map((x) => ({ b: x.b, ref: x.opt })) : bs[0].b;
          next.disabled = !bs.length;
        } });
      const tools = [h('span', null, many
        ? 'Arraste no fundo para desenhar uma caixa. Clique numa caixa para selecionar: o × tira ela, arrastar move, os cantos mudam o tamanho.'
        : 'Arraste sobre a imagem para desenhar a caixa. Arraste de novo para refazer; o × tira.')];
      if (t.optional) {
        tools.push(h('button', { class: 'btn secondary', onclick: () => { values[t.key] = 'skip'; next.disabled = false; next.click(); } }, 'Usar a imagem inteira'));
        if (values[t.key] === 'skip') next.disabled = false;
      }
      return [t.hint ? h('p', { class: 'muted sm' }, t.hint) : null, ed.el, ed.side, h('div', { class: 'tools' }, tools)];
    }
    function cropStage(t, img) {
      const { el, cv } = stageEl(img);
      const ctx = cv.getContext('2d'), col = BOX_COLORS[t.color] || '#E5392F';
      const draw = (b) => {
        ctx.clearRect(0, 0, cv.width, cv.height);
        if (!b) return;
        ctx.fillStyle = col + '30'; ctx.strokeStyle = col; ctx.lineWidth = 3;
        ctx.fillRect(b[0] * cv.width, b[1] * cv.height, b[2] * cv.width, b[3] * cv.height);
        ctx.strokeRect(b[0] * cv.width, b[1] * cv.height, b[2] * cv.width, b[3] * cv.height);
      };
      let start = null;
      cv.addEventListener('pointerdown', (e) => { cv.setPointerCapture(e.pointerId); start = pos(cv, e); });
      cv.addEventListener('pointermove', (e) => {
        if (!start) return;
        const p = pos(cv, e), b = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.abs(p[0] - start[0]), Math.abs(p[1] - start[1])];
        draw(b); cv._b = b;
      });
      cv.addEventListener('pointerup', () => {
        start = null;
        if (cv._b && cv._b[2] > 0.01 && cv._b[3] > 0.01) { values[t.key] = cv._b; next.disabled = false; }
        else { delete values[t.key]; draw(null); next.disabled = true; }
      });
      if (Array.isArray(values[t.key])) draw(values[t.key]);
      const tools = [h('span', null, 'Arraste sobre a imagem para desenhar a caixa. Arraste de novo para refazer.')];
      if (t.optional) {
        tools.push(h('button', { class: 'btn secondary', onclick: () => { values[t.key] = 'skip'; draw(null); next.disabled = false; next.click(); } }, 'Usar a imagem inteira'));
        if (values[t.key] === 'skip') next.disabled = false;
      }
      return [el, h('div', { class: 'tools' }, tools)];
    }
    function placeStage(t, img) {
      // O fundo com a(s) pessoa(s) ou objeto(s) por cima. Arraste para mover (pode sair da imagem: a parte de fora é cortada).
      // Cantos: mudam o tamanho mantendo a proporção. Meio dos lados: esticam só aquele lado. Bolinha acima do meio
      // superior: gira. Com várias camadas, clique numa delas (ou na lista) para escolher qual mexer.
      const [fw, fh] = fit(img), k = 0.74, SW = Math.round(fw * k), SH = Math.round(fh * k);
      const M = Math.round(Math.max(SW, SH) * 0.22), MIN = 24;
      const ovs = t.overlays && t.overlays.length ? t.overlays : [{ url: t.overlay_url, size: t.overlay_size }];
      const stage = h('div', { class: 'stage inner', style: `width:${SW}px;height:${SH}px;left:${M}px;top:${M}px` }, h('img', { src: img.src, alt: '' }));
      const clip = h('div', { class: 'clip' });
      stage.append(clip);
      const el = h('div', { class: 'stagewrap', style: `width:${SW + 2 * M}px;height:${SH + 2 * M}px` }, stage);
      const slider = h('input', { type: 'range', min: 4, max: 300, step: 1, 'aria-label': 'Tamanho' });
      const rotOut = h('output', { class: 'rotout' }, '0°');
      const L = ovs.map((ov, i) => {
        const ar0 = ov.size[0] / ov.size[1], n = ovs.length;
        let w = Math.min(0.7 * SW / n, 0.8 * SH * ar0), hh = w / ar0;
        const x = (SW / n) * (i + 0.5) - w / 2, y = Math.max(0, SH * 0.96 - hh);
        const ghost = h('img', { src: ov.url, alt: '' }), solid = h('img', { src: ov.url, alt: '' });
        const piece = h('div', { class: 'piece', tabindex: '0', role: 'img', 'aria-label': (n > 1 ? tr(`Camada ${i + 1}`) + '. ' : '') + tr('Setas movem, + e - mudam o tamanho, [ e ] giram.') }, ghost);
        clip.append(solid); el.append(piece);
        return { ar0, w, hh, x, y, rot: 0, piece, solid, ov };
      });
      let cur = 0;
      const A = () => L[cur];
      const rad = (d) => (d * Math.PI) / 180;
      const rotv = (vx, vy, deg) => { const a = rad(deg), c = Math.cos(a), s = Math.sin(a); return [vx * c - vy * s, vx * s + vy * c]; };
      const clampAll = (l) => {
        l.w = Math.max(MIN, Math.min(l.w, SW * 4)); l.hh = Math.max(MIN, Math.min(l.hh, SH * 4));
        const vx = Math.min(l.w, Math.max(MIN, 0.25 * l.w)), vy = Math.min(l.hh, Math.max(MIN, 0.25 * l.hh));      // sempre sobra um pedaço dentro do fundo
        l.x = Math.max(-l.w + vx, Math.min(l.x, SW - vx)); l.y = Math.max(-l.hh + vy, Math.min(l.y, SH - vy));
      };
      const apply = () => {
        L.forEach((l, i) => {
          clampAll(l);
          const tr = `rotate(${l.rot}deg)`;
          Object.assign(l.piece.style, { left: M + l.x + 'px', top: M + l.y + 'px', width: l.w + 'px', height: l.hh + 'px', transform: tr, zIndex: i === cur ? 3 : 2 });
          Object.assign(l.solid.style, { left: l.x + 'px', top: l.y + 'px', width: l.w + 'px', height: l.hh + 'px', transform: tr });
          l.piece.classList.toggle('active', i === cur);
        });
        const a = A();
        slider.value = Math.round(a.w / SW * 100);
        rotOut.textContent = Math.round(a.rot) + '°';
        values[t.key] = L.map((l) => [l.x / SW, l.y / SH, l.w / SW, l.hh / SH, Math.round(l.rot * 100) / 100]); next.disabled = false;
        chips.forEach((c, i) => c.classList.toggle('on', i === cur));
      };
      const local = (e) => { const r = el.getBoundingClientRect(); return [e.clientX - r.left - M, e.clientY - r.top - M]; };
      L.forEach((l, i) => {
        let drag = null;
        l.piece.addEventListener('pointerdown', (e) => {
          if (e.target.classList.contains('handle') || e.target.classList.contains('rknob')) return;
          cur = i; apply();
          l.piece.setPointerCapture(e.pointerId); l.piece.focus(); drag = { mx: e.clientX, my: e.clientY, x: l.x, y: l.y }; l.piece.classList.add('grab');
        });
        l.piece.addEventListener('pointermove', (e) => { if (drag) { l.x = drag.x + e.clientX - drag.mx; l.y = drag.y + e.clientY - drag.my; apply(); } });
        const stop = () => { drag = null; l.piece.classList.remove('grab'); };
        l.piece.addEventListener('pointerup', stop); l.piece.addEventListener('pointercancel', stop);
        // alças de tamanho: o canto ou lado oposto fica parado, mesmo com a peça girada
        const DIR = { nw: [-1, -1, 'nwse'], ne: [1, -1, 'nesw'], sw: [-1, 1, 'nesw'], se: [1, 1, 'nwse'], n: [0, -1, 'ns'], s: [0, 1, 'ns'], w: [-1, 0, 'ew'], e: [1, 0, 'ew'] };
        for (const [c, [sx, sy, cursor]] of Object.entries(DIR)) {
          const hd = h('span', { class: 'handle ' + c + (c.length === 1 ? ' side' : ''), title: c.length === 1 ? 'Estica só este lado' : 'Muda o tamanho mantendo a proporção' });
          let rs = null;
          hd.addEventListener('pointerdown', (e) => {
            e.stopPropagation(); cur = i; hd.setPointerCapture(e.pointerId);
            const cx = l.x + l.w / 2, cy = l.y + l.hh / 2, off = rotv(-sx * l.w / 2, -sy * l.hh / 2, l.rot);
            rs = { ax: cx + off[0], ay: cy + off[1], ar: l.w / l.hh, w0: l.w, h0: l.hh };
          });
          hd.addEventListener('pointermove', (e) => {
            if (!rs) return;
            const [px, py] = local(e), q = rotv(px - rs.ax, py - rs.ay, -l.rot);          // ponteiro no sistema da peça, a partir do ponto parado
            let nw = rs.w0, nh = rs.h0;
            if (sx && sy) { nw = Math.max(MIN, sx * q[0], sy * q[1] * rs.ar); nh = nw / rs.ar; }
            else if (sx) nw = Math.max(MIN, sx * q[0]);
            else nh = Math.max(MIN, sy * q[1]);
            const c2 = rotv(sx * nw / 2, sy * nh / 2, l.rot);
            l.w = nw; l.hh = nh; l.x = rs.ax + c2[0] - nw / 2; l.y = rs.ay + c2[1] - nh / 2;
            apply();
          });
          hd.addEventListener('pointerup', () => { rs = null; });
          hd.style.cursor = cursor + '-resize';
          l.piece.append(hd);
        }
        // bolinha de giro, acima do meio da borda superior
        const knob = h('span', { class: 'rknob', title: 'Gira (Shift prende de 15 em 15 graus)', role: 'slider', 'aria-label': 'Girar' });
        const line = h('span', { class: 'rline' });
        let rr = null;
        knob.addEventListener('pointerdown', (e) => { e.stopPropagation(); cur = i; knob.setPointerCapture(e.pointerId); rr = true; apply(); });
        knob.addEventListener('pointermove', (e) => {
          if (!rr) return;
          const [px, py] = local(e), cx = l.x + l.w / 2, cy = l.y + l.hh / 2;
          let d = (Math.atan2(px - cx, -(py - cy)) * 180) / Math.PI;
          if (e.shiftKey) d = Math.round(d / 15) * 15;
          else if (Math.abs(d) < 3) d = 0;
          l.rot = d; apply();
        });
        knob.addEventListener('pointerup', () => { rr = null; });
        l.piece.append(line, knob);
        l.piece.addEventListener('keydown', (e) => {
          const st = e.shiftKey ? 20 : 4, kk = e.key;
          if (kk === 'ArrowLeft') l.x -= st; else if (kk === 'ArrowRight') l.x += st; else if (kk === 'ArrowUp') l.y -= st; else if (kk === 'ArrowDown') l.y += st;
          else if (kk === '+' || kk === '=') scaleAround(l, 1.05);
          else if (kk === '-') scaleAround(l, 1 / 1.05);
          else if (kk === '[') l.rot -= e.shiftKey ? 15 : 5;
          else if (kk === ']') l.rot += e.shiftKey ? 15 : 5;
          else return;
          e.preventDefault(); apply();
        });
      });
      const scaleAround = (l, f) => { const cx = l.x + l.w / 2, cy = l.y + l.hh / 2; l.w *= f; l.hh *= f; l.x = cx - l.w / 2; l.y = cy - l.hh / 2; };
      slider.addEventListener('input', () => { const a = A(); scaleAround(a, (+slider.value / 100 * SW) / a.w); apply(); });
      const chips = L.length > 1 ? L.map((l, i) => h('button', { type: 'button', class: 'chip pickopt', onclick: () => { cur = i; apply(); l.piece.focus(); } },
        h('img', { src: l.ov.url, alt: '' }), `Camada ${i + 1}`)) : [];
      const center = h('button', { class: 'btn secondary', onclick: () => { const a = A(); a.x = (SW - a.w) / 2; a.y = (SH - a.hh) / 2; apply(); } }, 'Centralizar');
      const ratio = h('button', { class: 'btn secondary', title: 'Volta à proporção original', onclick: () => { const a = A(); a.hh = a.w / a.ar0; apply(); } }, 'Proporção original');
      const unrot = h('button', { class: 'btn secondary', title: 'Tira o giro', onclick: () => { A().rot = 0; apply(); } }, 'Desgirar');
      apply();
      return [el, chips.length ? h('div', { class: 'tools wrap' }, h('span', null, 'Mexer em:'), chips) : null, h('div', { class: 'tools wrap' },
        h('span', null, 'Arraste para mover (pode sair da imagem). Cantos: tamanho proporcional. Meio dos lados: esticar. Bolinha de cima: girar.'),
        slider, rotOut, center, ratio, unrot)];
    }
    function maskStage(t, img) {
      const { el, cv, w } = stageEl(img);
      const ctx = cv.getContext('2d');
      const mc = document.createElement('canvas'); mc.width = img.naturalWidth; mc.height = img.naturalHeight;
      const mctx = mc.getContext('2d'); mctx.fillStyle = '#000'; mctx.fillRect(0, 0, mc.width, mc.height);
      let size = 28, last = null, painted = false;
      const stroke = (a, b) => {
        ctx.strokeStyle = 'rgba(43,63,224,.55)'; ctx.lineCap = 'round'; ctx.lineWidth = size;
        ctx.beginPath(); ctx.moveTo(a[0] * cv.width, a[1] * cv.height); ctx.lineTo(b[0] * cv.width, b[1] * cv.height); ctx.stroke();
        mctx.strokeStyle = '#fff'; mctx.lineCap = 'round'; mctx.lineWidth = size * (mc.width / w);
        mctx.beginPath(); mctx.moveTo(a[0] * mc.width, a[1] * mc.height); mctx.lineTo(b[0] * mc.width, b[1] * mc.height); mctx.stroke();
      };
      cv.addEventListener('pointerdown', (e) => { cv.setPointerCapture(e.pointerId); last = pos(cv, e); stroke(last, last); painted = true; });
      cv.addEventListener('pointermove', (e) => { if (!last) return; const p = pos(cv, e); stroke(last, p); last = p; });
      cv.addEventListener('pointerup', () => { last = null; if (painted) { values[t.key] = mc.toDataURL('image/png'); next.disabled = false; } });
      const range = h('input', { type: 'range', min: 6, max: 90, value: size, 'aria-label': 'Tamanho do pincel', oninput: (e) => { size = +e.target.value; } });
      const clear = h('button', { class: 'btn secondary', onclick: () => {
        ctx.clearRect(0, 0, cv.width, cv.height); mctx.fillStyle = '#000'; mctx.fillRect(0, 0, mc.width, mc.height);
        painted = false; delete values[t.key]; next.disabled = true; } }, 'Limpar');
      return [el, h('div', { class: 'tools' }, h('span', null, 'Pincel'), range, clear)];
    }
    show();
  });
}

// ---------------------------------------------------------- adicionar workflow
async function importModal(done) {
  let close;
  const body = h('div', { class: 'body' }, h('p', { class: 'muted' }, 'Procurando arquivos…'));
  const footer = h('footer', null);
  const shell = h('div', { class: 'modal' }, h('header', null, h('h2', null, 'Adicionar workflow'),
    h('p', null, 'Escolha um workflow que você salvou no ComfyUI (Workflow → Salvar) e indique onde entram as imagens e o prompt.')), body, footer);
  close = openModal(shell, () => close());
  const cancel = h('button', { class: 'btn secondary', onclick: () => close() }, 'Cancelar');
  footer.replaceChildren(h('span', { class: 'grow' }), cancel);
  let files;
  try { files = await api('/api/importable'); } catch (e) { body.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
  if (!files.length) {
    body.replaceChildren(h('p', null, 'Nenhum arquivo novo encontrado.'),
      h('p', { class: 'muted' }, 'Copie o .json do seu workflow para ', h('code', null, ((S.status && S.status.custom_dir) || 'custom') + '/workflows'),
        ' (pode usar subpastas) e abra esta janela de novo. O PickFlow nunca altera nem apaga esses arquivos.'));
    return;
  }
  const st = { file: files[0].file, d: null };
  const pick = h('select', { 'aria-label': 'Arquivo', onchange: (e) => load(e.target.value) },
    files.map((f) => h('option', { value: f.file }, f.file)));
  const form = h('div');
  const msg = h('span', { class: 'msg' });
  const add = h('button', { class: 'btn', disabled: true }, 'Adicionar');
  footer.replaceChildren(msg, h('span', { class: 'grow' }), cancel, add);
  body.replaceChildren(field('Arquivo em custom/workflows', pick), form);
  async function load(file) {
    st.file = file; msg.textContent = '';
    form.replaceChildren(h('p', { class: 'muted' }, 'Lendo o workflow…')); add.disabled = true;
    try { st.d = await api('/api/importable/draft?file=' + encodeURIComponent(file)); } catch (e) { form.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
    const d = st.d;
    const imgs = d.images.map((im, i) => ({ node: im.node, on: true, required: true, label: im.title === 'Load Image' ? `Imagem ${i + 1}` : im.title }));
    const outs = new Set(d.outputs.slice(0, 1).map((o) => o.node));
    let prompt = d.prompts[0] || null;
    const title = h('input', { type: 'text', value: d.title }), idIn = h('input', { type: 'text', value: d.id });
    title.addEventListener('input', () => { idIn.value = slug(title.value); });
    form.replaceChildren(h('div', null,
      field('Nome na lista', title), field('Id (letras minúsculas, números e _)', idIn),
      h('label', { class: 'f' }, 'Imagens de entrada'),
      d.images.length ? imgs.map((im) => h('div', { class: 'row' },
        h('input', { type: 'checkbox', checked: true, 'aria-label': 'Usar', onchange: (e) => { im.on = e.target.checked; } }),
        h('span', { class: 'muted sm' }, `nó ${im.node}`), h('input', { type: 'text', value: im.label, oninput: (e) => { im.label = e.target.value; } }),
        h('label', { class: 'radio', title: 'Sem esta imagem o workflow não roda' }, h('input', { type: 'checkbox', checked: true,
          onchange: (e) => { im.required = e.target.checked; } }), ' obrigatória')))
        : h('p', { class: 'muted sm' }, 'Nenhum nó LoadImage encontrado: o workflow só gera do zero.'),
      h('label', { class: 'f' }, 'Onde entra o prompt'),
      d.prompts.length ? d.prompts.map((p, i) => h('label', { class: 'radio' },
        h('input', { type: 'radio', name: 'pn', checked: i === 0, onchange: () => { prompt = p; } }),
        ` nó ${p.node} · ${p.title}${p.text ? ' — ' + p.text : ''}`))
        : h('p', { class: 'muted sm' }, 'Nenhum nó de texto encontrado: o workflow rodará sem prompt.'),
      h('label', { class: 'f' }, 'Saída'),
      d.outputs.map((o) => h('label', { class: 'radio' }, h('input', { type: 'checkbox', checked: outs.has(o.node),
        onchange: (e) => { e.target.checked ? outs.add(o.node) : outs.delete(o.node); } }), ` nó ${o.node} · ${o.title}`)),
      h('p', { class: 'hint' }, 'Caixas, máscaras e nós a descartar não entram aqui: se precisar, edite o arquivo criado em custom/registry/.')));
    add.disabled = false;
    add.onclick = async () => {
      add.disabled = true; msg.textContent = '';
      try {
        const r = await api('/api/import', { method: 'POST', body: { file: st.file, id: idIn.value.trim(), title: title.value.trim(),
          images: imgs.filter((x) => x.on).map((x) => ({ node: x.node, label: x.label, required: x.required })),
          prompt: prompt ? { node: prompt.node, widget: prompt.widget } : null, outputs: [...outs] } });
        close(); toast('Workflow adicionado'); done && done(r.id);
      } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; add.disabled = false; }
    };
  }
  load(st.file);
}

// ------------------------------------------------- assistente: nós, LoRAs e modelos
const fmtBytes = (b) => (b >= 1024 ** 3 ? (b / 1024 ** 3).toFixed(1) + ' GB' : Math.max(1, Math.round(b / 1024 ** 2)) + ' MB');
const safeHref = (u) => (/^https:\/\//.test(u || '') ? u : null);
function extLink(href, text) {
  const u = safeHref(href);
  return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, text) : null;
}
async function setupModal(onClose) {
  let close, data = null, probe = {}, timer = null, working = false, firstLoad = true, didInstall = false;
  const pick = new Set(), nodes = { on: false, overwrite: false };
  const body = h('div', { class: 'body' }, h('p', { class: 'muted' }, 'Procurando o ComfyUI…'));
  const footer = h('footer', null);
  const shell = h('div', { class: 'modal wide' }, h('header', null, h('h2', null, 'Instalar nós e LoRAs'),
    h('p', null, 'Os workflows usam arquivos que ficam dentro do ComfyUI. Marque o que quer instalar: nada é baixado nem copiado antes de você confirmar.')), body, footer);
  const finish = () => { clearTimeout(timer); close(); S.settings && api('/api/settings').then((s) => { S.settings = s; }).catch(() => {}); refreshStatus().then(() => { if (!S.conv && S.view === 'chat') renderFeed(); }); onClose && onClose(); };
  close = openModal(shell, () => { if (!working) finish(); });
  const msg = h('span', { class: 'msg' });

  const sizeOf = (f) => (probe[f.id] && probe[f.id].ok && probe[f.id].size) || (f.size_gb ? f.size_gb * 1024 ** 3 : 0);
  const state = (f) => {
    const j = f.job, pr = probe[f.id];
    if (j && j.state === 'baixando') return { text: j.total ? `baixando ${Math.round(100 * j.done / j.total)}%` : 'baixando', cls: 'run' };
    if (j && j.state === 'fila') return { text: 'na fila', cls: 'run' };
    if (j && j.state === 'erro') return { text: 'erro: ' + j.error, cls: 'bad' };
    if (j && j.state === 'cancelado') return { text: 'cancelado', cls: 'warn' };
    if (f.installed === true) return { text: 'instalado', cls: 'ok' };
    if (f.unused) return { text: 'não usado (outro codificador escolhido em Ajustes)', cls: '' };
    if (pr && !pr.ok) return { text: 'indisponível: ' + pr.error, cls: 'bad' };
    return f.installed === false ? { text: 'falta', cls: 'warn' } : { text: 'não verificado', cls: '' };
  };
  const canPick = (f) => data.can_install && f.installed !== true && !(probe[f.id] && !probe[f.id].ok);

  function nodeRow(n) {
    let text, cls;
    if (n.files === 'ok') [text, cls] = n.loaded === false ? ['copiado; reinicie o ComfyUI', 'warn'] : ['instalado', 'ok'];
    else if (n.files === 'diferente') [text, cls] = ['diferente do que vem no PickFlow', 'warn'];
    else if (n.files === 'falta') [text, cls] = ['falta', 'warn'];
    else [text, cls] = n.loaded ? ['instalado', 'ok'] : ['falta', 'warn'];
    return h('div', { class: 'frow' }, h('span'), h('div', null, h('b', null, n.name === 'viggle_turbo.py' ? 'Viggle Turbo (LoRA e escalonador)' : 'Caixas vermelha e verde'),
      h('div', { class: 'hint' }, n.name === 'viggle_turbo.py' ? 'Necessário para os workflows Turbo.' : 'Necessário para mover, trocar e transferir objetos.')),
      h('span', { class: 'st ' + cls }, text));
  }
  function fileRow(f) {
    const s = state(f), sz = sizeOf(f), j = f.job;
    const row = h('div', { class: 'frow' },
      h('input', { type: 'checkbox', 'aria-label': 'Instalar ' + f.title, disabled: !canPick(f) || working, checked: pick.has(f.id) && canPick(f),
        onchange: (e) => { e.target.checked ? pick.add(f.id) : pick.delete(f.id); renderFooter(); } }),
      h('div', null, h('b', null, f.title), f.why ? h('div', { class: 'hint' }, f.why) : null,
        f.used_by.length ? h('div', { class: 'meta' }, tr('Usado em:') + ' ' + f.used_by.slice(0, 3).map(tr).join(', ') + (f.used_by.length > 3 ? tr(` e mais ${f.used_by.length - 3}`) : '')) : null,
        h('div', { class: 'meta' }, h('code', null, `models/${f.folder}/${f.file}`),
          sz ? ` · ${probe[f.id] && probe[f.id].size ? '' : '~'}${fmtBytes(sz)}` : '', f.license ? ` · ${tr(f.license)}` : '', ' · ', extLink(f.page, 'página do autor')),
        j && j.state === 'baixando' && j.total ? h('div', { class: 'prog' }, h('i', { style: `width:${Math.round(100 * j.done / j.total)}%` })) : null),
      h('span', { class: 'st ' + s.cls }, s.text));
    return row;
  }
  function render() {
    if (!data) return;
    const c = data.comfy, kids = [];
    // ---- ComfyUI
    const pathIn = h('input', { type: 'text', placeholder: 'C:\\caminho\\do\\ComfyUI', value: c.configured_path || '' });
    const pathBox = h('div', { class: 'row' }, pathIn, h('button', { class: 'btn secondary', disabled: working, onclick: async () => {
      try { await api('/api/setup/path', { method: 'PUT', body: { path: pathIn.value } }); msg.textContent = ''; await load(); } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
    } }, 'Usar esta pasta'));
    if (!c.local) {
      kids.push(h('div', { class: 'note' }, h('b', null, 'O ComfyUI está em outro computador. '),
        `O endereço ${c.url} não é este computador, então o assistente não instala nada nele. Copie os arquivos abaixo para as pastas indicadas no computador do ComfyUI e reinicie-o.`));
    } else if (!data.can_install) {
      kids.push(h('div', { class: 'note guide' }, h('b', null, c.ok ? 'Não achei a pasta do ComfyUI neste computador.' : 'Não achei o ComfyUI neste computador.'),
        h('p', null, 'O PickFlow não gera imagens sozinho: ele pede ao ComfyUI. Para começar:'),
        h('ol', null,
          h('li', null, 'Instale o ComfyUI: ', extLink(data.links.download, 'ComfyUI Desktop (comfy.org/download)'), ' ou a ', extLink(data.links.github, 'versão manual no GitHub'), '.'),
          h('li', null, 'Abra o ComfyUI e espere carregar. O endereço padrão é http://127.0.0.1:8000 (Desktop) ou :8188 (manual); ajuste em Ajustes.'),
          h('li', null, 'Volte aqui e clique em Verificar de novo. Se o ComfyUI já está instalado em outra pasta, informe-a abaixo.')),
        c.warn ? h('p', { class: 'msg err' }, c.warn) : null, pathBox,
        h('div', { class: 'row' }, h('button', { class: 'btn secondary', disabled: working, onclick: () => load() }, 'Verificar de novo'))));
    } else {
      const chg = h('details', { class: 'chg' }, h('summary', null, 'Mudar a pasta'), pathBox);
      kids.push(h('div', { class: 'note ok' }, c.ok ? 'ComfyUI respondendo em ' : 'ComfyUI instalado, mas fechado agora. Endereço: ', h('b', null, c.url), ' · pasta: ', h('code', null, c.root), c.warn ? h('div', { class: 'msg err' }, c.warn) : null, chg));
    }
    // ---- nós
    kids.push(h('h3', null, 'Nós do PickFlow'));
    const needNodes = data.nodes.items.some((n) => n.files === 'falta' || n.files === 'diferente' || (n.files == null && n.loaded === false));
    kids.push(data.nodes.items.map(nodeRow));
    if (data.can_install && needNodes) {
      kids.push(h('div', { class: 'frow' }, h('input', { type: 'checkbox', 'aria-label': 'Instalar os nós', disabled: working, checked: nodes.on,
        onchange: (e) => { nodes.on = e.target.checked; renderFooter(); } }), h('div', null, h('b', null, 'Copiar os nós para o custom_nodes do ComfyUI'),
        h('div', { class: 'hint' }, 'São arquivos pequenos do próprio PickFlow. Depois de copiar, reinicie o ComfyUI.')), h('span')));
      if (data.nodes.items.some((n) => n.files === 'diferente')) {
        kids.push(h('label', { class: 'radio sub-opt' }, h('input', { type: 'checkbox', disabled: working, checked: nodes.overwrite, onchange: (e) => { nodes.overwrite = e.target.checked; } }),
          ' Substituir também os arquivos que eu editei'));
      }
    }
    // ---- nodes de outros autores (o PickFlow não os instala)
    if ((data.external || []).length) {
      kids.push(h('h3', null, 'Nodes de outros autores'),
        h('p', { class: 'hint' }, 'Estes o PickFlow não instala. Instale pelo ComfyUI Manager (Custom Nodes Manager) ou pelo GitHub do autor e reinicie o ComfyUI.'),
        data.external.map((e) => h('div', { class: 'frow' }, h('span'),
          h('div', null, h('b', null, e.pack), h('div', { class: 'meta' }, 'Node ', h('code', null, e.class), ' · usado em: ' + e.used_by.join(', '), ' · ', extLink(e.url, 'GitHub'))),
          h('span', { class: 'st ' + (e.loaded ? 'ok' : e.loaded === false ? 'warn' : '') }, e.loaded ? 'instalado' : e.loaded === false ? 'falta' : 'não verificado'))));
    }
    // ---- arquivos
    const loras = data.files.filter((f) => f.group === 'lora'), models = data.files.filter((f) => f.group !== 'lora');
    kids.push(h('h3', null, 'LoRAs'), h('p', { class: 'hint' }, 'Cada workflow usa alguns. Instale os dos workflows que você quer usar. Os que o ComfyUI já tem aparecem como instalados.'),
      loras.map(fileRow));
    if (models.length) {
      const miss = models.filter((f) => f.installed === false && !f.unused).length;
      const d = h('details', { class: 'models', open: models.some((f) => pick.has(f.id) || (f.job && f.job.state !== 'pronto')) || miss > 0 && !data.can_install },
        h('summary', null, `Modelos base do Qwen-Image 2.1 (arquivos grandes)${miss ? `: faltam ${miss}` : ': todos instalados'}`),
        h('p', { class: 'hint' }, 'Sem eles nenhum workflow roda. Ocupam dezenas de GB; baixe só se ainda não tiver (também estão no template oficial do ComfyUI).'),
        models.map(fileRow));
      kids.push(d);
    }
    kids.push(h('p', { class: 'hint' }, 'Cada arquivo tem a licença do seu autor, e várias (Qwen Research) são de uso não comercial. Ao baixar, você concorda com essas licenças.'));
    body.replaceChildren(...kids.flat());
    renderFooter();
  }
  function renderFooter() {
    if (!data) return;
    const sel = data.files.filter((f) => pick.has(f.id) && canPick(f));
    const total = sel.reduce((a, f) => a + sizeOf(f), 0);
    const label = [nodes.on && data.can_install ? tr('os nós') : null, sel.length ? tr(`${sel.length} ${sel.length === 1 ? 'arquivo' : 'arquivos'}`) + (total ? ` (~${fmtBytes(total)})` : '') : null].filter(Boolean).join(tr(' e '));
    const install = h('button', { class: 'btn', disabled: working || !label, onclick: () => run(sel) }, label ? tr('Instalar {}').replace('{}', label) : 'Instalar selecionados');
    const dismiss = h('button', { class: 'btn secondary', onclick: async () => {
      if (working) { await api('/api/setup/cancel', { method: 'POST' }); msg.textContent = 'Cancelando…'; return; }
      if (!data.dismissed && !didInstall) { try { await api('/api/setup/dismiss', { method: 'POST', body: { value: true } }); toast('Você pode abrir o assistente de novo em Ajustes.'); } catch (_) { /* segue */ } }
      finish();
    } }, working ? 'Cancelar downloads' : data.dismissed || didInstall ? 'Fechar' : 'Agora não');
    footer.replaceChildren(msg, h('span', { class: 'grow' }), dismiss, install);
  }
  async function load(refresh = true) {
    try {
      data = await api('/api/setup' + (refresh ? '?refresh=true' : ''));
    } catch (e) { body.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
    if (firstLoad) {
      firstLoad = false;
      nodes.on = data.can_install && data.nodes.missing > 0;
      data.files.forEach((f) => { if (f.group === 'lora' && f.installed === false) pick.add(f.id); });
      api('/api/setup/probe').then((p) => { probe = p; render(); }).catch(() => {});
    }
    working = data.busy;
    render();
    if (working) pollJobs();
  }
  async function run(sel) {
    working = true; msg.textContent = ''; msg.className = 'msg';
    try {
      const r = await api('/api/setup/install', { method: 'POST', body: { ids: sel.map((f) => f.id), nodes: nodes.on, overwrite_nodes: nodes.overwrite } });
      if (r.nodes) { msg.textContent = 'Nós copiados. Reinicie o ComfyUI para carregá-los.'; nodes.on = false; }
      pick.clear(); didInstall = true;
      await load();
    } catch (e) { working = false; msg.textContent = e.message; msg.className = 'msg err'; render(); }
  }
  async function pollJobs() {
    try {
      const j = await api('/api/setup/jobs');
      const by = Object.fromEntries(j.jobs.map((x) => [x.id, x]));
      data.files.forEach((f) => { if (by[f.id]) f.job = by[f.id]; });
      if (j.busy) { render(); timer = setTimeout(pollJobs, 800); return; }
      working = false;
      const bad = j.jobs.filter((x) => x.state === 'erro').length, ok = j.jobs.filter((x) => x.state === 'pronto').length;
      await load();
      const prev = msg.textContent;
      msg.textContent = `${tr(`${ok} baixado${ok === 1 ? '' : 's'}`)}${bad ? tr(`, ${bad} com erro (veja na lista)`) : ''}. ${prev && /Reinicie|Restart/.test(prev) ? prev : ''}`.trim();
      msg.className = 'msg' + (bad ? ' err' : '');
    } catch (e) { working = false; msg.textContent = e.message; msg.className = 'msg err'; render(); }
  }
  load();
}

// ============================================================ WORKFLOWS (UI)
let wfSel = null, wfDraft = null, wfDirty = false;
function mountWorkflows(main) {
  if (!wfSel || !wfById(wfSel)) wfSel = S.workflows[0] && S.workflows[0].id;
  const list = h('div', { class: 'list' });
  const editor = h('div', { class: 'editor' });
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'Workflows e prompts'),
    h('span', { class: 'muted sm grow' }, 'O que o Clef sabe de cada workflow e quais prompts ele pode usar'),
    h('button', { class: 'btn secondary', onclick: () => importModal(async (id) => {
      await loadAll(); wfSel = id; wfDraft = null; wfDirty = false; renderList(); renderEditor();
    }) }, 'Adicionar workflow')),
    h('div', { class: 'split' }, list, editor));
  const renderList = () => {
    const groups = {};
    S.workflows.forEach((w) => (groups[w.group || 'outros'] ||= []).push(w));
    list.replaceChildren(...Object.entries(groups).flatMap(([g, ws]) => [h('div', { class: 'grp' }, g),
      ...ws.map((w) => h('button', { class: 'item' + (w.id === wfSel ? ' on' : '') + (w.enabled ? '' : ' off'), onclick: () => select(w.id) },
        h('span', { class: 'dot' }), h('span', { class: 'nm' }, w.title),
        w.source === 'custom' ? h('span', { class: 'tag', title: 'Workflow seu (pasta custom/)' }, 'seu')
          : w.customized ? h('span', { class: 'tag soft', title: 'Você editou os prompts ou a descrição deste padrão' }, 'editado') : null))]));
  };
  const select = (id) => {
    if (wfDirty && id !== wfSel && !confirm('Há alterações não salvas neste workflow. Descartar?')) return;
    wfSel = id; wfDraft = null; wfDirty = false; renderList(); renderEditor();
  };
  const renderEditor = () => {
    const w = wfById(wfSel);
    if (!w) { editor.replaceChildren(h('p', { class: 'muted' }, 'Nenhum workflow registrado.')); return; }
    if (!wfDraft) {
      wfDraft = clone({ enabled: w.enabled, description: w.description, default: w.default, presets: w.presets });
      wfDraft.active = new Set(w.active == null ? w.presets.map((p) => p.id) : w.active);
    }
    const d = wfDraft;
    const dirty = () => { wfDirty = true; msg.textContent = 'Alterações não salvas.'; msg.className = 'msg'; };
    const msg = h('span', { class: 'msg' }, '');
    const presetCards = () => d.presets.map((p, i) => {
      const on = d.active.has(p.id);
      return h('div', { class: 'preset' + (on ? '' : ' inactive') },
        h('div', { class: 'top' },
          h('input', { type: 'text', value: p.name, 'aria-label': 'Nome do prompt', oninput: (e) => { p.name = e.target.value; dirty(); } }),
          switchEl('O Clef pode usar', on, (v) => { v ? d.active.add(p.id) : d.active.delete(p.id); dirty(); renderEditor(); }),
          h('label', { class: 'radio', title: 'Usado quando nenhum prompt estiver liberado para o Clef' },
            h('input', { type: 'radio', name: 'def', checked: d.default === p.id, onchange: () => { d.default = p.id; dirty(); } }), 'Padrão'),
          h('button', { class: 'ghost sm', disabled: d.presets.length < 2, onclick: () => { d.presets.splice(i, 1); d.active.delete(p.id); if (d.default === p.id) d.default = d.presets[0].id; dirty(); renderEditor(); } }, 'Remover')),
        field('Quando usar (o Clef lê isto para escolher entre os prompts)', h('input', { type: 'text', value: p.when, oninput: (e) => { p.when = e.target.value; dirty(); } })),
        field('Texto do prompt', h('textarea', { rows: 5, value: p.text, oninput: (e) => { p.text = e.target.value; dirty(); } }),
          'Use {request} onde o pedido do usuário deve entrar. <image1>, <image2>… apontam para as imagens do workflow.'),
        switchEl('Deixar o redator (LLM) montar o prompt final (precisa do redator ativado em Ajustes)', !!p.rewrite, (v) => { p.rewrite = v; dirty(); }),
        w.lora_chain ? h('details', { class: 'tech' }, h('summary', null, 'LoRAs e ajustes deste prompt'),
          h('div', { class: 'stack', style: 'margin-top:8px' },
            field('LoRAs que este prompt liga (ids da aba LoRAs, separados por vírgula)', h('input', { type: 'text', value: lorasToText(p.loras), list: 'lora-ids',
              placeholder: 'ex.: bfs_body, flymy=0.5, natexp=0.8@turbo', oninput: (e) => { p.loras = textToLoras(e.target.value); if (!p.loras.length) delete p.loras; dirty(); } }),
              'id = força; id=força@modo vale só naquele modo; força 0 desliga uma LoRA da base. Use {loras} no texto para colocar as instruções das LoRAs.'),
            field('Ajustes do grafo (um por linha: nome = valor)', h('textarea', { rows: 3, value: settingsToText(p.settings), placeholder: 'resolution = 1024\nscene_mp = off',
              oninput: (e) => { p.settings = textToSettings(e.target.value); if (!Object.keys(p.settings).length) delete p.settings; dirty(); } }),
              tr('Nomes disponíveis neste workflow: {}. "off" ignora o nó.').replace('{}', w.knobs.join(', ') || tr('nenhum'))),
            field('Mínimo de imagens (0 = sem mínimo)', h('input', { type: 'number', min: 0, max: 8, value: p.needs || 0,
              oninput: (e) => { const n = Number(e.target.value); if (n > 0) p.needs = n; else delete p.needs; dirty(); } })),
            switchEl('As imagens entram nas entradas na ordem em que foram anexadas (sem o Clef escolher)', p.slots === 'order', (v) => { if (v) p.slots = 'order'; else delete p.slots; dirty(); }),
            (() => {
              const ta = h('textarea', { rows: 8, spellcheck: 'false', class: 'mono', value: p.vars ? JSON.stringify(p.vars, null, 2) : '',
                placeholder: '[ { "key": "escopo", "kind": "choice", "options": [ { "id": "a", "text": "..." } ] } ]' });
              const err = h('div', { class: 'hint' });
              ta.addEventListener('input', () => {
                const t = ta.value.trim();
                if (!t) { delete p.vars; err.textContent = ''; dirty(); return; }
                try { const v = JSON.parse(t); if (!Array.isArray(v)) throw new Error('precisa ser uma lista [ ... ]'); p.vars = v; err.textContent = ''; dirty(); }
                catch (x) { err.textContent = 'JSON inválido: ' + x.message; }
              });
              return h('div', null, h('label', { class: 'f' }, 'Variáveis do prompt (avançado, JSON)'), ta, err,
                h('div', { class: 'hint' }, 'O Clef responde cada variável (quantas pessoas, o que trocar…) e o texto entra onde estiver {chave}. Veja o prompt "Trocar pessoas" como exemplo.'));
            })())) : null);
    });
    const tech = h('details', { class: 'tech' }, h('summary', null, 'Ligação técnica (somente leitura)'),
      w.modes.length > 1 ? h('div', null, 'Modos: ', w.modes.map((m) => h('span', { class: 'tag', title: m.when }, m.title))) : null,
      h('div', null, 'Arquivo: ', h('code', null, w.file)),
      w.lora_chain ? h('div', { style: 'margin-top:6px' }, 'A cadeia de LoRAs deste workflow é montada a cada execução (aba LoRAs).') : null,
      h('table', null, h('tr', null, h('th', null, 'Entrada'), h('th', null, 'Nó'), h('th', null, 'Obrigatória')),
        w.images.map((s) => h('tr', null, h('td', null, `${s.key}: ${tr(s.label)}`), h('td', null, String(s.node)), h('td', null, s.required === false ? 'não' : 'sim')))),
      w.internal ? h('p', { style: 'margin-top:8px' }, 'Passo interno: o Clef só o usa dentro de um caso de uso (aba Casos de uso).') : null,
      w.params.length ? h('div', { style: 'margin-top:8px' }, tr('Desenhos pedidos ao usuário:') + ' ' + w.params.map((p) => tr(p.label)).join('; ')) : null,
      w.note ? h('p', { style: 'margin-top:8px' }, w.note) : null,
      h('p', { style: 'margin-top:8px' }, w.source === 'custom'
        ? ['Para mudar nós e entradas, edite ', h('code', null, `custom/registry/${w.id}.yaml`), '.']
        : ['Este é um padrão do repositório. Para mudar nós e entradas, copie ', h('code', null, `defaults/registry/${w.id}.yaml`),
          ' para ', h('code', null, 'custom/registry/'), ' e edite a cópia (a sua vale mais).']));
    const reset = (w.source === 'custom' || w.customized) ? h('button', { class: 'ghost sm', title: w.source === 'custom'
      ? 'Tira da lista. O arquivo .json continua na sua pasta.' : 'Descarta as suas edições de prompts e descrição.', onclick: async () => {
      const ask = w.source === 'custom' ? `Remover "${w.title}" da lista? O arquivo .json continua em custom/workflows.`
        : `Voltar "${w.title}" ao padrão do repositório? Suas edições de prompts e descrição serão descartadas.`;
      if (!confirm(ask)) return;
      try { await api(`/api/workflows/${w.id}`, { method: 'DELETE' }); await loadAll(); wfDraft = null; wfDirty = false; select(wfSel); toast(w.source === 'custom' ? 'Removido' : 'Padrão restaurado'); }
      catch (e) { toast(e.message); }
    } }, w.source === 'custom' ? 'Remover da lista' : 'Restaurar padrão') : null;
    const save = h('button', { class: 'btn', onclick: async () => {
      try {
        const saved = await api(`/api/workflows/${w.id}`, { method: 'PUT', body: {
          enabled: d.enabled, description: d.description, active: [...d.active], default: d.default, presets: d.presets } });
        S.workflows[S.workflows.findIndex((x) => x.id === w.id)] = saved;
        wfDraft = null; wfDirty = false; renderList(); renderEditor(); toast('Salvo');
      } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
    } }, 'Salvar alterações');
    const discard = h('button', { class: 'btn secondary', onclick: () => { wfDraft = null; wfDirty = false; renderEditor(); } }, 'Descartar');
    editor.replaceChildren(h('datalist', { id: 'lora-ids' }, (S.loras || []).map((l) => h('option', { value: l.id }, l.title))), h('div', { class: 'inner' },
      h('div', { class: 'row wrap' }, h('h2', { class: 'grow' }, w.title), reset, switchEl('Ativo para o Clef', d.enabled, (v) => { d.enabled = v; dirty(); })),
      h('div', { class: 'card' }, h('h2', null, 'O que o Clef lê sobre este workflow'),
        h('p', { class: 'sub' }, `${w.required_images} imagem(ns) obrigatória(s). As entradas e a necessidade de desenhar caixas são acrescentadas automaticamente ao texto.`),
        h('textarea', { rows: 4, value: d.description, 'aria-label': 'Descrição para o Clef', oninput: (e) => { d.description = e.target.value; dirty(); } }),
        h('div', { class: 'hint' }, 'Diga o que o workflow faz e quando usá-lo no lugar dos parecidos.')),
      h('div', { class: 'card' }, h('h2', null, 'Prompts deste workflow'),
        h('p', { class: 'sub' }, 'Os prompts liberados ficam à disposição do Clef. Com mais de um liberado, ele escolhe pelo texto de “quando usar”.'),
        h('div', { class: 'presets' }, presetCards()),
        h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn secondary', onclick: () => {
          const id = uniqueId('prompt_' + (d.presets.length + 1), new Set(d.presets.map((p) => p.id)));
          d.presets.push({ id, name: 'Novo prompt', when: '', text: '{request}', rewrite: false }); d.active.add(id); dirty(); renderEditor(); } }, 'Adicionar prompt'))),
      tech),
      h('div', { class: 'savebar' }, msg, discard, save));
  };
  renderList(); renderEditor();
}

// ============================================================ CASOS DE USO
function mountRecipes(main) {
  const draft = { instructions: S.clefCfg.instructions, recipes: clone(S.clefCfg.recipes) };
  const page = h('div', { class: 'page' });
  const msg = h('span', { class: 'msg' });
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'Casos de uso'), h('span', { class: 'muted sm' }, 'Instruções gerais e sequências de workflows')), page);
  let sim = null, simBusy = false, simMsg = '';
  const dirty = () => JSON.stringify(draft.recipes.map((r) => { const c = { ...r }; delete c._new; return c; })) !== JSON.stringify(S.clefCfg.recipes);
  const nameOf = (id) => (draft.recipes.find((r) => r.id === id) || {}).name || id;
  const checkRepeats = async () => {
    simBusy = true; simMsg = 'O Clef está comparando os casos de uso…'; render();
    try { sim = await api('/api/recipes/similarity', { method: 'POST', body: {} }); simMsg = ''; }
    catch (e) { simMsg = e.message; }
    simBusy = false; render();
  };
  const mergeIds = async (ids) => {
    if (dirty()) { toast('Salve as edições antes de fundir.'); return; }
    simBusy = true; simMsg = 'O LLM está propondo o caso de uso fundido…'; render();
    try {
      const res = await api('/api/recipes/merge', { method: 'POST', body: { ids } });
      simBusy = false; simMsg = ''; render();
      if (!res.mergeable) { simMsg = 'Não dá para fundir: ' + res.reason; render(); return; }
      const ok = await mergeModal(res);
      if (!ok) return;
      const done = await api('/api/recipes/merge/apply', { method: 'POST', body: { recipe: res.recipe, replaces: res.replaces, mode: ok.mode } });
      S.clefCfg = { instructions: done.instructions, recipes: done.recipes };
      draft.instructions = done.instructions; draft.recipes = clone(done.recipes);
      sim = null; toast('Casos de uso fundidos'); render();
    } catch (e) { simBusy = false; simMsg = e.message; render(); }
  };
  const simCard = () => {
    const pairs = sim ? sim.pairs : [];
    return h('div', { class: 'card stack' },
      h('div', { class: 'row wrap' }, h('div', { class: 'grow' }, h('h2', null, 'Casos de uso repetidos'),
        h('p', { class: 'sub', style: 'margin:0' }, 'O Clef compara os casos de uso dois a dois e dá uma nota de repetição. Você revisa e, se quiser, o LLM funde os parecidos em um só, mais geral.')),
        h('button', { class: 'btn secondary', disabled: simBusy || draft.recipes.filter((r) => r.enabled !== false).length < 2, onclick: checkRepeats }, 'Verificar repetições')),
      simMsg ? h('p', { class: simBusy ? 'sm muted' : 'sm', style: simBusy ? '' : 'color:var(--danger)' }, simMsg) : null,
      sim && !pairs.length ? h('p', { class: 'sm muted' }, `Nenhum par parecido o bastante entre ${sim.recipes} casos de uso ativos.`) : null,
      sim && pairs.length ? h('p', { class: 'sm muted' }, tr(pairs.length === 1 ? `${pairs.length} par parecido, do mais repetido ao menos` : `${pairs.length} pares parecidos, do mais repetido ao menos`)
        + tr(sim.source === 'estrutura' ? '. O Clef não respondeu: a nota usa só a estrutura dos passos.' : '.')
        + (sim.truncated ? ' ' + tr('Só os pares mais prováveis foram comparados.') : '')) : null,
      sim && sim.groups.length > 0 ? h('div', { class: 'row wrap' }, sim.groups.filter((g) => g.length > 2).map((g) =>
        h('button', { class: 'btn secondary', disabled: simBusy || g.length > 5, onclick: () => mergeIds(g) }, tr('Fundir o grupo: {}').replace('{}', g.map((x) => tr(nameOf(x))).join(' + '))))) : null,
      pairs.map((p) => h('div', { class: 'simrow' },
        h('div', null, h('b', null, p.a_name), ' e ', h('b', null, p.b_name),
          h('div', { class: 'sm muted' }, p.clef ? `Clef: mesmo caso ${pct(p.clef.same)}, parecidos ${pct(p.clef.overlap)}, diferentes ${pct(p.clef.different)} · estrutura ${pct(p.structure)}` : `Estrutura dos passos: ${pct(p.structure)}`)),
        h('div', { class: 'bar2m', title: `Repetição ${pct(p.score)}` }, h('div', { class: 'tr' }, h('i', { style: `width:${Math.round(p.score * 100)}%` })),
          h('span', { class: 'sm' }, `${pct(p.score)} · ${p.level}`)),
        h('button', { class: 'btn secondary', disabled: simBusy, onclick: () => mergeIds([p.a, p.b]) }, 'Fundir os dois'))));
  };
  const render = () => {
    const wfOptions = (sel) => S.workflows.map((w) => h('option', { value: w.id, selected: w.id === sel }, w.title));
    page.append(h('datalist', { id: 'lora-ids' }, (S.loras || []).map((l) => h('option', { value: l.id }, l.title))));
    const recipeCard = (r, ri) => {
      const stepCard = (s, si) => {
        const w = wfById(s.workflow) || S.workflows[0];
        s.inputs = s.inputs || {};
        const presetSel = h('select', { onchange: (e) => { s.prompt = e.target.value || undefined; } },
          h('option', { value: '' }, 'O Clef escolhe'), w.presets.map((p) => h('option', { value: p.id, selected: s.prompt === p.id }, p.name)));
        const srcOptions = (slot) => [
          h('option', { value: '' }, 'O Clef decide'),
          ...[1, 2, 3, 4].map((n) => h('option', { value: `user:${n}` }, `Imagem ${n} enviada pelo usuário`)),
          ...(r.roles || []).map((ro) => h('option', { value: `role:${ro.key}` }, `O Clef acha: ${ro.label}`)),
          si > 0 ? h('option', { value: 'previous' }, 'Resultado do passo anterior') : null,
          slot.required === false ? h('option', { value: 'none' }, 'Não usar') : null];
        return h('div', { class: 'stepcard' },
          h('div', { class: 'sh' }, h('b', null, `Passo ${si + 1}`),
            h('button', { class: 'ghost sm', disabled: si === 0, onclick: () => { const a = r.steps; [a[si - 1], a[si]] = [a[si], a[si - 1]]; render(); } }, 'Subir'),
            h('button', { class: 'ghost sm', disabled: si === r.steps.length - 1, onclick: () => { const a = r.steps; [a[si + 1], a[si]] = [a[si], a[si + 1]]; render(); } }, 'Descer'),
            h('button', { class: 'ghost sm', disabled: r.steps.length < 2, onclick: () => { r.steps.splice(si, 1); render(); } }, 'Remover')),
          h('div', { class: 'map' },
            h('span', null, 'Workflow'), h('select', { onchange: (e) => { s.workflow = e.target.value; s.prompt = undefined; s.mode = undefined; s.loras = undefined; s.inputs = {}; render(); } }, wfOptions(s.workflow)),
            h('span', null, 'Prompt'), presetSel,
            w.modes.length > 1 ? [h('span', null, 'Modo'), h('select', { onchange: (e) => { s.mode = e.target.value || undefined; } },
              h('option', { value: '' }, 'O Clef escolhe'), w.modes.map((m) => h('option', { value: m.id, selected: s.mode === m.id }, m.title)))] : null,
            w.lora_chain ? [h('span', null, 'LoRAs'), h('input', { type: 'text', list: 'lora-ids', value: s.loras ? (s.loras.length ? s.loras.join(', ') : 'nenhuma') : '',
              placeholder: 'vazio: o Clef decide', oninput: (e) => { const v = e.target.value.trim();
                s.loras = !v ? undefined : /^(nenhuma|none)$/i.test(v) ? [] : v.split(/[,\s]+/).filter(Boolean); } })] : null,
            h('span', null, 'Instrução'), h('input', { type: 'text', value: s.request || '', placeholder: 'vazio: o pedido inteiro do usuário. {request} insere o pedido.',
              oninput: (e) => { const v = e.target.value.trim(); if (v) s.request = v; else delete s.request; } }),
            w.images.map((slot) => [h('span', null, slot.label), (() => {
              const sel = h('select', { onchange: (e) => { if (e.target.value) s.inputs[slot.key] = e.target.value; else delete s.inputs[slot.key]; } }, srcOptions(slot));
              sel.value = s.inputs[slot.key] || '';
              return sel;
            })()]).flat()));
      };
      return h('div', { class: 'card stack' },
        h('div', { class: 'row wrap' },
          h('input', { type: 'text', class: 'grow', value: r.name, 'aria-label': 'Nome do caso de uso', style: 'font-weight:600;max-width:420px', oninput: (e) => { r.name = e.target.value; } }),
          r.generated ? h('span', { class: 'tag soft', title: r.generated.request ? `Criado para o pedido: ${r.generated.request}` : '' },
            r.generated.status === 'kept' ? 'criado pelo agente · mantido' : 'criado pelo agente · em teste') : null,
          r.generated && r.generated.status !== 'kept' ? h('button', { class: 'btn secondary sm', title: 'Em teste, o mais antigo sai quando o agente cria outro além do limite. Mantido não sai.', onclick: async () => {
            try {
              S.clefCfg = await api(`/api/recipes/${r.id}/keep`, { method: 'POST' });
              draft.recipes = clone(S.clefCfg.recipes); toast('Mantido'); render();
            } catch (e) { toast(e.message); }
          } }, 'Manter') : null,
          switchEl('Ativo', r.enabled !== false, (v) => { r.enabled = v; }),
          h('button', { class: 'ghost sm', onclick: () => { draft.recipes.splice(ri, 1); render(); } }, 'Apagar')),
        field('Quando usar (o Clef lê isto)', h('textarea', { rows: 3, value: r.description, oninput: (e) => { r.description = e.target.value; } })),
        field('Quantas imagens o usuário envia (vazio: qualquer quantidade que os passos pedem)',
          h('input', { type: 'number', min: 0, max: 8, value: r.images || '', style: 'max-width:90px',
            oninput: (e) => { const n = parseInt(e.target.value, 10); if (n > 0) r.images = n; else delete r.images; } })),
        h('div', { class: 'stack' },
          h('b', null, 'Papéis das imagens'),
          h('p', { class: 'sub' }, 'Opcional. Cada papel (cena, pessoa, fundo...) é decidido pelo Clef olhando o conteúdo das imagens e o pedido, e pode ser usado nos passos como "O Clef acha: ...". Sem Clef seguro, vale a ordem de envio. O Clef lê a descrição junto com o pedido: descreva o papel em termos gerais ("a imagem com o objeto que o pedido manda colar") em vez de citar um objeto; o nome que o usuário escreve é que diz qual é.'),
          (r.roles || []).map((ro, k) => h('div', { class: 'row wrap' },
            h('input', { type: 'text', value: ro.label, 'aria-label': 'Nome do papel', style: 'max-width:260px', oninput: (e) => { ro.label = e.target.value; } }),
            h('input', { type: 'text', class: 'grow', value: ro.about || '', 'aria-label': 'Como reconhecer', placeholder: 'Como reconhecer esta imagem (ex.: a imagem com a coisa que o pedido manda colar)', oninput: (e) => { ro.about = e.target.value; } }),
            h('button', { class: 'ghost sm', onclick: () => { r.roles.splice(k, 1); if (!r.roles.length) delete r.roles; render(); } }, 'Remover'))),
          h('div', null, h('button', { class: 'btn secondary', onclick: () => {
            r.roles = r.roles || [];
            const key = uniqueId('papel_' + (r.roles.length + 1), new Set(r.roles.map((x) => x.key)));
            r.roles.push({ key, label: 'Novo papel', about: '', default: r.roles.length + 1 }); render(); } }, 'Adicionar papel'))),
        h('div', { class: 'stack' }, r.steps.map(stepCard)),
        h('div', null, h('button', { class: 'btn secondary', onclick: () => { r.steps.push({ workflow: S.workflows[0].id }); render(); } }, 'Adicionar passo')));
    };
    page.replaceChildren(h('div', { class: 'inner' },
      h('div', { class: 'card' }, h('h2', null, 'Instruções gerais do Clef'),
        h('p', { class: 'sub' }, 'Texto que vai junto de todo pedido que o Clef lê. Regras de preferência (Turbo ou Quality, papel de cada imagem) ficam aqui.'),
        h('textarea', { rows: 8, value: draft.instructions, 'aria-label': 'Instruções gerais', oninput: (e) => { draft.instructions = e.target.value; } })),
      simCard(),
      h('div', null, h('h2', null, 'Sequências de workflows'),
        h('p', { class: 'muted sm', style: 'margin-top:2px' }, 'Um caso de uso é uma rota fixa com vários passos. O Clef escolhe entre eles e os workflows soltos.')),
      draft.recipes.map(recipeCard),
      h('div', { class: 'row' }, h('button', { class: 'btn secondary', onclick: () => {
        const id = uniqueId('caso_' + (draft.recipes.length + 1), new Set(draft.recipes.map((r) => r.id)));
        draft.recipes.push({ id, name: 'Novo caso de uso', description: '', enabled: true, steps: [{ workflow: S.workflows[0].id }], _new: true }); render(); } }, 'Novo caso de uso'),
        h('span', { class: 'grow' }), msg,
        h('button', { class: 'btn', onclick: async () => {
          try {
            const taken = new Set();
            draft.recipes.forEach((r) => { if (r._new) { r.id = uniqueId(slug(r.name), taken); } taken.add(r.id); delete r._new; });
            S.clefCfg = await api('/api/clef-config', { method: 'PUT', body: { instructions: draft.instructions, recipes: draft.recipes } });
            draft.recipes = clone(S.clefCfg.recipes); msg.textContent = ''; toast('Salvo'); render();
          } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
        } }, 'Salvar'))));
  };
  render();
}

// ===================================================================== LORAS
function mountLoras(main) {
  const page = h('div', { class: 'page' });
  const list = h('div', { class: 'stack' });
  const addBtn = h('button', { class: 'btn', onclick: () => { prefill = null; open.add('__new'); render(); } }, 'Nova LoRA');
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'LoRAs'), addBtn), page);
  const open = new Set();
  let prefill = null;
  const modeLines = (p) => (p && typeof p === 'object' ? Object.entries(p).map(([k, v]) => `${k}: ${String(v).replace(/\n+/g, ' ')}`).join('\n') : '');
  const parseModeLines = (t) => {
    const o = {};
    t.split('\n').forEach((l) => { const m = l.match(/^\s*([a-z0-9_]+)\s*:\s*(.+?)\s*$/i); if (m) o[m[1]] = m[2]; });
    return o;
  };
  const adjustText = (a) => Object.entries(a || {}).map(([k, v]) => `${k} = ${v}`).join('\n');
  const textAdjust = (t) => { const o = textToSettings(t), r = {}; for (const [k, v] of Object.entries(o)) if (typeof v === 'number') r[k] = v; return r; };

  const card = (src, isNew) => {
    const d = clone(src);
    const info = S.lorasInfo || {};
    const msg = h('span', { class: 'sm muted' });
    const promptDefault = typeof d.prompt === 'string' ? d.prompt : '';
    let perMode = modeLines(d.prompt);
    let adjust = adjustText(d.adjust), settings = settingsToText(d.settings);
    const installed = d.installed;
    const badge = isNew ? null
      : installed === true ? h('span', { class: 'tag' }, 'instalada')
      : installed === false ? h('span', { class: 'tag soft', title: 'O arquivo não está na pasta de LoRAs do ComfyUI' }, 'não encontrada no ComfyUI')
      : null;
    const origin = isNew ? null : h('span', { class: 'tag soft' }, d.source === 'custom' ? 'sua' : 'padrão');
    const base = (info.base || []).includes(d.id) ? h('span', { class: 'tag', title: 'Faz parte da pilha fixa de algum workflow' }, 'pilha fixa') : null;
    const files = (info.unlisted || []);
    const save = async () => {
      const body = { id: d.id, title: d.title, file: d.file, description: d.description, strength: Number(d.strength), auto: !!d.auto };
      if (d.strength_clip !== '' && d.strength_clip != null) body.strength_clip = Number(d.strength_clip);
      const pm = parseModeLines(perMode);
      if (Object.keys(pm).length) body.prompt = pm; else if (d.prompt && typeof d.prompt === 'string') body.prompt = d.prompt;
      const adj = textAdjust(adjust); if (Object.keys(adj).length) body.adjust = adj;
      const st = textToSettings(settings); if (Object.keys(st).length) body.settings = st;
      if (d.needs) body.needs = Number(d.needs);
      if (d.group) body.group = d.group;
      if ((d.conflicts || []).length) body.conflicts = d.conflicts;
      if (d.catalog) body.catalog = d.catalog;
      try {
        await api(`/api/loras/${encodeURIComponent(d.id)}`, { method: 'PUT', body });
        toast('LoRA salva'); prefill = null; open.delete(isNew ? '__new' : d.id); await loadLoras(); render();
      } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
    };
    const remove = async () => {
      try {
        const r = (await api(`/api/loras/${encodeURIComponent(d.id)}`, { method: 'DELETE' })).result;
        toast(r === 'reset' ? 'Voltou ao padrão' : r === 'hidden' ? 'LoRA escondida' : 'LoRA removida');
        await loadLoras(); render();
      } catch (e) { msg.textContent = e.message; }
    };
    if (isNew) d.strength = d.strength ?? 1;
    const idField = isNew ? h('input', { type: 'text', value: d.id, placeholder: 'id_curto', oninput: (e) => { d.id = slug(e.target.value); } }) : null;
    const head = h('summary', { class: 'row' }, h('b', { class: 'grow' }, d.title || d.id || 'Nova LoRA'), base, badge, origin,
      h('span', { class: 'tag soft' }, `força ${d.strength}`), d.auto ? h('span', { class: 'tag' }, 'Clef pode usar') : null);
    return h('details', { class: 'card lora', open: isNew || open.has(d.id), ontoggle: (e) => { if (!isNew) (e.target.open ? open.add(d.id) : open.delete(d.id)); } }, head,
      h('div', { class: 'stack', style: 'margin-top:12px' },
        idField ? field('Id (letras minúsculas, números e _)', idField) : null,
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('Nome', h('input', { type: 'text', value: d.title, oninput: (e) => { d.title = e.target.value; } }))),
          h('div', { class: 'grow' }, field('Arquivo no ComfyUI', h('input', { type: 'text', value: d.file, list: 'lora-files', placeholder: 'pasta\\arquivo.safetensors', oninput: (e) => { d.file = e.target.value; } })))),
        field('Descrição (o Clef lê isto para decidir quando usar)', h('textarea', { rows: 3, value: d.description, oninput: (e) => { d.description = e.target.value; } }),
          'Diga o que a LoRA faz e em que pedido ela entra. Quanto mais claro, melhor o Clef escolhe.'),
        field('Prompt padrão (acompanha a LoRA sempre que ela entrar)', h('textarea', { rows: 3, value: promptDefault, placeholder: 'ex.: body_swap: use the body from image 2…', oninput: (e) => { d.prompt = e.target.value; } }),
          'Com redator (LLM) ativo, ele adapta este texto ao pedido. Sem redator, o PickFlow junta este texto com o seu pedido.'),
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('Força', h('input', { type: 'number', step: 0.05, min: 0, max: 2, value: d.strength, oninput: (e) => { d.strength = e.target.value; } }), '0 desliga.')),
          h('div', { class: 'grow' }, field('Força do texto (CLIP)', h('input', { type: 'number', step: 0.05, min: 0, max: 2, value: d.strength_clip ?? '', placeholder: 'igual à força', oninput: (e) => { d.strength_clip = e.target.value; } }))),
          h('div', { class: 'grow' }, field('Grupo', h('input', { type: 'text', value: d.group || '', placeholder: 'ex.: troca', oninput: (e) => { d.group = e.target.value; } }), 'Só uma LoRA por grupo entra.')),
          h('div', { class: 'grow' }, field('Mínimo de imagens', h('input', { type: 'number', min: 0, max: 8, step: 1, value: d.needs || '', oninput: (e) => { d.needs = e.target.value; } })))),
        field('Não funciona junto com (ids separados por vírgula)', h('input', { type: 'text', value: (d.conflicts || []).join(', '), list: 'lora-ids', placeholder: 'ex.: consistency',
          oninput: (e) => { d.conflicts = e.target.value.split(',').map((x) => x.trim()).filter(Boolean); } }),
          'Quando esta LoRA entra, as que conflitam saem da pilha (a regra vale nos dois sentidos). Uma LoRA que o Clef acrescenta nunca derruba a do prompt escolhido.'),
        switchEl('O Clef pode acrescentar esta LoRA sozinho', !!d.auto, (v) => { d.auto = v; }),
        h('details', { class: 'tech' }, h('summary', null, 'Ajustes avançados'),
          h('div', { class: 'stack', style: 'margin-top:8px' },
            field('Prompt diferente por modo (uma linha por modo: turbo: texto)', h('textarea', { rows: 2, value: perMode, oninput: (e) => { perMode = e.target.value; } }), 'Se preencher, vale no lugar do prompt padrão.'),
            field('Muda a força de outras LoRAs enquanto esta estiver ligada (id = força)', h('textarea', { rows: 2, value: adjust, placeholder: 'natexp = 0\nflymy = 0.3', oninput: (e) => { adjust = e.target.value; } })),
            field('Ajustes do workflow enquanto esta estiver ligada (nome = valor)', h('textarea', { rows: 3, value: settings, placeholder: 'resolution = 1024\nscene_mp = off', oninput: (e) => { settings = e.target.value; } }),
              'Os nomes (resolution, scene_mp, steps…) aparecem em “Detalhes técnicos” de cada workflow.'))),
        h('div', { class: 'row' }, msg, h('span', { class: 'grow' }),
          isNew ? h('button', { class: 'btn secondary', onclick: () => { open.delete('__new'); prefill = null; render(); } }, 'Cancelar')
            : h('button', { class: 'btn secondary', onclick: remove }, d.source === 'custom' ? 'Remover ou voltar ao padrão' : 'Esconder'),
          h('button', { class: 'btn', onclick: save }, 'Salvar'))));
  };

  const hf = { url: '', files: null, pick: 0, name: '', msg: '', bad: false, busy: false, job: null };
  const hfCard = () => {
    const urlIn = h('input', { type: 'text', value: hf.url, placeholder: 'https://huggingface.co/dono/modelo (ou o link de um arquivo .safetensors)',
      oninput: (e) => { hf.url = e.target.value; hf.files = null; } });
    const find = async () => {
      hf.msg = 'Procurando…'; hf.bad = false; hf.busy = true; render();
      try {
        const r = await api('/api/loras/hf/inspect', { method: 'POST', body: { url: hf.url } });
        hf.files = r.files; hf.pick = 0; hf.name = r.files[0].name; hf.page = r.page; hf.msg = r.files.length > 1 ? 'Escolha o arquivo:' : '';
        hf.hasToken = r.has_token;
      } catch (e) { hf.files = null; hf.msg = e.message; hf.bad = true; }
      hf.busy = false; render();
    };
    const poll = async () => {
      try {
        const j = await api('/api/setup/jobs');
        const job = (j.jobs || [])[0];
        hf.job = job || null;
        if (j.busy) { hf.msg = job && job.total ? `Baixando… ${Math.round((job.done / job.total) * 100)}% (${(job.done / 1e9).toFixed(2)} de ${(job.total / 1e9).toFixed(2)} GB)` : 'Baixando…'; render(); return setTimeout(poll, 1500); }
        if (job && job.state === 'erro') { hf.msg = job.error; hf.bad = true; }
        else { hf.msg = 'Pronto. A LoRA foi baixada para o ComfyUI e cadastrada; descreva-a abaixo para o Clef saber quando usar.'; hf.bad = false; hf.files = null; hf.url = ''; open.add(hf.lastId); }
        hf.busy = false; await loadLoras(); render();
      } catch (e) { hf.msg = e.message; hf.bad = true; hf.busy = false; render(); }
    };
    const download = async () => {
      const f = hf.files[hf.pick];
      hf.busy = true; hf.bad = false; hf.msg = 'Iniciando…'; render();
      try {
        const r = await api('/api/loras/hf/download', { method: 'POST', body: { url: f.url, file: hf.name } });
        hf.lastId = r.lora.id;
        if (r.installed) { hf.msg = 'Esse arquivo já está no ComfyUI; cadastrei a LoRA na biblioteca.'; hf.busy = false; hf.files = null; hf.url = ''; open.add(r.lora.id); await loadLoras(); render(); }
        else poll();
      } catch (e) { hf.msg = e.message; hf.bad = true; hf.busy = false; render(); }
    };
    return h('div', { class: 'card stack' }, h('h2', null, 'Baixar do Hugging Face'),
      h('p', { class: 'sub' }, 'Cole o link da página do modelo ou de um arquivo .safetensors. O arquivo vai para a pasta de LoRAs do ComfyUI e entra na biblioteca. Se o repositório for restrito ou pedir para aceitar uma licença, crie um token em huggingface.co/settings/tokens e escreva HF_TOKEN=seu_token no arquivo .env da pasta do PickFlow (copie o .env.example); depois reinicie o PickFlow.'),
      h('div', { class: 'row' }, h('div', { class: 'grow' }, urlIn),
        h('button', { class: 'btn secondary', disabled: hf.busy || !hf.url.trim(), onclick: find }, 'Procurar')),
      hf.files ? h('div', { class: 'stack' },
        hf.files.length > 1 ? h('div', { class: 'choice-list' }, hf.files.map((f, i) => h('label', { class: 'radio' },
          h('input', { type: 'radio', name: 'hf-file', checked: i === hf.pick, onchange: () => { hf.pick = i; hf.name = f.name; render(); } }),
          h('span', null, f.path), f.size ? h('span', { class: 'muted sm' }, ` ${(f.size / 1e9).toFixed(2)} GB`) : null))) : null,
        field('Salvar no ComfyUI com o nome', h('input', { type: 'text', value: hf.name, oninput: (e) => { hf.name = e.target.value; } }),
          'Pode renomear (ex.: o arquivo do autor tem outro nome). Precisa terminar em .safetensors.'),
        h('div', null, h('button', { class: 'btn', disabled: hf.busy, onclick: download }, 'Baixar e cadastrar'))) : null,
      hf.msg ? h('div', { class: 'hint' + (hf.bad ? ' bad' : '') }, hf.msg) : null);
  };
  const render = () => {
    const info = S.lorasInfo || { loras: [], unlisted: [] };
    const unl = info.unlisted || [];
    list.replaceChildren(...[
      h('datalist', { id: 'lora-files' }, unl.map((f) => h('option', { value: f }))),
      h('p', { class: 'sub muted' }, 'Cada LoRA tem uma descrição que o Clef lê para decidir quais ligar a cada pedido, e pode ter um prompt padrão. Os workflows unificados (como Editar) trocam as LoRAs conforme o contexto; tudo aqui é editável e fica na sua pasta custom/.'),
      hfCard(),
      open.has('__new') ? card(prefill || { id: '', title: '', file: '', description: '', strength: 1, auto: false }, true) : null,
      ...(info.loras || []).map((l) => card(l, false)),
      unl.length ? h('div', { class: 'card stack' }, h('h2', null, 'LoRAs do ComfyUI sem descrição'),
        h('p', { class: 'sub' }, 'Estes arquivos existem no ComfyUI mas o Clef ainda não sabe para que servem. Descreva para ele poder usar.'),
        h('div', { class: 'chips' }, unl.map((f) => h('button', { class: 'chip', title: f, onclick: () => {
          const id = uniqueId(slug(f.split(/[\\/]/).pop().replace(/\.[a-z]+$/i, '')), new Set((info.loras || []).map((x) => x.id)));
          prefill = { id, title: id, file: f, description: '', strength: 1, auto: true };
          open.add('__new'); render();
        } }, f.split(/[\\/]/).pop())))) : null,
      (info.hidden || []).length ? h('div', { class: 'card stack' }, h('h2', null, 'LoRAs escondidas'),
        h('p', { class: 'sub' }, 'Você escondeu estas LoRAs padrão. Restaure para o Clef e os prompts voltarem a usá-las.'),
        (info.hidden || []).map((l) => h('div', { class: 'row' }, h('b', { class: 'grow' }, l.title || l.id),
          h('button', { class: 'btn secondary', onclick: async () => {
            try { await api(`/api/loras/${encodeURIComponent(l.id)}/restore`, { method: 'POST' }); toast('LoRA restaurada'); await loadLoras(); render(); } catch (e) { toast(e.message); }
          } }, 'Restaurar')))) : null,
      info.comfy_ok === false ? h('p', { class: 'hint' }, 'Sem resposta do ComfyUI: não consegui conferir quais arquivos estão instalados.') : null,
    ].filter(Boolean));
  };
  page.append(h('div', { class: 'inner' }, list));
  loadLoras().then(render);
  render();
}

// =================================================================== AJUSTES
// Redator de prompt: vários provedores (cada um com endereço e chave de API) e vários modelos por provedor. A chave vai
// para o arquivo .env (nunca fica em custom/settings.yaml e a tela nunca a recebe de volta: só sabe se existe).
const defaultKeyEnv = (id) => 'LLM_API_KEY_' + String(id).toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
function llmCard(s) {
  const w = s.writer;
  if (!(w.providers || []).length && w.url) {         // formato antigo (um endereço e um modelo só) vira o primeiro provedor
    w.providers = [{ id: 'padrao', name: 'Padrão', url: w.url, key_env: 'LLM_API_KEY', models: w.model ? [{ id: w.model, vision: false }] : [] }];
    w.active = { provider: 'padrao', model: w.model || '' };
    w.url = ''; w.model = '';
  }
  w.providers = w.providers || [];
  w.active = w.active || {};
  const card = h('div', { class: 'card stack' });
  let info = { presets: [], providers: [], env_file: '.env' };
  const keySet = {};
  const status = {};                                 // id do provedor -> { ok, text }
  async function loadInfo() {
    try { info = await api('/api/llm'); (info.providers || []).forEach((p) => { keySet[p.key_env] = p.key_set; }); } catch (_) { /* sem servidor: segue sem os modelos prontos */ }
    draw();
  }
  const first = () => { const p = w.providers[0]; return p ? { provider: p.id, model: (p.models[0] || {}).id || '' } : {}; };
  const fixActive = () => {
    const p = w.providers.find((x) => x.id === w.active.provider);
    if (!p) w.active = first();
    else if (!p.models.some((m) => m.id === w.active.model)) w.active = { provider: p.id, model: (p.models[0] || {}).id || '' };
  };
  function providerView(p) {
    p.models = p.models || [];
    p.key_env = p.key_env || defaultKeyEnv(p.id);
    const keyIn = h('input', { type: 'password', class: 'grow', autocomplete: 'off', placeholder: keySet[p.key_env] ? '•••••••• (já guardada; cole outra para trocar)' : 'Cole a chave de API', 'aria-label': `Chave de API de ${p.name}` });
    const st = status[p.id];
    const saveKey = async (value) => {
      try {
        const r = await api('/api/env', { method: 'PUT', body: { name: p.key_env, value } });
        keySet[p.key_env] = r.set;
        status[p.id] = { ok: true, text: value ? `Chave guardada em ${p.key_env} no arquivo .env.` : 'Chave removida do .env.' };
      } catch (e) { status[p.id] = { ok: false, text: e.message }; }
      draw();
    };
    const test = async (btn) => {
      btn.disabled = true; status[p.id] = { ok: null, text: 'Testando…' }; draw();
      try {
        const m = p.models.find((x) => x.id === w.active.model && w.active.provider === p.id) || p.models[0] || { id: '' };
        const r = await api('/api/llm/test', { method: 'POST', body: { provider: { id: p.id, name: p.name, url: p.url, key_env: p.key_env, models: p.models }, model: m.id } });
        status[p.id] = r.ok ? { ok: true, text: `Respondeu em ${(r.ms / 1000).toFixed(1)} s${m.id ? ` (${m.id})` : ''}.` } : { ok: false, text: r.error };
      } catch (e) { status[p.id] = { ok: false, text: e.message }; }
      draw();
    };
    return h('div', { class: 'llm-prov' },
      h('div', { class: 'row wrap' },
        h('div', { class: 'grow' }, field('Nome', h('input', { type: 'text', value: p.name, oninput: (e) => { p.name = e.target.value; } }))),
        h('div', { class: 'grow2' }, field('Endereço (termina em /v1)', h('input', { type: 'text', value: p.url, oninput: (e) => { p.url = e.target.value.trim(); } })))),
      field(`Chave de API (variável ${p.key_env} do arquivo .env)`, h('div', { class: 'row' }, keyIn,
        h('button', { class: 'btn secondary', type: 'button', onclick: () => { if (keyIn.value.trim()) saveKey(keyIn.value.trim()); else toast('Cole a chave primeiro.'); } }, 'Guardar chave'),
        keySet[p.key_env] ? h('button', { class: 'ghost sm', type: 'button', onclick: () => saveKey('') }, 'Remover chave') : null),
      keySet[p.key_env] ? 'Chave configurada.' : 'Servidor local não precisa de chave: deixe em branco. A chave fica só no .env desta pasta, nunca em custom/settings.yaml.'),
      h('div', null, h('label', { class: 'f' }, 'Modelos deste provedor'),
        p.models.map((m, i) => h('div', { class: 'row llm-model' },
          h('input', { type: 'text', class: 'grow', value: m.id, placeholder: 'nome do modelo (ex.: gpt-5-mini)', 'aria-label': 'Nome do modelo', oninput: (e) => { const was = m.id; m.id = e.target.value.trim(); if (w.active.provider === p.id && w.active.model === was) w.active.model = m.id; } }),
          h('label', { class: 'switch', title: 'O PickFlow manda as imagens do passo junto: o redator descreve pelo que vê, e não só pelo texto' },
            h('input', { type: 'checkbox', checked: !!m.vision, onchange: (e) => { m.vision = e.target.checked; } }), h('span', { class: 'track' }), h('span', null, 'enxerga imagens')),
          h('button', { class: 'ghost sm', type: 'button', 'aria-label': 'Tirar este modelo', onclick: () => { p.models.splice(i, 1); fixActive(); draw(); } }, 'Tirar'))),
        h('button', { class: 'ghost sm', type: 'button', onclick: () => { p.models.push({ id: '', vision: false }); draw(); } }, '+ Adicionar modelo')),
      h('div', { class: 'row' },
        h('button', { class: 'btn secondary', type: 'button', onclick: (e) => test(e.currentTarget) }, 'Testar'),
        h('button', { class: 'ghost sm', type: 'button', onclick: () => { w.providers = w.providers.filter((x) => x !== p); fixActive(); draw(); } }, 'Remover provedor'),
        st ? h('span', { class: 'sm ' + (st.ok === true ? 'okc' : st.ok === false ? 'badc' : 'muted'), role: 'status' }, st.text) : null));
  }
  function draw() {
    fixActive();
    const opts = w.providers.flatMap((p) => (p.models.length ? p.models : [{ id: '' }]).map((m) => ({ pid: p.id, mid: m.id, label: `${tr(p.name)} · ${m.id || tr('modelo padrão')}` })));
    const sel = h('select', { 'aria-label': 'Modelo em uso', onchange: (e) => { const [pid, mid] = e.target.value.split('\u0001'); w.active = { provider: pid, model: mid }; } },
      opts.length ? opts.map((o) => h('option', { value: `${o.pid}\u0001${o.mid}`, selected: o.pid === w.active.provider && o.mid === w.active.model }, o.label)) : h('option', { value: '' }, 'Nenhum provedor ainda'));
    const addSel = h('select', { 'aria-label': 'Provedor a acrescentar' }, (info.presets || []).map((p, i) => h('option', { value: String(i) }, p.name)));
    card.replaceChildren(
      h('h2', null, 'Redator de prompt (opcional)'),
      h('p', { class: 'sub' }, tr('Um LLM monta o prompt final a partir do pedido, das instruções das LoRAs e (se o modelo enxerga) das imagens do passo. Vale nos prompts marcados para usar o redator, como o "completar o corpo" da fusão. Vários provedores e vários modelos por provedor; as chaves de API ficam no arquivo .env ({}).').replace('{}', info.env_file || '.env')),
      switchEl('Ativar redator', !!w.enabled, (v) => { w.enabled = v; }),
      field('Modelo em uso', sel, 'O mesmo LLM também serve ao agente de casos de uso e aos addons.'),
      h('div', { class: 'stack' }, w.providers.map(providerView)),
      h('div', { class: 'row' }, addSel, h('button', { class: 'btn secondary', type: 'button', onclick: () => {
        const pr = (info.presets || [])[Number(addSel.value)];
        if (!pr) { toast('Não consegui ler a lista de provedores prontos; recarregue a página.'); return; }
        const id = uniqueId(pr.id, new Set(w.providers.map((x) => x.id)));
        w.providers.push({ id, name: pr.name, url: pr.url, key_env: defaultKeyEnv(id), models: (pr.models || []).map((m) => ({ id: m, vision: false })) });
        if (!w.active.provider) w.active = first();
        draw();
      } }, 'Adicionar provedor'), h('button', { class: 'ghost sm', type: 'button', onclick: () => {
        const id = uniqueId('outro', new Set(w.providers.map((x) => x.id)));
        w.providers.push({ id, name: 'Outro provedor', url: '', key_env: defaultKeyEnv(id), models: [{ id: '', vision: false }] }); draw();
      } }, 'Provedor em branco')),
      field('O que o redator faz', h('select', { onchange: (e) => { w.mode = e.target.value; } },
        h('option', { value: 'refine', selected: (w.mode || 'refine') === 'refine' }, 'Ajusta o prompt montado ao pedido (recomendado)'),
        h('option', { value: 'translate', selected: w.mode === 'translate' }, 'Só traduz o pedido para inglês')),
        'Ajustar: recebe o prompt com as instruções das LoRAs e o pedido, e devolve o prompt final. Se o redator falhar, o PickFlow segue com o prompt montado e avisa o motivo na conversa.'),
      h('details', null, h('summary', { class: 'sm' }, 'Instruções do redator'),
        field('Instrução ao traduzir', h('textarea', { rows: 3, value: w.system || '', oninput: (e) => { w.system = e.target.value; } })),
        field('Instrução ao ajustar', h('textarea', { rows: 4, value: w.refine_system || '', oninput: (e) => { w.refine_system = e.target.value; } }))));
  }
  draw();
  loadInfo();
  return card;
}

// Pede ao servidor do Clef para trocar entre CPU (RAM) e GPU (VRAM) sem reiniciá-lo. O ajuste salvo já decide a
// política de memória; isto aqui só avisa o servidor (um servidor antigo recusa e a mensagem diz o que fazer).
async function applyClefDevice(btn, device) {
  if (btn) btn.disabled = true;
  try {
    const r = await api('/api/clef/device', { method: 'POST', body: { device: device || 'cpu' } });
    toast(r.ok ? 'Clef trocado: ele recarrega no próximo pedido.' : (r.error || 'Não deu para trocar.'));
    refreshStatus();
    refreshClefMemory();
  } catch (e) {
    toast(e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}
// Aprendizado com o feedback: cada execução ganha uma pergunta discreta ("era isso?") e o histórico entra na
// decisão seguinte — como exemplo de pedidos parecidos e corrigindo a probabilidade do Clef (pickflow/feedback.py).
// Recebe os construtores de campo de `mountSettings` (`num`, `field`, `switchEl`), que são locais de lá.
function learnCard(s, num, field, switchEl) {
  const info = h('div', { class: 'hint' }, 'Lendo o seu histórico…');
  api('/api/feedback').then((r) => {
    const sum = (r && r.summary) || {};
    const judged = (sum.up || 0) + (sum.down || 0) + (sum.other || 0);
    info.textContent = judged
      ? tr(`${judged} execuções julgadas · ${Math.round((sum.accuracy || 0) * 100)}% de acerto`)
      : 'Nada julgado ainda: cada resposta sua passa a ensinar o Clef.';
  }).catch(() => { info.textContent = ''; });
  const learn = (s.clef && s.clef.learn) || (s.clef.learn = {});
  return h('div', { class: 'card stack' }, h('h2', null, 'Aprendizado com o seu feedback'),
    h('p', { class: 'sub' }, 'Cada execução ganha uma pergunta discreta ("era isso?"). Os casos ficam em data/feedback.jsonl e o histórico é usado de dois jeitos: pedidos parecidos que deram certo entram na decisão seguinte, e quando o histórico discorda do Clef a probabilidade é corrigida. Baixar a imagem conta como um positivo fraco; fechar a pergunta não conta como erro.'),
    switchEl('Aprender com o meu feedback', learn.enabled !== false, (v) => { learn.enabled = v; }),
    info,
    h('div', { class: 'row wrap' },
      h('div', { class: 'grow' }, field('Peso do histórico na probabilidade', num(learn, 'weight', 0, 0.8, 0.05),
        '0 = só o Clef decide; 0,5 deixa o histórico valer metade quando ele discorda do Clef.')),
      h('div', { class: 'grow' }, field('Casos parecidos como exemplo', num(learn, 'examples', 0, 8, 1),
        'Quantos pedidos parecidos que deram certo entram no estado do Clef. 0 desliga.')),
      h('div', { class: 'grow' }, field('Perguntar na tela abaixo de', num(learn, 'ask_preset_below', 0, 1, 0.05),
        'Se a confiança no prompt ficar menor que isto, o PickFlow pergunta em vez de arriscar. 0 desliga.'))));
}
// Descarrega o modelo do Clef agora (libera RAM/VRAM). Serve depois de cancelar um pedido: a carga roda no processo
// do servidor do Clef e não pode ser interrompida pela metade, então ela termina e o modelo é liberado em seguida.
async function unloadClef(btn) {
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Descarregando…'; }
  try {
    const r = await api('/api/clef/unload', { method: 'POST' });
    toast(r.ok ? 'O Clef foi descarregado; ele recarrega no próximo pedido.' : (r.error || 'Não deu para descarregar.'));
  } catch (e) {
    toast(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
    refreshClefMemory();
  }
}
function refreshClefMemory() { clefMemory(document.querySelector('#clef-memory')); }
function mountSettings(main) {
  const s = clone(S.settings);
  const page = h('div', { class: 'page' });
  const msg = h('span', { class: 'sm muted' });
  const docBox = h('div', { class: 'doctor' });
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'Ajustes')), page);
  const num = (obj, k, min, max, step) => h('input', { type: 'number', value: obj[k], min, max, step, oninput: (e) => { obj[k] = Number(e.target.value); } });
  const txt = (obj, k) => h('input', { type: 'text', value: obj[k], oninput: (e) => { obj[k] = e.target.value; } });
  const comfyInput = txt(s.comfyui, 'url');
  const comfyMsg = h('div', { class: 'hint' });
  const testComfy = async (e) => {
    const btn = e && e.currentTarget; if (btn) btn.disabled = true;
    comfyMsg.className = 'hint'; comfyMsg.textContent = 'Testando…';
    try {
      const r = await api('/api/comfy/test', { method: 'POST', body: { url: s.comfyui.url } });
      if (r.ok) {
        await api('/api/settings', { method: 'PUT', body: { comfyui: { url: s.comfyui.url } } });
        S.settings.comfyui.url = s.comfyui.url;
        comfyMsg.className = 'hint ok';
        comfyMsg.textContent = `Conectado ao ComfyUI${r.version ? ' ' + r.version : ''}${r.device ? ` (${r.device})` : ''} e endereço salvo.`;
        refreshStatus();
      } else {
        comfyMsg.className = 'hint bad';
        comfyMsg.textContent = `Não consegui falar com o ComfyUI em ${r.url}: ${r.error}.`;
        if (r.detected_url && r.detected_url !== r.url) {
          comfyMsg.append(' Achei um ComfyUI em ', h('code', null, r.detected_url), ' ',
            h('button', { class: 'ghost sm', type: 'button', onclick: () => { s.comfyui.url = r.detected_url; comfyInput.value = r.detected_url; testComfy(); } }, 'Usar este'));
        }
      }
    } catch (err) { comfyMsg.className = 'hint bad'; comfyMsg.textContent = err.message; }
    if (btn) btn.disabled = false;
  };
  const doctor = async (btn) => {
    btn.disabled = true; docBox.replaceChildren(h('p', { class: 'muted sm' }, 'Convertendo cada workflow contra o ComfyUI…'));
    try {
      const res = await api('/api/doctor', { method: 'POST' });
      docBox.replaceChildren(...res.map((r) => h('div', { class: 'd' + (r.ok ? '' : ' bad') },
        h('b', null, `${tr(r.ok ? 'Pronto' : 'Com problema')}: ${tr(r.title)}`),
        r.errors.length || r.warnings.length ? h('ul', null, r.errors.map((x) => h('li', null, x)), r.warnings.map((x) => h('li', null, 'Aviso: ' + x))) : null)));
    } catch (e) { docBox.replaceChildren(h('div', { class: 'd bad' }, e.message)); }
    btn.disabled = false;
  };
  const mode = h('select', { onchange: (e) => { s.clef.mode = e.target.value; } },
    h('option', { value: 'systemone', selected: s.clef.mode === 'systemone' }, 'Clef (servidor System One)'),
    h('option', { value: 'mock', selected: s.clef.mode === 'mock' }, 'Teste sem modelo (palavras em comum)'));
  const unloadSel = h('select', { onchange: (e) => { s.clef.unload_policy = e.target.value; } },
    ...[['auto', 'Automático (recomendado)'], ['always', 'Sempre'], ['never', 'Nunca']].map(([v, t]) =>
      h('option', { value: v, selected: (s.clef.unload_policy || 'auto') === v }, t)));
  // Onde o Clef roda: na RAM ele não disputa a placa com o Qwen (é o padrão); na GPU é mais rápido, mas ocupa VRAM.
  const deviceSel = h('select', { onchange: (e) => { s.clef.device = e.target.value; } },
    ...[['cpu', 'CPU: na memória RAM'], ['gpu', 'GPU: na placa de vídeo']].map(([v, t]) =>
      h('option', { value: v, selected: (s.clef.device || 'cpu') === v }, t)));
  // O que o servidor do Clef informa + o que a política de memória vai fazer com isso
  // Codificador de texto (CLIP): "automático" deixa cada workflow com o seu; as versões que o ComfyUI não tem ficam desligadas.
  s.comfyui.text_encoder = s.comfyui.text_encoder || 'auto';
  const encSel = h('select', { 'aria-label': 'Codificador de texto (CLIP)', onchange: (e) => { s.comfyui.text_encoder = e.target.value; } },
    h('option', { value: 'auto' }, 'Automático: cada workflow usa o seu'));
  encSel.value = 'auto';
  api('/api/encoders').then((r) => {
    for (const o of r.options) {
      encSel.append(h('option', { value: o.id, translate: 'no', disabled: o.installed === false && s.comfyui.text_encoder !== o.id },
        o.installed === false ? `${o.title} (não instalado)` : o.title));
    }
    encSel.value = [...encSel.options].some((o) => o.value === s.comfyui.text_encoder) ? s.comfyui.text_encoder : 'auto';
  }).catch(() => {});
  const memBox = h('div', { class: 'hint', id: 'clef-memory' }, 'Consultando o servidor do Clef…');
  const dbtn = h('button', { class: 'btn secondary', onclick: (e) => doctor(e.target) }, 'Verificar workflows');
  // Idioma: grava na hora e recarrega a página (os nomes das opções ficam no próprio idioma, sem tradução).
  const langSel = h('select', { 'aria-label': 'Idioma da interface', onchange: async (e) => {
    try { await api('/api/settings', { method: 'PUT', body: { ui: { language: e.target.value } } }); location.reload(); } catch (err) { toast(err.message); }
  } }, [['en', 'English'], ['pt', 'Português (Brasil)']].map(([v, t]) =>
    h('option', { value: v, selected: ((s.ui || {}).language || 'en') === v, translate: 'no' }, t)));
  // Ajustes em seções (abas): cada cartão fala de uma coisa só, e há UM botão Salvar, sempre à vista, para tudo o que é campo.
  // Idioma, "Testar" do ComfyUI e os botões dos addons gravam na hora, como sempre.
  const section = (title, sub, ...kids) => h('div', { class: 'card stack' }, h('h2', null, title),
    sub ? h('p', { class: 'sub' }, sub) : null, ...kids);
  const save = async () => {
    try { S.settings = await api('/api/settings', { method: 'PUT', body: s }); toast('Salvo'); msg.textContent = ''; refreshStatus(); } catch (e) { msg.textContent = e.message; }
  };
  const panels = {
    general: [
      section('Idioma', null,
        field('Idioma da interface', langSel, 'Vale para a interface e para as mensagens do PickFlow. As descrições e instruções que o Clef lê continuam como estão.'))],
    comfyui: [
      section('Conexão', null,
        field('Endereço', h('div', { class: 'row' }, h('div', { class: 'grow' }, comfyInput), h('button', { class: 'btn secondary', onclick: (e) => testComfy(e) }, 'Testar')), 'Desktop costuma usar a porta 8000; a versão portátil, 8188.'),
        comfyMsg),
      section('Imagens e modelos', null,
        field('Reduzir imagens enviadas para até (megapixels)', num(s.comfyui, 'max_input_mp', 0, 16, 0.5),
          'Fotos de celular têm 12 MP e deixam o ComfyUI muito lento sem melhorar o resultado. 0 desliga a redução.'),
        field('Codificador de texto (CLIP)', encSel,
          'O Qwen3-VL 8B em duas versões. Os workflows de editar, transferir, pose e 3D usam o fp8 (heretic); os de trocar pessoa e roupa, o int8 (convrot). Escolher uma versão troca o arquivo em todos os workflows; só dá para escolher as que o ComfyUI tem.')),
      section('Memória do ComfyUI', 'Ajuda quando o ComfyUI e o Clef dividem a mesma placa de vídeo.',
        switchEl('Liberar a VRAM do ComfyUI antes de o Clef decidir', s.comfyui.free_vram_before_clef, (v) => { s.comfyui.free_vram_before_clef = v; }),
        switchEl('Liberar a VRAM ao terminar cada execução', s.comfyui.free_vram_after_run, (v) => { s.comfyui.free_vram_after_run = v; })),
      section('Instalação e conferência', 'O assistente confere o que os workflows precisam dentro do ComfyUI (nós do PickFlow, LoRAs, modelos) e instala o que você marcar. A checagem converte cada workflow para a API do ComfyUI e confere nós, ligações e modelos; rode depois de mexer nos workflows.',
        h('div', { class: 'row wrap' }, h('button', { class: 'btn secondary', onclick: () => setupModal() }, 'Abrir o assistente'), dbtn),
        docBox)],
    clef: [
      section('Servidor', 'Onde o servidor do Clef está. Para ligar o que vem com o PickFlow, rode start_clef (ele pergunta bf16 ou int4).',
        field('Modo', mode),
        h('div', { class: 'row wrap' }, h('div', { class: 'grow' }, field('Endereço do servidor', txt(s.clef, 'url'))), h('div', null, field('Caminho', txt(s.clef, 'path')))),
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('Tempo máximo de resposta (segundos)', num(s.clef, 'timeout_s', 10, 900, 10),
            'Ele pode levar uns 30 s só para carregar o modelo no primeiro pedido.')),
          h('div', { class: 'grow' }, field('Chave de API (só se o servidor exigir)', txt(s.clef, 'api_key'))))),
      section('Decisão', null,
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('Máximo de etapas por pedido', num(s.clef, 'max_steps', 1, 6, 1), 'Quantos workflows em sequência o Clef pode propor.')),
          h('div', { class: 'grow' }, field('Perguntar a você abaixo de', num(s.clef, 'ask_below', 0, 1, 0.05), 'Se a confiança do Clef na rota for menor, você escolhe. 0 desliga.')),
          h('div', { class: 'grow' }, field('Imagens enviadas ao Clef', num(s.clef, 'max_images', 1, 8, 1))))),
      section('Memória', 'Clef e ComfyUI na mesma máquina: o PickFlow mede a memória e descarrega o Clef antes de gerar só quando os dois não cabem juntos.',
        field('Onde o Clef roda', h('div', { class: 'row' }, h('div', { class: 'grow' }, deviceSel),
          h('button', { class: 'btn secondary', onclick: (e) => applyClefDevice(e.target, deviceSel.value) }, 'Aplicar no servidor')),
          'Na CPU o Clef usa a RAM (~18 GB em bf16) e não disputa a placa com o Qwen. Na GPU é bem mais rápido, mas ocupa VRAM e costuma ser descarregado antes de cada geração; o int4 (~7,9 GB) só roda em placa NVIDIA. O servidor informa o que está usando em /health: este ajuste vale quando ele não informa. "Aplicar no servidor" troca sem reiniciar.'),
        field('Descarregar o Clef antes de gerar a imagem', unloadSel,
          'Automático: só descarrega se o Clef e o ComfyUI não couberem juntos na memória. O Clef recarrega no próximo pedido (alguns segundos).'),
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('VRAM que o workflow usa (GB)', num(s.clef, 'need_vram_gb', 1, 96, 1))),
          h('div', { class: 'grow' }, field('RAM que o workflow usa (GB)', num(s.clef, 'need_ram_gb', 1, 256, 1))),
          h('div', { class: 'grow' }, field('Folga (GB)', num(s.clef, 'margin_gb', 0, 16, 0.5)))),
        switchEl('Liberar os modelos do ComfyUI se faltar VRAM para carregar o Clef', s.clef.make_room_for_clef, (v) => { s.clef.make_room_for_clef = v; }),
        h('div', { class: 'row' }, h('button', { class: 'btn secondary', onclick: (e) => unloadClef(e.target) }, 'Descarregar o Clef agora')),
        memBox),
      learnCard(s, num, field, switchEl)],
    ai: [
      llmCard(s),
      section('Agente de casos de uso', 'Quando o Clef acha que nenhum workflow ou caso de uso cobre o pedido todo (por exemplo, "deixe a pessoa colorida e depois cole no cenário"), um LLM monta um caso de uso novo consultando o Clef. Usa o modelo em uso do redator (acima). Fica desligado até você ligar.',
        switchEl('Ligar o agente', !!s.agent.enabled, (v) => { s.agent.enabled = v; }),
        switchEl('Perguntar a você antes de usar e guardar o caso criado', s.agent.confirm !== false, (v) => { s.agent.confirm = v; }),
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('Confiança mínima na lacuna', num(s.agent, 'gap_min', 0.3, 1, 0.05), 'O Clef precisa estar pelo menos assim seguro de que falta uma etapa para o agente agir.')),
          h('div', { class: 'grow' }, field('Respostas do LLM por criação', num(s.agent, 'max_rounds', 2, 20, 1))),
          h('div', { class: 'grow' }, field('Consultas ao Clef por criação', num(s.agent, 'max_clef_calls', 0, 20, 1))),
          h('div', { class: 'grow' }, field('Passos no máximo', num(s.agent, 'max_steps', 2, 8, 1)))),
        h('div', { class: 'row wrap' },
          h('div', { class: 'grow' }, field('Casos em teste guardados', num(s.agent, 'max_trial', 1, 50, 1), 'Passando disso, sai o mais antigo que você não mandou manter.')),
          h('div', { class: 'grow' }, field('Casos mantidos no máximo', num(s.agent, 'max_generated', 1, 200, 1), 'Chegando aqui o agente para de criar até você apagar ou fundir alguns.'))))],
    addons: [addonsCard()],
  };
  const TABS = [['general', 'Geral'], ['comfyui', 'ComfyUI'], ['clef', 'Clef'], ['ai', 'Redator e agente'], ['addons', 'Addons']];
  const saveBar = h('div', { class: 'savebar' }, msg, h('span', { class: 'grow' }), h('button', { class: 'btn', onclick: save }, 'Salvar ajustes'));
  const panelEls = Object.fromEntries(Object.entries(panels).map(([id, kids]) => [id, h('div', { class: 'tab-panel stack', role: 'tabpanel' }, ...kids)]));
  const tabBar = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Seções dos ajustes' }, TABS.map(([id, label]) =>
    h('button', { type: 'button', role: 'tab', 'data-tab': id, onclick: () => show(id) }, label)));
  // a aba aberta ao entrar: a última que você usou; na primeira vez, a do que está faltando (ComfyUI e depois Clef)
  const first = S.settingsTab || (S.status && !S.status.comfy.ok ? 'comfyui' : (S.status && !S.status.clef.ok ? 'clef' : 'general'));
  function show(id) {
    S.settingsTab = id;
    for (const [k, el] of Object.entries(panelEls)) el.hidden = k !== id;
    for (const b of tabBar.children) { const on = b.dataset.tab === id; b.classList.toggle('on', on); b.setAttribute('aria-selected', String(on)); }
    saveBar.hidden = id === 'general' || id === 'addons';        // só os campos precisam do botão Salvar
  }
  page.append(h('div', { class: 'inner' }, tabBar, ...Object.values(panelEls), saveBar));
  show(panelEls[first] ? first : 'general');
  clefMemory(memBox);
}

// O que o servidor do Clef informa (quantização e quanto ele ocupa) e o que a política vai fazer.
// O número vem do servidor: trocar bf16 por int4 na linha de comando já muda a decisão, sem ajuste aqui.
async function clefMemory(box) {
  if (!box) return;
  try {
    const r = await api('/api/clef/memory');
    if (!r.ok) {
      box.replaceChildren(h('div', null, 'O servidor do Clef não respondeu — confira se ele está de pé.'));
      return;
    }
    const bits = r.quant === 'nf4' ? '4 bits' : (r.quant === 'int8' ? '8 bits' : 'bf16');
    const onde = r.device === 'cpu'
      ? `na RAM (${r.ram_gb} GB do processo)`
      : `na placa (${r.clef_resting_gb} GB de pesos, até ~${r.clef_gb} GB para decidir)`;
    box.replaceChildren(...[
      h('div', null, `Servidor do Clef: ${bits}, ${onde}${r.loaded ? ', carregado agora' : ', descarregado agora'}.`),
      h('div', null, r.unload_before_generation
        ? `Antes de gerar: descarrega — ${r.unload_reason}`
        : `Antes de gerar: continua carregado (o workflow pede ${r.need_vram_gb} GB e o Clef cabe ao lado).`),
      r.free_comfy_before_clef ? h('div', null, `Antes de decidir: libera o ComfyUI — ${r.free_reason}`) : null,
    ].filter(Boolean));        // `replaceChildren` com null escreveria a palavra "null" na tela
  } catch (e) {
    box.replaceChildren(h('div', null, e.message));
  }
}

// ------------------------------------------------------- addons: window.PickFlow
// A API que os addons (addons/<nome>/web/*.js) usam para mexer na interface. Documentada em addons/README.md.
const PickFlow = {
  version: 1,
  h, api, toast, openModal, boxEditor, uniqueId, slug, tr,
  /** Idioma da interface: 'en' ou 'pt'. Textos do addon em português que estão em web/locales/en.json saem traduzidos. */
  get lang() { return window.I18N ? window.I18N.lang : 'pt'; },
  /** Dados atuais: conversa aberta, imagens anexadas, ajustes e workflows. Somente leitura. */
  get state() { return { view: S.view, conversation: S.conv, tray: S.tray.slice(), settings: S.settings, workflows: S.workflows, running: !!S.live }; },
  /** Eventos: 'ready', 'view' {view}, 'run:event' (cada evento da execução), 'run:end' {runId, images}. */
  on(event, fn) { if (!AX.listeners.has(event)) AX.listeners.set(event, new Set()); AX.listeners.get(event).add(fn); return () => AX.listeners.get(event).delete(fn); },
  /** Tela nova no menu da esquerda: { id, title, mount(container, PickFlow) }. */
  addView({ id, title, mount }) {
    if (!/^[a-z0-9_-]+$/.test(id || '') || VIEWS[id]) throw new Error(`Tela "${id}" inválida ou já existe.`);
    VIEWS[id] = (main) => { main.append(h('div', { class: 'page-head' }, h('h1', null, title))); const page = h('div', { class: 'page' }, h('div', { class: 'inner' })); main.append(page); return mount(page.firstChild, PickFlow); };
    $('#nav').append(h('button', { 'data-view': id, class: 'addon-nav' }, title));
  },
  /** Botão ao lado de "Anexar imagem": { label, title, onClick(PickFlow) }. */
  addButton({ label, title, onClick, where = 'composer' }) {
    const b = { label, title, onClick, where }; AX.buttons.push(b);
    if (S.C && S.C.composer) { const bar = S.C.composer.querySelector('.addon-bar'); if (bar) bar.append(addonButtonEl(b)); }
  },
  /** Botão em cada imagem de resultado: { label, title, onClick(image, PickFlow) }; image = {id, url, name}. */
  addResultAction({ label, title, onClick }) { AX.resultActions.push({ label, title, onClick }); },
  /** Bloco dentro de Ajustes > Addons: { id, title, mount(container, PickFlow) }. */
  addSettings({ id, title, mount }) { AX.settings.push({ id, title, mount }); },
  /** Tela de entrada própria: o Python pede com `await call.ask("meu_tipo", ...)` e esta função devolve a resposta.
   *  fn(evento, PickFlow) -> Promise<valor>; devolver null cancela a execução. */
  registerInput(kind, fn) { AX.inputs.set(kind, fn); },
  /** Chama as rotas do próprio addon: PickFlow.addonApi('meu_addon', '/rota', { method, body }). */
  addonApi(name, path, opts) { return api(`/api/addons/${name}${path.startsWith('/') ? path : '/' + path}`, opts); },
  /** Janela simples: { title, body (Node ou texto), buttons: [{ label, value, primary }] } -> Promise<value|null>. */
  modal({ title, body, buttons }) {
    return new Promise((resolve) => {
      let close; const done = (v) => { close(); resolve(v); };
      const btns = (buttons && buttons.length ? buttons : [{ label: 'Fechar', value: true, primary: true }]);
      close = openModal(h('div', { class: 'modal' }, h('header', null, h('h2', null, title || '')), h('div', { class: 'body' }, body),
        h('footer', null, h('span', { class: 'grow' }), btns.map((b) => h('button', { class: 'btn' + (b.primary ? '' : ' secondary'), onclick: () => done(b.value === undefined ? b.label : b.value) }, b.label)))), () => done(null));
    });
  },
  /** Texto do pedido (campo de mensagem da conversa). */
  setText(text) { go('chat'); if (S.C) { S.C.ta.value = text; autosize(); S.C.ta.focus(); } },
  /** Anexa uma imagem (File/Blob) às imagens da próxima mensagem. */
  async attach(file) { go('chat'); await addFiles([file]); },
  /** Envia uma mensagem: { text, images? (objetos de imagem já enviados) }. */
  async send({ text = '', images } = {}) { go('chat'); if (images) { S.tray = images.slice(); renderTray(); } S.C.ta.value = text; autosize(); return sendMessage(); },
  /** Ajustes guardados do addon (settings.yaml > addons.config.<nome>) e gravação deles. */
  config(name) { return clone(((S.settings.addons || {}).config || {})[name] || {}); },
  async saveConfig(name, cfg) { S.settings = await api('/api/settings', { method: 'PUT', body: { addons: { config: { [name]: cfg } } } }); return this.config(name); },
};
window.PickFlow = PickFlow;
window.ClefFlow = PickFlow;                             // nome antigo: addons escritos antes do rename continuam funcionando

async function loadAddons() {
  let info = { addons: [] };
  try { info = await api('/api/addons'); } catch (_) { return; }
  AX.info = info.addons;
  for (const a of info.addons) {
    if (!a.enabled || a.error) continue;
    for (const href of a.styles || []) document.head.append(h('link', { rel: 'stylesheet', href }));
    for (const src of a.scripts || []) {
      await new Promise((ok) => {
        const sc = h('script', { src, onload: ok, onerror: () => { toast(`O addon ${a.name} não carregou ${src.split('/').pop()}.`); ok(); } });
        document.head.append(sc);
      });
    }
  }
}

// Ajustes > Addons: lista, liga/desliga, config e criação
function addonsCard() {
  const box = h('div', { class: 'stack' });
  const nameIn = h('input', { type: 'text', placeholder: 'nome-do-addon', 'aria-label': 'Nome do novo addon' });
  const render = async () => {
    let info;
    try { info = await api('/api/addons'); } catch (e) { box.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
    box.replaceChildren(h('div', { class: 'stack' },
      h('p', { class: 'sub' }, 'Extensões suas, na pasta ', h('code', null, 'addons/'), ' (o git não publica). Um addon pode trazer workflows, prompts, ganchos em Python, ações novas e telas. Ligar, desligar ou criar vale depois de reiniciar o PickFlow.'),
      info.addons.length ? info.addons.map((a) => h('div', { class: 'addon-row' },
        h('div', { class: 'grow' },
          h('b', null, a.title), a.version ? h('span', { class: 'muted sm' }, ' v' + a.version) : null,
          a.description ? h('div', { class: 'muted sm' }, a.description) : null,
          h('div', { class: 'muted sm' }, [a.resources.length ? tr('recursos:') + ' ' + a.resources.join(', ') : '', a.hooks.length ? tr('ganchos:') + ' ' + a.hooks.join(', ') : '', a.actions.length ? tr('ações:') + ' ' + a.actions.join(', ') : '', a.scripts.length ? tr('tela própria') : ''].filter(Boolean).join(' · ') || 'sem nada ainda'),
          a.error ? h('div', { class: 'msg err' }, a.error) : null),
        switchEl(a.enabled ? 'Ligado' : 'Desligado', a.enabled, async (v) => {
          try { await api(`/api/addons/${a.name}`, { method: 'PUT', body: { enabled: v } }); toast('Reinicie o PickFlow para valer'); render(); } catch (e) { toast(e.message); }
        }))) : h('p', { class: 'muted sm' }, 'Nenhum addon ainda.'),
      h('div', { class: 'row' }, h('div', { class: 'grow' }, nameIn), h('button', { class: 'btn secondary', onclick: async () => {
        try { const r = await api('/api/addons', { method: 'POST', body: { name: nameIn.value.trim() } }); toast('Criado em ' + r.path + '. Reinicie o PickFlow.'); nameIn.value = ''; render(); } catch (e) { toast(e.message); }
      } }, 'Criar addon a partir do modelo')),
      h('div', { class: 'hint' }, 'Para o agente (Claude Code) criar um por você, use a skill pickflow-addons.'),
      AX.settings.map((x) => { const c = h('div', { class: 'stack' }); try { x.mount(c, PickFlow); } catch (e) { c.append(String(e.message || e)); } return h('div', { class: 'addon-set' }, h('h3', null, x.title), c); })));
  };
  render();
  return h('div', { class: 'card stack' }, h('h2', null, 'Addons'), box);
}

// -------------------------------------------------------------------- start
(async function init() {
  try {
    await loadAll();
    await I18N.start((S.settings.ui || {}).language || 'en');
    await loadAddons();
    if (S.convs.length) S.conv = await api(`/api/conversations/${S.convs[0].id}`);
  } catch (e) {
    if (!S.settings) await I18N.start('en');
    toast(tr('Não consegui carregar: {}').replace('{}', tr(e.message)));
  }
  document.documentElement.classList.remove('i18n-wait');
  renderConvs();
  go('chat');
  reattach();
  AXemit('ready', {});
  setInterval(refreshStatus, 15000);
})();
