"""Prueba de la agenda: segundo teléfono y correo, y cantidad por examen (mismo código varias veces).
Requiere la agenda en :3000 con ADMIN_CLAVE=clave-agenda-123 (base nueva)."""
from datetime import date

import requests

A = "http://localhost:3000"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


hoy = date.today().isoformat()
ag = requests.Session()
ag.post(A + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})
rx = next(a for a in ag.get(A + "/api/aranceles").json() if a["equipo"] == "rx" and a["fonasa_total"] > 0)

base = {"fecha": hoy, "hora": "09:00", "duracion": 15, "equipo": "rx", "paciente": "Prueba Cantidad", "rut": "11.111.111-1",
        "telefono": "+56 9 1111 1111", "telefono2": "+56 9 2222 2222", "email": "uno@correo.cl", "email2": "DOS@Correo.cl",
        "prevision": "Fonasa", "examenes": [{"codigo": rx["codigo"], "nombre": "", "cantidad": 4,
                                              "valor_unit": 1, "bonif_unit": 0}]}
c = ag.post(A + "/api/citas", json=base).json()
chk(c.get("telefono2") == "+56 9 2222 2222" and c.get("email2") == "dos@correo.cl", "guarda segundo teléfono y correo")
chk(c["valor"] == rx["fonasa_total"] * 4 and c["bonificacion"] == rx["fonasa_bonif"] * 4,
    f"Fonasa x4: valor {c['valor']} = 4 × {rx['fonasa_total']}")
chk(c["copago"] == (rx["fonasa_total"] - rx["fonasa_bonif"]) * 4, "copago x4")
chk(c["examen"].endswith("(X4)") and c["examen"] == c["examen"].upper(), f"examen con cantidad: {c['examen']}")
import json
ls = json.loads(c["examenes"])
chk(ls[0]["cantidad"] == 4 and ls[0]["valor_unit"] == rx["fonasa_total"], "guarda cantidad y valor unitario")

r = ag.post(A + "/api/citas", json={**base, "email2": "malo"})
chk(r.status_code == 400 and "segundo correo" in r.text, "valida el segundo correo")
p = ag.get(A + "/api/paciente?rut=11111111-1").json()
chk(p["telefono2"] == "+56 9 2222 2222" and p["email2"] == "dos@correo.cl", "al escribir el RUT trae también los segundos contactos")

# Particular con dos líneas, una repetida
c2 = ag.post(A + "/api/citas", json={**base, "prevision": "Particular", "examenes": [
    {"nombre": "Rx de mano", "cantidad": 2, "valor_unit": 10000}, {"nombre": "Rx de tórax", "valor": 15000}]}).json()
chk(c2["valor"] == 35000 and c2["copago"] == 35000, "particular: 2 × 10.000 + 15.000 = 35.000")
pago = ag.post(A + f"/api/citas/{c2['id']}/pago", json={"medio": "Efectivo", "descuento_tipo": "monto", "descuento_valor": 5000,
                                                        "descuento_motivo": "Seguro complementario"}).json()
chk(pago.get("ok") is not False and ag.get(A + f"/api/citas?fecha={hoy}").json(), "cobro con descuento por seguro complementario")
cita = next(x for x in ag.get(A + f"/api/citas?fecha={hoy}").json() if x["id"] == c2["id"])
chk(cita["pago_monto"] == 30000 and cita["pago_desc_motivo"] == "Seguro complementario", "queda el motivo «Seguro complementario»")
# Una sola línea sin cantidad (como antes)
c3 = ag.post(A + "/api/citas", json={**base, "prevision": "Particular", "examenes": [{"nombre": "Eco", "valor": 20000}]}).json()
chk(c3["valor"] == 20000 and c3["examenes"] == "" and c3["examen"] == "ECO", "una línea sin cantidad funciona como antes")
print("\nFALLAS:", fallas)
