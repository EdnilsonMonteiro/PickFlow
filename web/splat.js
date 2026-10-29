/* PickFlow: visualizador de Gaussian Splat (.spz) no navegador, com three.js + Spark (web/vendor/).
   Carregado sob demanda por app.js (import dinâmico). O splat vem no referencial 3DGS (Y para baixo, Z para a frente);
   girado 180° em X ele fica no mesmo mundo do three.js e do CreateCameraInfo do ComfyUI (Y para cima). No TripoSplat a
   foto fica de frente para +X (no ComfyUI: yaw 90°, pitch 0); o splat ainda gira -90° em Y para a foto ficar de frente
   para +Z, onde a câmera começa, como na foto original.

   Navegação como no Blender (viewport 3D), em volta de um ponto de giro (pivô):
     botão do meio: girar · Shift+meio: arrastar a vista · Ctrl+meio: zoom · roda: zoom na direção do mouse
     "emular mouse de 3 botões": Alt+esquerdo = meio (com Shift/Ctrl igual). Sem nada para selecionar, o esquerdo sozinho
     também gira e o direito arrasta a vista (mais fácil no touchpad)
     duplo clique (ou Alt+clique do meio): o ponto clicado vira o centro do giro
     teclado numérico (ou a fileira 0-9): 1 frente, 3 direita, 7 cima (Ctrl = lado oposto), 2/4/6/8 giram 15°,
     Shift+4/6 inclinam, 9 lado oposto, 0 volta à câmera da foto, + e - zoom, "." centraliza o giro no meio da cena
   Modo "Andar" (o Walk do Blender, Shift+`): W A S D andam (segurando), Q/E descem e sobem, Shift corre, Alt anda
   devagar, arrastar o mouse olha em volta, a roda muda a velocidade, Enter (ou Shift+` de novo) sai. */
import * as THREE from 'three';
import { SparkRenderer, SplatMesh } from 'spark';

export const BACKGROUND = '#808080';      // o mesmo cinza que a LoRA de Outpaint completa
const UP = new THREE.Vector3(0, 1, 0);
const D2R = THREE.MathUtils.degToRad;

