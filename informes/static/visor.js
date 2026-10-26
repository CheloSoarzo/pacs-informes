/* Visor simple de imágenes (vista previa JPEG de Orthanc).
   Uso: new VisorSimple(elemento, urlSeries, prefijoImg)
   - urlSeries devuelve {series:[{id, modalidad, descripcion, instancias:[ids]}]}
   - prefijoImg + idInstancia = URL de la imagen
   Rueda del mouse, flechas o arrastrar arriba/abajo para recorrer cortes. */
class VisorSimple {
  constructor(el, urlSeries, prefijoImg) {
    this.el = el; this.prefijo = prefijoImg; this.cache = new Map();
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
      <div class="lienzo" tabindex="0"><img alt="Imagen del estudio"><div class="info"></div></div>
      <div class="controles"><span class="pos"></span><input type="range" min="0" value="0"></div>`;
    this.$series = this.el.querySelector('.series');
    this.$img = this.el.querySelector('.lienzo img');
    this.$info = this.el.querySelector('.info');
    this.$pos = this.el.querySelector('.pos');
    this.$rango = this.el.querySelector('input[type=range]');
    const lienzo = this.el.querySelector('.lienzo');

    series.forEach((s, i) => {
      const b = document.createElement('button');
      const medio = s.instancias[Math.floor(s.instancias.length / 2)];
      b.innerHTML = `<img loading="lazy" src="${this.prefijo}${medio}" alt="">${s.modalidad} · ${s.instancias.length}<br>${(s.descripcion || '').slice(0, 22)}`;
      b.onclick = () => this.serie(i);
      this.$series.appendChild(b);
    });

    this.$rango.oninput = () => this.ir(+this.$rango.value);
    lienzo.addEventListener('wheel', e => { e.preventDefault(); this.ir(this.i + (e.deltaY > 0 ? 1 : -1)); }, { passive: false });
    lienzo.addEventListener('keydown', e => {
      if (['ArrowDown', 'ArrowRight'].includes(e.key)) { e.preventDefault(); this.ir(this.i + 1); }
      if (['ArrowUp', 'ArrowLeft'].includes(e.key)) { e.preventDefault(); this.ir(this.i - 1); }
    });
    let y0 = null;
    lienzo.addEventListener('pointerdown', e => { y0 = e.clientY; lienzo.setPointerCapture(e.pointerId); });
    lienzo.addEventListener('pointermove', e => {
      if (y0 === null) return;
      const paso = Math.trunc((e.clientY - y0) / 8);
      if (paso) { this.ir(this.i + paso); y0 = e.clientY; }
    });
    lienzo.addEventListener('pointerup', () => { y0 = null; });
    this.serie(0);
  }

  serie(n) {
    this.s = n;
    [...this.$series.children].forEach((b, i) => b.classList.toggle('activo', i === n));
    const total = this.series[n].instancias.length;
    this.$rango.max = total - 1;
    this.ir(Math.floor(total / 2));
  }

  ir(i) {
    const inst = this.series[this.s].instancias;
    this.i = Math.max(0, Math.min(inst.length - 1, i));
    this.$img.src = this.prefijo + inst[this.i];
    this.$rango.value = this.i;
    const s = this.series[this.s];
    this.$pos.textContent = `${this.i + 1} / ${inst.length}`;
    this.$info.textContent = `${s.modalidad} ${s.descripcion || ''}`;
    // Precarga de vecinos para recorrer fluido
    for (const d of [1, 2, 3, -1, -2]) {
      const id = inst[this.i + d];
      if (id && !this.cache.has(id)) { const im = new Image(); im.src = this.prefijo + id; this.cache.set(id, im); }
    }
  }
}
