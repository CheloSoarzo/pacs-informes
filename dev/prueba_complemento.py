"""v1.1.75: una cita de complemento («Contraste») agendada aparte no impide que el estudio se una solo a su cita,
y en el portal se ve como un solo examen («TC de tórax + Contraste»). Mismos servicios que prueba_agenda.py."""
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


def cita(hora, examen, rut="10.619.161-K", equipo="tc"):
    c = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": hora, "equipo": equipo, "paciente": "Pedro Muestra",
                                        "rut": rut, "prevision": "Particular",
                                        "examenes": [{"nombre": examen, "valor": 1000}]}).json()
    r = ag.post(A + f"/api/citas/{c['id']}/pago", json={"medio": "Efectivo"}).json()
    return c, r


rut = "11.111.111-1"
c1, r1 = cita("09:30", "TOMOGRAFÍA COMPUTARIZADA DE TÓRAX", rut)
c2, _ = cita("10:00", "CONTRASTE", rut)
clave = r1["portal"].get("clave", "")
# El tomógrafo sin worklist: otro número de acceso, el RUT escrito a mano
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "TC999", "id": "est-tc-contraste",
                                                       "PatientID": "11111111-1", "PatientName": "MUESTRA^PEDRO",
                                                       "Modality": "CT", "StudyDescription": "TC TORAX CON CONTRASTE"})


def estado(cid):
    return next((c.get("pacs_estado") for c in ag.get(A + f"/api/citas?fecha={hoy}").json() if c["id"] == cid), None)


chk(esperar(lambda: estado(c1["id"]) == "imagenes"), "el estudio se une solo a la cita de tórax (el contraste no cuenta)")
chk(esperar(lambda: estado(c2["id"]) == "imagenes", 20), "la cita de contraste también muestra «con imágenes»")
pac = requests.Session()
cs = re.search(r'name="csrf" value="([^"]+)"', pac.get(P + "/paciente").text).group(1)
t = pac.post(P + "/paciente", data={"csrf": cs, "rut": rut, "clave": clave}).text
t = pac.get(P + "/paciente/examenes").text
chk(t.count('class="pt-examen ') == 1, "el portal muestra un solo examen")
chk("+ Contraste" in t, "como «… + Contraste»")

# Dos exámenes reales del mismo equipo el mismo día: sigue sin adivinar (se une a mano)
rut2 = "22.222.222-2"
d1, _ = cita("11:00", "TC DE TÓRAX", rut2)
d2, _ = cita("11:30", "TC DE ABDOMEN", rut2)
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "TC998", "id": "est-tc-dos",
                                                       "PatientID": "22222222-2", "PatientName": "MUESTRA^PEDRO",
                                                       "Modality": "CT", "StudyDescription": "TC TORAX"})
time.sleep(15)
chk(estado(d1["id"]) != "imagenes" and estado(d2["id"]) != "imagenes", "dos exámenes reales el mismo día: no se adivina")
print("\nFALLAS:", fallas)
