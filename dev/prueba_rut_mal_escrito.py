"""v1.1.76: RUT mal escrito en el equipo (no válido): el estudio se une a su cita por nombre + fecha + equipo, el
estudio avisa que el RUT no coincide, y al corregir el RUT con «Corregir datos» la unión con la cita se mantiene.
Mismos servicios que prueba_agenda.py."""
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


def esperar(cond, seg=40):
    t = time.time()
    while time.time() - t < seg:
        if cond():
            return True
        time.sleep(0.5)
    return False


hoy = date.today().isoformat()
ag = requests.Session()
ag.post(A + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})


def cita(hora, paciente, rut, equipo="tc", examen="TC DE MUÑECA"):
    c = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": hora, "equipo": equipo, "paciente": paciente, "rut": rut,
                                        "prevision": "Particular", "examenes": [{"nombre": examen, "valor": 1000}]}).json()
    ag.post(A + f"/api/citas/{c['id']}/pago", json={"medio": "Efectivo"})
    return c


def estado(cid):
    return next((c.get("pacs_estado") for c in ag.get(A + f"/api/citas?fecha={hoy}").json() if c["id"] == cid), None)


c1 = cita("12:00", "Ignacio Ejemplo Lastra", "23.689.031-7")
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "SINWL7", "id": "est-rutmalo",
                                                       "PatientID": "23699031-7", "PatientName": "EJEMPLO LASTRA^IGNACIO",
                                                       "Modality": "CT", "StudyDescription": "TC DE MUÑECA DERECHA"})
chk(esperar(lambda: estado(c1["id"]) == "imagenes"), "RUT mal escrito en el equipo: se une a su cita por nombre")
adm = requests.Session()
adm.post(P + "/login", data={"usuario": "admin", "clave": "clave-prueba-123"})
t = adm.get(P + "/estudio/est-rutmalo").text
chk("aviso-rut" in t and "23699031-7" in t, "el estudio avisa que el RUT de las imágenes no coincide con el de la cita")
cs = re.search(r'name="csrf" value="([^"]+)"', t).group(1)
d = adm.get(P + "/estudio/est-rutmalo/datos").json()["datos"]
r = adm.post(P + "/estudio/est-rutmalo/datos", data=dict(d, csrf=cs, rut="23689031-7"))
nuevo = r.url.rsplit("/", 1)[-1].split("?")[0]
t = adm.get(P + f"/estudio/{nuevo}").text
chk(nuevo != "est-rutmalo" and "Orden de agenda" in t and "aviso-rut" not in t,
    "corregido el RUT, el estudio sigue unido a su cita y ya no avisa")
chk(esperar(lambda: estado(c1["id"]) == "imagenes", 15), "la agenda sigue viendo las imágenes")

# Dos citas con el mismo nombre ese día: no se adivina
cita("13:00", "Ana Repetida Soto", "11.111.111-1", examen="TC DE TORAX")
cita("13:30", "Ana Repetida Soto", "22.222.222-2", examen="TC DE CEREBRO")
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "SINWL8", "id": "est-repetida",
                                                       "PatientID": "12345678-0", "PatientName": "REPETIDA SOTO^ANA",
                                                       "Modality": "CT", "StudyDescription": "TC"})
time.sleep(15)
chk("Orden de agenda" not in adm.get(P + "/estudio/est-repetida").text, "dos citas posibles con el mismo nombre: no se une")
# RUT válido distinto: no se une por nombre (podría ser otra persona)
c3 = cita("14:00", "Luis Distinto Paz", "15.555.555-6")
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "SINWL9", "id": "est-otro",
                                                       "PatientID": "11.111.111-1", "PatientName": "DISTINTO PAZ^LUIS",
                                                       "Modality": "CT", "StudyDescription": "TC"})
time.sleep(15)
chk(estado(c3["id"]) != "imagenes", "con un RUT válido distinto no se une por nombre")
print("\nFALLAS:", fallas)
