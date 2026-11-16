"""Paciente extranjero sin RUT: pasaporte en la agenda → clave, hoja, orden en el PACS, portal y unión de imágenes.
Mismos servicios que prueba_agenda.py (base de datos vacía)."""
import re
import time
from datetime import date

import requests

A, P = "http://localhost:3000", "http://localhost:8000"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def esperar(cond, seg=20):
    t = time.time()
    while time.time() - t < seg:
        if cond():
            return True
        time.sleep(0.5)
    return False


hoy = date.today().isoformat()
ag = requests.Session()
ag.post(A + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})


def nueva(rut, hora, nombre="John Smith"):
    return ag.post(A + "/api/citas", json={"fecha": hoy, "hora": hora, "equipo": "rx", "paciente": nombre, "rut": rut,
                                           "prevision": "Particular", "examenes": [{"nombre": "RX de tórax", "valor": 20000}]}).json()


c1 = nueva("AB-123456", "09:00")
r = ag.post(A + f"/api/citas/{c1['id']}/pago", json={"medio": "Efectivo"}).json()
clave = (r.get("portal") or {}).get("clave", "")
chk(r["portal"]["estado"] == "nueva" and len(clave) == 8, f"pasaporte AB-123456: clave nueva al pagar ({clave})")
h = ag.get(A + f"/api/citas/{c1['id']}/hoja").json()
chk(h.get("clave") == clave, "la hoja de retiro se puede imprimir/reimprimir con la clave")

c2 = nueva("ab123456", "09:30")
r2 = ag.post(A + f"/api/citas/{c2['id']}/pago", json={"medio": "Efectivo"}).json()
chk(r2["portal"]["estado"] == "existente", "mismo pasaporte escrito distinto: usa la misma clave")

c3 = nueva("PAS 987654321", "10:00", "Ana Gómez")
r3 = ag.post(A + f"/api/citas/{c3['id']}/pago", json={"medio": "Efectivo"}).json()
chk(r3["portal"]["estado"] == "nueva", "pasaporte solo de números con PAS adelante: clave nueva")

c4 = nueva("12.345.678-0", "10:30", "Rut Malo")
r4 = ag.post(A + f"/api/citas/{c4['id']}/pago", json={"medio": "Efectivo"}).json()
chk(r4["portal"]["estado"] == "sin_rut", "un RUT con dígito verificador malo sigue sin clave (no se confunde con pasaporte)")

c5 = nueva("11.111.111-1", "11:00", "Rut Chileno")
r5 = ag.post(A + f"/api/citas/{c5['id']}/pago", json={"medio": "Efectivo"}).json()
clave5 = r5["portal"].get("clave", "")
chk(r5["portal"]["estado"] == "nueva", "RUT chileno: igual que antes")

pac = requests.Session()
csrf = re.search(r'name="csrf" value="([^"]+)"', pac.get(P + "/paciente").text).group(1)
chk(esperar(lambda: "RX DE TÓRAX" in pac.post(P + "/paciente", data={"csrf": csrf, "rut": "AB123456", "clave": clave}).text),
    "el paciente entra al portal con su pasaporte y la clave")
t = pac.get(P + "/paciente/examenes").text
chk(t.count('class="pt-examen ') == 2, "ve sus 2 exámenes")
chk("Pasaporte AB123456" in t and "RUT Pasaporte" not in t, "el portal muestra «Pasaporte AB123456»")

o = requests.Session()
cs = re.search(r'name="csrf" value="([^"]+)"', o.get(P + "/paciente").text).group(1)
chk("incorrectos" in o.post(P + "/paciente", data={"csrf": cs, "rut": "123456", "clave": clave}).text,
    "solo los números del pasaporte no entran")
o = requests.Session()
cs = re.search(r'name="csrf" value="([^"]+)"', o.get(P + "/paciente").text).group(1)
chk("RX DE TÓRAX" in o.post(P + "/paciente", data={"csrf": cs, "rut": "11.111.111-1", "clave": clave5}).text,
    "el paciente con RUT sigue entrando igual")

# Llegan las imágenes con el pasaporte como PatientID (como lo manda la worklist): se une solo
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": c1["accesion"], "PatientID": "AB123456",
                                                       "PatientName": "SMITH^JOHN"})
ok = esperar(lambda: any(c["id"] == c1["id"] and c["pacs_estado"] == "imagenes"
                         for c in ag.get(A + f"/api/citas?fecha={hoy}").json()), 30)
chk(ok, "la agenda ve las imágenes del pasaporte")
adm = requests.Session()
adm.post(P + "/login", data={"usuario": "admin", "clave": "clave-prueba-123"})
r = adm.get(P + f"/agenda/{c1['accesion']}")
chk("/ficha/AB123456" in r.url and "Ficha del paciente" in r.text, "«Ver en el PACS» abre la ficha por pasaporte")

# --- v1.1.64: documento extranjero solo de números
c6 = nueva("555666777", "12:00", "Doc Numerico")
r6 = ag.post(A + f"/api/citas/{c6['id']}/pago", json={"medio": "Efectivo"}).json()
chk(r6["portal"]["estado"] == "sin_rut", "documento solo de números: al pagar pide confirmar")
h6 = ag.get(A + f"/api/citas/{c6['id']}/hoja").json()
chk(h6.get("motivo") == "sin_rut", "la hoja avisa que falta el documento (no «se crea al pagar»)")
x = ag.post(A + f"/api/citas/{c6['id']}/extranjero").json()
clave6 = x.get("clave", "")
chk(x.get("rut") == "PAS 555666777" and x.get("estado") == "nueva" and len(clave6) == 8, f"«Es pasaporte / documento extranjero» crea la clave ({clave6})")
chk(ag.get(A + f"/api/citas/{c6['id']}/hoja").json().get("clave") == clave6, "la hoja sale con esa clave")
chk(ag.post(A + f"/api/citas/{c5['id']}/extranjero").status_code == 400, "no se puede usar con un RUT válido")
o = requests.Session()
cs = re.search(r'name="csrf" value="([^"]+)"', o.get(P + "/paciente").text).group(1)
chk(esperar(lambda: "RX DE TÓRAX" in o.post(P + "/paciente", data={"csrf": cs, "rut": "555.666.777", "clave": clave6}).text),
    "el extranjero entra al portal escribiendo solo los números")
t = o.get(P + "/paciente/examenes").text
chk("Pasaporte 555666777" in t, "el portal muestra «Pasaporte 987654321»")
print("\nTODO OK" if not fallas else f"\n{fallas} FALLA(S)")