// opts: container, url, aspect (largura/altura da foto), background, onReady(), onError(msg), onChange()
export function createViewer(opts) {
  const aspect = opts.aspect > 0 ? opts.aspect : 1;
  const bg = new THREE.Color(opts.background || BACKGROUND);
  const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true, alpha: false });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setClearColor(bg, 1);
  const canvas = renderer.domElement;
  canvas.className = 'splat-canvas';
  canvas.tabIndex = 0;
  opts.container.append(canvas);

  const scene = new THREE.Scene();
  scene.background = bg;
  const spark = new SparkRenderer({ renderer });
  scene.add(spark);
  const camera = new THREE.PerspectiveCamera(35, aspect, 0.01, 100);
  const pivot = new THREE.Vector3();
  const changed = () => opts.onChange && opts.onChange();
  let mode = 'nav';                          // 'nav' (viewport do Blender) ou 'walk' (andar)
  let walkSpeed = 0.6;                       // unidades da cena por segundo (a foto mede 1)
  let anim = null;                           // transição suave entre vistas (como o "smooth view" do Blender)

  const forward = () => new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
  const right = () => new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  const camUp = () => new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
  const dist = () => camera.position.distanceTo(pivot);
  const lookQuat = (eye, at, up = UP) => new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(eye, at, up));

  // Câmera da foto: a foto ocupa o cubo [-0.5, 0.5] (o lado maior = 1) e enche a tela, como no original.
  function photoPose() {
    const fov = 35;
    const halfH = aspect >= 1 ? 0.5 / aspect : 0.5;
    const d = (halfH / Math.tan(D2R(fov / 2))) * 1.08 + 0.1;
    return { pos: new THREE.Vector3(0, 0, d), quat: new THREE.Quaternion(), pivot: new THREE.Vector3(), fov };
  }
  function go(pose, smooth = true) {
    if (pose.fov) { camera.fov = pose.fov; camera.updateProjectionMatrix(); }
    if (!smooth) {
      camera.position.copy(pose.pos);
      camera.quaternion.copy(pose.quat);
      pivot.copy(pose.pivot);
      anim = null;
      changed();
      return;
    }
    anim = { t0: performance.now(), dur: 260, from: { pos: camera.position.clone(), quat: camera.quaternion.clone(), pivot: pivot.clone() }, to: pose };
  }
  const home = () => { setMode('nav'); go(photoPose()); };
  go(photoPose(), false);

  let disposed = false;
  const mesh = new SplatMesh({ url: opts.url, raycastable: true, minRaycastOpacity: 0.3 });
  const flip = new THREE.Quaternion(1, 0, 0, 0);                                        // 3DGS (Y para baixo) -> three.js
  const face = new THREE.Quaternion().setFromAxisAngle(UP, -Math.PI / 2);                // foto de frente para +Z
  mesh.quaternion.copy(face.multiply(flip));
  scene.add(mesh);
  mesh.initialized.then(() => { if (!disposed && opts.onReady) opts.onReady(); })
    .catch((e) => { if (!disposed && opts.onError) opts.onError(String((e && e.message) || e)); });

  const fit = () => {
    const r = opts.container.getBoundingClientRect();
    let w = r.width, hgt = w / aspect;
    if (r.height > 0 && hgt > r.height) { hgt = r.height; w = hgt * aspect; }
    renderer.setSize(Math.max(1, Math.floor(w)), Math.max(1, Math.floor(hgt)));
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(fit);
  ro.observe(opts.container);
  fit();

  // ------------------------------------------------------------------- viewport
  // graus; giro "turntable" em volta do pivô: dYaw + = a câmera vai para a direita, dPitch + = a câmera sobe
  function orbit(dYaw, dPitch) {
    anim = null;
    const qYaw = new THREE.Quaternion().setFromAxisAngle(UP, D2R(dYaw));
    const qPitch = new THREE.Quaternion().setFromAxisAngle(right(), D2R(-dPitch));
    const q = qYaw.multiply(qPitch);
    const off = camera.position.clone().sub(pivot).applyQuaternion(q);
    camera.position.copy(pivot).add(off);
    camera.quaternion.premultiply(q);
    changed();
  }
  function roll(deg) {                       // inclina a câmera em volta da direção em que ela olha
    anim = null;
    camera.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(forward(), D2R(-deg)));
    changed();
  }
  function pan(dxPx, dyPx) {                 // arrasta a vista: câmera e pivô andam juntos (a cena acompanha o mouse)
    anim = null;
    const u = (2 * Math.tan(D2R(camera.fov / 2)) * Math.max(0.05, dist())) / Math.max(1, canvas.clientHeight);
    const d = right().multiplyScalar(-dxPx * u).add(camUp().multiplyScalar(dyPx * u));
    camera.position.add(d);
    pivot.add(d);
    changed();
  }
  // zoom: aproxima do pivô; com o ponto da tela (ndc), aproxima daquele ponto (o "Zoom to Mouse Position" do Blender)
  function zoom(factor, ndc = null) {
    anim = null;
    factor = THREE.MathUtils.clamp(factor, 0.2, 5);
    if (dist() * factor < 0.02) return;
    let at = pivot.clone();
    if (ndc) {
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, camera);
      const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(forward(), pivot);
      const hit = ray.ray.intersectPlane(plane, new THREE.Vector3());
      if (hit) at = hit;
    }
    camera.position.sub(at).multiplyScalar(factor).add(at);
    pivot.sub(at).multiplyScalar(factor).add(at);
    changed();
  }
  // vistas do teclado numérico, em volta do pivô e na distância atual
  function view(name) {
    if (name === 'photo') { home(); return; }
    setMode('nav');
    const d = dist();
    const dirs = { front: [0, 0, 1], back: [0, 0, -1], right: [1, 0, 0], left: [-1, 0, 0], top: [0, 1, 0], bottom: [0, -1, 0] };
    const v = new THREE.Vector3(...dirs[name]);
    const eye = pivot.clone().add(v.multiplyScalar(d));
    const up = name === 'top' ? new THREE.Vector3(0, 0, -1) : name === 'bottom' ? new THREE.Vector3(0, 0, 1) : UP;
    go({ pos: eye, quat: lookQuat(eye, pivot, up), pivot: pivot.clone() });
  }
  function opposite() {                      // numpad 9: o mesmo ângulo, do outro lado
    const eye = pivot.clone().sub(camera.position.clone().sub(pivot));
    go({ pos: eye, quat: lookQuat(eye, pivot, camUp()), pivot: pivot.clone() });
  }
  function centerPivot(point) {              // o ponto vira o centro do giro; a câmera não sai do lugar, só mira nele
    go({ pos: camera.position.clone(), quat: lookQuat(camera.position, point, camUp()), pivot: point.clone() });
  }
  function pick(clientX, clientY) {          // ponto do 3D sob o mouse (ou o do plano do pivô)
    const r = canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, camera);
    try {
      const hits = ray.intersectObject(mesh, false);
      if (hits.length) return hits[0].point;
    } catch (_) { /* sem raycast: usa o plano */ }
    return ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(forward(), pivot), new THREE.Vector3());
  }
  const ndcOf = (e) => {
    const r = canvas.getBoundingClientRect();
    return new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  };

  // ------------------------------------------------------------------- andar (walk)
  const held = new Set();
  function look(dYaw, dPitch) {              // graus: olhar em volta sem sair do lugar (como o walk do Blender)
    anim = null;
    const qYaw = new THREE.Quaternion().setFromAxisAngle(UP, D2R(-dYaw));
    const qPitch = new THREE.Quaternion().setFromAxisAngle(right(), D2R(dPitch));
    camera.quaternion.premultiply(qYaw.multiply(qPitch));
    changed();
  }
  function step(dt) {                        // movimento contínuo enquanto as teclas estão apertadas
    if (mode !== 'walk' || !held.size) return;
    const f = forward(); f.y = 0;
    if (f.lengthSq() < 1e-6) f.copy(forward());
    f.normalize();
    const r = right(); r.y = 0; r.normalize();
    const v = new THREE.Vector3();
    if (held.has('w')) v.add(f);
    if (held.has('s')) v.sub(f);
    if (held.has('d')) v.add(r);
    if (held.has('a')) v.sub(r);
    if (held.has('e')) v.add(UP);
    if (held.has('q')) v.sub(UP);
    if (!v.lengthSq()) return;
    const k = walkSpeed * (held.has('shift') ? 3 : held.has('alt') ? 0.3 : 1) * dt;
    v.normalize().multiplyScalar(k);
    camera.position.add(v);
    pivot.add(v);
    changed();
  }
  function setMode(m) {
    if (m === mode || (m !== 'nav' && m !== 'walk')) return;
    if (m === 'nav') {                       // o pivô volta a ficar à frente da câmera, na distância de antes
      const d = THREE.MathUtils.clamp(dist(), 0.3, 4);
      pivot.copy(camera.position).add(forward().multiplyScalar(d));
    }
    held.clear();
    mode = m;
    changed();
  }

  // ------------------------------------------------------------------- mouse
  let drag = null;
  const onDown = (e) => {
    canvas.focus();
    if (e.button === 1 && e.altKey && mode === 'nav') { e.preventDefault(); const p = pick(e.clientX, e.clientY); if (p) centerPivot(p); return; }
    let act;
    if (mode === 'walk') act = e.button === 0 || e.button === 1 ? 'look' : 'pan';
    else if (e.button === 2) act = 'pan';
    else if (e.button === 1 || e.button === 0) act = e.shiftKey ? 'pan' : e.ctrlKey || e.metaKey ? 'zoom' : 'orbit';
    if (!act) return;
    drag = { act, x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY };
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add('dragging');
    e.preventDefault();
  };
  const onMove = (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX;
    drag.y = e.clientY;
    const hpx = Math.max(1, canvas.clientHeight);
    if (drag.act === 'orbit') orbit(-dx * (240 / hpx), dy * (180 / hpx));      // como no Blender: arrastar "pega" a cena
    else if (drag.act === 'pan') pan(dx, dy);
    else if (drag.act === 'zoom') zoom(Math.pow(1.005, dy));
    else if (drag.act === 'look') look(dx * (2 * camera.fov) / hpx, -dy * (2 * camera.fov) / hpx);
  };
  const onUp = (e) => {
    if (!drag) return;
    drag = null;
    canvas.classList.remove('dragging');
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) { /* já solto */ }
  };
  const onDbl = (e) => { if (mode === 'nav') { const p = pick(e.clientX, e.clientY); if (p) centerPivot(p); } };
  const onWheel = (e) => {
    e.preventDefault();
    if (mode === 'walk') { walkSpeed = THREE.MathUtils.clamp(walkSpeed * (e.deltaY < 0 ? 1.2 : 1 / 1.2), 0.05, 5); changed(); return; }
    if (e.ctrlKey) { orbit(0, Math.sign(e.deltaY) * 5); return; }                 // pinça do touchpad: não vira zoom do navegador
    zoom(e.deltaY < 0 ? 0.88 : 1 / 0.88, ndcOf(e));
  };
  const onMenu = (e) => e.preventDefault();
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  canvas.addEventListener('dblclick', onDbl);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('contextmenu', onMenu);

  // ------------------------------------------------------------------- teclado
  const NUM = (code) => (/^(Numpad|Digit)(\d)$/.exec(code) || [])[2];
  const onKey = (e) => {
    const k = String(e.key).toLowerCase();
    if (e.code === 'Backquote' && e.shiftKey) { e.preventDefault(); setMode(mode === 'walk' ? 'nav' : 'walk'); return; }
    if (mode === 'walk') {
      if (k === 'enter') { e.preventDefault(); setMode('nav'); return; }
      if ('wasdqe'.includes(k) && k.length === 1) { e.preventDefault(); held.add(k); }
      if (e.shiftKey) held.add('shift');
      if (e.altKey) { held.add('alt'); e.preventDefault(); }
      return;
    }
    const n = NUM(e.code);
    const done = () => { e.preventDefault(); changed(); };
    if (n !== undefined) {
      if (n === '1') view(e.ctrlKey ? 'back' : 'front');
      else if (n === '3') view(e.ctrlKey ? 'left' : 'right');
      else if (n === '7') view(e.ctrlKey ? 'bottom' : 'top');
      else if (n === '4') (e.shiftKey ? roll(-15) : orbit(-15, 0));
      else if (n === '6') (e.shiftKey ? roll(15) : orbit(15, 0));
      else if (n === '8') orbit(0, 15);
      else if (n === '2') orbit(0, -15);
      else if (n === '9') opposite();
      else if (n === '0') home();
      else return;
      done();
      return;
    }
    if (e.code === 'NumpadAdd' || k === '+' || k === '=') { zoom(0.88); done(); }
    else if (e.code === 'NumpadSubtract' || k === '-') { zoom(1 / 0.88); done(); }
    else if (e.code === 'NumpadDecimal' || k === '.') { centerPivot(new THREE.Vector3()); done(); }
    else if (k === 'home') { home(); done(); }
    else if (k === 'arrowleft') { orbit(-15, 0); done(); }
    else if (k === 'arrowright') { orbit(15, 0); done(); }
    else if (k === 'arrowup') { orbit(0, 15); done(); }
    else if (k === 'arrowdown') { orbit(0, -15); done(); }
  };
  const onKeyUp = (e) => {
    const k = String(e.key).toLowerCase();
    held.delete(k);
    if (!e.shiftKey) held.delete('shift');
    if (!e.altKey) held.delete('alt');
  };
  const onBlur = () => held.clear();
  canvas.addEventListener('keydown', onKey);
  canvas.addEventListener('keyup', onKeyUp);
  canvas.addEventListener('blur', onBlur);

  // ------------------------------------------------------------------- laço
  let last = performance.now();
  renderer.setAnimationLoop((now) => {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (anim) {
      const t = Math.min(1, (now - anim.t0) / anim.dur);
      const s = t * t * (3 - 2 * t);
      camera.position.lerpVectors(anim.from.pos, anim.to.pos, s);
      camera.quaternion.slerpQuaternions(anim.from.quat, anim.to.quat, s);
      pivot.lerpVectors(anim.from.pivot, anim.to.pivot, s);
      if (t >= 1) anim = null;
      changed();
    }
    step(dt);
    renderer.render(scene, camera);
  });

  // Botões da interface: os mesmos atalhos do teclado.
  function nudge(what) {
    const acts = {
      left: () => orbit(-15, 0), right: () => orbit(15, 0), up: () => orbit(0, 15), down: () => orbit(0, -15),
      in: () => zoom(0.88), out: () => zoom(1 / 0.88), rollLeft: () => roll(-15), rollRight: () => roll(15),
      opposite, center: () => centerPivot(new THREE.Vector3()),
      lookLeft: () => look(-10, 0), lookRight: () => look(10, 0), lookUp: () => look(0, 8), lookDown: () => look(0, -8),
      fwd: () => { const v = forward().multiplyScalar(0.08); camera.position.add(v); pivot.add(v); changed(); },
      back: () => { const v = forward().multiplyScalar(-0.08); camera.position.add(v); pivot.add(v); changed(); },
      stepLeft: () => { const v = right().multiplyScalar(-0.06); camera.position.add(v); pivot.add(v); changed(); },
      stepRight: () => { const v = right().multiplyScalar(0.06); camera.position.add(v); pivot.add(v); changed(); },
      rise: () => { const v = UP.clone().multiplyScalar(0.06); camera.position.add(v); pivot.add(v); changed(); },
      sink: () => { const v = UP.clone().multiplyScalar(-0.06); camera.position.add(v); pivot.add(v); changed(); },
    };
    if (acts[what]) acts[what]();
    else if (['front', 'back', 'left', 'right', 'top', 'bottom', 'photo'].includes(what.replace('view:', ''))) view(what.replace('view:', ''));
  }
  function setFov(f) {                       // lente: mais aberta ou mais fechada, sem mover a câmera
    camera.fov = THREE.MathUtils.clamp(Number(f) || 35, 15, 100);
    camera.updateProjectionMatrix();
    changed();
  }

  // Onde a câmera está e para onde aponta. `yaw`/`pitch`: posição em volta do pivô (+ = foi para a direita / subiu);
  // `look`: direção (+ = olhando para a direita / para cima); `roll`: inclinação; `fov`: lente.
  function state() {
    const off = camera.position.clone().sub(pivot);
    const s = new THREE.Spherical().setFromVector3(off);
    const f = forward();
    const e = new THREE.Euler().setFromQuaternion(camera.quaternion, 'YXZ');
    const deg = (r) => Math.round(THREE.MathUtils.radToDeg(r));
    const xyz = (v) => ({ x: +v.x.toFixed(4), y: +v.y.toFixed(4), z: +v.z.toFixed(4) });
    return { mode, yaw: deg(s.theta), pitch: Math.round(90 - THREE.MathUtils.radToDeg(s.phi)), distance: +s.radius.toFixed(3),
      look: { yaw: deg(Math.atan2(f.x, -f.z)), pitch: deg(Math.asin(THREE.MathUtils.clamp(f.y, -1, 1))) }, roll: deg(e.z),
      fov: Math.round(camera.fov), speed: +walkSpeed.toFixed(2), position: xyz(camera.position), target: xyz(pivot) };
  }

  // PNG do ângulo atual no tamanho pedido (mesma proporção da foto). Renderiza fora da tela e volta ao tamanho de antes.
  function snapshot(width, height) {
    const prev = new THREE.Vector2();
    renderer.getSize(prev);
    const pr = renderer.getPixelRatio();
    renderer.setPixelRatio(1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
    const url = canvas.toDataURL('image/png');
    renderer.setPixelRatio(pr);
    renderer.setSize(prev.x, prev.y, false);
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
    fit();
    return url;
  }

  function dispose() {
    disposed = true;
    ro.disconnect();
    renderer.setAnimationLoop(null);
    for (const [ev, fn] of [['keydown', onKey], ['keyup', onKeyUp], ['blur', onBlur], ['pointerdown', onDown], ['pointermove', onMove],
      ['pointerup', onUp], ['pointercancel', onUp], ['dblclick', onDbl], ['wheel', onWheel], ['contextmenu', onMenu]]) canvas.removeEventListener(ev, fn);
    try { mesh.dispose(); } catch (_) { /* já liberado */ }
    renderer.dispose();
    canvas.remove();
  }
  return { canvas, orbit, roll, pan, zoom, look, nudge, setMode, setFov, home, view: state, snapshot, dispose, mode: () => mode };
}
