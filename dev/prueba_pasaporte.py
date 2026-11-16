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


for malo in ("AB-123456", "12.345.678-0", "87654321"):
    r = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "08:00", "equipo": "rx", "paciente": "X", "rut": malo,
                                        "prevision": "Particular", "examenes": [{"nombre": "RX", "valor": 1}]})
    chk(r.status_code == 400 and "no es válido" in r.json().get("error", ""), f"RUT no válido ({malo}): no deja guardar")
r = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "08:00", "equipo": "rx", "paciente": "X", "rut": "PAS 12",
                                    "prevision": "Particular", "examenes": [{"nombre": "RX", "valor": 1}]})
chk(r.status_code == 400, "extranjero con documento muy corto: no deja guardar")
sin = nueva("", "08:30", "Sin Documento")
r = ag.post(A + f"/api/citas/{sin['id']}/pago", json={"medio": "Efectivo"})
chk(r.status_code == 400 and "Falta el RUT" in r.json().get("error", ""), "sin RUT no deja cobrar")

c1 = nueva("PAS ab-123456", "09:00")
chk(c1.get("rut") == "PAS AB-123456", "extranjero: se guarda «PAS AB-123456»")
r = ag.post(A + f"/api/citas/{c1['id']}/pago", json={"medio": "Efectivo"}).json()
clave = (r.get("portal") or {}).get("clave", "")
chk(r["portal"]["estado"] == "nueva" and len(clave) == 8, f"extranjero: clave nueva al pagar ({clave})")
chk(ag.get(A + f"/api/citas/{c1['id']}/hoja").json().get("clave") == clave, "la hoja sale con la clave")
c2 = nueva("PAS AB123456", "09:30")
r2 = ag.post(A + f"/api/citas/{c2['id']}/pago", json={"medio": "Efectivo"}).json()
chk(r2["portal"]["estado"] == "existente", "mismo pasaporte escrito distinto: misma clave")
c5 = nueva("11.111.111-1", "11:00", "Rut Chileno")
r5 = ag.post(A + f"/api/citas/{c5['id']}/pago", json={"medio": "Efectivo"}).json()
clave5 = r5["portal"].get("clave", "")
chk(r5["portal"]["estado"] == "nueva", "RUT chileno válido: igual que antes")
c6 = nueva("PAS 555666777", "12:00", "Doc Numerico")
r6 = ag.post(A + f"/api/citas/{c6['id']}/pago", json={"medio": "Efectivo"}).json()
clave6 = r6["portal"].get("clave", "")
chk(r6["portal"]["estado"] == "nueva", "extranjero con documento de solo números: clave nueva")


def entrar(doc, cl):
    o = requests.Session()
    cs = re.search(r'name="csrf" value="([^"]+)"', o.get(P + "/paciente").text).group(1)
    ok = esperar(lambda: "RX DE TÓRAX" in o.post(P + "/paciente", data={"csrf": cs, "rut": doc, "clave": cl}).text)
    return ok, o


ok, pac = entrar("AB123456", clave)
chk(ok, "el extranjero entra al portal con su pasaporte (sin PAS)")
t = pac.get(P + "/paciente/examenes").text
chk(t.count('class="pt-examen ') == 2 and "Pasaporte AB123456" in t, "ve sus 2 exámenes y «Pasaporte AB123456»")
chk(entrar("555.666.777", clave6)[0], "documento de solo números: entra con el número")
chk(entrar("11.111.111-1", clave5)[0], "el paciente con RUT sigue entrando igual")
o = requests.Session()
cs = re.search(r'name="csrf" value="([^"]+)"', o.get(P + "/paciente").text).group(1)
chk("incorrectos" in o.post(P + "/paciente", data={"csrf": cs, "rut": "123456", "clave": clave}).text, "otro número no entra")

requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": c1["accesion"], "PatientID": "PASAB-123456",
                                                       "PatientName": "SMITH^JOHN"})
chk(esperar(lambda: any(c["id"] == c1["id"] and c["pacs_estado"] == "imagenes"
                        for c in ag.get(A + f"/api/citas?fecha={hoy}").json()), 30), "la agenda ve las imágenes del extranjero")
adm = requests.Session()
adm.post(P + "/login", data={"usuario": "admin", "clave": "clave-prueba-123"})
r = adm.get(P + f"/agenda/{c1['accesion']}")
chk("Ficha del paciente" in r.text, "«Ver en el PACS» abre la ficha del extranjero")
print("\nTODO OK" if not fallas else f"\n{fallas} FALLA(S)")
