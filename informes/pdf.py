"""Generación del PDF del informe."""
import io
import os
import re

import formato
from xml.sax.saxutils import escape

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as canvas_mod
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

AZUL = colors.HexColor("#1F3A5F")
ACENTO = colors.HexColor("#2F7DB8")        # azul más claro: detalles y subtítulos
GRIS = colors.HexColor("#5B6573")
LINEA = colors.HexColor("#C9D1DB")
FONDO = colors.HexColor("#F3F6F9")
FONDO_CONCL = colors.HexColor("#EAF2FA")   # recuadro de la impresión diagnóstica

ESTILOS = {
    "centro": ParagraphStyle("centro", fontName="Helvetica-Bold", fontSize=13, textColor=AZUL, leading=16),
    "contacto": ParagraphStyle("contacto", fontName="Helvetica", fontSize=8, textColor=GRIS, leading=10),
    "tipo_doc": ParagraphStyle("tipo_doc", fontName="Helvetica-Bold", fontSize=8, textColor=ACENTO, leading=10,
                               alignment=TA_RIGHT),
    "tipo_doc2": ParagraphStyle("tipo_doc2", fontName="Helvetica", fontSize=8, textColor=GRIS, leading=10,
                                alignment=TA_RIGHT),
    "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=12.5, textColor=colors.white,
                             leading=15),
    "titulo_pag": ParagraphStyle("titulo_pag", fontName="Helvetica-Bold", fontSize=12, textColor=AZUL,
                                 leading=15, spaceBefore=4, spaceAfter=14),
    "etq": ParagraphStyle("etq", fontName="Helvetica", fontSize=7, textColor=GRIS, leading=9),
    "val": ParagraphStyle("val", fontName="Helvetica-Bold", fontSize=9.5, textColor=colors.HexColor("#1B2430"),
                          leading=12),
    "seccion": ParagraphStyle("seccion", fontName="Helvetica-Bold", fontSize=9.5, textColor=ACENTO,
                              leading=12),
    "texto": ParagraphStyle("texto", fontName="Helvetica", fontSize=10, leading=14.5, alignment=TA_LEFT,
                            textColor=colors.HexColor("#1B2430")),
    "firma": ParagraphStyle("firma", fontName="Helvetica", fontSize=9, leading=12, textColor=GRIS),
    "firma_b": ParagraphStyle("firma_b", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=AZUL),
    "pie": ParagraphStyle("pie", fontName="Helvetica", fontSize=7, textColor=GRIS, leading=9),
}

# Líneas como "HALLAZGOS:" o "IMPRESIÓN:" se muestran como subtítulo
RE_SECCION = re.compile(r"^[A-ZÁÉÍÓÚÑÜ0-9 /().,-]{3,60}:$")
# Subtítulos habituales aunque estén escritos en minúscula («Hallazgos:», «Impresión diagnóstica:»)
RE_SECCION_COMUN = re.compile(r"^(técnica|tecnica|antecedentes|indicación|indicacion|motivo( del examen)?|hallazgos?|"
                              r"impresión|impresion|conclusión|conclusion|conclusiones|diagnóstico|diagnostico|"
                              r"comentarios?|observaciones?|sugerencias?|recomendaciones?)( [a-záéíóúñ]+){0,2}:$", re.I)
# La impresión diagnóstica / conclusión va destacada en un recuadro
RE_CONCLUSION = re.compile(r"^(impresi[oó]n|conclusi[oó]n|conclusiones|diagn[oó]stico)", re.I)


def _subtitulo(marcado, ancho):
    """Subtítulo de sección con una barrita de color a la izquierda."""
    t = Table([["", Paragraph(marcado.strip().rstrip(":").upper(), ESTILOS["seccion"])]],
              colWidths=[1.2 * mm, ancho - 1.2 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), ACENTO),
                           ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
                           ("LEFTPADDING", (1, 0), (1, 0), 6),
                           ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    t.spaceBefore, t.spaceAfter = 9, 5
    t.keepWithNext = True          # nunca solo al final de una página
    return t


def _recuadro(flow, ancho):
    # Una fila por párrafo: si la conclusión es larga, el recuadro puede seguir en la página siguiente
    t = Table([[f] for f in flow], colWidths=[ancho])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FONDO_CONCL),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.2, AZUL),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    return t


