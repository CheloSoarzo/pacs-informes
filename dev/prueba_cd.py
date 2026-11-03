"""Prueba: CD/DVD del paciente con visor. Requiere el PACS simulado y la app en localhost:8000 con
ADMIN_PASSWORD=clave-prueba-123 (base nueva). Sin xorriso en el equipo, el disco sale como .zip.

    python dev/prueba_cd.py [carpeta_donde_dejar_el_disco]
"""
import io
import json
import re
import sys
import time
import zipfile

import requests

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
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
for u, n, p in [("rad", "Dr. Andrés López", "Radiólogos"), ("tec", "TM Daniela", "Tecnólogos"),
                ("sec", "Ana Secretaría", "Secretarias de Recepción"), ("trans", "Carla Muñoz", "Transcripcionistas")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
S = {u: sesion(u) for u in ["rad", "tec", "sec", "trans"]}
rid = re.search(r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-ct").text).group(1)
adm.post(B + "/estudio/est-ct/asignar", data={"csrf": adm.csrf, "radiologo_id": rid, "prioridad": "normal"})
S["rad"].post(B + "/estudio/est-ct/firmar", headers={"X-CSRF": S["rad"].csrf},
              json={"cuerpo": "HALLAZGOS:\nSin alteraciones.\n\nIMPRESIÓN:\nNormal."})

t = S["tec"].get(B + "/?desde=2000-01-01").text
chk('data-cd="/estudio/est-ct/cd"' in t and 'id="dlg-cd"' in t, "el TM ve el botón «Grabar CD» en la lista")
chk("data-cd" in S["sec"].get(B + "/?desde=2000-01-01").text, "recepción también lo ve")
chk("data-cd" not in S["trans"].get(B + "/?desde=2000-01-01").text, "la transcripcionista no lo ve")
chk('data-cd="/estudio/est-ct/cd"' in S["tec"].get(B + "/estudio/est-ct").text, "también está en la ficha del estudio")

info = S["tec"].get(B + "/estudio/est-ct/cd").json()
chk(info["medio"] == "CD" and info["informe"] is True and info["imagenes"] > 0, f"tamaño y medio ({info['mb']} MB, {info['medio']})")
chk(S["trans"].get(B + "/estudio/est-ct/cd").status_code == 403, "sin permiso: 403")

r = S["tec"].post(B + "/estudio/est-ct/cd/preparar", headers={"X-CSRF": S["tec"].csrf}, json={"entrega": True})
chk(r.ok and "estado" in r.json(), "empieza a preparar el disco")
t = r.json()
for _ in range(60):
    e = S["tec"].get(B + t["estado"]).json()
    if e["listo"] or e["error"]:
        break
    time.sleep(0.5)
chk(e["listo"] and not e["error"], f"disco listo ({e.get('mb')} MB) {e.get('error', '')}")
chk(S["sec"].get(B + t["descargar"]).status_code == 403, "otro usuario no puede descargar ese disco")
d = S["tec"].get(B + t["descargar"])
chk(d.ok and "attachment" in d.headers.get("Content-Disposition", ""), "se descarga el disco")
z = zipfile.ZipFile(io.BytesIO(d.content))
nombres = z.namelist()
chk("DICOMDIR" in nombres and any(n.startswith("IMAGES/") for n in nombres), "trae DICOMDIR e imágenes DICOM")
chk("ABRIR_VISOR.HTML" in nombres and "VISOR/DATOS.JS" in nombres, "trae el visor")
datos = json.loads(z.read("VISOR/DATOS.JS").decode().split("=", 1)[1].rstrip(";"))
jpgs = [n for n in nombres if n.endswith(".JPG")]
chk(datos["series"] and len(jpgs) == sum(len(s["imagenes"]) for s in datos["series"]), f"imágenes del visor ({len(jpgs)})")
chk("INFORME.PDF" in nombres and z.read("INFORME.PDF")[:4] == b"%PDF", "trae el informe validado en PDF")
chk("LEAME.TXT" in nombres and "ABRIR_VISOR" in z.read("LEAME.TXT").decode("utf-8-sig"), "trae las instrucciones")
chk("CD grabado" in adm.get(B + "/estudio/est-ct").text, "queda registrada la entrega en CD")
chk("grabar_cd" in adm.get(B + "/admin/auditoria").text, "queda en la auditoría")
chk(S["tec"].get(B + "/cd/xxxxxxxxxxxxxxxxxxxxxxxx/estado").status_code == 404, "token desconocido: 404")
if len(sys.argv) > 1:
    z.extractall(sys.argv[1])
print("\nFALLAS:", fallas)
