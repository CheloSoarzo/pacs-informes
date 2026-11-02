"""Prueba: los tecnólogos asignan exámenes y cada radiólogo solo ve los que tiene asignados.
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


def filas(s):
    return s.get(B + "/?desde=2000-01-01").text.count('class="paciente"')


adm = sesion("admin", "clave-prueba-123")
t = adm.get(B + "/admin/perfiles").text
chk('name="solo_asignados"' in t, "perfiles: casilla «Solo ve los estudios que tiene asignados»")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
for u, n, p in [("rad1", "Dr. Uno", "Radiólogos"), ("rad2", "Dra. Dos", "Radiólogos"), ("tec", "TM Tres", "Tecnólogos"),
                ("trans", "Transcriptora", "Transcripcionistas")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
S = {u: sesion(u) for u in ["rad1", "rad2", "tec", "trans"]}
total = filas(adm)
chk(total >= 3, f"admin ve todos los estudios ({total})")
chk(filas(S["rad1"]) == 0 and filas(S["rad2"]) == 0, "radiólogos sin asignaciones no ven estudios")
chk(filas(S["trans"]) == total, "transcripcionista ve todos (no cambia)")
chk(S["rad1"].get(B + "/estudio/est-ct").status_code == 403, "radiólogo no abre un estudio que no tiene asignado")

# El tecnólogo asigna desde el estudio
t = S["tec"].get(B + "/estudio/est-ct").text
rid1 = re.search(r'<option value="(\d+)"[^>]*>Dr\. Uno</option>', t)
chk(rid1 is not None, "tecnólogo ve el selector de radiólogo en el estudio")
rid1 = rid1.group(1)
rid2 = re.search(r'<option value="(\d+)"[^>]*>Dra\. Dos</option>', t).group(1)
r = S["tec"].post(B + "/estudio/est-ct/asignar", data={"csrf": S["tec"].csrf, "radiologo_id": rid1, "prioridad": "urgente"})
chk("Asignación actualizada" in r.text, "tecnólogo asigna el TC a Dr. Uno (urgente)")
# y en lote desde la lista
r = S["tec"].post(B + "/estudios/acciones", data={"csrf": S["tec"].csrf, "accion": "asignar", "ids": ["est-us", "est-rx"],
                                                  "radiologo_id": rid2, "volver": ""})
chk(r.status_code == 200 and "asignados" in r.text, "tecnólogo asigna 2 estudios en lote a Dra. Dos")

chk(filas(S["rad1"]) == 1 and "JUAN ANDRES" in S["rad1"].get(B + "/?desde=2000-01-01").text, "Dr. Uno ve solo su TC")
chk(filas(S["rad2"]) == 2, "Dra. Dos ve solo sus 2 estudios")
chk(S["rad1"].get(B + "/estudio/est-ct").status_code == 200, "Dr. Uno abre su estudio")
chk(S["rad1"].get(B + "/estudio/est-us").status_code == 403, "Dr. Uno no abre el de Dra. Dos")
t = S["rad1"].get(B + "/estudio/est-ct").text
chk("Tomar este estudio" not in t, "radiólogo ya no tiene «Tomar este estudio»")
chk(S["rad2"].post(B + "/estudio/est-ct/tomar", data={"csrf": S["rad2"].csrf}).status_code == 403,
    "radiólogo no puede tomarse un estudio por su cuenta")
chk('class="paciente"' not in S["rad1"].get(B + "/?q=GONZALEZ").text, "la búsqueda tampoco muestra estudios ajenos")

# Visor: solo sus estudios
def visor(s, uid):
    return s.get(B + "/api/auth/pacs", headers={"X-Forwarded-Method": "GET",
                                               "X-Forwarded-Uri": f"/dicom-web/studies/{uid}/series"}).status_code
chk(visor(S["rad1"], "1.2.826.0.1.1") == 200, "visor: Dr. Uno abre las imágenes de su TC")
chk(visor(S["rad1"], "1.2.826.0.1.2") == 403, "visor: Dr. Uno no abre imágenes ajenas")

# Dictados: cada radiólogo dicta en lo suyo; la cola de la transcripcionista ve todo
r = S["rad1"].post(B + "/estudio/est-ct/dictados", files={"audio": ("d.webm", b"\x1aE\xdf\xa3" + b"0" * 3000, "audio/webm")},
                   data={"duracion": "10"}, headers={"X-CSRF": S["rad1"].csrf})
chk(r.status_code == 200, "Dr. Uno dicta en su estudio")
chk(S["rad2"].post(B + "/estudio/est-ct/dictados", files={"audio": ("d.webm", b"x" * 100, "audio/webm")},
                   headers={"X-CSRF": S["rad2"].csrf}).status_code in (403, 404), "Dra. Dos no puede dictar en un estudio ajeno")
chk("JUAN ANDRES" in S["trans"].get(B + "/dictados").text.upper(), "la transcripcionista ve el dictado en su cola")

# Reasignar: el estudio pasa al otro radiólogo
S["tec"].post(B + "/estudio/est-ct/asignar", data={"csrf": S["tec"].csrf, "radiologo_id": rid2, "prioridad": "normal"})
chk(filas(S["rad1"]) == 0 and filas(S["rad2"]) == 3, "al reasignar, el estudio pasa a Dra. Dos")

# El administrador puede quitar la restricción a un perfil
pid = perf["Radiólogos"]
t = adm.get(B + "/admin/perfiles").text
form = next(f for f in t.split("<form") if f'name="id" value="{pid}"' in f)
chk('name="solo_asignados" checked' in form, "el perfil Radiólogos viene con la casilla marcada")
permisos = re.findall(r'name="permisos" value="([^"]+)" checked', form)
adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "id": pid, "nombre": "Radiólogos", "permisos": permisos})
chk(filas(sesion("rad1")) == total, "sin la casilla, el radiólogo vuelve a ver todo")
print("\nFALLAS:", fallas)
