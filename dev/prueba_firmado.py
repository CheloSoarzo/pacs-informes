"""Prueba: subir el informe final ya firmado (PDF o imagen). Requiere el PACS simulado y la app en
localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva)."""
import io
import re

import requests
from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas

B = "http://localhost:8000"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def sesion(u, c="clave-larga-123"):
    s = requests.Session()
    s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text
    m = re.search(r'name="csrf" value="([^"]+)"', t)
    s.csrf = m.group(1) if m else ""
    return s


adm = sesion("admin", "clave-prueba-123")

# Estas pruebas usan radiólogos sobre estudios sin asignar: se quita «solo ve lo asignado» al perfil Radiólogos
# (esa restricción la prueba dev/prueba_asignacion.py)
def _radiologos_ven_todo(adm):
    pid = re.search(r'<option value="(\d+)"[^>]*>Radiólogos</option>', adm.get(B + "/admin/usuarios").text).group(1)
    form = next(f for f in adm.get(B + "/admin/perfiles").text.split("<form") if f'name="id" value="{pid}"' in f)
    adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "id": pid, "nombre": "Radiólogos",
                                          "permisos": re.findall(r'name="permisos" value="([^"]+)" checked', form)})


_radiologos_ven_todo(adm)
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
for u, n, p in [("rad", "Dra. Paula Reyes", "Radiólogos"), ("trans", "Carla Muñoz", "Transcripcionistas"),
                ("tec", "TM Daniela", "Tecnólogos")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
S = {u: sesion(u) for u in ["rad", "trans", "tec"]}
t = S["trans"].get(B + "/estudio/est-ct").text
rid = re.search(r'<option value="(\d+)"[^>]*>Dra\. Paula Reyes</option>', t)
chk(rid is not None, "transcripcionista ve el formulario con la lista de radiólogos")
rid = rid.group(1)
chk("Subir informe firmado" in t and "subir-informe-firmado" in t, "botón «Subir informe firmado» en el editor")
chk('accept=".docx,.odt,.pdf,.txt"' not in t, "ya no está el botón que copiaba el texto al editor")
buf = io.BytesIO()
c = canvas.Canvas(buf)
c.drawString(100, 750, "INFORME TC CEREBRO - Firmado Dra. Reyes")
c.save()
PDF = buf.getvalue()


def subir(s, eid, nombre, datos, firmante=None):
    return s.post(B + f"/estudio/{eid}/subir-informe-firmado",
                  data={"csrf": s.csrf, "firmante_id": rid if firmante is None else firmante},
                  files={"archivo": (nombre, datos)})


chk(subir(S["tec"], "est-ct", "i.pdf", PDF).status_code == 403, "tecnólogo no puede subir informes firmados")
r = subir(S["trans"], "est-ct", "i.docx", b"PK\x03\x04xxxx")
chk("Guardar como → PDF" in r.text, "Word se rechaza con instrucciones para guardarlo en PDF")
r = subir(S["trans"], "est-ct", "i.pdf", PDF, firmante="")
chk("Elige el médico" in r.text, "exige elegir el médico que firmó")
r = subir(S["trans"], "est-ct", "informe.pdf", PDF)
chk("quedó validado" in r.text and "Dra. Paula Reyes" in r.text, "PDF subido: estudio validado a nombre del radiólogo")
t = S["rad"].get(B + "/estudio/est-ct").text
chk('class="pdf-informe"' in t, "el estudio muestra el PDF subido")
inf_id = re.search(r'/informe/(\d+)\.pdf', t).group(1)
def paginas(datos):
    r = PdfReader(io.BytesIO(datos))
    return [p.extract_text() or "" for p in r.pages]


pg = paginas(adm.get(B + f"/informe/{inf_id}.pdf").content)
chk(len(pg) == 2 and "Firmado Dra. Reyes" in pg[0] and "ACCESO EN L" in pg[1] and "Clave de acceso" in pg[1],
    "PDF oficial = informe firmado tal cual + página con QR y clave de acceso")
r = subir(S["trans"], "est-ct", "otro.pdf", PDF)
chk("ya está validado" in r.text, "no se puede subir encima de un informe validado")
j = adm.post(B + f"/informe/{inf_id}/link", headers={"X-CSRF": adm.csrf}).json()
pac = requests.Session()
if j.get("clave"):
    pac.post(j["url"], data={"clave": j["clave"]})
pub = pac.get(j["url"])
chk('class="pdf-informe"' in pub.text, "la página del paciente muestra el PDF")
pg = paginas(pac.get(j["url"] + "/informe.pdf").content)
chk(len(pg) == 2 and "Firmado Dra. Reyes" in pg[0] and "/r/" in pg[1], "el paciente descarga el informe firmado + su QR")
im = Image.new("RGB", (800, 1100), "white")
b2 = io.BytesIO()
im.save(b2, "JPEG")
r = subir(S["trans"], "est-us", "foto.jpg", b2.getvalue())
chk("quedó validado" in r.text, "foto JPG del informe firmado se acepta")
inf2 = re.search(r'/informe/(\d+)\.pdf', adm.get(B + "/estudio/est-us").text).group(1)
chk(adm.get(B + f"/informe/{inf2}.pdf").content[:4] == b"%PDF", "la foto quedó convertida a PDF")
# Informe escrito en el sistema: QR con la clave impresa
t = S["rad"].post(B + "/estudio/est-rx/firmar", json={"cuerpo": "HALLAZGOS:\nNormal.\n\nIMPRESIÓN:\nNormal."},
                  headers={"X-CSRF": S["rad"].csrf})
inf3 = re.search(r'/informe/(\d+)\.pdf', adm.get(B + "/estudio/est-rx").text).group(1)
pg = paginas(adm.get(B + f"/informe/{inf3}.pdf").content)
chk("imágenes en línea" in pg[-1] and "Clave:" in pg[-1], "informe escrito en el sistema trae QR con su clave")
r = S["rad"].post(B + "/estudio/est-ct/reabrir", data={"csrf": S["rad"].csrf, "motivo": "cambiar archivo"})
chk("Informe reabierto" in r.text, "radiólogo firmante puede rectificar")
t = S["trans"].get(B + "/estudio/est-ct").text
chk('id="cuerpo"' in t and "Subir informe firmado" in t, "tras rectificar se puede volver a subir o escribir")
r = subir(S["trans"], "est-ct", "nuevo.pdf", PDF)
chk("quedó validado" in r.text, "se sube la versión corregida")
print("\nFALLAS:", fallas)
