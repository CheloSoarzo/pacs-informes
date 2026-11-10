"""Prueba: la fecha de nacimiento de la agenda llega al estudio cuando el equipo no la trajo.

Requiere el PACS simulado (dev/mock_orthanc.py) y la app en :8000 con ADMIN_PASSWORD=clave-prueba-123
INTEGRACION_TOKEN=token-prueba (base nueva)."""
import sys
import time
from datetime import date

import requests

P, O = "http://localhost:8000", "http://localhost:8042"
TOK = {"Authorization": "Bearer token-prueba"}
hoy = date.today().isoformat()
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def orden(acc, rut, fnac, equipo="rx"):
    return requests.post(P + "/api/integracion/ordenes", headers=TOK, json={"ordenes": [{
        "accesion": acc, "cita_id": int(acc[1:]), "fecha": hoy, "hora": "10:00", "equipo": equipo, "paciente": "Paciente Prueba",
        "rut": rut, "fecha_nac": fnac, "prevision": "Fonasa", "examen": "RX TORAX", "estado": "llego", "pagado": True}]}).ok


def llega(id_, acc, rut, fnac="", estable=True):
    requests.post(O + "/dev/llega", json={"id": id_, "AccessionNumber": acc, "PatientID": rut, "Modality": "CR",
                                          "PatientBirthDate": fnac, "estable": estable})


def fnac(eid):
    return requests.get(O + f"/studies/{eid}").json()["PatientMainDicomTags"].get("PatientBirthDate", "")


def estados_y_esperar():
    requests.get(P + "/api/integracion/estados", headers=TOK)
    time.sleep(2)


# 1) sin worklist: el tecnólogo escribió el RUT pero no la fecha -> se completa con la de la agenda
chk(orden("F100", "15.555.555-5", "1980-03-25"), "cita con fecha de nacimiento")
llega("est-f100", "", "15555555-5")
estados_y_esperar()
chk(fnac("est-f100") == "19800325", "el estudio queda con la fecha de nacimiento de la agenda")
# 2) el equipo sí trajo una fecha: no se toca
chk(orden("F200", "16.666.666-6", "1975-01-01"), "otra cita")
llega("est-f200", "F200", "16666666-6", fnac="19750102")
estados_y_esperar()
chk(fnac("est-f200") == "19750102", "si el estudio ya trae fecha, no se cambia")
# 3) estudio todavía llegando: se espera a que termine
chk(orden("F300", "17.111.111-1", "1990-12-31"), "tercera cita")
llega("est-f300", "F300", "17111111-1", estable=False)
estados_y_esperar()
chk(fnac("est-f300") == "", "mientras llegan imágenes no se modifica")
llega("est-f300", "F300", "17111111-1", estable=True)
estados_y_esperar()
chk(fnac("est-f300") == "19901231", "cuando terminó de llegar se completa")
# 4) cita sin fecha de nacimiento: nada que poner
chk(orden("F400", "18.222.222-2", ""), "cita sin fecha de nacimiento")
llega("est-f400", "F400", "18222222-2")
estados_y_esperar()
chk(fnac("est-f400") == "", "sin fecha en la agenda, el estudio queda igual")

print("\nFALLAS:", fallas)
sys.exit(1 if fallas else 0)
