/* Lista de estudios: selección para acciones en lote, validación en lote y actualización automática. */
(function () {
  const $ = s => document.querySelector(s);
  const checks = () => [...document.querySelectorAll('input[name=ids]')];
  const barra = $('#acciones-lote'), conf = $('#confirmar-lote');

  function actualizar() {
    const sel = checks().filter(c => c.checked);
    if (barra) {
      barra.hidden = sel.length === 0;
      $('#n-sel').textContent = sel.length === 1 ? '1 seleccionado' : `${sel.length} seleccionados`;
    }
    if (conf && !sel.length) conf.hidden = true;
  }
  document.addEventListener('change', e => {
    if (e.target.id === 'sel-todos') checks().forEach(c => { c.checked = e.target.checked; });
    if (e.target.id === 'sel-todos' || e.target.name === 'ids') actualizar();
  });

  const vl = $('#btn-validar-lote');
  vl && vl.addEventListener('click', () => {
    const n = checks().filter(c => c.checked && c.dataset.estado === 'por_validar').length;
    $('#n-validar').textContent = n;
    $('#btn-confirmar-lote').disabled = n === 0;
    conf.hidden = false;
    $('#confirmado').value = '1';
  });
  const cl = $('#btn-cancelar-lote');
  cl && cl.addEventListener('click', () => { conf.hidden = true; $('#confirmado').value = '0'; });

  // Doble clic en una fila: abre el estudio (quien puede corregir datos ve la ventana de edición, paciente.js)
  document.addEventListener('dblclick', e => {
    const fila = e.target.closest('tr[data-url]');
    if (!fila || fila.dataset.estudio || e.target.closest('a, button, input, select, label')) return;
    location.href = fila.dataset.url;
  });

  // Pantalla de dictado: se guarda el orden de la lista para el botón "Siguiente →"
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-dictar]');
    if (!a) return;
    const ids = [...document.querySelectorAll('table.lista tbody a[data-dictar]')].map(x => x.dataset.dictar);
    try { sessionStorage.setItem('cola_estudios', JSON.stringify(ids)); } catch (err) {}
  });

  // Actualización automática (se recuerda en este navegador)
  const sel = $('#refresco');
  let seg = 0;
  try { seg = +(localStorage.getItem('refresco') || 0); } catch (e) {}
  if (sel) {
    sel.value = String(seg);
    sel.addEventListener('change', () => { try { localStorage.setItem('refresco', sel.value); } catch (e) {} location.reload(); });
  }
  if (seg > 0) {
    setInterval(() => {
      const ocupado = checks().some(c => c.checked) || document.querySelector('details[open], dialog[open]') || document.body.classList.contains('con-panel-lista') ||
        (document.activeElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName));
      if (!ocupado && document.visibilityState === 'visible') location.reload();
    }, seg * 1000);
  }
})();


// Botones de período rápido (Hoy, Ayer, 3 días, Última semana, Este mes, Últimos 30 días): llenan Desde/Hasta y buscan
(function () {
  const caja = document.querySelector('.rangos-rapidos');
  const fd = document.getElementById('desde'), fh = document.getElementById('hasta');
  if (!caja || !fd || !fh) return;
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const menos = n => { const d = new Date(); d.setDate(d.getDate() - n); return iso(d); };
  const hoy = iso(new Date());
  const rangos = {
    hoy: [hoy, hoy], ayer: [menos(1), menos(1)], '3d': [menos(2), hoy], '7d': [menos(6), hoy],
    mes: [hoy.slice(0, 8) + '01', hoy], '30d': [menos(29), hoy],
  };
  const q = document.getElementById('q');
  for (const b of caja.querySelectorAll('.rango')) {
    const [a, z] = rangos[b.dataset.r];
    b.setAttribute('aria-pressed', String(!(q && q.value) && fd.value === a && fh.value === z));
    b.addEventListener('click', () => {
      fd.value = a; fh.value = z;
      if (q) q.value = '';                     // la búsqueda por texto ignora las fechas: se limpia
      document.getElementById('form-filtros').submit();
    });
  }
})();
