"""Generación del PDF del informe."""
import io
import os
import re
from xml.sax.saxutils import escape

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

AZUL = colors.HexColor("#1F3A5F")
GRIS = colors.HexColor("#5B6573")
LINEA = colors.HexColor("#C9D1DB")

ESTILOS = {
    "centro": ParagraphStyle("centro", fontName="Helvetica-Bold", fontSize=13, textColor=AZUL, leading=16),
    "contacto": ParagraphStyle("contacto", fontName="Helvetica", fontSize=8, textColor=GRIS, leading=10),
    "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=12, textColor=AZUL,
                             leading=15, spaceBefore=4, spaceAfter=6),
    "etq": ParagraphStyle("etq", fontName="Helvetica-Bold", fontSize=8, textColor=GRIS, leading=10),
    "val": ParagraphStyle("val", fontName="Helvetica", fontSize=9, leading=11),
    "seccion": ParagraphStyle("seccion", fontName="Helvetica-Bold", fontSize=10, textColor=AZUL,
                              leading=13, spaceBefore=8, spaceAfter=2),
    "texto": ParagraphStyle("texto", fontName="Helvetica", fontSize=10, leading=14, alignment=TA_LEFT),
    "firma": ParagraphStyle("firma", fontName="Helvetica", fontSize=9, leading=12),
    "firma_b": ParagraphStyle("firma_b", fontName="Helvetica-Bold", fontSize=10, leading=13),
    "pie": ParagraphStyle("pie", fontName="Helvetica", fontSize=7, textColor=GRIS, leading=9),
}

# Líneas como "HALLAZGOS:" o "IMPRESIÓN:" se muestran como subtítulo
RE_SECCION = re.compile(r"^[A-ZÁÉÍÓÚÑÜ0-9 /().,-]{3,60}:$")


def _cuerpo(texto):
    flow = []
    for bloque in re.split(r"\n\s*\n", (texto or "").strip()):
        lineas = [l.rstrip() for l in bloque.split("\n")]
        buf = []
        for l in lineas:
            if RE_SECCION.match(l.strip()):
                if buf:
                    flow.append(Paragraph("<br/>".join(buf), ESTILOS["texto"]))
                    buf = []
                flow.append(Paragraph(escape(l.strip()), ESTILOS["seccion"]))
            else:
                buf.append(escape(l))
        if buf:
            flow.append(Paragraph("<br/>".join(buf), ESTILOS["texto"]))
        flow.append(Spacer(1, 4))
    return flow


def _qr(url, size=24 * mm):
    w = qr.QrCodeWidget(url)
    b = w.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    return d


def generar_pdf(informe, firmante, centro, url_verificacion=None, logo_path=None):
    """informe: dict con datos del estudio y cuerpo. firmante: dict del radiólogo o None."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title=f"Informe {informe.get('paciente_nombre', '')}", author=centro["nombre"],
    )
    el = []

    # Encabezado
    contacto = " · ".join(x for x in (centro.get("direccion"), centro.get("telefono"), centro.get("email")) if x)
    cab = [Paragraph(escape(centro["nombre"]), ESTILOS["centro"]),
           Paragraph(escape(contacto), ESTILOS["contacto"])]
    if logo_path and os.path.exists(logo_path):
        t = Table([[Image(logo_path, width=28 * mm, height=14 * mm, kind="proportional"), cab]],
                  colWidths=[32 * mm, None])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        el.append(t)
    else:
        el.extend(cab)
    el.append(Spacer(1, 3 * mm))
    linea = Table([[""]], colWidths=[doc.width], rowHeights=[1])
    linea.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, AZUL)]))
    el.append(linea)
    el.append(Spacer(1, 3 * mm))

    # Datos del paciente
    def c(etq, val):
        return [Paragraph(etq, ESTILOS["etq"]), Paragraph(escape(val or "—"), ESTILOS["val"])]

    datos = [
        c("PACIENTE", informe.get("paciente_nombre")) + c("RUT / ID", informe.get("paciente_id")),
        c("EDAD", informe.get("edad")) + c("SEXO", informe.get("paciente_sexo")),
        c("FECHA EXAMEN", informe.get("fecha")) + c("N° EXAMEN", informe.get("accession")),
        c("MÉDICO SOLICITANTE", informe.get("solicitante")) + c("MODALIDAD", informe.get("modalidad")),
    ]
    t = Table(datos, colWidths=[30 * mm, 60 * mm, 26 * mm, None])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F6F9")),
        ("BOX", (0, 0), (-1, -1), 0.5, LINEA),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    el.append(t)
    el.append(Spacer(1, 5 * mm))

    titulo = informe.get("descripcion") or "Informe imagenológico"
    el.append(Paragraph(escape(titulo.upper()), ESTILOS["titulo"]))
    el.extend(_cuerpo(informe.get("cuerpo", "")))
    el.append(Spacer(1, 8 * mm))

    # Firma + QR
    bloque_firma = []
    if firmante:
        if firmante.get("firma_png") and os.path.exists(firmante["firma_png"]):
            bloque_firma.append(Image(firmante["firma_png"], width=45 * mm, height=18 * mm, kind="proportional"))
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
        bloque_firma.append(Spacer(1, 2))
        bloque_firma.append(Paragraph(escape(validado), ESTILOS["pie"]))
    else:
        bloque_firma.append(Paragraph("<b>BORRADOR · informe no validado</b>", ESTILOS["firma"]))

    celdas = [[bloque_firma, ""]]
    if url_verificacion:
        celdas = [[bloque_firma, [_qr(url_verificacion),
                                  Paragraph("Ver informe e imágenes en línea", ESTILOS["pie"])]]]
    t = Table(celdas, colWidths=[doc.width - 32 * mm, 32 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    el.append(KeepTogether(t))

    def pie(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRIS)
        txt = f"{informe.get('paciente_nombre', '')} · {informe.get('paciente_id', '')} · {informe.get('fecha', '')}"
        canvas.drawString(20 * mm, 10 * mm, txt)
        canvas.drawRightString(LETTER[0] - 20 * mm, 10 * mm, f"Página {d.page}")
        if not firmante:
            canvas.setFont("Helvetica-Bold", 60)
            canvas.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12))
            canvas.translate(LETTER[0] / 2, LETTER[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "BORRADOR")
        canvas.restoreState()

    doc.build(el, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
