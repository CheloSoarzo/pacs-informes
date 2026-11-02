"""Prueba: un TM o una transcripcionista corrige un informe validado y vuelve a quedar validado por el mismo médico.
Requiere el PACS simulado y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva)."""
import io
import re

import requests
from pypdf import PdfReader

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
                ("trans", "Carla Muñoz", "Transcripcionistas"), ("sec", "Ana Secretaría", "Secretarias de Recepción")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
S = {u: sesion(u) for u in ["rad", "tec", "trans", "sec"]}
rid = re.search(r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-ct").text).group(1)
S["tec"].post(B + "/estudio/est-ct/asignar", data={"csrf": S["tec"].csrf, "radiologo_id": rid, "prioridad": "normal"})
S["rad"].post(B + "/estudio/est-ct/firmar", headers={"X-CSRF": S["rad"].csrf},
              json={"cuerpo": "HALLAZGOS:\nLesión en riñón derecho.\n\nIMPRESIÓN:\nQuiste renal derecho."})
t = S["tec"].get(B + "/estudio/est-ct").text
chk("Corregir error en el informe" in t, "el TM ve «Corregir error en el informe» en un informe validado")
chk("Corregir error en el informe" not in S["sec"].get(B + "/estudio/est-ct").text, "la secretaria no lo ve")
inf_id = re.search(r"/informe/(\d+)\.pdf", t).group(1)
link = adm.post(B + f"/informe/{inf_id}/link", headers={"X-CSRF": adm.csrf}).json()


def corregir(s, cuerpo, motivo="lateralidad"):
    return s.post(B + "/estudio/est-ct/corregir", data={"csrf": s.csrf, "cuerpo": cuerpo, "motivo": motivo})


chk(corregir(S["sec"], "x" * 20).status_code == 403, "la secretaria no puede corregir")
chk("Indica qué se corrigió" in corregir(S["tec"], "HALLAZGOS:\nLesión en riñón izquierdo.", motivo="").text, "exige el motivo")
r = corregir(S["tec"], "HALLAZGOS:\nLesión en riñón izquierdo.\n\nIMPRESIÓN:\nQuiste renal izquierdo.")
chk("sigue validado por Dr. Andrés López" in r.text and "versión 2" in r.text, "el TM corrige: sigue validado por el médico, versión 2")
t = adm.get(B + "/estudio/est-ct").text
chk("Validado por <b>Dr. Andrés López</b>" in t and "versión 2" in t and "izquierdo" in t, "el estudio muestra la versión corregida")
chk("CORRECCIÓN: lateralidad" in t, "la versión anterior y el motivo quedan en el historial")
chk("corregir_validado" in adm.get(B + "/admin/auditoria").text, "queda en la auditoría")
pac = requests.Session()
if link.get("clave"):
    pac.post(link["url"], data={"clave": link["clave"]})
chk("izquierdo" in pac.get(link["url"]).text, "el paciente ve la corrección con el mismo link")
txt = PdfReader(io.BytesIO(pac.get(link["url"] + "/informe.pdf").content)).pages[0].extract_text()
chk("izquierdo" in txt and "Dr. Andrés López" in txt and "Versión 2" in txt, "el PDF sale corregido, firmado por el médico, versión 2")
r = corregir(S["trans"], "HALLAZGOS:\nLesión en riñón izquierdo.\n\nIMPRESIÓN:\nQuiste renal simple izquierdo.", "error de tipeo")
chk("versión 3" in r.text, "la transcripcionista también puede corregir (versión 3)")
chk(S["rad"].get(B + "/estudio/est-ct").status_code == 200 and
    "Validado por <b>Dr. Andrés López</b>" in S["rad"].get(B + "/estudio/est-ct").text, "el médico sigue como firmante")
print("\nFALLAS:", fallas)
