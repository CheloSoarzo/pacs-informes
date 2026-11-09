"""Prueba: el paciente ve sus imágenes en el portal aunque el informe aún no esté listo.

Requiere el PACS simulado y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 INTEGRACION_TOKEN=token-prueba."""
import hashlib
import re
import secrets
import sys
from datetime import date

import requests

P, O = "http://localhost:8000", "http://localhost:8042"
TOK = {"Authorization": "Bearer token-prueba"}
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def hash_agenda(clave):            # mismo formato que la agenda (Node): scrypt$<sal>$<hex>
    sal = secrets.token_hex(16)
    return f"scrypt${sal}$" + hashlib.scrypt(clave.encode(), salt=sal.encode(), n=16384, r=8, p=1, dklen=64).hex()


def orden(acc, rut, clave):
    return requests.post(P + "/api/integracion/ordenes", headers=TOK, json={"ordenes": [{
        "accesion": acc, "fecha": date.today().isoformat(), "hora": "10:00", "equipo": "rx", "paciente": "Paciente " + acc,
        "rut": rut, "examen": "RX TORAX", "examenes": [{"nombre": "RX TORAX"}], "estado": "llego", "pagado": True,
        "clave_hash": hash_agenda(clave)}]}).ok


def portal(rut, clave):
    s = requests.Session()
    t = s.get(P + "/paciente").text
    cs = re.search(r'name="csrf" value="([^"]+)"', t).group(1)
    s.post(P + "/paciente", data={"csrf": cs, "rut": rut, "clave": clave})
    return s


chk(orden("P100", "13.131.313-1", "ABCD2345"), "cita pagada con clave del paciente")
chk(orden("P200", "14.141.414-1", "WXYZ6789"), "cita de otro paciente")
for acc, rut in (("P100", "13131313-1"), ("P200", "14141414-1")):
    requests.post(O + "/dev/llega", json={"AccessionNumber": acc, "PatientID": rut, "Modality": "CR", "StudyDescription": "RX TORAX"})
requests.get(P + "/api/integracion/estados", headers=TOK)

pac = portal("13.131.313-1", "ABCD2345")
t = pac.get(P + "/paciente/examenes").text
chk("informe en proceso" in t and "Ver imágenes" in t, "informe en proceso: aparece el botón «Ver imágenes»")
url = re.search(r'href="(/paciente/imagenes/[^"]+)"', t).group(1)
t = pac.get(P + url).text
chk("Imágenes" in t and "aún está en proceso" in t, "abre la página de imágenes, avisando que el informe está en proceso")
sr = pac.get(P + url + "/series").json()["series"]
chk(sr and sr[0]["instancias"], f"se cargan las series ({len(sr)})")
r = pac.get(P + url + "/img/" + sr[0]["instancias"][0])
chk(r.ok and r.headers["Content-Type"].startswith("image/"), "se ve la imagen")

otro = "est-p200"
chk(pac.get(P + f"/paciente/imagenes/{otro}").status_code == 404, "no puede ver las imágenes de otro paciente")
chk(pac.get(P + f"/paciente/imagenes/{otro}/series").status_code == 404, "ni sus series")
otra_img = "est-ct-s1-i1"
chk(pac.get(P + url + "/img/" + otra_img).status_code == 404, "ni una imagen de otro estudio por su propia página")
r = requests.get(P + url, allow_redirects=False)
chk(r.status_code == 302 and "/paciente" in r.headers["Location"], "sin ingresar con RUT y clave, lo manda a ingresar")
chk(requests.get(P + url + "/series").status_code == 404, "sin sesión no entrega imágenes")

print("\nFALLAS:", fallas)
sys.exit(1 if fallas else 0)
