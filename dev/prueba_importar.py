"""Prueba: subir un informe escrito fuera del sistema (Word, LibreOffice, PDF, texto).

Requiere dev/mock_orthanc.py y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva).
"""
import io
import re
import zipfile

import requests
from reportlab.pdfgen import canvas

B = "http://localhost:8000"; fallas = 0
TEXTO = ["TC DE CEREBRO", "HALLAZGOS:", "Sin hemorragia ni efecto de masa.", "", "IMPRESIÓN:", "Estudio normal."]


def chk(c, m):
    global fallas; fallas += (not c); print(("OK   " if c else "FALLA ") + m)


def sesion(u, c):
    s = requests.Session(); s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text; m = re.search(r'name="csrf" value="([^"]+)"', t); s.csrf = m.group(1) if m else ""
    return s


def docx():
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ps = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{l}</w:t></w:r></w:p>" for l in TEXTO)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{ps}</w:body></w:document>')
    return b.getvalue()


def odt():
    T = 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ps = "".join(f"<text:p>{l}</text:p>" for l in TEXTO)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("content.xml", f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {T}><office:body><office:text>{ps}</office:text></office:body></office:document-content>')
    return b.getvalue()


def pdf(con_texto=True):
    b = io.BytesIO(); c = canvas.Canvas(b)
    if con_texto:
        y = 800
        for l in TEXTO:
            c.drawString(72, y, l); y -= 16
    else:
        c.rect(72, 600, 200, 100, fill=1)   # "escaneado": solo una imagen, sin texto
    c.showPage(); c.save(); return b.getvalue()


adm = sesion("admin", "clave-prueba-123")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
for u, p in (("trans1", "Transcripcionistas"), ("sec1", "Secretarias de Recepción")):
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": u, "perfil_id": perf[p],
                                         "clave": "clave-larga-123", "activo": "on"})
tr, sec = sesion("trans1", "clave-larga-123"), sesion("sec1", "clave-larga-123")
URL = B + "/estudio/est-ct/importar-informe"


def subir(s, nombre, datos):
    return s.post(URL, files={"archivo": (nombre, datos)}, headers={"X-CSRF": s.csrf})


chk("📄 Subir informe" in tr.get(B + "/estudio/est-ct").text, "transcripcionista ve el botón Subir informe")
for nombre, datos in (("informe.docx", docx()), ("informe.odt", odt()), ("informe.pdf", pdf()),
                      ("informe.txt", "\r\n".join(TEXTO).encode("cp1252"))):
    r = subir(tr, nombre, datos); t = r.json().get("texto", "") if r.ok else ""
    chk(r.ok and "HALLAZGOS:" in t and "IMPRESIÓN:" in t and "Sin hemorragia ni efecto de masa." in t,
        f"extrae texto de {nombre}")
r = subir(tr, "viejo.doc", b"\xd0\xcf\x11\xe0basura")
chk(r.status_code == 400 and ".docx" in r.json()["error"], ".doc antiguo: pide guardarlo como .docx")
r = subir(tr, "escaneo.pdf", pdf(False))
chk(r.status_code == 400 and "escaneada" in r.json()["error"], "PDF escaneado sin texto: avisa")
r = subir(tr, "virus.exe", b"MZ")
chk(r.status_code == 400 and "no permitido" in r.json()["error"], "formato no permitido")
r = subir(tr, "roto.docx", b"no es un zip")
chk(r.status_code == 400 and "dañado" in r.json()["error"], "archivo dañado")
chk(subir(sec, "informe.docx", docx()).status_code == 403, "secretaría no puede subir informes")
chk("importar_informe" in adm.get(B + "/admin/auditoria").text, "queda en la auditoría")

# Flujo completo: el texto subido se envía a validación como un informe normal
t = subir(tr, "informe.docx", docx()).json()["texto"]
r = tr.post(B + "/estudio/est-ct/enviar-validacion", json={"cuerpo": t}, headers={"X-CSRF": tr.csrf})
chk(r.ok, "el informe subido se envía a validación")
print("FALLAS:", fallas)
