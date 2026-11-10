"""Formato del texto del informe: negrita, cursiva y subrayado.

El informe se guarda como texto simple; el formato va marcado solo con <b>, <i> y <u>. Cualquier otra cosa
parecida a HTML es texto común (ej. "< 5 mm"). Estas funciones lo muestran en páginas y en el PDF siempre
con las marcas bien cerradas, aunque el texto venga desordenado.
"""
import re
from xml.sax.saxutils import escape

from markupsafe import Markup

RE_MARCA = re.compile(r"(</?[biuBIU]>)")
ORDEN = "biu"


def trozos(texto):
    """[(texto, 'bi'), ...]: el texto sin marcas, con el formato que tiene cada trozo."""
    activos = {e: 0 for e in ORDEN}
    out = []
    for parte in RE_MARCA.split(texto or ""):
        if not parte:
            continue
        m = re.fullmatch(r"<(/?)([biu])>", parte, re.I)
        if m:
            e = m.group(2).lower()
            activos[e] = max(0, activos[e] + (-1 if m.group(1) else 1))
            continue
        lineas = parte.split("\n")
        for k, linea in enumerate(lineas):
            if k:                              # cada línea parte sin formato (una marca sin cerrar no sigue de largo)
                out.append(("\n", ""))
                activos = {e: 0 for e in ORDEN}
            if linea:
                out.append((linea, "".join(e for e in ORDEN if activos[e])))
    return out


def sin_marcas(texto):
    return RE_MARCA.sub("", texto or "")


def _envolver(t, fmt):
    for e in reversed(ORDEN):
        if e in fmt:
            t = f"<{e}>{t}</{e}>"
    return t


def a_html(texto):
    """Texto del informe listo para una página (el salto de línea lo respeta white-space: pre-wrap)."""
    return Markup("".join(_envolver(str(Markup.escape(t)), f) for t, f in trozos(texto)))


def lineas_pdf(texto):
    """Lista de (texto plano de la línea, línea con <b>/<i>/<u> para reportlab, escapada y bien cerrada)."""
    lineas, plano, marcado = [], "", ""
    for t, f in trozos(texto):
        partes = t.split("\n")
        for k, p in enumerate(partes):
            if k:
                lineas.append((plano, marcado))
                plano, marcado = "", ""
            if p:
                plano += p
                marcado += _envolver(escape(p), f)
    lineas.append((plano, marcado))
    return lineas
