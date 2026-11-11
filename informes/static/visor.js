/* Visor simple de imágenes (vista previa JPEG de Orthanc).
   Uso: new VisorSimple(elemento, urlSeries, prefijoImg)
   - urlSeries devuelve {series:[{id, modalidad, descripcion, instancias:[ids]}]}
   - prefijoImg + idInstancia = URL de la imagen
   Rueda del mouse, flechas o arrastrar arriba/abajo para recorrer cortes.
   «Brillo y contraste»: arrastrar a los lados cambia el contraste y arriba/abajo el brillo; el PACS vuelve a
   dibujar la imagen desde los datos originales con la nueva ventana (?wc=&ww=). En el TC hay ventanas listas.
   borrar (opcional): {url, csrf, validado} para borrar la imagen o la serie que se está viendo. */
class VisorSimple {
  constructor(el, urlSeries, prefijoImg, borrar) {
    this.el = el; this.prefijo = prefijoImg; this.cache = new Map(); this.borrar = borrar || null;
    el.innerHTML = '<div class="vacio">Cargando imágenes…</div>';
    fetch(urlSeries, { credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(d => this.iniciar(d.series || []))
      .catch(() => { el.innerHTML = '<div class="vacio">No se pudieron cargar las imágenes.</div>'; });
  }

  iniciar(series) {
    if (!series.length) { this.el.innerHTML = '<div class="vacio">El estudio no tiene imágenes.</div>'; return; }
    this.series = series;
    this.el.innerHTML = `
      <div class="series"></div>
      <div class="ajustes" role="toolbar" aria-label="Ajustes de imagen">
        <button type="button" class="aj-modo" aria-pressed="false" title="Arrastra sobre la imagen: a los lados cambia el contraste, arriba y abajo el brillo">☀ Brillo y contraste</button>
        <select class="aj-preset" aria-label="Ventana" hidden></select>
        <button type="button" class="aj-original" hidden>Original</button>
        <span class="aj-valor" aria-live="polite"></span>
      </div>
      <div class="lienzo" tabindex="0"><img alt="Imagen del estudio"><div class="info"></div></div>
      <svg width="0" height="0" style="position:absolute" aria-hidden="true"><filter id="vs-ventana-${VisorSimple.n = (VisorSimple.n || 0) + 1}" color-interpolation-filters="sRGB">
        <feComponentTransfer><feFuncR type="linear"/><feFuncG type="linear"/><feFuncB type="linear"/></feComponentTransfer></filter></svg>
      <div class="controles"><span class="pos"></span><input type="range" min="0" value="0">
        ${this.borrar ? '<button type="button" class="btn-borrar" data-b="imagen">Borrar imagen</button><button type="button" class="btn-borrar" data-b="serie">Borrar serie</button>' : ''}</div>
      ${this.borrar ? `<form class="borrar-img" hidden>
        <p class="que"></p>
        <label>Motivo <input name="motivo" required maxlength="300" placeholder="Ej. imagen repetida, de otro paciente…" autocomplete="off"></label>
        ${this.borrar.validado ? '<label class="chk"><input type="checkbox" name="validado_ok"> El informe ya está validado: igual quiero borrar</label>' : ''}
        <div class="acc"><button type="submit" class="btn peligro">Borrar</button><button type="button" class="btn" data-b="cancelar">Cancelar</button>
        <span class="msg" role="status"></span></div></form>` : ''}`;
    this.$series = this.el.querySelector('.series');
    if (this.borrar) this.prepararBorrar();
    this.$img = this.el.querySelector('.lienzo img');
    this.$info = this.el.querySelector('.info');
    this.$pos = this.el.querySelector('.pos');
    this.$rango = this.el.querySelector('input[type=range]');
    const lienzo = this.el.querySelector('.lienzo');

    this.pintarSeries();

    this.$rango.oninput = () => this.ir(+this.$rango.value);
    lienzo.addEventListener('wheel', e => { e.preventDefault(); this.ir(this.i + (e.deltaY > 0 ? 1 : -1)); }, { passive: false });
    lienzo.addEventListener('keydown', e => {
      if (['ArrowDown', 'ArrowRight'].includes(e.key)) { e.preventDefault(); this.ir(this.i + 1); }
      if (['ArrowUp', 'ArrowLeft'].includes(e.key)) { e.preventDefault(); this.ir(this.i - 1); }
    });
    let y0 = null;
    lienzo.addEventListener('pointerdown', e => {
      lienzo.setPointerCapture(e.pointerId);
      if (this.ajustando) { this.arrastre = { x: e.clientX, y: e.clientY, ...this.ventanaActual() }; return; }
      y0 = e.clientY;
    });
    lienzo.addEventListener('pointermove', e => {
      if (this.ajustando && this.arrastre) { this.arrastrar(e); return; }
      if (y0 === null) return;
      const paso = Math.trunc((e.clientY - y0) / 8);
      if (paso) { this.ir(this.i + paso); y0 = e.clientY; }
    });
    const soltar = () => { y0 = null; if (this.arrastre) { this.arrastre = null; if (this.nueva) this.aplicar(this.nueva); } };
    lienzo.addEventListener('pointerup', soltar);
    lienzo.addEventListener('pointercancel', soltar);
    lienzo.addEventListener('dblclick', () => { if (this.ajustando) this.aplicar(null); });
    this.prepararAjustes(lienzo);
    this.serie(0);
  }

  pintarSeries() {
    this.$series.innerHTML = '';
    this.series.forEach((s, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      const medio = s.instancias[Math.floor(s.instancias.length / 2)];
      b.innerHTML = `<img loading="lazy" src="${this.url(medio, null)}" alt="">${s.modalidad} · ${s.instancias.length}<br>${(s.descripcion || '').slice(0, 22)}`;
      b.onclick = () => this.serie(i);
      this.$series.appendChild(b);
    });
  }

  // Borrar la imagen o la serie que se está viendo (con motivo; queda en la auditoría)
  prepararBorrar() {
    const f = this.el.querySelector('.borrar-img'), msg = f.querySelector('.msg');
    this.el.querySelectorAll('.controles [data-b]').forEach(b => b.addEventListener('click', () => {
      this.tipoBorrar = b.dataset.b;
      const s = this.series[this.s];
      f.querySelector('.que').textContent = this.tipoBorrar === 'imagen'
        ? `Borrar la imagen ${this.i + 1} de ${s.instancias.length} de la serie «${s.modalidad} ${s.descripcion || ''}». No se puede deshacer.`
        : `Borrar la serie completa «${s.modalidad} ${s.descripcion || ''}» (${s.instancias.length} imágenes). No se puede deshacer.`;
      msg.textContent = ''; f.hidden = false; f.motivo.focus();
    }));
    f.querySelector('[data-b=cancelar]').addEventListener('click', () => { f.hidden = true; });
    f.addEventListener('submit', async e => {
      e.preventDefault();
      const s = this.series[this.s], tipo = this.tipoBorrar;
      const id = tipo === 'imagen' ? s.instancias[this.i] : s.id;
      const btn = f.querySelector('button[type=submit]'); btn.disabled = true; msg.textContent = 'Borrando…';
      try {
        const r = await fetch(this.borrar.url, { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF': this.borrar.csrf },
          body: JSON.stringify({ tipo, id, motivo: f.motivo.value, validado_ok: !!(f.validado_ok && f.validado_ok.checked) }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { msg.textContent = d.error || 'No se pudo borrar.'; return; }
        if (tipo === 'imagen') s.instancias.splice(this.i, 1);
        if (tipo === 'serie' || !s.instancias.length) this.series.splice(this.s, 1);
        f.hidden = true; f.motivo.value = '';
        this.pintarSeries();
        const n = Math.min(this.s, this.series.length - 1);
        if (tipo === 'imagen' && this.series[n] === s) { this.serie(n); this.ir(Math.min(this.i, s.instancias.length - 1)); }
        else this.serie(n);
        this.$pos.textContent += ` · ${tipo === 'imagen' ? 'imagen borrada' : 'serie borrada'}`;
      } catch (err) { msg.textContent = 'Sin conexión: no se borró.'; }
      finally { btn.disabled = false; }
    });
  }

  // ---------- brillo y contraste (ventana) ----------
  url(id, v) {
    v = v === undefined ? this.ventanas && this.ventanas[this.s] : v;
    return this.prefijo + id + (v ? `?wc=${Math.round(v.centro * 10) / 10}&ww=${Math.round(v.ancho * 10) / 10}` : '');
  }

  ventanaActual() {
    const s = this.series[this.s];
    return (this.ventanas[this.s]) || (s.ventana ? { centro: s.ventana.centro, ancho: s.ventana.ancho } : null);
  }

  prepararAjustes(lienzo) {
    this.ventanas = {};
    this.$ajModo = this.el.querySelector('.aj-modo');
    this.$preset = this.el.querySelector('.aj-preset');
    this.$original = this.el.querySelector('.aj-original');
    this.$valor = this.el.querySelector('.aj-valor');
    this.$filtro = this.el.querySelector('filter');
    this.$img.style.filter = '';
    this.$ajModo.addEventListener('click', () => {
      this.ajustando = !this.ajustando;
      this.$ajModo.setAttribute('aria-pressed', String(this.ajustando));
      lienzo.classList.toggle('ajustando', this.ajustando);
      this.$valor.textContent = this.ajustando ? 'Arrastra sobre la imagen · doble clic: original' : this.textoVentana();
    });
    this.$original.addEventListener('click', () => this.aplicar(null));
    this.$preset.addEventListener('change', () => {
      const v = this.$preset.value;
      if (!v) { this.aplicar(null); return; }
      const [c, a] = v.split(',').map(Number);
      this.aplicar({ centro: c, ancho: a });
    });
  }

  // Ventanas habituales del TC (en unidades Hounsfield)
  static PRESETS_TC = [['Partes blandas', 40, 400], ['Pulmón', -600, 1500], ['Hueso', 400, 1800], ['Cerebro', 40, 80], ['Hígado', 60, 160]];

  pintarAjustes() {
    const s = this.series[this.s], v = this.ventanas[this.s];
    const se_puede = !!s.ventana;
    this.$ajModo.hidden = !se_puede;
    if (!se_puede && this.ajustando) this.$ajModo.click();
    const tc = se_puede && s.modalidad === 'CT';
    this.$preset.hidden = !tc;
    if (tc) {
      this.$preset.innerHTML = '<option value="">Ventana original</option>' + VisorSimple.PRESETS_TC.map(([n, c, a]) =>
        `<option value="${c},${a}"${v && v.centro === c && v.ancho === a ? ' selected' : ''}>${n}</option>`).join('');
    }
    this.$original.hidden = !v;
    if (!this.ajustando) this.$valor.textContent = this.textoVentana();
  }

  textoVentana() {
    const v = this.ventanas && this.ventanas[this.s];
    return v ? `Ventana ${Math.round(v.centro)} / ${Math.round(v.ancho)}` : '';
  }

  arrastrar(e) {
    const a0 = this.arrastre, s = this.series[this.s];
    if (!a0 || !a0.ancho) return;
    const dx = e.clientX - a0.x, dy = e.clientY - a0.y;
    const ancho = Math.max(1, a0.ancho * Math.exp(dx / 220));
    const centro = a0.centro + (s.ventana.invertida ? 1 : -1) * dy * a0.ancho / 300;   // arriba = más claro
    this.nueva = { centro, ancho };
    // Vista previa al instante: transforma los grises de la imagen que se ve hasta que llega la nueva
    const vis = this.mostrada || this.ventanaActual();
    const k = vis.ancho / ancho, b = ((vis.centro - vis.ancho / 2) - (centro - ancho / 2)) / ancho;
    const [pend, inter] = s.ventana.invertida ? [k, 1 - k - b] : [k, b];
    this.$filtro.querySelectorAll('feFuncR,feFuncG,feFuncB').forEach(f => { f.setAttribute('slope', pend); f.setAttribute('intercept', inter); });
    this.$img.style.filter = `url(#${this.$filtro.id})`;
    this.$valor.textContent = `Ventana ${Math.round(centro)} / ${Math.round(ancho)}`;
  }

  aplicar(v) {
    this.nueva = null;
    if (v) this.ventanas[this.s] = v; else delete this.ventanas[this.s];
    for (const k of [...this.cache.keys()]) if (k.startsWith(this.s + ':')) this.cache.delete(k);
    this.pintarAjustes();
    this.ir(this.i);
  }

  serie(n) {
    this.s = n;
    [...this.$series.children].forEach((b, i) => b.classList.toggle('activo', i === n));
    const total = this.series[n].instancias.length;
    this.$rango.max = total - 1;
    if (this.$ajModo) this.pintarAjustes();
    this.ir(Math.floor(total / 2));
  }

  ir(i) {
    const inst = this.series[this.s].instancias;
    this.i = Math.max(0, Math.min(inst.length - 1, i));
    const url = this.url(inst[this.i]), v = this.ventanaActual();
    if (this.$img.style.filter) {          // había vista previa: se cambia apenas llega la imagen nueva
      const im = new Image();
      im.onload = () => { if (this.url(inst[this.i]) === url) { this.$img.src = url; this.$img.style.filter = ''; this.mostrada = v; } };
      im.src = url;
    } else { this.$img.src = url; this.mostrada = v; }
    this.$rango.value = this.i;
    const s = this.series[this.s];
    this.$pos.textContent = `${this.i + 1} / ${inst.length}`;
    this.$info.textContent = `${s.modalidad} ${s.descripcion || ''}`;
    // Precarga de vecinos para recorrer fluido
    for (const d of [1, 2, 3, -1, -2]) {
      const id = inst[this.i + d], clave = this.s + ':' + id + ':' + this.textoVentana();
      if (id && !this.cache.has(clave)) { const im = new Image(); im.src = this.url(id); this.cache.set(clave, im); }
    }
  }
}

/* Pestañas del visor en la pantalla del informe: visor diagnóstico (OHIF), Stone o vista rápida.
   Los visores completos se cargan solo al elegirlos y la elección se recuerda en este navegador. */
function iniciarVisores(urlSeries, borrar) {
  const caja = document.getElementById('visor-caja');
  if (!caja) return;
  const estudio = caja.closest('.estudio');
  const marco = document.getElementById('visor-marco');
  const iframe = marco && marco.querySelector('iframe');
  const rapida = document.getElementById('visor');
  const tabs = [...caja.querySelectorAll('[role=tab]')];
  let simple = null;
  const leer = (k, d) => { try { return localStorage.getItem(k) || d; } catch (e) { return d; } };
  const guardar = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

  function mostrar(modo, recordar = true) {
    if (!tabs.some(t => t.dataset.modo === modo)) modo = 'rapida';
    tabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.modo === modo)));
    caja.querySelectorAll('.visor-ayuda').forEach(p => { p.hidden = p.dataset.modo !== modo; });
    const completo = modo !== 'rapida';
    if (marco) marco.hidden = !completo;
    rapida.hidden = completo;
    estudio && estudio.classList.toggle('con-visor', completo);
    if (completo) {
      const src = iframe.dataset[modo];
      if (iframe.dataset.actual !== modo) { iframe.src = src; iframe.dataset.actual = modo; }
    } else if (!simple) {
      simple = new VisorSimple(rapida, urlSeries, '/img/', borrar);
    }
    if (recordar) guardar('visor_modo', modo);
  }

  tabs.forEach(t => t.addEventListener('click', () => mostrar(t.dataset.modo)));
  const angosta = window.matchMedia('(max-width: 900px)').matches;   // en el celular, la vista rápida
  // Botón «Vista rápida» de la lista (?visor=rapida): abre directo en la vista rápida, sin cambiar el visor
  // que el usuario tiene elegido para los demás exámenes
  const pedida = new URLSearchParams(location.search).get('visor') === 'rapida';
  if (pedida) mostrar('rapida', false);
  else mostrar(angosta ? 'rapida' : leer('visor_modo', caja.dataset.defecto));
  document.querySelectorAll('[data-vista-rapida]').forEach(b => b.addEventListener('click', e => {
    e.preventDefault(); mostrar('rapida'); caja.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));

  const ampliar = document.getElementById('visor-ampliar');
  if (ampliar && estudio) {
    const aplicar = on => { estudio.classList.toggle('ampliado', on); ampliar.setAttribute('aria-pressed', String(on)); };
    aplicar(leer('visor_ampliado', '0') === '1');
    ampliar.addEventListener('click', () => {
      const on = !estudio.classList.contains('ampliado'); aplicar(on); guardar('visor_ampliado', on ? '1' : '0');
    });
  }
  const completa = document.getElementById('visor-completa');
  completa && completa.addEventListener('click', () => {
    const el = (marco && !marco.hidden) ? marco : rapida;
    if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
  });
}
