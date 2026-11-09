"""Prueba: una cita con varios exámenes (RX de columna, pelvis y rodilla) que el equipo envía como varios estudios.

Todos los estudios deben quedar unidos a la misma cita: el médico solicitante los ve todos, cada uno lo tiene
como solicitante, la cita sigue en el worklist hasta que llegan todos y el paciente ve cada informe en el portal.

Requiere corriendo:
  - dev/mock_orthanc.py                        (PACS simulado en :8042)
  - la app de informes en :8000 con ADMIN_PASSWORD=clave-prueba-123 INTEGRACION_TOKEN=token-prueba
    WORKLIST_DIR=<carpeta> (variable WL de esta prueba)

    WL=<carpeta del worklist> python dev/prueba_varios_examenes.py
"""
import os
import re
import sys
from datetime import date

import requests

P, O = "http://localhost:8000", "http://localhost:8042"
TOK = {"Authorization": "Bearer token-prueba"}
WL = os.environ["WL"]
hoy = date.today().isoformat()
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def sesion(u, c):
    s = requests.Session()
    s.post(P + "/login", data={"usuario": u, "clave": c})
    return s


def orden(acc, rut, examenes, medico_id=0, medico="", equipo="rx"):
    r = requests.post(P + "/api/integracion/ordenes", headers=TOK, json={"ordenes": [{
        "accesion": acc, "cita_id": int(re.sub(r"\D", "", acc) or 1), "fecha": hoy, "hora": "10:00", "equipo": equipo,
        "paciente": "Paciente Varios", "rut": rut, "medico": medico, "medico_id": medico_id, "prevision": "Fonasa",
        "examen": " + ".join(examenes), "examenes": [{"codigo": "", "nombre": e} for e in examenes],
        "estado": "llego", "pagado": True}]})
    return r.ok


def estados():
    unir = requests.get(P + "/api/integracion/estados", headers=TOK).json()["estados"]
    return {e["accesion"]: e["estado"] for e in unir}


def llega(id_, acc, rut, desc):
    requests.post(O + "/dev/llega", json={"id": id_, "AccessionNumber": acc, "PatientID": rut, "PatientName": "VARIOS^PACIENTE",
                                          "Modality": "CR", "StudyDescription": desc})


adm = sesion("admin", "clave-prueba-123")
t = adm.get(P + "/admin/usuarios").text
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', t))
cs = re.search(r'name="csrf" value="([^"]+)"', t).group(1)
adm.post(P + "/admin/usuarios", data={"csrf": cs, "usuario": "drvarios", "nombre": "Dr. Juan Derivador",
                                      "perfil_id": perf["Médicos solicitantes"], "clave": "clave-larga-123", "activo": "on"})
mid = next(m["id"] for m in requests.get(P + "/api/integracion/medicos", headers=TOK).json()["medicos"]
           if m["nombre"] == "Dr. Juan Derivador")
med = sesion("drvarios", "clave-larga-123")

# --- Caso 1: el tecnólogo elige al paciente del worklist para cada examen (mismo número de acceso)
chk(orden("V100", "15.555.555-5", ["RX COLUMNA LUMBAR", "RX PELVIS AP", "RX RODILLA DERECHA"], mid, "Dr. Juan Derivador"),
    "cita con 3 exámenes llega al PACS")
chk(os.path.exists(os.path.join(WL, "V100.wl")), "la cita aparece en el worklist del equipo")
llega("est-v100-a", "V100", "15555555-5", "RX COLUMNA LUMBAR")
estados()
chk(os.path.exists(os.path.join(WL, "V100.wl")), "llegó el 1° estudio: la cita SIGUE en el worklist (faltan 2)")
llega("est-v100-b", "V100", "15555555-5", "RX PELVIS AP")
llega("est-v100-c", "V100", "15555555-5", "RX RODILLA DERECHA")
chk(estados().get("V100") == "imagenes", "la agenda ve la cita con imágenes")
chk(not os.path.exists(os.path.join(WL, "V100.wl")), "llegaron los 3: la cita sale del worklist")
for eid, desc in (("est-v100-a", "columna"), ("est-v100-b", "pelvis"), ("est-v100-c", "rodilla")):
    chk(med.get(P + f"/estudio/{eid}").status_code == 200, f"el médico derivador ve el estudio de {desc}")
    t = adm.get(P + f"/estudio/{eid}").text
    chk("<span>Solicitante</span>DR. JUAN DERIVADOR" in t and "Orden de agenda · V100" in t,
        f"el estudio de {desc} tiene al médico como solicitante y muestra la cita")
