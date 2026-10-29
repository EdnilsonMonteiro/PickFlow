/* PickFlow: idioma da interface.
 *
 * O texto-fonte da interface (app.js, mensagens do servidor, nomes em defaults/) é português. Em outro idioma, um
 * catálogo (web/locales/<lang>.json) traduz o que aparece na tela:
 *   strings    {"texto em português": "tradução"}  (texto inteiro, sem espaços nas pontas)
 *   templates  {"Baixando… {}% ({} de {} GB)": "Downloading… {}% ({} of {} GB)"}: cada {} pega um pedaço variável,
 *              que também é traduzido se estiver no catálogo; na tradução, {0} {1}… reordenam.
 * A tradução é feita no DOM (nós de texto e os atributos title, placeholder, aria-label, alt e label), conforme a página é
 * desenhada. Por isso campos editáveis (input, textarea) continuam com o conteúdo real, e o que está dentro de
 * translate="no" (o pedido do usuário, o prompt enviado) não é tocado.
 */
'use strict';
(function () {
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt', 'label'];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'CODE', 'PRE']);
  const FIELDS = new Set(['TEXTAREA', 'INPUT']);
  const I = { lang: 'pt', strings: new Map(), byHead: new Map(), loose: [], observer: null };

  function compile(key, value) {
    const parts = key.split('{}');
    const esc = parts.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    return { re: new RegExp('^' + esc.join('([\\s\\S]*?)') + '$'), value, n: parts.length - 1 };
  }
  function load(cat) {
    I.strings = new Map(Object.entries(cat.strings || {}));
    I.byHead = new Map(); I.loose = [];
    for (const [k, v] of Object.entries(cat.templates || {})) {
      const t = compile(k, v), head = k.split('{}')[0];
      if (head.length >= 2) { const h2 = head.slice(0, 2); (I.byHead.get(h2) || I.byHead.set(h2, []).get(h2)).push(t); }
      else I.loose.push(t);
    }
  }
  function fill(t, m, depth) {
    const caps = m.slice(1).map((c) => tr(c, depth + 1));
    let i = 0;
    return t.value.replace(/\{(\d*)\}/g, (_, n) => (n === '' ? caps[i++] : caps[Number(n)]) ?? '');
  }
  // Traduz um texto inteiro (ou devolve como veio). Espaços nas pontas são mantidos.
  function tr(text, depth = 0) {
    if (I.lang === 'pt' || text == null) return text;
    const s = String(text), core = s.trim();
    if (!core || depth > 3 || !/[A-Za-zÀ-ú]/.test(core)) return s;
    let out = I.strings.get(core);
    if (out === undefined) {
      const list = (I.byHead.get(core.slice(0, 2)) || []).concat(I.loose);
      for (const t of list) { const m = core.match(t.re); if (m) { out = fill(t, m, depth); break; } }
    }
    if (out === undefined) return s;
    return s.slice(0, s.indexOf(core)) + out + s.slice(s.indexOf(core) + core.length);
  }
  const blocked = (el) => !el || SKIP.has(el.nodeName) || (el.closest && el.closest('[translate="no"]'));
  function textNode(n) {
    if (blocked(n.parentElement)) return;
    const v = tr(n.nodeValue);
    if (v !== n.nodeValue) n.nodeValue = v;
  }
  function attrs(el) {
    if (el.closest && el.closest('[translate="no"]')) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v) { const t = tr(v); if (t !== v) el.setAttribute(a, t); }
    }
  }
  function apply(root) {
    if (I.lang === 'pt' || !root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      if (FIELDS.has(root.nodeName)) { attrs(root); return; }       // campo: só placeholder/título, nunca o valor
      if (blocked(root)) return;
      attrs(root);
    }
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        if (n.nodeType !== 1) return NodeFilter.FILTER_ACCEPT;
        if (n.getAttribute('translate') === 'no') return NodeFilter.FILTER_REJECT;
        if (FIELDS.has(n.nodeName)) { attrs(n); return NodeFilter.FILTER_REJECT; }
        return SKIP.has(n.nodeName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      },
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) { if (n.nodeType === 3) textNode(n); else attrs(n); }
  }
  function watch() {
    if (I.observer || I.lang === 'pt') return;
    I.observer = new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === 'childList') m.addedNodes.forEach(apply);
        else if (m.type === 'characterData') textNode(m.target);
        else if (m.type === 'attributes') attrs(m.target);
      }
    });
    I.observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    apply(document.body);
    document.title = tr(document.title);
    // janelas nativas do navegador também recebem o texto traduzido
    for (const fn of ['alert', 'confirm', 'prompt']) {
      const orig = window[fn].bind(window);
      window[fn] = (msg, ...rest) => orig(tr(msg), ...rest);
    }
  }
  // Idioma escolhido em Ajustes ('en' | 'pt'). Sem catálogo (ou com erro), a página fica em português.
  async function start(lang) {
    I.lang = lang === 'en' ? 'en' : 'pt';
    document.documentElement.lang = I.lang === 'pt' ? 'pt-BR' : I.lang;
    if (I.lang === 'pt') return;
    try {
      const r = await fetch(`/static/locales/${I.lang}.json`, { cache: 'no-cache' });
      if (!r.ok) throw new Error(String(r.status));
      load(await r.json());
    } catch (e) { console.error('[i18n] catálogo não carregou:', e); I.lang = 'pt'; return; }
    watch();
  }
  window.I18N = { start, tr, apply, get lang() { return I.lang; } };
}());
