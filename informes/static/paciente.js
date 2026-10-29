/* Corregir datos del paciente: doble clic en una fila de la lista o botón [data-editar-paciente]. */
(function () {
  const dlg = document.getElementById('dlg-paciente');
  if (!dlg || typeof dlg.showModal !== 'function') return;
  const form = document.getElementById('form-paciente');
  const campos = document.getElementById('dlg-paciente-campos');
  const cargando = document.getElementById('dlg-paciente-cargando');
  const guardar = document.getElementById('dlg-paciente-guardar');
  const aviso = document.getElementById('dlg-paciente-validado');

  function abrir(estudioId) {
    const url = '/estudio/' + encodeURIComponent(estudioId) + '/datos';
    form.action = url;
    campos.hidden = true; aviso.hidden = true; guardar.disabled = true;
    cargando.hidden = false; cargando.textContent = 'Cargando…';
    dlg.showModal();
    fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(d => {
        for (const [k, v] of Object.entries(d.datos)) if (form.elements[k]) form.elements[k].value = v || '';
        aviso.hidden = !d.informe_validado;
        cargando.hidden = true; campos.hidden = false; guardar.disabled = false;
        form.elements.apellidos.focus();
      })
      .catch(() => { cargando.textContent = 'No se pudieron cargar los datos del estudio.'; });
  }

  form.addEventListener('submit', () => { guardar.disabled = true; guardar.textContent = 'Guardando…'; });
  document.getElementById('dlg-paciente-cancelar').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });   // clic fuera de la ventana

  // Lista: doble clic en la fila (no en casillas, enlaces ni botones)
  document.addEventListener('dblclick', e => {
    const fila = e.target.closest('tr[data-estudio]');
    if (!fila || e.target.closest('a, button, input, select, label')) return;
    window.getSelection && window.getSelection().removeAllRanges();
    abrir(fila.dataset.estudio);
  });
  document.querySelectorAll('[data-editar-paciente]').forEach(b =>
    b.addEventListener('click', () => abrir(b.dataset.editarPaciente)));
})();