lista_med = med.get(P + "/").text
chk(all(x in lista_med for x in ("est-v100-a", "est-v100-b", "est-v100-c")), "en la lista del médico aparecen los 3 estudios")

# --- Caso 2: sin worklist (el tecnólogo escribe el RUT, sin número de acceso): se une por RUT + fecha + equipo
chk(orden("V200", "16.666.666-6", ["RX HOMBRO", "RX CODO"], mid, "Dr. Juan Derivador"), "cita con 2 exámenes (sin worklist)")
llega("est-v200-a", "", "16666666-6", "RX HOMBRO")
llega("est-v200-b", "", "16666666-6", "RX CODO")
chk(estados().get("V200") == "imagenes", "se une sola por RUT y fecha")
chk(med.get(P + "/estudio/est-v200-a").status_code == 200 and med.get(P + "/estudio/est-v200-b").status_code == 200,
    "el médico ve los 2 estudios sin que nadie los asigne")
# llega un tercero más tarde el mismo día (ej. una proyección que se repitió)
llega("est-v200-c", "", "16666666-6", "RX CODO LATERAL")
estados()
chk(med.get(P + "/estudio/est-v200-c").status_code == 200, "un estudio que llega más tarde ese día también se suma a la cita")

# --- Caso 3: unir a mano un estudio a una cita que ya tiene otro
chk(orden("V300", "17.111.111-1", ["RX TORAX", "RX ABDOMEN"], mid, "Dr. Juan Derivador"), "cita con 2 exámenes")
llega("est-v300-a", "V300", "17111111-1", "RX TORAX")
llega("est-v300-b", "", "SIN-RUT", "RX ABDOMEN")        # el tecnólogo no escribió el RUT
estados()
chk(med.get(P + "/estudio/est-v300-b").status_code == 403, "el estudio sin RUT no se une solo")
t = adm.get(P + "/estudio/est-v300-b").text
chk("V300" in t and "ya tiene 1 estudio" in t, "en el estudio se ofrece la cita, avisando que ya tiene 1 estudio")
cs = re.search(r'name="csrf" value="([^"]+)"', t).group(1)
r = adm.post(P + "/estudio/est-v300-b/orden", data={"csrf": cs, "accesion": "V300"})
chk("ahora el médico solicitante ve todos" in r.text, "unir a mano a la misma cita")
chk(med.get(P + "/estudio/est-v300-b").status_code == 200 and med.get(P + "/estudio/est-v300-a").status_code == 200,
    "el médico ve los 2 estudios")
r = adm.post(P + "/estudio/est-v300-b/orden", data={"csrf": cs, "accion": "quitar"})
chk(med.get(P + "/estudio/est-v300-b").status_code == 403 and med.get(P + "/estudio/est-v300-a").status_code == 200,
    "separar uno: pierde acceso solo a ese, el otro sigue")
chk(estados().get("V300") == "imagenes", "la cita sigue con imágenes (le queda un estudio)")

# --- Caso 4: dos citas de RX el mismo día para el mismo paciente: no se adivina
chk(orden("V400", "18.222.222-2", ["RX MANO"]) and orden("V401", "18.222.222-2", ["RX PIE"]), "dos citas de RX el mismo día")
llega("est-v400-a", "", "18222222-2", "RX MANO")
e = estados()
chk("V400" not in e and "V401" not in e, "con dos citas posibles no se une solo (se une a mano)")

# --- Ficha del paciente: los 3 estudios aparecen unidos a la cita
t = adm.get(P + "/ficha/15555555-5").text
chk(t.count("V100") >= 3, "la ficha del paciente muestra los 3 estudios de la cita")

print("\nFALLAS:", fallas)
sys.exit(1 if fallas else 0)
