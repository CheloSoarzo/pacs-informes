"""Prueba: pantalla de dictado del radiólogo (Informar → dictar con las imágenes a pantalla completa).

Requiere dev/mock_orthanc.py y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva).
"""
import re
import requests

B = "http://localhost:8000"; fallas = 0


def chk(c, m):
    global fallas; fallas += (not c); print(("OK   " if c else "FALLA ") + m)


def sesion(u, c):
    s = requests.Session(); s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text; m = re.search(r'name="csrf" value="([^"]+)"', t); s.csrf = m.group(1) if m else ""
    return s


adm = sesion("admin", "clave-prueba-123")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
for u, p in (("rad9", "Radiólogos"), ("tra9", "Transcripcionistas")):
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": u.upper(), "perfil_id": perf[p],
                                         "clave": "clave-larga-123", "activo": "on"})
rad, tra = sesion("rad9", "clave-larga-123"), sesion("tra9", "clave-larga-123")

lr = rad.get(B + "/").text
chk('href="/estudio/est-ct/dictar"' in lr and 'data-dictar="est-ct"' in lr, "radiólogo: Informar abre la pantalla de dictado")
lt = tra.get(B + "/").text
chk("/dictar" not in lt, "transcripcionista: Informar sigue abriendo el editor")

t = rad.get(B + "/estudio/est-ct/dictar").text
chk('id="grabador"' in t and "grabador-grande" in t, "grabadora grande")
chk('id="dict-iframe"' in t and "/ohif/viewer?StudyInstanceUIDs=1.2.826.0.1.1" in t, "visor OHIF ocupando la pantalla")
chk("PEREZ SOTO" in t and "12.345.678-5" in t and "TC CEREBRO SIN CONTRASTE" in t and "A1001" in t,
    "datos del paciente y del estudio")
chk('data-comparar="1.2.826.0.1.4"' in t and "Estudios anteriores (1)" in t, "estudio anterior para comparar")
chk('class="barra"' not in t and "← Estudios" in t, "sin menú: pantalla completa")
chk('href="/estudio/est-ct"' in t, "enlace para escribir el informe")
chk(tra.get(B + "/estudio/est-ct/dictar").status_code == 403, "transcripcionista no entra a dictar (403)")

r = rad.post(B + "/estudio/est-ct/tomar", data={"csrf": rad.csrf, "volver": "dictar"})
chk(r.url.endswith("/estudio/est-ct/dictar") and "RAD9" in r.text, "Tomar este estudio vuelve a la pantalla de dictado")

r = rad.post(B + "/estudio/est-ct/dictados", headers={"X-CSRF": rad.csrf},
             files={"audio": ("d.webm", b"\x1aE\xdf\xa3" + b"0" * 2000, "audio/webm")}, data={"duracion": "12.5"})
chk(r.ok, "sube un dictado desde la pantalla")
t = rad.get(B + "/estudio/est-ct/dictar").text
chk("Audio 1" in t and "Por transcribir" in t and "Eliminar audio" in t, "el audio aparece con opción de eliminar")
did = re.search(r'/dictados/(\d+)/anular', t).group(1)
r = rad.post(B + f"/dictados/{did}/anular", data={"csrf": rad.csrf, "volver": "dictar"})
chk(r.url.endswith("/estudio/est-ct/dictar") and "Audio 1" not in r.text, "eliminar audio vuelve a la pantalla de dictado")

# --- panel inferior de la lista (clic en un paciente)
r = rad.post(B + "/estudio/est-ct/dictados", headers={"X-CSRF": rad.csrf},
             files={"audio": ("d.webm", b"\x1aE\xdf\xa3" + b"0" * 2000, "audio/webm")}, data={"duracion": "26"})
p = rad.get(B + "/estudio/est-ct/panel").json()
chk(len(p["series"]) == 2 and p["series"][0]["descripcion"] == "Axial 5mm" and len(p["series"][0]["instancias"]) == 40,
    "panel: series con sus imágenes")
chk(len(p["dictados"]) == 1 and p["dictados"][0]["audio"].endswith("/audio") and p["dictados"][0]["duracion"] == 26,
    "panel: audio del dictado")
sec = None
adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": "sec9", "nombre": "SEC9",
                                     "perfil_id": perf["Secretarias de Recepción"], "clave": "clave-larga-123", "activo": "on"})
sec = sesion("sec9", "clave-larga-123")
p2 = sec.get(B + "/estudio/est-ct/panel").json()
chk(len(p2["series"]) == 2 and p2["dictados"] == [], "panel: secretaría ve series pero no los audios")
lt = rad.get(B + "/").text
chk('id="panel-estudio"' in lt and "panel_lista.js" in lt and "audio.js" in lt, "lista incluye el panel")
print("FALLAS:", fallas)
