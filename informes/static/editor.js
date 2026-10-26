/* Editor de informe: guardado automático, plantillas, dictado por voz, firma y link. */
(function () {
  const cfg = window.EDITOR;               // {estudioId, csrf, editable, whisper, urls:{...}}
  const $ = s => document.querySelector(s);
  const txt = $('#cuerpo'), solicitante = $('#solicitante');
  const estado = $('#estado-guardado'), interino = $('#interino');
  if (!txt) return;

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
  const datos = () => ({ cuerpo: txt.value, solicitante: solicitante ? solicitante.value : null });

  // ---------- guardado automático ----------
  let cambios = false, temporizador = null, guardando = false;
  async function guardar() {
    if (!cfg.editable || !cambios || guardando) return;
    guardando = true; cambios = false;
    estado.textContent = 'Guardando…';
    try { const r = await post(cfg.urls.guardar, datos()); estado.textContent = 'Guardado ' + r.guardado; }
    catch (e) { cambios = true; estado.textContent = '⚠ ' + e.message; }
    guardando = false;
  }
  function marcar() { cambios = true; estado.textContent = 'Cambios sin guardar'; clearTimeout(temporizador); temporizador = setTimeout(guardar, 2500); }
  if (cfg.editable) {
    txt.addEventListener('input', marcar);
    solicitante && solicitante.addEventListener('input', marcar);
    window.addEventListener('beforeunload', e => { if (cambios) { e.preventDefault(); e.returnValue = ''; } });
  }

  // ---------- plantillas ----------
  const sel = $('#plantilla');
  sel && sel.addEventListener('change', () => {
    const p = cfg.plantillas[sel.value];
    if (!p) return;
    if (txt.value.trim() && !confirm('¿Reemplazar el texto actual por la plantilla?')) { sel.value = ''; return; }
    txt.value = p; sel.value = ''; marcar(); txt.focus();
    const i = txt.value.indexOf('______'); if (i >= 0) txt.setSelectionRange(i, i + 6);
  });

  // ---------- insertar texto en el cursor ----------
  function insertar(t) {
    if (!t) return;
    const ini = txt.selectionStart, fin = txt.selectionEnd, antes = txt.value.slice(0, ini);
    const previo = antes.trimEnd().slice(-1);
    const inicioFrase = !antes.trim() || /[.!?:\n]$/.test(antes.trimEnd()) || antes.endsWith('\n');
    if (inicioFrase) t = t.replace(/^\s*([a-záéíóúñü])/, (m, c) => m.replace(c, c.toUpperCase()));
    const necesitaEspacio = antes && !/[\s(]$/.test(antes) && !/^[\s.,;:)\n]/.test(t);
    t = (necesitaEspacio ? ' ' : '') + t;
    txt.setRangeText(t, ini, fin, 'end');
    // Mayúscula después de punto dentro del texto dictado
    marcar();
  }

  // Comandos de voz → puntuación
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

  // ---------- dictado ----------
  const boton = $('#btn-dictar');
  let activo = false;

  // Modo 1: reconocimiento del navegador (Chrome / Edge)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
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
    rec.onerror = ev => {
      if (ev.error === 'not-allowed') { alert('Debes permitir el micrófono para dictar.'); detener(); }
    };
    rec.onend = () => { if (activo) { try { rec.start(); } catch (e) {} } };  // Chrome corta tras silencios
    rec.start();
  }

  // Modo 2: graba audio y lo transcribe el servidor Whisper
  let grabador = null, trozos = [];
  async function iniciarServidor() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    grabador = new MediaRecorder(stream);
    trozos = [];
    grabador.ondataavailable = e => e.data.size && trozos.push(e.data);
    grabador.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const fd = new FormData();
      fd.append('audio', new Blob(trozos, { type: grabador.mimeType }), 'dictado.webm');
      interino.textContent = 'Transcribiendo…';
      try {
        const r = await fetch(cfg.urls.transcribir, { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-CSRF': cfg.csrf } });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error);
        insertar(procesar(j.texto));
        interino.textContent = '';
      } catch (e) { interino.textContent = '⚠ ' + e.message; }
    };
    grabador.start();
  }

  async function iniciar() {
    try {
      if (cfg.whisper) await iniciarServidor();
      else if (SR) iniciarNavegador();
      else { alert('Este navegador no permite dictado. Usa Google Chrome o Microsoft Edge.'); return; }
      activo = true; boton.classList.add('grabando'); boton.textContent = '■ Detener dictado (F2)';
      txt.focus();
    } catch (e) { alert('No se pudo acceder al micrófono: ' + e.message); }
  }
  function detener() {
    activo = false;
    if (rec) { rec.onend = null; rec.stop(); rec = null; }
    if (grabador && grabador.state !== 'inactive') grabador.stop();
    boton.classList.remove('grabando'); boton.textContent = '🎙 Dictar (F2)';
    if (!cfg.whisper) interino.textContent = '';
  }
  if (boton) {
    if (!cfg.whisper && !SR) boton.title = 'Usa Chrome o Edge para dictar';
    boton.addEventListener('click', () => activo ? detener() : iniciar());
  }

  document.addEventListener('keydown', e => {
    if (e.key === 'F2' && boton) { e.preventDefault(); boton.click(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); cambios = true; guardar(); }
  });

  // ---------- vista previa ----------
  const prev = $('#btn-previa');
  prev && prev.addEventListener('click', () => {
    const f = $('#form-previa');
    f.querySelector('[name=cuerpo]').value = txt.value;
    f.querySelector('[name=solicitante]').value = solicitante ? solicitante.value : '';
    f.submit();
  });

  // ---------- firmar ----------
  const firmar = $('#btn-firmar');
  firmar && firmar.addEventListener('click', async () => {
    if (activo) detener();
    if (!confirm('¿Firmar y validar el informe? Después quedará bloqueado y disponible para el paciente.')) return;
    firmar.disabled = true;
    try { await post(cfg.urls.firmar, datos()); cambios = false; location.reload(); }
    catch (e) { alert(e.message); firmar.disabled = false; }
  });

  // ---------- link para paciente ----------
  const nuevo = $('#btn-nuevo-link');
  nuevo && nuevo.addEventListener('click', async () => {
    if (cfg.hayLink && !confirm('Se creará un link nuevo y el anterior dejará de funcionar. ¿Continuar?')) return;
    try {
      const r = await post(cfg.urls.link);
      mostrarLink(r.url, r.clave);
    } catch (e) { alert(e.message); }
  });
  function mostrarLink(url, clave) {
    $('#link-url').value = url;
    const c = $('#link-clave'); if (c) c.textContent = clave || '';
    actualizarWhatsapp();
  }
  function actualizarWhatsapp() {
    const w = $('#btn-whatsapp'); if (!w) return;
    const url = $('#link-url').value, clave = $('#link-clave') ? $('#link-clave').textContent : '';
    let msg = `Hola, su informe de ${cfg.centro} ya está disponible. Puede verlo y descargarlo aquí: ${url}`;
    if (clave) msg += `\nClave de acceso: ${clave}`;
    w.href = 'https://wa.me/?text=' + encodeURIComponent(msg);
  }
  const copiar = $('#btn-copiar');
  copiar && copiar.addEventListener('click', async () => {
    const v = $('#link-url').value;
    try { await navigator.clipboard.writeText(v); } catch (e) { $('#link-url').select(); document.execCommand('copy'); }
    copiar.textContent = '✓ Copiado'; setTimeout(() => copiar.textContent = 'Copiar', 1500);
  });
  if ($('#link-url')) actualizarWhatsapp();
})();
