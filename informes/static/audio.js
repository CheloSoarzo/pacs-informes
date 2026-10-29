/* Dictados de audio: reproductor para transcripción y grabador para el radiólogo.
   Atajos de transcripción: F4 reproducir/pausa · F3 retroceder 3 s · F6 adelantar 3 s. */
(function () {
  const fmt = s => (!isFinite(s) || s < 0) ? '0:00' : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  let activo = null;   // reproductor que reciben los atajos

  class Reproductor {
    constructor(el) {
      this.el = el;
      el.innerHTML = `
        <button type="button" class="btn ico" data-a="atras" title="Retroceder 3 s (F3)" aria-label="Retroceder 3 segundos">⏪</button>
        <button type="button" class="btn ico prim" data-a="play" title="Reproducir o pausar (F4)" aria-label="Reproducir">▶</button>
        <button type="button" class="btn ico" data-a="adelante" title="Adelantar 3 s (F6)" aria-label="Adelantar 3 segundos">⏩</button>
        <span class="tiempo-audio mono">0:00 / 0:00</span>
        <input type="range" min="0" max="1000" value="0" aria-label="Posición del audio">
        <select aria-label="Velocidad"><option value="0.75">0,75×</option><option value="1" selected>1×</option><option value="1.25">1,25×</option><option value="1.5">1,5×</option><option value="2">2×</option></select>
        ${el.dataset.auto ? '<button type="button" class="btn peq" data-a="auto">Usar texto automático</button>' : ''}`;
      this.a = new Audio();
      this.a.preload = 'metadata';
      this.a.src = el.dataset.src;
      this.dur = 0;
      this.$t = el.querySelector('.tiempo-audio');
      this.$r = el.querySelector('input[type=range]');
      this.$p = el.querySelector('[data-a=play]');
      // Los audios webm de los navegadores a veces no informan la duración: se fuerza a calcularla
      this.a.addEventListener('loadedmetadata', () => {
        if (!isFinite(this.a.duration)) { this.arreglando = true; this.a.currentTime = 1e7; }
        else this.dur = this.a.duration;
        this.pintar();
      });
      this.a.addEventListener('durationchange', () => {
        if (isFinite(this.a.duration)) { this.dur = this.a.duration; if (this.arreglando) { this.arreglando = false; this.a.currentTime = 0; } }
        this.pintar();
      });
      this.a.addEventListener('timeupdate', () => this.pintar());
      this.a.addEventListener('play', () => { this.$p.textContent = '❚❚'; this.$p.setAttribute('aria-label', 'Pausar'); });
      this.a.addEventListener('pause', () => { this.$p.textContent = '▶'; this.$p.setAttribute('aria-label', 'Reproducir'); });
      this.$r.addEventListener('input', () => { if (this.dur) this.a.currentTime = this.$r.value / 1000 * this.dur; });
      el.querySelector('select').addEventListener('change', e => { this.a.playbackRate = +e.target.value; });
      el.addEventListener('click', e => {
        activo = this;
        const b = e.target.closest('[data-a]'); if (!b) return;
        ({ atras: () => this.mover(-3), adelante: () => this.mover(3), play: () => this.alternar(), auto: () => this.auto() })[b.dataset.a]();
      });
      if (!activo || el.closest('.dictado-item.activo')) activo = activo && activo.el.closest('.dictado-item.activo') ? activo : this;
    }
    pintar() {
      if (this.arreglando) return;
      this.$t.textContent = `${fmt(this.a.currentTime)} / ${fmt(this.dur)}`;
      if (this.dur) this.$r.value = Math.round(this.a.currentTime / this.dur * 1000);
    }
    alternar() { this.a.paused ? this.a.play() : this.a.pause(); }
    mover(s) { this.a.currentTime = Math.max(0, Math.min((this.dur || 1e9), this.a.currentTime + s)); }
    async auto() {
      const r = await fetch(this.el.dataset.auto, { credentials: 'same-origin' });
      const j = await r.json();
      if (j.texto) document.dispatchEvent(new CustomEvent('insertar-texto', { detail: j.texto }));
    }
  }

  window.ReproductorAudio = Reproductor;   // lo usa el panel de la lista
  document.querySelectorAll('.reproductor').forEach(el => new Reproductor(el));
  document.addEventListener('keydown', e => {
    if (!activo) return;
    if (e.key === 'F4') { e.preventDefault(); activo.alternar(); }
    if (e.key === 'F3') { e.preventDefault(); activo.mover(-3); }
    if (e.key === 'F6') { e.preventDefault(); activo.mover(3); }
  });

  // ------------------------------------------------------------------ grabador
  const g = document.getElementById('grabador');
  if (!g) return;
  const cfg = () => window.EDITOR;
  let rec = null, trozos = [], stream = null, t0 = 0, acumulado = 0, reloj = null, blob = null, ctx = null, analizador = null;

  function vista(estado) {
    const t = acumulado + (rec && rec.state === 'recording' ? (Date.now() - t0) / 1000 : 0);
    if (estado === 'inicio') {
      g.innerHTML = `<button type="button" class="btn rec-btn" data-g="grabar">● Grabar dictado</button>
        <span class="peq suave">El audio queda en el estudio para que la transcripcionista lo escriba.</span>`;
    } else if (estado === 'grabando' || estado === 'pausa') {
      g.innerHTML = `<span class="punto-rec ${estado === 'grabando' ? 'vivo' : ''}"></span>
        <span class="mono reloj-rec">${fmt(t)}</span><span class="nivel"><span></span></span>
        <button type="button" class="btn" data-g="pausa">${estado === 'grabando' ? '❚❚ Pausa' : '● Continuar'}</button>
        <button type="button" class="btn peligro" data-g="detener">■ Terminar</button>`;
    } else if (estado === 'listo') {
      g.innerHTML = `<audio controls src="${URL.createObjectURL(blob)}"></audio>
        <input type="text" id="nota-dictado" maxlength="300" placeholder="Nota para la transcripcionista (opcional)" aria-label="Nota para la transcripcionista">
        <div class="fila"><button type="button" class="btn prim" data-g="enviar">Enviar a transcripción</button>
        <button type="button" class="btn" data-g="descartar">Descartar</button><span class="peq suave">${fmt(acumulado)}</span></div>`;
    } else if (estado === 'enviando') {
      g.innerHTML = '<span class="peq">Enviando dictado…</span>';
    }
  }

  function medir() {
    if (!analizador) return;
    const d = new Uint8Array(analizador.fftSize);
    analizador.getByteTimeDomainData(d);
    let max = 0; for (const v of d) max = Math.max(max, Math.abs(v - 128));
    const n = g.querySelector('.nivel span'); if (n) n.style.width = Math.min(100, max / 64 * 100) + '%';
    const r = g.querySelector('.reloj-rec'); if (r && rec) r.textContent = fmt(acumulado + (rec.state === 'recording' ? (Date.now() - t0) / 1000 : 0));
  }

  async function grabar() {
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch (e) { g.insertAdjacentHTML('beforeend', '<p class="peq error-txt">No se pudo usar el micrófono. Revisa el permiso del navegador.</p>'); return; }
    const tipo = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find(t => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || '';
    rec = new MediaRecorder(stream, tipo ? { mimeType: tipo, audioBitsPerSecond: 32000 } : undefined);
    trozos = []; acumulado = 0; t0 = Date.now();
    rec.ondataavailable = e => e.data.size && trozos.push(e.data);
    rec.onstop = () => {
      blob = new Blob(trozos, { type: rec.mimeType || 'audio/webm' });
      stream.getTracks().forEach(t => t.stop());
      clearInterval(reloj); if (ctx) ctx.close(); ctx = analizador = null;
      vista('listo');
    };
    rec.start(1000);
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    analizador = ctx.createAnalyser(); analizador.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analizador);
    reloj = setInterval(medir, 120);
    vista('grabando');
  }

  g.addEventListener('click', async e => {
    const b = e.target.closest('[data-g]'); if (!b) return;
    const a = b.dataset.g;
    if (a === 'grabar') grabar();
    if (a === 'pausa') {
      if (rec.state === 'recording') { rec.pause(); acumulado += (Date.now() - t0) / 1000; vista('pausa'); }
      else { rec.resume(); t0 = Date.now(); vista('grabando'); }
    }
    if (a === 'detener') { if (rec.state === 'recording') acumulado += (Date.now() - t0) / 1000; rec.stop(); }
    if (a === 'descartar') { blob = null; vista('inicio'); }
    if (a === 'enviar') {
      const nota = (document.getElementById('nota-dictado') || {}).value || '';
      vista('enviando');
      const fd = new FormData();
      const ext = (blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm');
      fd.append('audio', blob, 'dictado.' + ext);
      fd.append('duracion', acumulado.toFixed(1));
      fd.append('nota', nota);
      try {
        const r = await fetch(cfg().urls.subirDictado, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF': cfg().csrf } });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || 'Error ' + r.status);
        location.reload();
      } catch (err) {
        vista('listo');
        g.insertAdjacentHTML('beforeend', `<p class="peq error-txt">No se pudo enviar: ${err.message}. Intenta de nuevo.</p>`);
      }
    }
  });
  // F8: empezar a grabar / terminar la grabación (útil con la vista en las imágenes)
  document.addEventListener('keydown', e => {
    if (e.key !== 'F8') return;
    e.preventDefault();
    const b = g.querySelector('[data-g=grabar]') || g.querySelector('[data-g=detener]');
    if (b) b.click();
  });
  window.addEventListener('beforeunload', e => { if (rec && rec.state !== 'inactive' || (blob && g.querySelector('[data-g=enviar]'))) { e.preventDefault(); e.returnValue = ''; } });
  vista('inicio');
})();
