"""Prueba: el informe dictado queda validado a nombre del médico que dictó cuando la transcripcionista termina.
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
for u, n, p, extra in [("rad", "Dr. Andrés López", "Radiólogos", {"titulo": "Médico Radiólogo", "registro": "RNPI 12345"}),
                       ("trans", "Carla Muñoz", "Transcripcionistas", {})]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on", **extra})
S = {u: sesion(u) for u in ["rad", "trans"]}
t = adm.get(B + "/admin/ajustes").text
chk('name="validar_al_transcribir" checked' in t, "ajuste «validar al transcribir» viene activado")

rid = re.search(r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-ct").text).group(1)
for eid in ("est-ct", "est-us"):
    adm.post(B + f"/estudio/{eid}/asignar", data={"csrf": adm.csrf, "radiologo_id": rid, "prioridad": "normal"})


def dictar(eid):
    return S["rad"].post(B + f"/estudio/{eid}/dictados", data={"duracion": "20"}, headers={"X-CSRF": S["rad"].csrf},
                         files={"audio": ("d.webm", b"\x1aE\xdf\xa3" + b"0" * 3000, "audio/webm")})


chk(dictar("est-ct").status_code == 200, "el médico dicta el TC")
t = S["trans"].get(B + "/estudio/est-ct").text
chk("Terminar y validar (Dr. Andrés López)" in t, "la transcripcionista ve «Terminar y validar (Dr. Andrés López)»")
r = S["trans"].post(B + "/estudio/est-ct/enviar-validacion", headers={"X-CSRF": S["trans"].csrf},
                    json={"cuerpo": "HALLAZGOS:\nSin alteraciones.\n\nIMPRESIÓN:\nNormal."})
j = r.json()
chk(r.ok and j.get("validado") and j.get("firmante") == "Dr. Andrés López", "al terminar, queda validado por el médico que dictó")
t = adm.get(B + "/estudio/est-ct").text
chk("Validado por <b>Dr. Andrés López</b>" in t and "transcrito por Carla Muñoz" in t,
    "el estudio muestra «Validado por Dr. Andrés López · transcrito por Carla Muñoz»")
inf = re.search(r'/informe/(\d+)\.pdf', t).group(1)
texto = "".join(p.extract_text() for p in PdfReader(io.BytesIO(adm.get(B + f"/informe/{inf}.pdf").content)).pages)
chk("Dr. Andrés López" in texto and "Médico Radiólogo" in texto and "RNPI 12345" in texto,
    "el PDF sale con el nombre, título y registro del médico")
chk("validado_al_transcribir" in adm.get(B + "/admin/auditoria").text, "queda registrado en la auditoría")
chk(S["rad"].get(B + "/estudio/est-ct").text.count("Rectificar informe") >= 1, "el médico puede rectificarlo si hay un error")

# Sin dictado: sigue el camino normal
t = S["trans"].get(B + "/estudio/est-us").text
chk("Enviar a validación" in t and "Terminar y validar" not in t, "sin dictado: botón normal «Enviar a validación»")
r = S["trans"].post(B + "/estudio/est-us/enviar-validacion", headers={"X-CSRF": S["trans"].csrf},
                    json={"cuerpo": "HALLAZGOS:\nHígado normal.\n\nIMPRESIÓN:\nNormal."})
chk(r.ok and not r.json().get("validado"), "sin dictado: queda «Por validar»")

# Con el ajuste desactivado vuelve el flujo anterior
cs = re.search(r'name="csrf" value="([^"]+)"', adm.get(B + "/admin/ajustes").text).group(1)
adm.post(B + "/admin/ajustes", data={"csrf": cs, "correo_asunto": "x", "correo_cuerpo": "y", "horas_alerta": "24",
                                     "horas_alerta_urgente": "2"})
adm2 = requests.Session()
adm2.post(B + "/login", data={"usuario": "admin", "clave": "clave-prueba-123"})
chk('name="validar_al_transcribir" checked' not in adm2.get(B + "/admin/ajustes").text, "se puede desactivar")
adm.post(B + "/estudio/est-rx/asignar", data={"csrf": adm.csrf, "radiologo_id": rid, "prioridad": "normal"})
dictar("est-rx")
t = S["trans"].get(B + "/estudio/est-rx").text
chk("Terminar y validar" not in t, "desactivado: la transcripcionista vuelve a «Enviar a validación»")
r = S["trans"].post(B + "/estudio/est-rx/enviar-validacion", headers={"X-CSRF": S["trans"].csrf},
                    json={"cuerpo": "HALLAZGOS:\nNormal.\n\nIMPRESIÓN:\nNormal."})
chk(r.ok and not r.json().get("validado"), "desactivado: queda «Por validar» para el médico")
print("\nFALLAS:", fallas)
