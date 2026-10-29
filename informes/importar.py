"""Extrae el texto de un informe escrito fuera del sistema (Word, LibreOffice, PDF o texto) para cargarlo
en el editor. Después sigue el flujo normal: revisar, enviar a validación y firmar (el PDF lo genera el sistema).
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

FORMATOS = (".docx", ".odt", ".pdf", ".txt")
MAX_BYTES = 15 * 1024 * 1024

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


class ImportarError(Exception):
    pass


def extraer_texto(nombre, datos):
    ext = "." + nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if ext == ".doc":
        raise ImportarError("Los archivos .doc antiguos no se pueden leer. En Word usa "
                            "«Guardar como» → Documento de Word (.docx) o PDF, y súbelo de nuevo.")
    if ext not in FORMATOS:
        raise ImportarError("Formato no permitido. Sube un archivo Word (.docx), LibreOffice (.odt), PDF o .txt.")
    if len(datos) > MAX_BYTES:
        raise ImportarError("El archivo es demasiado grande (máximo 15 MB).")
    try:
        if ext == ".docx":
            texto = _docx(datos)
        elif ext == ".odt":
            texto = _odt(datos)
        elif ext == ".pdf":
            texto = _pdf(datos)
        else:
            texto = _txt(datos)
    except ImportarError:
        raise
    except Exception:  # archivo dañado o con formato inesperado
        raise ImportarError("No se pudo leer el archivo. Revisa que no esté dañado o protegido con clave.")
    texto = _limpiar(texto)
    if len(texto) < 10:
        if ext == ".pdf":
            raise ImportarError("El PDF no tiene texto (parece una imagen escaneada). Sube el Word original.")
        raise ImportarError("El archivo no tiene texto.")
    return texto


def _docx(datos):
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        raiz = ET.fromstring(z.read("word/document.xml"))
    cuerpo = raiz.find(W + "body")
    lineas = []
    for el in (cuerpo if cuerpo is not None else raiz):
        if el.tag == W + "p":
            lineas.append(_docx_parrafo(el))
        elif el.tag == W + "tbl":
            # Tablas: una línea por fila, celdas separadas por tabulación
            for fila in el.iter(W + "tr"):
                celdas = [" ".join(_docx_parrafo(p) for p in c.iter(W + "p")).strip() for c in fila.iter(W + "tc")]
                lineas.append("\t".join(celdas))
    return "\n".join(lineas)


def _docx_parrafo(p):
    partes = []
    for el in p.iter():
        if el.tag == W + "t":
            partes.append(el.text or "")
        elif el.tag == W + "tab":
            partes.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            partes.append("\n")
    texto = "".join(partes)
    ppr = p.find(W + "pPr")
    estilo = ""
    if ppr is not None and ppr.find(W + "pStyle") is not None:
        estilo = (ppr.find(W + "pStyle").get(W + "val") or "").lower()
    es_lista = ppr is not None and (ppr.find(W + "numPr") is not None or "list" in estilo or "vi" in estilo[:3])
    if texto.strip() and es_lista:
        texto = "- " + texto.lstrip()        # elemento de lista (viñeta o numeración)
    return texto


def _odt(datos):
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        raiz = ET.fromstring(z.read("content.xml"))

    def texto(el):
        out = [el.text or ""]
        for h in el:
            if h.tag == TEXT + "tab":
                out.append("\t")
            elif h.tag == TEXT + "line-break":
                out.append("\n")
            elif h.tag == TEXT + "s":
                out.append(" " * int(h.get(TEXT + "c", "1")))
            else:
                out.append(texto(h))
            out.append(h.tail or "")
        return "".join(out)

    return "\n".join(texto(p) for p in raiz.iter() if p.tag in (TEXT + "p", TEXT + "h"))


def _pdf(datos):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportarError("Este servidor no puede leer PDF todavía. Sube el informe en Word (.docx).")
    lector = PdfReader(io.BytesIO(datos))
    if lector.is_encrypted:
        raise ImportarError("El PDF está protegido con clave.")
    return "\n\n".join((pag.extract_text() or "") for pag in lector.pages)


def _txt(datos):
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(cod)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", "replace")


def _limpiar(t):
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").replace("​", "")
    lineas = [re.sub(r"[ \t]+$", "", l) for l in t.split("\n")]
    # Viñetas de PDF/Word (•, ▪, símbolos de fuentes Symbol/Wingdings) → "- "
    lineas = [re.sub(r"^\s*[•▪◦●■\uf0b7\uf0a7\uf076\uf0d8]\s*", "- ", l) for l in lineas]
    t = "\n".join(lineas)
    t = re.sub(r"\n{3,}", "\n\n", t)   # como máximo una línea en blanco seguida
    return t.strip()
