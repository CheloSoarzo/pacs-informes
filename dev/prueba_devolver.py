"""Prueba: «Devolver a la cola» un dictado tomado.

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

# Estas pruebas usan radiólogos sobre estudios sin asignar: se quita «solo ve lo asignado» al perfil Radiólogos
# (esa restricción la prueba dev/prueba_asignacion.py)
def _radiologos_ven_todo(adm):
    pid = re.search(r'<option value="(\d+)"[^>]*>Radiólogos</option>', adm.get(B + "/admin/usuarios").text).group(1)
    form = next(f for f in adm.get(B + "/admin/perfiles").text.split("<form") if f'name="id" value="{pid}"' in f)
    adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "id": pid, "nombre": "Radiólogos",
                                          "permisos": re.findall(r'name="permisos" value="([^"]+)" checked', form)})


_radiologos_ven_todo(adm)
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
for u, p in (("rad9", "Radiólogos"), ("tra9", "Transcripcionistas"), ("tra8", "Transcripcionistas")):
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": u.upper(), "perfil_id": perf[p],
                                         "clave": "clave-larga-123", "activo": "on"})
rad, tra, otra = (sesion("rad9", "clave-larga-123"), sesion("tra9", "clave-larga-123"),
                  sesion("tra8", "clave-larga-123"))

r = rad.post(B + "/estudio/est-ct/dictados", headers={"X-CSRF": rad.csrf},
             files={"audio": ("d.webm", b"\x1aE\xdf\xa3" + b"0" * 2000, "audio/webm")}, data={"duracion": "8"})
chk(r.ok, "el radiólogo sube un dictado")
did = re.search(r'/dictados/(\d+)/tomar', tra.get(B + "/dictados").text).group(1)
tra.post(B + f"/dictados/{did}/tomar", data={"csrf": tra.csrf})
t = tra.get(B + "/dictados").text
chk(f"/dictados/{did}/devolver" in t, "quien lo tomó ve «Devolver» en la cola")
chk(f"/dictados/{did}/devolver" in tra.get(B + "/estudio/est-ct").text, "y en la ficha del estudio")
chk(f"/dictados/{did}/devolver" not in otra.get(B + "/dictados").text, "otra transcripcionista no ve «Devolver»")
chk(otra.post(B + f"/dictados/{did}/devolver", data={"csrf": otra.csrf}).status_code == 403,
    "otra transcripcionista no puede devolverlo (403)")
r = tra.post(B + f"/dictados/{did}/devolver", data={"csrf": tra.csrf, "volver": "cola"})
chk(r.url.endswith("/dictados") and "devuelto a la cola" in r.text, "devolver vuelve a la cola con aviso")
t = otra.get(B + "/dictados").text
chk(f"/dictados/{did}/tomar" in t, "queda libre: otra persona lo puede tomar")
otra.post(B + f"/dictados/{did}/tomar", data={"csrf": otra.csrf})
r = adm.post(B + f"/dictados/{did}/devolver", data={"csrf": adm.csrf})
chk(r.ok and "devuelto a la cola" in r.text, "un supervisor (puede anular dictados) también lo puede devolver")
print("FALLAS:", fallas)
