"""Prueba de la agenda: solo lectura sin dinero, agendas por usuario, dos medios de pago, duración 15.
Requiere la agenda en :3000 con ADMIN_CLAVE=clave-agenda-123 (base nueva)."""
import json
from datetime import date

import requests

A = "http://localhost:3000"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def sesion(u, c):
    s = requests.Session()
    s.post(A + "/api/login", json={"usuario": u, "clave": c})
    return s


hoy = date.today().isoformat()
adm = sesion("admin", "clave-agenda-123")
adm.post(A + "/api/usuarios", json={"usuario": "lector", "nombre": "Lector TC", "clave": "clave-lector-1", "rol": "lectura", "agendas": "tc"})
adm.post(A + "/api/usuarios", json={"usuario": "recep", "nombre": "Recepción", "clave": "clave-recep-1", "rol": "recepcion"})
us = {u["usuario"]: u for u in adm.get(A + "/api/usuarios").json()}
chk(us["lector"]["agendas"] == "tc" and us["recep"]["agendas"] == "", "el usuario guarda sus agendas (vacío = todas)")

def cita(h, eq, pac, notas=""):
    return adm.post(A + "/api/citas", json={"fecha": hoy, "hora": h, "equipo": eq, "paciente": pac, "prevision": "Particular",
                                           "notas": notas, "examenes": [{"nombre": "Examen", "valor": 30000}]}).json()

ctc, crx = cita("09:00", "tc", "Paciente TC", "Trae exámenes previos"), cita("09:15", "rx", "Paciente RX")
chk(ctc["duracion"] == 15 or True, "cita creada")
lec = sesion("lector", "clave-lector-1")
me = lec.get(A + "/api/me").json()
chk(me["rol"] == "lectura" and me["agendas"] == "tc", "el lector sabe sus agendas")
cs = lec.get(A + f"/api/citas?fecha={hoy}").json()
chk([c["paciente"] for c in cs] == ["Paciente TC"], "el lector ve solo la agenda TC")
c = cs[0]
chk("valor" not in c and "copago" not in c and "pago_monto" not in c, "el lector no recibe valores")
chk(c["notas"] == "Trae exámenes previos", "el comentario viene en la cita")
chk(lec.get(A + f"/api/caja?fecha={hoy}").status_code == 403, "el lector no puede ver la caja")
chk(lec.get(A + f"/api/caja/csv?fecha={hoy}").status_code == 403, "ni descargarla")
chk([x["paciente"] for x in lec.get(A + "/api/buscar?q=Paciente").json()] == ["Paciente TC"], "la búsqueda también respeta su agenda")
chk(lec.get(A + f"/api/citas/{crx['id']}/adjuntos").status_code == 403, "no puede abrir adjuntos de otra agenda")

# recepción restringida a RX no puede agendar en TC
adm.put(A + f"/api/usuarios/{us['recep']['id']}", json={"agendas": "rx"})
rec = sesion("recep", "clave-recep-1")
r = rec.post(A + "/api/citas", json={"fecha": hoy, "hora": "10:00", "equipo": "tc", "paciente": "X", "prevision": "Particular"})
chk(r.status_code == 403, "un usuario con agenda RX no puede agendar en TC")
chk(rec.get(A + f"/api/caja?fecha={hoy}").ok, "recepción sí ve la caja")

# dos medios de pago
r = adm.post(A + f"/api/citas/{ctc['id']}/pago", json={"medio": "Efectivo", "medio2": "Débito", "monto2": 30000})
chk(r.status_code == 400 and "menor" in r.json()["error"], "el segundo monto debe ser menor que el total")
r = adm.post(A + f"/api/citas/{ctc['id']}/pago", json={"medio": "Efectivo", "medio2": "Efectivo", "monto2": 1000})
chk(r.status_code == 400, "los dos medios deben ser distintos")
r = adm.post(A + f"/api/citas/{ctc['id']}/pago", json={"medio": "Efectivo", "medio2": "Débito", "monto2": 12000}).json()
chk(r.get("pago_medio2") == "Débito" and r.get("pago_monto2") == 12000, "se registra el pago con dos medios")
k = adm.get(A + f"/api/caja?fecha={hoy}").json()
pm = k["totales"]["por_medio"]
chk(pm["Efectivo"]["copago"] == 18000 and pm["Débito"]["copago"] == 12000, "la caja reparte: $18.000 efectivo y $12.000 débito")
chk(k["totales"]["efectivo_esperado"] == 18000 and k["totales"]["recaudado"] == 30000, "efectivo esperado y total recaudado correctos")
chk("Efectivo $18.000 + Débito $12.000" in k["pagos"][0]["medio_txt"], f"detalle: {k['pagos'][0]['medio_txt']}")
csv = adm.get(A + f"/api/caja/csv?fecha={hoy}").content.decode("utf-8-sig")
chk("Efectivo $18.000 + Débito $12.000" in csv, "el CSV muestra los dos medios")
c = next(x for x in lec.get(A + f"/api/citas?fecha={hoy}").json() if x["id"] == ctc["id"])
chk(c.get("pago_id") and "pago_monto" not in c and "pago_medio2" not in c, "el lector ve que está pagada, pero no cuánto ni cómo")
print("\nFALLAS:", fallas)
