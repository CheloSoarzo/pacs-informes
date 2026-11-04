"""Prueba: médico solicitante con acceso a un examen dado desde la ficha del examen (lista desplegable).
Requiere el PACS simulado y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva)."""
import re

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
t = adm.get(B + "/admin/usuarios").text
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', t))
chk("Médicos solicitantes" in perf and "Médicos de Consultas" not in perf, "el perfil se llama «Médicos solicitantes»")
r = adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": "drsol", "nombre": "Dra. Carolina Rojas",
                                          "perfil_id": perf["Médicos solicitantes"], "clave": "clave-larga-123", "activo": "on"})
chk("Dra. Carolina Rojas" in adm.get(B + "/admin/usuarios").text, "se crea el médico sin tener que escribir su nombre DICOM")
for u, n, p in [("rad", "Dr. Andrés López", "Radiólogos"), ("tec", "TM Daniela", "Tecnólogos"),
                ("sec", "Ana Secretaría", "Secretarias de Recepción"), ("trans", "Carla Muñoz", "Transcripcionistas")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
S = {u: sesion(u) for u in ["rad", "tec", "sec", "trans", "drsol"]}

chk("est-ct" not in S["drsol"].get(B + "/?desde=2000-01-01").text, "sin acceso, el médico no ve el examen")
chk(S["drsol"].get(B + "/estudio/est-ct").status_code == 403, "ni puede abrirlo")
t = S["tec"].get(B + "/estudio/est-ct").text
chk("Médicos solicitantes" in t and "Médicos del centro" in t and "Dra. Carolina Rojas" in t, "el TM ve la lista de médicos en el examen")
mid = re.search(r'<option value="(\d+)"[^>]*>Dra\. Carolina Rojas</option>', t)
chk(mid and "selected" in mid.group(0), "sugiere al médico cuyo nombre es el solicitante del examen (ROJAS^CAROLINA)")
mid = mid.group(1)
chk(S["trans"].post(B + "/estudio/est-ct/acceso-medico", data={"csrf": S["trans"].csrf, "usuario_id": mid}).status_code == 403,
    "la transcripcionista no puede dar acceso")
r = S["tec"].post(B + "/estudio/est-ct/acceso-medico", data={"csrf": S["tec"].csrf, "usuario_id": mid, "accion": "agregar"})
chk("ya puede ver este examen" in r.text, "el TM le da acceso")

lista = S["drsol"].get(B + "/?desde=2000-01-01").text
chk("/estudio/est-ct" in lista and "/estudio/est-us" not in lista and "/estudio/est-rx" not in lista,
    "el médico ve solo ese examen en su lista")
chk("/estudio/est-ct" in S["drsol"].get(B + "/").text, "también sin filtro de fechas")
chk(S["drsol"].get(B + "/estudio/est-ct").status_code == 200, "puede abrir el examen")
chk(S["drsol"].get(B + "/estudio/est-us").status_code == 403, "otro examen: no")
uid = re.search(r"StudyInstanceUIDs=([0-9.]+)", S["drsol"].get(B + "/estudio/est-ct").text).group(1)
pa = S["drsol"].get(B + "/api/auth/pacs", headers={"X-Forwarded-Method": "GET", "X-Forwarded-Uri": f"/dicom-web/studies/{uid}/series"})
chk(pa.status_code == 200, "puede ver las imágenes en el visor")
S["rad"].post(B + "/estudio/est-ct/firmar", headers={"X-CSRF": S["rad"].csrf},
              json={"cuerpo": "HALLAZGOS:\nSin alteraciones.\n\nIMPRESIÓN:\nNormal."})
adm.post(B + "/estudio/est-ct/asignar", data={"csrf": adm.csrf, "radiologo_id": re.search(
    r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-ct").text).group(1), "prioridad": "normal"})
S["rad"].post(B + "/estudio/est-ct/firmar", headers={"X-CSRF": S["rad"].csrf},
              json={"cuerpo": "HALLAZGOS:\nSin alteraciones.\n\nIMPRESIÓN:\nNormal."})
t = S["drsol"].get(B + "/estudio/est-ct").text
inf = re.search(r"/informe/(\d+)\.pdf", t)
chk(inf and S["drsol"].get(B + inf.group(0)).content[:4] == b"%PDF", "ve el informe validado en PDF")
chk('name="usuario_id"' not in t, "el médico no ve los controles para dar acceso")
chk("dar_acceso_medico" in adm.get(B + "/admin/auditoria").text, "queda en la auditoría")

r = S["sec"].post(B + "/estudio/est-ct/acceso-medico", data={"csrf": S["sec"].csrf, "usuario_id": mid, "accion": "quitar"})
chk("ya no puede ver" in r.text, "recepción puede quitar el acceso")
chk(S["drsol"].get(B + "/estudio/est-ct").status_code == 403, "sin acceso de nuevo")

# --- Otro médico escrito a mano (sin acceso)
t = S["tec"].get(B + "/estudio/est-us").text
chk('value="otro"' in t and "Médicos del centro" in t, "la lista trae «Médicos del centro» y «Otro médico (escribir)»")
r = S["tec"].post(B + "/estudio/est-us/acceso-medico", data={"csrf": S["tec"].csrf, "accion": "otro", "nombre_otro": "dr. juan  pérez soto"})
chk("guardado (sin acceso" in r.text and "DR. JUAN PÉREZ SOTO" in r.text and "sin acceso" in r.text, "se guarda el médico escrito, sin acceso")
chk("<span>Solicitante</span>DR. JUAN PÉREZ SOTO" in S["tec"].get(B + "/estudio/est-us").text,
    "queda como médico solicitante del examen")
S["rad"].post(B + "/estudio/est-us/guardar", headers={"X-CSRF": S["rad"].csrf}, json={"cuerpo": "HALLAZGOS:\nNormal."})
adm.post(B + "/estudio/est-us/asignar", data={"csrf": adm.csrf, "radiologo_id": re.search(
    r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-us").text).group(1), "prioridad": "normal"})
S["rad"].post(B + "/estudio/est-us/firmar", headers={"X-CSRF": S["rad"].csrf},
              json={"cuerpo": "HALLAZGOS:\nNormal.\n\nIMPRESIÓN:\nNormal."})
import io
from pypdf import PdfReader
inf2 = re.search(r"/informe/(\d+)\.pdf", adm.get(B + "/estudio/est-us").text).group(0)
chk("JUAN PÉREZ SOTO" in PdfReader(io.BytesIO(adm.get(B + inf2).content)).pages[0].extract_text(), "sale en el PDF del informe")
chk(S["drsol"].get(B + "/estudio/est-us").status_code == 403, "el médico escrito no da acceso a nadie")
r = S["tec"].post(B + "/estudio/est-us/acceso-medico", data={"csrf": S["tec"].csrf, "accion": "quitar_otro"})
chk("Se quitó" in r.text and "ya validado no cambia" in r.text, "se puede quitar (el informe validado no cambia)")
chk("Escribe el nombre" in S["tec"].post(B + "/estudio/est-rx/acceso-medico", data={"csrf": S["tec"].csrf, "accion": "otro", "nombre_otro": "x"}).text,
    "exige escribir un nombre")
print("\nFALLAS:", fallas)