def _cuerpo(texto, ancho=170 * mm):
    """Párrafos del informe. Respeta negrita/cursiva/subrayado (<b>, <i>, <u>) y los subtítulos tipo «HALLAZGOS:».
    La sección de impresión diagnóstica / conclusión va en un recuadro destacado."""
    flow, buf = [], []
    destino = flow                 # dentro de la conclusión, el texto se junta para el recuadro
    concl = None

    def cerrar():
        if buf:
            destino.append(Paragraph("<br/>".join(buf), ESTILOS["texto"]))
            buf.clear()

    def cerrar_concl():
        nonlocal concl, destino
        if concl is not None:
            while concl and isinstance(concl[-1], Spacer):
                concl.pop()
            if concl:
                flow.append(_recuadro(concl, ancho))
            concl, destino = None, flow

    for plano, marcado in formato.lineas_pdf((texto or "").strip()):
        if not plano.strip():                    # línea en blanco: separa párrafos
            if buf:
                cerrar()
                destino.append(Spacer(1, 6))
            continue
        if RE_SECCION.match(plano.strip()) or RE_SECCION_COMUN.match(plano.strip()):
            cerrar()
            cerrar_concl()
            flow.append(_subtitulo(marcado, ancho))
            if RE_CONCLUSION.match(plano.strip()):
                concl = []
                destino = concl
        else:
            buf.append(marcado.rstrip())
    cerrar()
    cerrar_concl()
    flow.append(Spacer(1, 4))
    return flow


def _qr(url, size=24 * mm):
    w = qr.QrCodeWidget(url)
    b = w.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    return d


def _canvas_numerado(dibujar):
    """Canvas que conoce el total de páginas («Página 1 de 2») y dibuja el pie al final."""
    class Lienzo(canvas_mod.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._paginas)
            for estado in self._paginas:
                self.__dict__.update(estado)
                dibujar(self, total)
                super().showPage()
            super().save()
    return Lienzo


