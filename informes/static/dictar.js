/* Pantalla de dictado: pestañas, notas, visor (con comparación) y "Siguiente estudio". */
(function () {
  const $ = s => document.querySelector(s);

  // Pestañas "Estudio para dictar" / "Estudios anteriores"
  document.querySelectorAll('.dict-pestanas [role=tab]').forEach(t => t.addEventListener('click', () => {
    document.querySelectorAll('.dict-pestanas [role=tab]').forEach(x => x.setAttribute('aria-selected', String(x === t)));
    document.querySelectorAll('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== t.dataset.tab; });
  }));

  // Notas
  const bn = $('#btn-notas'), pn = $('#dict-notas');
  bn && bn.addEventListener('click', () => { pn.hidden = !pn.hidden; bn.setAttribute('aria-expanded', String(!pn.hidden)); });

  // Confirmaciones (eliminar audio)
  document.querySelectorAll('form[data-confirmar]').forEach(f =>
    f.addEventListener('submit', e => { if (!confirm(f.dataset.confirmar)) e.preventDefault(); }));

  // Visor: OHIF o Stone; "Comparar" abre el estudio anterior junto al actual
  const iframe = $('#dict-iframe'), quitar = $('#dict-quitar-comparar');
  const tabsVisor = [...document.querySelectorAll('.dict-visor-pestanas [role=tab]')];
  function visor(modo, comparar) {
    tabsVisor.forEach(t => t.setAttribute('aria-selected', String(t.dataset.visor === modo)));
    let src = iframe.dataset[modo];
    if (comparar) {
      src = modo === 'ohif'
        ? `${iframe.dataset.base}/ohif/viewer?StudyInstanceUIDs=${iframe.dataset.uid},${encodeURIComponent(comparar)}`
        : `${iframe.dataset.base}/stone-webviewer/index.html?study=${iframe.dataset.uid},${encodeURIComponent(comparar)}`;
    }
    iframe.dataset.comparar = comparar || '';
    quitar.hidden = !comparar;
    if (iframe.getAttribute('src') !== src) iframe.src = src;
    try { localStorage.setItem('dictar_visor', modo); } catch (e) {}
  }
  tabsVisor.forEach(t => t.addEventListener('click', () => visor(t.dataset.visor, iframe.dataset.comparar)));
  document.querySelectorAll('[data-comparar]').forEach(b => b.addEventListener('click', () => {
    const modo = (tabsVisor.find(t => t.getAttribute('aria-selected') === 'true') || {}).dataset;
    visor(modo ? modo.visor : 'ohif', b.dataset.comparar);
  }));
  quitar.addEventListener('click', () => visor((tabsVisor.find(t => t.getAttribute('aria-selected') === 'true') || tabsVisor[0]).dataset.visor, ''));
  let pref = 'ohif'; try { pref = localStorage.getItem('dictar_visor') || 'ohif'; } catch (e) {}
  if (pref !== 'ohif') visor(pref, '');

  // F8 también funciona con el foco dentro del visor (mismo dominio)
  iframe.addEventListener('load', () => {
    try {
      iframe.contentWindow.document.addEventListener('keydown', e => {
        if (e.key === 'F8') { e.preventDefault(); document.dispatchEvent(new KeyboardEvent('keydown', { key: 'F8' })); }
      }, true);
    } catch (e) {}
  });

  // Siguiente estudio: la lista guarda el orden de los estudios que se estaban viendo
  try {
    const cola = JSON.parse(sessionStorage.getItem('cola_estudios') || '[]');
    const actual = location.pathname.split('/')[2];
    const i = cola.indexOf(actual);
    const sig = $('#btn-siguiente');
    if (i >= 0 && i < cola.length - 1 && sig) {
      sig.href = '/estudio/' + encodeURIComponent(cola[i + 1]) + '/dictar';
      sig.hidden = false;
    }
  } catch (e) {}
})();
