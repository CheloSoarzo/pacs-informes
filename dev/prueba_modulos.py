"""Prueba del usuario «raxio» (proveedor) y de los interruptores de módulos.

Requiere la agenda en :3400 con base nueva, CENTRO_NOMBRE=INSI, ADMIN_CLAVE=clave-agenda-123 y
CLAVE_PROVEEDOR_HASH = sha256("raxio:clave-de-prueba-123") = 890fa7304f3c70d0bad2a9d6ecf5e8097fadc8c68a51278c1d976b5cbd37015e,
corriendo dentro de un ciclo que la vuelva a levantar al reiniciarse (como Docker), por ejemplo:
  while true; do node server.js; sleep 1; done
"""
import os, sqlite3, time
import requests

B = "http://localhost:3400"
DATA = os.environ.get("DATA_DIR", "/tmp/modt")
f = 0


def chk(c, m):
    global f
    f += not c
    print(("OK   " if c else "FALLA ") + m)


adm = requests.Session(); adm.post(B + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})
rx = requests.Session()
chk(rx.post(B + "/api/login", json={"usuario": "raxio", "clave": "otra-clave"}).status_code == 401, "raxio con clave incorrecta: rechazado")
chk(rx.post(B + "/api/login", json={"usuario": "raxio", "clave": "clave-de-prueba-123"}).ok, "raxio entra con la clave del servidor")
chk(rx.get(B + "/api/me").json().get("proveedor") is True, "raxio es proveedor")
chk(all(u["usuario"] != "raxio" for u in adm.get(B + "/api/usuarios").json()), "raxio no aparece en la lista de usuarios")
chk(adm.post(B + "/api/usuarios", json={"usuario": "raxio", "clave": "12345678", "nombre": "x", "rol": "admin"}).status_code == 409, "nombre reservado")
rid = sqlite3.connect(DATA + "/agenda.db").execute("select id from usuarios where usuario='raxio'").fetchone()[0]
chk(adm.delete(B + f"/api/usuarios/{rid}").status_code == 404, "el admin del centro no puede eliminar a raxio")
chk(adm.get(B + "/api/modulos").json()["puede"] is False, "el admin del centro solo ve los módulos")
chk(adm.put(B + "/api/modulos", json={"modulo": "turnos", "activo": True}).status_code == 403, "y no puede cambiarlos")
r = rx.put(B + "/api/modulos", json={"modulo": "clinico", "activo": True, "confirmacion": "INSI"})
chk(r.ok and r.json()["reinicio"], "raxio enciende la ficha clínica")
time.sleep(5); adm.post(B + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})
chk(adm.get(B + "/api/me").json().get("clinico") is True, "ficha encendida tras el reinicio")
rx.post(B + "/api/login", json={"usuario": "raxio", "clave": "clave-de-prueba-123"})
chk(adm.post(B + "/api/profesionales", json={"titulo": "Dra.", "nombre": "Prueba Apagado", "duracion": 20}).status_code == 201, "se crea un profesional")
chk(rx.put(B + "/api/modulos", json={"modulo": "clinico", "activo": False}).ok, "raxio apaga la ficha")
time.sleep(5); rx.post(B + "/api/login", json={"usuario": "raxio", "clave": "clave-de-prueba-123"})
chk(sqlite3.connect(DATA + "/agenda.db").execute("select count(*) from profesionales").fetchone()[0] == 1, "apagada: los datos siguen guardados")
chk(rx.put(B + "/api/modulos", json={"modulo": "clinico", "activo": True}).ok, "volver a encenderla no pide confirmación")
time.sleep(5); adm.post(B + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})
chk(any(p["nombre"] == "Prueba Apagado" for p in adm.get(B + "/api/profesionales").json()), "al encender de nuevo aparece todo como estaba")
print("FALLAS:", f)