def generar_pdf(informe, firmante, centro, url_verificacion=None, logo_path=None, clave_link=""):
    """informe: dict con datos del estudio y cuerpo. firmante: dict del radiólogo o None."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=14 * mm, bottomMargin=22 * mm,
        title=f"Informe {informe.get('paciente_nombre', '')}", author=centro["nombre"],
    )
    ancho = doc.width
    el = []

    # Encabezado: logo (o nombre del centro) a la izquierda; tipo de documento a la derecha
    contacto = " · ".join(x for x in (centro.get("direccion"), centro.get("telefono"), centro.get("email")) if x)
    if logo_path and os.path.exists(logo_path):
        izq = Image(logo_path, width=46 * mm, height=20 * mm, kind="proportional")
        izq.hAlign = "LEFT"
        izq = [izq]
    else:
        izq = [Paragraph(escape(centro["nombre"]), ESTILOS["centro"])]
    der = [Paragraph("INFORME IMAGENOLÓGICO", ESTILOS["tipo_doc"])]
    if contacto:
        der.append(Paragraph(escape(contacto), ESTILOS["tipo_doc2"]))
    cab = Table([[izq, der]], colWidths=[ancho * 0.5, ancho * 0.5])
    cab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                             ("LINEBELOW", (0, 0), (-1, -1), 1.4, AZUL)]))
    el.append(cab)
    el.append(Spacer(1, 4 * mm))

    # Datos del paciente (sin número de examen: es interno del centro)
    def c(etq, val):
        return [Paragraph(etq, ESTILOS["etq"]), Paragraph(escape(val or "—"), ESTILOS["val"])]

    edad_sexo = " · ".join(x for x in (informe.get("edad"), {"F": "Femenino", "M": "Masculino"}.get(
        (informe.get("paciente_sexo") or "").upper(), informe.get("paciente_sexo"))) if x)
    filas = [
        [c("PACIENTE", informe.get("paciente_nombre")), c("RUT / ID", informe.get("paciente_id")),
         c("EDAD · SEXO", edad_sexo)],
        [c("FECHA DEL EXAMEN", informe.get("fecha")), c("MODALIDAD", informe.get("modalidad")),
         c("MÉDICO SOLICITANTE", informe.get("solicitante"))],
    ]
    t = Table(filas, colWidths=[ancho * 0.42, ancho * 0.24, ancho * 0.34])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), FONDO),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINEA),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    el.append(t)
    el.append(Spacer(1, 5 * mm))

    # Título del examen en una franja de color
    titulo = informe.get("descripcion") or "Informe imagenológico"
    franja = Table([[Paragraph(escape(titulo.upper()), ESTILOS["titulo"])]], colWidths=[ancho])
    franja.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AZUL),
                                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    el.append(franja)
    el.append(Spacer(1, 2 * mm))
    el.extend(_cuerpo(informe.get("cuerpo", ""), ancho))
    el.append(Spacer(1, 10 * mm))

    # Firma + QR
    bloque_firma = []
    if firmante:
        if firmante.get("firma_png") and os.path.exists(firmante["firma_png"]):
            bloque_firma.append(Image(firmante["firma_png"], width=45 * mm, height=18 * mm, kind="proportional"))
        linea = Table([[""]], colWidths=[70 * mm], rowHeights=[2])
        linea.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 0.8, AZUL),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        linea.hAlign = "LEFT"
        bloque_firma.append(linea)
        bloque_firma.append(Paragraph(escape(firmante.get("nombre", "")), ESTILOS["firma_b"]))
        if firmante.get("titulo"):
            bloque_firma.append(Paragraph(escape(firmante["titulo"]), ESTILOS["firma"]))
        if firmante.get("registro"):
            bloque_firma.append(Paragraph(escape(firmante["registro"]), ESTILOS["firma"]))
        validado = f"Informe validado electrónicamente el {informe.get('firmado_en_txt', '')}"
        if informe.get("version", 1) > 1:
            validado += f" · Versión {informe['version']} (rectifica versiones anteriores)"
        if informe.get("transcriptor_iniciales"):
            validado += f" · Transcripción: {informe['transcriptor_iniciales']}"
        bloque_firma.append(Spacer(1, 3))
        bloque_firma.append(Paragraph(escape(validado), ESTILOS["pie"]))
    else:
        bloque_firma.append(Paragraph("<b>BORRADOR · informe no validado</b>", ESTILOS["firma"]))

    celdas = [[bloque_firma, ""]]
    if url_verificacion:
        txt_qr = "Escanee para ver su informe e imágenes en línea"
        if clave_link:
            txt_qr += f"<br/><b>Clave: {escape(clave_link)}</b>"
        celdas = [[bloque_firma, [_qr(url_verificacion), Paragraph(txt_qr, ESTILOS["pie"])]]]
    t = Table(celdas, colWidths=[ancho - 36 * mm, 36 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    el.append(KeepTogether(t))

    def pie(cv, total):
        cv.saveState()
        cv.setStrokeColor(LINEA)
        cv.setLineWidth(0.5)
        cv.line(20 * mm, 14 * mm, LETTER[0] - 20 * mm, 14 * mm)
        cv.setFont("Helvetica", 7)
        cv.setFillColor(GRIS)
        txt = f"{informe.get('paciente_nombre', '')} · {informe.get('paciente_id', '')} · {informe.get('fecha', '')}"
        cv.drawString(20 * mm, 10 * mm, txt)
        cv.drawCentredString(LETTER[0] / 2, 6.5 * mm, centro.get("nombre", ""))
        cv.drawRightString(LETTER[0] - 20 * mm, 10 * mm, f"Página {cv.getPageNumber()} de {total}")
        if not firmante:
            cv.setFont("Helvetica-Bold", 60)
            cv.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12))
            cv.translate(LETTER[0] / 2, LETTER[1] / 2)
            cv.rotate(35)
            cv.drawCentredString(0, 0, "BORRADOR")
        cv.restoreState()

    doc.build(el, canvasmaker=_canvas_numerado(pie))
    return buf.getvalue()


def pagina_acceso(informe, centro, url, clave="", logo_path=None):
    """Página final con el código QR de acceso, para agregar al informe subido ya firmado."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=20 * mm)
    el = []
    if logo_path and os.path.exists(logo_path):
        logo = Image(logo_path, width=46 * mm, height=20 * mm, kind="proportional")
        logo.hAlign = "LEFT"
        el.append(logo)
    else:
        el.append(Paragraph(escape(centro["nombre"]), ESTILOS["centro"]))
    el.append(Spacer(1, 10 * mm))
    el.append(Paragraph("ACCESO EN LÍNEA A SU EXAMEN", ESTILOS["titulo_pag"]))
    el.append(Paragraph(escape(f"{informe.get('paciente_nombre', '')} · {informe.get('paciente_id', '')} · "
                               f"{informe.get('descripcion') or ''} · {informe.get('fecha', '')}"), ESTILOS["val"]))
    el.append(Spacer(1, 8 * mm))
    filas = [[_qr(url, size=55 * mm),
              [Paragraph("Escanee el código con la cámara de su celular para ver su informe y sus imágenes, "
                         "o escriba esta dirección en el navegador:", ESTILOS["val"]),
               Spacer(1, 3 * mm), Paragraph(f"<font face='Courier'>{escape(url)}</font>", ESTILOS["val"]),
               Spacer(1, 4 * mm)] + ([Paragraph(f"<b>Clave de acceso: {escape(clave)}</b>", ESTILOS["firma_b"])]
                                     if clave else [])]]
    t = Table(filas, colWidths=[62 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 0.5, LINEA),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F6F9")),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    el.append(t)
    doc.build(el)
    return buf.getvalue()


def agregar_pagina(pdf_original, pdf_extra):
    """Une el informe subido con la página de acceso al final."""
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for p in PdfReader(io.BytesIO(pdf_original)).pages:
        w.add_page(p)
    for p in PdfReader(io.BytesIO(pdf_extra)).pages:
        w.add_page(p)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()
