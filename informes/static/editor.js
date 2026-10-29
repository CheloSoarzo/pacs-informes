/* Editor de informe: guardado automático, plantillas, frases con atajos, voz a texto,
   envío a validación, firma y entrega (link / WhatsApp). */
(function () {
  const cfg = window.EDITOR;
  const $ = s => document.querySelector(s);

  // Formularios con confirmación (desactivar link, anular, etc.)
  document.querySelectorAll('form[data-confirmar]').forEach(f =>
    f.addEventListener('submit', e => { if (!confirm(f.dataset.confirmar)) e.preventDefault(); }));

  async function post(url, datos) {
    const r = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF': cfg.csrf },
      body: JSON.stringify(datos || {})
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('Error ' + r.status));
    return j;
  }

  // ---------- entrega: link, copiar, WhatsApp ----------
  function actualizarWhatsapp() {
    const w = $('#btn-whatsapp'); if (!w) return;
    const url = ($('#link-url') || {}).value || '';
    const clave = $('#link-clave') ? $('#link-clave').textContent.trim() : '';
    let msg = `Hola, su informe de ${cfg.centro} ya está disponible. Puede verlo y descargarlo aquí: ${url}`;
    if (clave) msg += `\nClave de acceso: ${clave}`;
    const tel = (w.dataset.tel || '').replace(/\D/g, '');
    w.href = (tel.length >= 8 ? `https://wa.me/${tel.length === 9 ? '56' + tel : tel}` : 'https://wa.me/') + '?text=' + encodeURIComponent(msg);
    w.classList.toggle('deshabilitado', !url);
  }
  const nuevo = $('#btn-nuevo-link');
  nuevo && nuevo.addEventListener('click', async () => {
    if (cfg.hayLink && !confirm('Se creará un link nuevo y el anterior dejará de funcionar. ¿Continuar?')) return;
    try {
      const r = await post(cfg.urls.link);
      $('#link-url').value = r.url;
      const c = $('#link-clave'); if (c) c.textContent = r.clave || '';
      cfg.hayLink = true; actualizarWhatsapp();
    } catch (e) { alert(e.message); }
  });
  const copiar = $('#btn-copiar');
  copiar && copiar.addEventListener('click', async () => {
    const v = $('#link-url').value;
    try { await navigator.clipboard.writeText(v); } catch (e) { $('#link-url').select(); document.execCommand('copy'); }
    copiar.textContent = '✓ Copiado'; setTimeout(() => { copiar.textContent = 'Copiar'; }, 1500);
  });
  actualizarWhatsapp();

  const txt = $('#cuerpo');
  if (!txt || !cfg.editable) return;
  const solicitante = $('#solicitante'), estado = $('#estado-guardado'), interino = $('#interino');
  const datos = () => ({ cuerpo: txt.value, solicitante: solicitante ? solicitante.value : null });

  // ---------- guardado automático ----------
  let cambios = false, temporizador = null, guardando = false;
  async function guardar() {
    if (!cambios || guardando) return;
    guardando = true; cambios = false;
    estado.textContent = 'Guardando…';
    try { const r = await post(cfg.urls.guardar, datos()); estado.textContent = 'Guardado ' + r.guardado; }
    catch (e) { cambios = true; estado.textContent = '⚠ ' + e.message; }
    guardando = false;
  }
  function marcar() { cambios = true; estado.textContent = 'Cambios sin guardar'; clearTimeout(temporizador); temporizador = setTimeout(guardar, 2500); }
  txt.addEventListener('input', marcar);
  solicitante && solicitante.addEventListener('input', marcar);
  window.addEventListener('beforeunload', e => { if (cambios) { e.preventDefault(); e.returnValue = ''; } });

  // ---------- insertar texto en el cursor ----------
  function insertar(t, comoParrafo) {
    if (!t) return;
    const ini = txt.selectionStart, fin = txt.selectionEnd, antes = txt.value.slice(0, ini);
    const inicioFrase = !antes.trim() || /[.!?:]\s*$/.test(antes) || /\n\s*$/.test(antes);
    if (inicioFrase) t = t.replace(/^\s*([a-záéíóúñü])/, (m, c) => m.replace(c, c.toUpperCase()));
    let pre = '';
    if (comoParrafo && antes && !/\n$/.test(antes)) pre = '\n';
    else if (antes && !/[\s(]$/.test(antes) && !/^[\s.,;:)\n]/.test(t)) pre = ' ';
    txt.setRangeText(pre + t, ini, fin, 'end');
    txt.focus();
    // Selecciona el primer campo a completar (___) del texto insertado
    const hueco = txt.value.indexOf('___', ini);
    if (hueco >= 0 && hueco < ini + pre.length + t.length) txt.setSelectionRange(hueco, hueco + (txt.value.slice(hueco).match(/^_+/)[0].length));
    marcar();
  }
  document.addEventListener('insertar-texto', e => insertar(e.detail, true));

  // ---------- plantillas ----------
  const sel = $('#plantilla');
  sel && sel.addEventListener('change', () => {
    const p = cfg.plantillas[sel.value];
    sel.value = '';
    if (!p) return;
    if (txt.value.trim() && !confirm('¿Reemplazar el texto actual por la plantilla?')) return;
    txt.value = p; marcar(); txt.focus();
    const i = txt.value.indexOf('___'); if (i >= 0) txt.setSelectionRange(i, i + txt.value.slice(i).match(/^_+/)[0].length);
  });

  // ---------- subir un informe escrito en Word / LibreOffice / PDF ----------
  const archivo = $('#archivo-informe');
  archivo && archivo.addEventListener('change', async () => {
    const f = archivo.files[0];
    archivo.value = '';
    if (!f) return;
    if (txt.value.trim() && !confirm('¿Reemplazar el texto actual por el del archivo «' + f.name + '»?')) return;
    const fd = new FormData(); fd.append('archivo', f);
    estado.textContent = 'Leyendo ' + f.name + '…';
    try {
      const r = await fetch(cfg.urls.importar, { method: 'POST', body: fd, credentials: 'same-origin',
                                                 headers: { 'X-CSRF': cfg.csrf } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || 'No se pudo leer el archivo');
      txt.value = j.texto; marcar(); txt.focus(); txt.setSelectionRange(0, 0); txt.scrollTop = 0;
      estado.textContent = 'Cargado desde ' + f.name + ': revísalo antes de enviarlo';
    } catch (e) { estado.textContent = '⚠ ' + e.message; alert(e.message); }
  });

  // ---------- frases (hallazgos y conclusiones) ----------
  const frases = cfg.frases || [];
  const panel = $('#panel-frases'), lista = $('#lista-frases'), buscar = $('#buscar-frase'), bf = $('#btn-frases');
  const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  function pintarFrases() {
    const q = norm(buscar.value);
    const vis = frases.filter(f => !q || norm(f.titulo + ' ' + f.texto + ' ' + f.atajo).includes(q));
    const grupo = tipo => vis.filter(f => f.tipo === tipo).map(f =>
      `<button type="button" class="frase" data-id="${f.id}" title="${f.texto.replace(/"/g, '&quot;')}">
        ${f.atajo ? `<span class="atajo">.${f.atajo}</span>` : ''}${f.titulo}</button>`).join('');
    lista.innerHTML = `<div><h3>Hallazgos</h3>${grupo('hallazgo') || '<p class="peq suave">Sin resultados</p>'}</div>
      <div><h3>Conclusiones</h3>${grupo('conclusion') || '<p class="peq suave">Sin resultados</p>'}</div>`;
  }
  if (panel) {
    let cursor = 0;
    txt.addEventListener('blur', () => { cursor = txt.selectionStart; });
    bf.addEventListener('click', () => {
      panel.hidden = !panel.hidden; bf.setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) { pintarFrases(); buscar.focus(); }
    });
    buscar.addEventListener('input', pintarFrases);
    lista.addEventListener('mousedown', e => e.preventDefault());   // no robar el cursor del texto
    lista.addEventListener('click', e => {
      const b = e.target.closest('.frase'); if (!b) return;
      const f = frases.find(x => String(x.id) === b.dataset.id);
      if (document.activeElement !== txt) { txt.focus(); txt.setSelectionRange(cursor, cursor); }
      insertar(f.texto, false);
    });
  }
  // Atajos: ".atajo" + espacio, Tab o Enter
  const porAtajo = {};
  frases.forEach(f => { if (f.atajo && !porAtajo[f.atajo]) porAtajo[f.atajo] = f; });
  txt.addEventListener('keydown', e => {
    if (![' ', 'Tab', 'Enter'].includes(e.key)) return;
    const pos = txt.selectionStart;
    const m = txt.value.slice(0, pos).match(/(?:^|\s)\.([a-z0-9ñ_]+)$/i);
    if (!m || !porAtajo[m[1].toLowerCase()]) return;
    e.preventDefault();
    txt.setSelectionRange(pos - m[1].length - 1, pos);
    insertar(porAtajo[m[1].toLowerCase()].texto, false);
  });

  // ---------- voz a texto ----------
  const COMANDOS = [
    [/\s*\b(punto y aparte|punto aparte|nuevo párrafo)\b\s*/gi, '.\n'],
    [/\s*\bnueva línea\b\s*/gi, '\n'],
    [/\s*\bpunto y coma\b/gi, ';'],
    [/\s*\b(punto seguido|punto)\b/gi, '.'],
    [/\s*\bcoma\b/gi, ','],
    [/\s*\bdos puntos\b/gi, ':'],
    [/\s*\babrir paréntesis\b\s*/gi, ' ('],
    [/\s*\bcerrar paréntesis\b/gi, ')'],
    [/\s*\bguion\b\s*/gi, ' - '],
  ];
  function procesar(t) {
    for (const [re, r] of COMANDOS) t = t.replace(re, r);
    t = t.replace(/([.:]\s+|\n)([a-záéíóúñü])/g, (m, a, c) => a + c.toUpperCase());
    return t.replace(/[ \t]+\n/g, '\n').replace(/ {2,}/g, ' ');
  }
  const boton = $('#btn-dictar');
  let activo = false, rec = null, grabador = null, trozos = [];
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  function iniciarNavegador() {
    rec = new SR();
    rec.lang = 'es-CL'; rec.continuous = true; rec.interimResults = true;
    rec.onresult = ev => {
      let parcial = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) insertar(procesar(r[0].transcript.trim()));
        else parcial += r[0].transcript;
      }
      interino.textContent = parcial;
    };
    rec.onerror = ev => { if (ev.error === 'not-allowed') { alert('Debes permitir el micrófono para dictar.'); detener(); } };
    rec.onend = () => { if (activo) { try { rec.start(); } catch (e) {} } };
    rec.start();
  }
  async function iniciarServidor() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    grabador = new MediaRecorder(stream); trozos = [];
    grabador.ondataavailable = e => e.data.size && trozos.push(e.data);
    grabador.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const fd = new FormData();
      fd.append('audio', new Blob(trozos, { type: grabador.mimeType }), 'dictado.webm');
      interino.textContent = 'Transcribiendo…';
      try {
        const r = await fetch(cfg.urls.transcribir, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF': cfg.csrf } });
        const j = await r.json(); if (!r.ok) throw new Error(j.error);
        insertar(procesar(j.texto)); interino.textContent = '';
      } catch (e) { interino.textContent = '⚠ ' + e.message; }
    };
    grabador.start();
  }
  async function iniciar() {
    try {
      if (cfg.whisper) await iniciarServidor();
      else if (SR) iniciarNavegador();
      else { alert('Este navegador no permite voz a texto. Usa Google Chrome o Microsoft Edge.'); return; }
      activo = true; boton.classList.add('grabando'); boton.textContent = '■ Detener voz (F2)'; txt.focus();
    } catch (e) { alert('No se pudo acceder al micrófono: ' + e.message); }
  }
  function detener() {
    activo = false;
    if (rec) { rec.onend = null; rec.stop(); rec = null; }
    if (grabador && grabador.state !== 'inactive') grabador.stop();
    boton.classList.remove('grabando'); boton.textContent = '🎙 Voz a texto (F2)';
    if (!cfg.whisper) interino.textContent = '';
  }
  boton && boton.addEventListener('click', () => activo ? detener() : iniciar());
  document.addEventListener('keydown', e => {
    if (e.key === 'F2' && boton) { e.preventDefault(); boton.click(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); cambios = true; guardar(); }
  });

  // ---------- vista previa ----------
  const prev = $('#btn-previa');
  prev && prev.addEventListener('click', () => {
    const f = $('#form-previa');
    f.querySelector('[name=cuerpo]').value = txt.value;
    f.querySelector('[name=solicitante]').value = solicitante ? solicitante.value : '';
    f.submit();
  });

  // ---------- enviar a validación / firmar (con confirmación en la página) ----------
  const caja = $('#confirmar');
  function confirmar(mensaje, etiqueta, clase, accion) {
    if (activo) detener();
    if (txt.value.trim().length < 10) { caja.hidden = false; caja.className = 'confirmar error'; caja.textContent = 'El informe está vacío.'; return; }
    caja.hidden = false; caja.className = 'confirmar';
    caja.innerHTML = `<p>${mensaje}</p><div class="fila"><button type="button" class="btn ${clase}" id="c-si">${etiqueta}</button>
      <button type="button" class="btn" id="c-no">Cancelar</button></div>`;
    $('#c-no').onclick = () => { caja.hidden = true; };
    $('#c-si').onclick = async () => {
      $('#c-si').disabled = true;
      try { const r = await accion(); cambios = false; if (r && r.aviso) alert(r.aviso); location.reload(); }
      catch (e) { caja.className = 'confirmar error'; caja.textContent = e.message; }
    };
    $('#c-si').focus();
  }
  const ev = $('#btn-enviar-validacion');
  ev && ev.addEventListener('click', () => confirmar(
    'El informe pasará a «Por validar» y lo revisará el radiólogo.', 'Enviar a validación', 'prim',
    () => post(cfg.urls.enviarValidacion, datos())));
  const fi = $('#btn-firmar');
  fi && fi.addEventListener('click', () => confirmar(
    'Al validar, el informe queda firmado con tu nombre, se bloquea y queda disponible para el paciente.', '✓ Sí, validar y firmar', 'ok',
    () => post(cfg.urls.firmar, datos())));
})();
