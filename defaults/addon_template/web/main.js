/* Interface do addon __NAME__. Carregada sozinha pelo PickFlow; usa window.PickFlow (veja addons/README.md). */
(function () {
  const CF = window.PickFlow, h = CF.h;

  // Botão ao lado de "Anexar imagem": escreve um pedido pronto.
  CF.addButton({ label: 'Girar 90°', title: 'Escreve o pedido para girar a imagem', onClick: () => CF.setText('gire a imagem 90 graus') });

  // Botão em cada imagem de resultado.
  CF.addResultAction({ label: 'Girar de novo', onClick: () => CF.setText('gire a imagem 90 graus') });

  // Tela nova no menu da esquerda.
  CF.addView({
    id: '__ID__', title: 'Addon __NAME__',
    mount(box) {
      const out = h('pre', { class: 'muted sm' }, 'Chame a rota do addon…');
      box.append(h('div', { class: 'card stack' }, h('h2', null, 'Olá do addon __NAME__'),
        h('button', { class: 'btn secondary', onclick: async () => { out.textContent = JSON.stringify(await CF.addonApi('__NAME__', '/ola'), null, 1); } }, 'Chamar /ola'), out));
    },
  });

  // Bloco em Ajustes > Addons.
  CF.addSettings({ id: '__ID__', title: 'Addon __NAME__', mount(box) {
    const cfg = CF.config('__NAME__');
    const g = h('input', { type: 'number', value: cfg.graus_padrao ?? 90, min: 1, max: 359 });
    box.append(h('label', { class: 'f' }, 'Graus padrão'), g,
      h('button', { class: 'btn secondary', onclick: async () => { await CF.saveConfig('__NAME__', { ...cfg, graus_padrao: Number(g.value) }); CF.toast('Salvo'); } }, 'Salvar'));
  } });

  // Eventos da execução ('ready', 'view', 'run:event', 'run:end').
  CF.on('run:end', ({ images }) => { /* ex.: avisar, salvar histórico… */ });

  // Tela própria pedida pelo Python:  valor = await call.ask("__ID___escolha", title="...", opcoes=[...])
  CF.registerInput('__ID___escolha', (ev) => CF.modal({ title: ev.title, body: h('p', null, 'Escolha:'),
    buttons: (ev.opcoes || []).map((o) => ({ label: String(o), value: o, primary: true })) }));
})();
