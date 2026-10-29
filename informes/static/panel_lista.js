/* Panel inferior de la lista: clic en un paciente → audio del dictado, series del estudio y minivisor.
   Flechas ↑ ↓ cambian de paciente; Enter abre el estudio (igual que el botón de la fila). */
(function () {
  const panel = document.getElementById('panel-estudio');
  if (!panel) return;
  const $ = s => panel.querySelector(s);
  const filas = () => [...document.querySelectorAll('table.lista tbody tr[data-url]')];
  const esc = t => String(t == null ? '' : t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const mmss = s => { s = Math.round(+s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const ESTADO = { pendiente: 'Por transcribir', tomado: 'En transcripción', transcrito: 'Transcrito' };
  let actual = null, ctrl = null, serie = null, idx = 0;
  const cache = new Map();

  function seleccionar(tr, desplazar) {
    if (!tr || tr === actual) return;
    filas().forEach(f => f.classList.toggle('sel', f === tr));
    actual = tr;
    if (desplazar) tr.scrollIntoView({ block: 'nearest' });
    panel.hidden = false;
    document.body.classList.add('con-panel-lista');
    const nombre = tr.querySelector('.paciente');
    $('#pl-paciente').textContent = nombre ? nombre.textContent.trim() : '';
    const boton = tr.querySelector('td:last-child a');
    $('#pl-abrir').href = boton ? boton.getAttribute('href') : tr.dataset.url;
    $('#pl-abrir').textContent = boton ? boton.textContent.trim() + ' →' : 'Abrir →';
    if (boton && boton.dataset.dictar) $('#pl-abrir').dataset.dictar = boton.dataset.dictar;
    else delete $('#pl-abrir').dataset.dictar;
    cargar(tr.dataset.url.split('/')[2]);
  }

  async function cargar(id) {
    if (ctrl) ctrl.abort();
    $('#pl-audios').innerHTML = '<p class="peq suave">Cargando…</p>';
    $('#pl-series tbody').innerHTML = '';
    $('#pl-img').removeAttribute('src'); $('#pl-pos').textContent = '';
    let d = cache.get(id);
    if (!d) {
      ctrl = new AbortController();
      try {
        const r = await fetch('/estudio/' + encodeURIComponent(id) + '/panel', { credentials: 'same-origin', signal: ctrl.signal });
        if (!r.ok) throw new Error(r.status);
        d = await r.json(); cache.set(id, d);
      } catch (e) {
        if (e.name !== 'AbortError') $('#pl-audios').innerHTML = '<p class="peq suave">No se pudo cargar el estudio.</p>';
        return;
      }
    }
    pintarAudios(d.dictados || []);
    pintarSeries(d.series || []);
  }

  function pintarAudios(lista) {
    const c = $('#pl-audios');
    if (!lista.length) { c.innerHTML = '<p class="peq suave">Este estudio no tiene audio.</p>'; return; }
    c.innerHTML = lista.map((a, i) => `
      <div class="pl-audio-item">
        <div class="peq"><b>Audio ${i + 1}</b> · ${esc(a.autor)} · ${mmss(a.duracion)} ·
          <span class="badge ${a.estado === 'transcrito' ? 'firmado' : 'dictado'}">${ESTADO[a.estado] || esc(a.estado)}</span></div>
        ${a.nota ? `<div class="peq suave">Nota: ${esc(a.nota)}</div>` : ''}
        <div class="reproductor" data-src="${esc(a.audio)}"></div>
      </div>`).join('');
    if (window.ReproductorAudio) c.querySelectorAll('.reproductor').forEach(el => new window.ReproductorAudio(el));
  }

  function pintarSeries(series) {
    const tb = $('#pl-series tbody');
    if (!series.length) { tb.innerHTML = '<tr><td colspan="6" class="suave peq">Sin imágenes.</td></tr>'; return; }
    tb.innerHTML = series.map((s, i) => `
      <tr data-i="${i}"><td class="nowrap">${esc(s.fecha)} ${esc(s.hora)}</td><td>${esc(s.numero || '')}</td>
        <td>${esc(s.descripcion)}</td><td>${esc(s.region)}</td><td>${esc(s.modalidad)}</td><td>${s.instancias.length}</td></tr>`).join('');
    tb.querySelectorAll('tr').forEach(tr => tr.addEventListener('click', () => verSerie(series, +tr.dataset.i)));
    verSerie(series, 0);
  }

  function verSerie(series, i) {
    serie = series[i];
    $('#pl-series tbody').querySelectorAll('tr').forEach(tr => tr.classList.toggle('sel', +tr.dataset.i === i));
    const r = $('#pl-rango'); r.max = serie.instancias.length - 1; r.hidden = serie.instancias.length < 2;
    ir(Math.floor(serie.instancias.length / 2));
  }

  function ir(i) {
    if (!serie) return;
    const inst = serie.instancias;
    idx = Math.max(0, Math.min(inst.length - 1, i));
    $('#pl-img').src = '/img/' + inst[idx];
    $('#pl-rango').value = idx;
    $('#pl-pos').textContent = inst.length > 1 ? `${idx + 1} / ${inst.length}` : '';
    for (const k of [1, 2, -1]) { const id = inst[idx + k]; if (id) { const im = new Image(); im.src = '/img/' + id; } }
  }
  $('#pl-rango').addEventListener('input', e => ir(+e.target.value));
  $('#pl-visor').addEventListener('wheel', e => { if (!serie) return; e.preventDefault(); ir(idx + (e.deltaY > 0 ? 1 : -1)); }, { passive: false });

  $('#pl-cerrar').addEventListener('click', () => {
    panel.hidden = true; document.body.classList.remove('con-panel-lista');
    filas().forEach(f => f.classList.remove('sel')); actual = null;
  });

  // Clic en la fila (no en casillas, enlaces ni botones)
  document.addEventListener('click', e => {
    const tr = e.target.closest('table.lista tbody tr[data-url]');
    if (!tr || e.target.closest('a, button, input, select, label')) return;
    seleccionar(tr);
  });
  // Flechas para recorrer pacientes, Enter para abrir
  document.addEventListener('keydown', e => {
    if (!actual || e.altKey || e.ctrlKey || e.metaKey) return;
    if (document.activeElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
    if (document.querySelector('dialog[open]')) return;
    const fs = filas(), i = fs.indexOf(actual);
    if (e.key === 'ArrowDown' && i < fs.length - 1) { e.preventDefault(); seleccionar(fs[i + 1], true); }
    if (e.key === 'ArrowUp' && i > 0) { e.preventDefault(); seleccionar(fs[i - 1], true); }
    if (e.key === 'Enter') { e.preventDefault(); $('#pl-abrir').click(); }
  });
})();
