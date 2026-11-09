"""Prueba: borrar imágenes sueltas o series completas de un examen (vista rápida).

Requiere el PACS simulado y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva).
Variable DB: ruta de informes.db de esa app (para simular un informe validado)."""
import os
import re
import sqlite3
import sys

import requests

B, O = "http://localhost:8000", "http://localhost:8042"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def sesion(u, c):
    s = requests.Session()
    s.post(B + "/login", data={"usuario": u, "clave": c})
    s.csrf = re.search(r'name="csrf" value="([^"]+)"', s.get(B + "/cuenta").text).group(1)
    return s


def borrar(s, est, tipo, id_, motivo="imagen repetida", **extra):
    return s.post(B + f"/estudio/{est}/imagenes/borrar", headers={"X-CSRF": s.csrf},
                  json={"tipo": tipo, "id": id_, "motivo": motivo, **extra})


def series(s, est):
    return {x["id"]: x["instancias"] for x in s.get(B + f"/estudio/{est}/series").json()["series"]}


adm = sesion("admin", "clave-prueba-123")
t = adm.get(B + "/admin/usuarios").text
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', t))
for u, p in (("tm1", "Tecnólogos"), ("rad1", "Radiólogos")):
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": u.upper(), "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
tm, rad = sesion("tm1", "clave-larga-123"), sesion("rad1", "clave-larga-123")

sr = series(adm, "est-ct")
s1, s2 = list(sr)
chk(len(sr[s1]) == 40 and len(sr[s2]) == 24, "TC con 2 series (40 y 24 imágenes)")
t = adm.get(B + "/estudio/est-ct").text
chk("imagenes/borrar" in t, "el administrador tiene el borrado en la vista rápida")
chk("imagenes/borrar" not in rad.get(B + "/estudio/est-ct").text, "el radiólogo no lo ve")
r = borrar(rad, "est-ct", "imagen", sr[s1][5])
chk(r.status_code == 403, f"el radiólogo no puede borrar ({r.status_code})")

r = borrar(tm, "est-ct", "imagen", sr[s1][5])
chk(r.ok and r.json()["quedan"] == 63, "el TM borra una imagen (quedan 63)")
chk(sr[s1][5] not in series(adm, "est-ct")[s1], "la imagen ya no está en el PACS")
r = borrar(adm, "est-ct", "serie", s2, motivo="serie de otro paciente")
chk(r.ok and r.json()["borradas"] == 24 and s2 not in series(adm, "est-ct"), "el administrador borra la serie coronal completa")

chk(borrar(adm, "est-ct", "imagen", sr[s1][1], motivo="  ").status_code == 400, "sin motivo no se borra")
r = adm.post(B + "/estudio/est-ct/imagenes/borrar", json={"tipo": "imagen", "id": sr[s1][1], "motivo": "x"})
chk(r.status_code in (400, 403) and sr[s1][1] in series(adm, "est-ct")[s1], "sin CSRF no se borra")
otra = list(series(adm, "est-us").values())[0][0]
r = borrar(adm, "est-ct", "imagen", otra)
chk(r.status_code == 404 and otra in list(series(adm, "est-us").values())[0], "una imagen de otro examen no se borra desde este")

cr = series(adm, "est-rx")
pa, lat = list(cr)
chk(borrar(adm, "est-rx", "serie", pa).ok, "RX tórax: borrar la proyección PA")
r = borrar(adm, "est-rx", "imagen", cr[lat][0])
chk(r.status_code == 409 and "última imagen" in r.json()["error"], "no deja borrar la última imagen (para eso «Borrar examen»)")

# informe validado: pide confirmación extra
db = sqlite3.connect(os.environ["DB"])
vals = {"estudio_id": "est-us", "estado": "firmado", "creado": "2026-11-09 10:00:00", "actualizado": "2026-11-09 10:00:00"}
for c in db.execute("PRAGMA table_info(informes)"):      # columnas obligatorias sin valor por defecto
    if c[3] and c[4] is None and not c[5] and c[1] not in vals:
        vals[c[1]] = 0 if "INT" in (c[2] or "").upper() else "x"
cols = list(vals)
db.execute(f"INSERT INTO informes ({','.join(k for k in vals if k in cols)}) VALUES ({','.join('?' for k in vals if k in cols)})",
           [v for k, v in vals.items() if k in cols])
db.commit()
us = series(adm, "est-us")
iu = list(us.values())[0]
r = borrar(adm, "est-us", "imagen", iu[0])
chk(r.status_code == 409 and r.json().get("pide_confirmar"), "con informe validado pide confirmar")
chk(borrar(adm, "est-us", "imagen", iu[0], validado_ok=True).ok, "confirmando, se borra")

aud = adm.get(B + "/admin/auditoria").text if adm.get(B + "/admin/auditoria").ok else ""
chk("borrar_imagen" in aud and "serie de otro paciente" in aud, "queda en la auditoría con el motivo")

print("\nFALLAS:", fallas)
sys.exit(1 if fallas else 0)
