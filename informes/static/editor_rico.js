/* Editor de texto del informe con negrita, cursiva y subrayado.

   El texto se sigue guardando como texto simple; el formato va marcado solo con <b>, <i> y <u>
   (ej. "IMPRESIÓN:\n<b>Sin hallazgos</b>"). Así las plantillas, frases, dictado e informes antiguos
   siguen funcionando igual, y el PDF y el portal muestran el formato.

   EditorRico.desdeTextarea(textarea) esconde el textarea, pone en su lugar la barra y el área editable,
   y mantiene textarea.value al día (los formularios lo envían sin cambios). Si este archivo no carga,
   queda el textarea de siempre. */
(function () {
  const ETIQUETAS = ['b', 'i', 'u'];
  const RE_TOKEN = /(<\/?[biu]>)/i;

  // ---------- texto con marcas  <->  área editable ----------
  function poner(div, valor) {
    div.innerHTML = '';
    const activos = { b: 0, i: 0, u: 0 };   // cada línea parte sin formato (igual que el PDF)
    for (const parte of String(valor || '').split(RE_TOKEN)) {
      if (!parte) continue;
      const m = parte.match(/^<(\/?)([biu])>$/i);
      if (m) { const t = m[2].toLowerCase(); activos[t] = Math.max(0, activos[t] + (m[1] ? -1 : 1)); continue; }
      parte.split('\n').forEach((linea, k) => {
        if (k) { div.appendChild(document.createElement('br')); activos.b = activos.i = activos.u = 0; }
        if (!linea) return;
        let nodo = document.createTextNode(linea);
        for (const t of ETIQUETAS.slice().reverse()) {
          if (activos[t]) { const e = document.createElement(t); e.appendChild(nodo); nodo = e; }
        }
        div.appendChild(nodo);
      });
    }
    if (div.lastChild && div.lastChild.nodeName === 'BR') div.appendChild(document.createElement('br'));
  }

  // Recorre el área: devuelve los trozos de texto con su formato (y '\n' por cada salto de línea)
  function trozos(div) {
    const out = [];
    (function rec(nodo, fmt) {
      for (const n of nodo.childNodes) {
        if (n.nodeType === 3) { if (n.data) out.push({ t: n.data, f: fmt, nodo: n }); continue; }
        if (n.nodeType !== 1) continue;
        const nom = n.nodeName.toLowerCase();
        if (nom === 'br') { out.push({ t: '\n', f: '', nodo: n }); continue; }
        const bloque = nom === 'div' || nom === 'p';
        if (bloque && out.length && out[out.length - 1].t !== '\n') out.push({ t: '\n', f: '', nodo: null });
        let f = fmt;
        const est = n.style || {};
        if ((nom === 'b' || nom === 'strong' || /^(bold|[6-9]00)$/.test(est.fontWeight || '')) && !f.includes('b')) f += 'b';
        if ((nom === 'i' || nom === 'em' || est.fontStyle === 'italic') && !f.includes('i')) f += 'i';
        if ((nom === 'u' || /underline/.test(est.textDecoration || est.textDecorationLine || '')) && !f.includes('u')) f += 'u';
        rec(n, f);
      }
    })(div, '');
    return out;
  }

  function valor(div) {
    let s = '';
    for (const p of trozos(div)) {
      if (p.t === '\n') { s += '\n'; continue; }
      let t = p.t.replace(/\u00a0/g, ' ');
      for (const e of ETIQUETAS.slice().reverse()) if (p.f.includes(e)) t = `<${e}>${t}</${e}>`;
      s += t;
    }
    // une marcas iguales seguidas: "<b>a</b><b>b</b>" -> "<b>ab</b>"
    let antes;
    do { antes = s; s = s.replace(/<\/([biu])>((?:<[biu]>)*)<\1>/g, (m, e, abiertas) => abiertas ? m : ''); } while (s !== antes);
    return s.replace(/\n$/, '');
  }
  const plano = div => trozos(div).map(p => p.t).join('').replace(/\u00a0/g, ' ').replace(/\n$/, '');

  // ---------- posiciones del cursor como número de caracteres del texto plano ----------
  function offsetDe(div, nodo, off) {
    const r = document.createRange();
    r.selectNodeContents(div);
    try { r.setEnd(nodo, off); } catch (e) { return 0; }
    const frag = document.createElement('div');
    frag.appendChild(r.cloneContents());
    return trozos(frag).map(p => p.t).join('').length;
  }
  function posicionDe(div, n) {
    let resto = n;
    for (const p of trozos(div)) {
      if (!p.nodo) { resto -= 1; if (resto < 0) break; continue; }
      if (p.nodo.nodeType === 3) {
        if (resto <= p.t.length) return [p.nodo, resto];
        resto -= p.t.length;
      } else {                                   // <br>
        const padre = p.nodo.parentNode, idx = Array.prototype.indexOf.call(padre.childNodes, p.nodo);
        if (resto === 0) return [padre, idx];
        resto -= 1;
        if (resto === 0) return [padre, idx + 1];
      }
    }
    return [div, div.childNodes.length];
  }

  function crear(textarea) {
    const div = document.createElement('div');
    div.className = 'editor-rico';
    div.contentEditable = 'true';
    div.spellcheck = true;
    div.lang = textarea.lang || 'es';
    div.setAttribute('role', 'textbox');
    div.setAttribute('aria-multiline', 'true');
    if (textarea.id) {
      const etq = document.querySelector(`label[for="${textarea.id}"]`);
      if (etq) { etq.id = etq.id || textarea.id + '-etq'; div.setAttribute('aria-labelledby', etq.id); etq.addEventListener('click', () => div.focus()); }
    }
    if (textarea.placeholder) div.dataset.placeholder = textarea.placeholder;
    if (textarea.id === 'cuerpo') div.classList.add('principal');
    else if (textarea.rows) div.style.minHeight = (textarea.rows * 1.6 + 1) + 'em';

    const barra = document.createElement('div');
    barra.className = 'barra-formato';
    barra.setAttribute('role', 'toolbar');
    barra.setAttribute('aria-label', 'Formato del texto');
    const BOTONES = [['bold', 'B', 'Negrita (Ctrl+B)', 'b'], ['italic', 'I', 'Cursiva (Ctrl+I)', 'i'],
                     ['underline', 'U', 'Subrayado (Ctrl+U)', 'u']];
    for (const [cmd, letra, titulo, e] of BOTONES) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'btn-formato f-' + e; b.dataset.cmd = cmd;
      b.title = titulo; b.setAttribute('aria-label', titulo.replace(/ \(.*/, '')); b.setAttribute('aria-pressed', 'false');
      b.innerHTML = `<${e}>${letra}</${e}>`;
      barra.appendChild(b);
    }
    const quitar = document.createElement('button');
    quitar.type = 'button'; quitar.className = 'btn-formato f-quitar'; quitar.dataset.cmd = 'removeFormat';
    quitar.title = 'Quitar formato del texto seleccionado'; quitar.textContent = 'Quitar formato';
    barra.appendChild(quitar);

    textarea.hidden = true;
    textarea.parentNode.insertBefore(barra, textarea);
    textarea.parentNode.insertBefore(div, textarea);
    try { document.execCommand('styleWithCSS', false, false); } catch (e) { /* navegadores antiguos */ }

    const ed = {
      div, textarea,
      valor: () => valor(div),
      plano: () => plano(div),
      poner(v) { poner(div, v); sincronizar(); vacio(); },
      focus() { div.focus(); },
      enfocado: () => document.activeElement === div,
      sel() {
        const s = window.getSelection();
        if (!s.rangeCount || !div.contains(s.anchorNode)) { const n = plano(div).length; return { ini: n, fin: n }; }
        const r = s.getRangeAt(0);
        return { ini: offsetDe(div, r.startContainer, r.startOffset), fin: offsetDe(div, r.endContainer, r.endOffset) };
      },
      seleccionar(ini, fin) {
        const r = document.createRange();
        r.setStart(...posicionDe(div, ini));
        r.setEnd(...posicionDe(div, fin === undefined ? ini : fin));
        const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      },
      // Inserta texto simple donde está el cursor (reemplaza lo seleccionado); queda en el deshacer (Ctrl+Z)
      insertar(t) {
        if (!ed.enfocado()) div.focus();
        String(t).split('\n').forEach((linea, k) => {
          if (k) document.execCommand('insertLineBreak');
          if (linea) document.execCommand('insertText', false, linea);
        });
      },
    };

    function sincronizar() { textarea.value = valor(div); }
    function vacio() { div.classList.toggle('vacio', !plano(div).trim()); }
    function estados() {
      for (const b of barra.querySelectorAll('[aria-pressed]')) {
        let on = false; try { on = document.queryCommandState(b.dataset.cmd); } catch (e) { /* */ }
        b.setAttribute('aria-pressed', String(!!on)); b.classList.toggle('activo', !!on);
      }
    }

    poner(div, textarea.value);
    vacio();
    barra.addEventListener('mousedown', e => e.preventDefault());       // no quitar el cursor del texto
    barra.addEventListener('click', e => {
      const b = e.target.closest('.btn-formato'); if (!b) return;
      if (!ed.enfocado()) div.focus();
      document.execCommand(b.dataset.cmd);
      if (b.dataset.cmd === 'removeFormat') {                      // removeFormat no siempre quita <u>
        if (document.queryCommandState('underline')) document.execCommand('underline');
      }
      estados();
      div.dispatchEvent(new Event('input', { bubbles: true }));
    });
    // Enter: salto de línea simple (no párrafos en <div>), igual que en el texto de siempre
    div.addEventListener('beforeinput', e => {
      if (e.inputType === 'insertParagraph') { e.preventDefault(); document.execCommand('insertLineBreak'); }
      if (e.inputType === 'formatBold' || e.inputType === 'formatItalic' || e.inputType === 'formatUnderline') setTimeout(estados);
    });
    // Pegar: solo el texto (sin colores, tipos de letra ni tablas de Word o páginas web)
    div.addEventListener('paste', e => {
      e.preventDefault();
      const t = (e.clipboardData || window.clipboardData).getData('text/plain').replace(/\r\n?/g, '\n');
      ed.insertar(t);
    });
    div.addEventListener('drop', e => e.preventDefault());
    div.addEventListener('input', () => {
      // Si el navegador metió párrafos o estilos sueltos, se rearma limpio y el cursor queda donde estaba
      if (div.querySelector('div, p, span, font, strong, em, a, img, table, ul, ol, h1, h2, h3')) {
        const s = ed.enfocado() ? ed.sel() : null;
        poner(div, valor(div));
        if (s) ed.seleccionar(s.ini, s.fin);
      }
      sincronizar(); vacio();
    });
    document.addEventListener('selectionchange', () => { if (ed.enfocado()) estados(); });
    div.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && ['b', 'i', 'u'].includes(e.key.toLowerCase())) setTimeout(estados);
    });
    return ed;
  }

  window.EditorRico = { desdeTextarea: crear };
  // Los textareas marcados con data-editor-rico (ej. «Corregir error en el informe») lo usan solos
  document.querySelectorAll('textarea[data-editor-rico]').forEach(crear);
})();
