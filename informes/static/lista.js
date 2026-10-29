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
      const ocupado = checks().some(c => c.checked) || document.querySelector('details[open], dialog[open]') ||
        (document.activeElement && ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName));
      if (!ocupado && document.visibilityState === 'visible') location.reload();
    }, seg * 1000);
  }
})();
