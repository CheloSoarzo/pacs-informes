"""Prueba automática de la unión agenda + PACS.

Requiere corriendo:
  - dev/mock_orthanc.py                        (PACS simulado en :8042)
  - la app de informes en :8000 con ADMIN_PASSWORD=clave-prueba-123 INTEGRACION_TOKEN=token-prueba
    WORKLIST_DIR=<carpeta> (base de datos vacía)
  - la agenda en :3000 con ADMIN_CLAVE=clave-agenda-123 PACS_URL=http://localhost:8000
    INTEGRACION_TOKEN=token-prueba PORTAL_URL=http://localhost:8000/paciente PACS_INTERVALO_SEG=5

    python dev/prueba_agenda.py
"""
import re
import sys
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
chk(ag.post(A + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"}).ok, "login agenda")
cfg = ag.get(A + "/api/me").json()
chk(cfg["pacs"] is True and cfg["portal_url"] == "http://localhost:8000/paciente", "agenda conectada al PACS")

# --- agendar
cita = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "10:00", "duracion": 20, "equipo": "tc",
                                       "paciente": "María José Muñoz Pérez", "rut": "11.111.111-1", "fecha_nac": "1980-05-02",
                                       "email": "mjose@correo.cl", "prevision": "Particular", "medico": "Dra. Rojas",
                                       "notas": "Creatinina 0,9", "examenes": [{"codigo": "", "nombre": "TC de tórax", "valor": 50000}]}).json()
acc = cita.get("accesion", "")
chk(bool(re.fullmatch(r"AG\d{8}", acc)), f"número de acceso al agendar ({acc})")
t = requests.post(P + "/api/integracion/ordenes", json={"ordenes": []})
chk(t.status_code == 404, "API de integración rechaza sin token")

pac = requests.Session()
# Aún no pagado: el paciente no tiene clave
r = pac.get(P + "/paciente")
csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
r = pac.post(P + "/paciente", data={"csrf": csrf, "rut": "11.111.111-1", "clave": "XXXX"})
chk("incorrectos" in r.text, "sin clave no entra")

# --- cobrar: se crea la clave
r = ag.post(A + f"/api/citas/{cita['id']}/pago", json={"medio": "Efectivo"}).json()
clave = (r.get("portal") or {}).get("clave", "")
chk(r["portal"]["estado"] == "nueva" and len(clave) == 8, f"clave nueva al pagar ({clave})")

# Segundo examen del mismo paciente: ya tiene clave
c2 = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "11:00", "equipo": "rx", "paciente": "María José Muñoz Pérez",
                                     "rut": "11111111-1", "prevision": "Particular",
                                     "examenes": [{"nombre": "RX de tórax", "valor": 20000}]}).json()
r2 = ag.post(A + f"/api/citas/{c2['id']}/pago", json={"medio": "Débito"}).json()
chk(r2["portal"]["estado"] == "existente", "segundo examen usa la clave que ya tenía")

# Sin RUT válido: no hay clave
c3 = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "12:00", "equipo": "eco", "paciente": "Sin Rut",
                                     "rut": "123", "prevision": "Particular", "examenes": [{"nombre": "Eco", "valor": 10000}]}).json()
r3 = ag.post(A + f"/api/citas/{c3['id']}/pago", json={"medio": "Efectivo"}).json()
chk(r3["portal"]["estado"] == "sin_rut", "sin RUT válido no se crea clave")

# --- el paciente entra antes de que lleguen las imágenes
r = pac.get(P + "/paciente")
csrf = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
ok = esperar(lambda: "informe en proceso" in pac.post(P + "/paciente", data={"csrf": csrf, "rut": "11.111.111-1",
                                                                          "clave": clave.lower()}).text)
chk(ok, "paciente entra con RUT + clave y ve 'informe en proceso'")
t = pac.get(P + "/paciente/examenes").text
chk(t.count('class="examen"') == 2 and "TC DE TÓRAX" in t and "RX DE TÓRAX" in t, "ve sus 2 exámenes (en mayúsculas)")
c_ag = next(c for c in ag.get(A + f"/api/citas?fecha={hoy}").json() if c["id"] == cita["id"])
chk(c_ag["examen"] == "TC DE TÓRAX", "la agenda guarda el examen en mayúsculas")
chk(all(a["nombre"] == a["nombre"].upper() for a in ag.get(A + "/api/aranceles").json()), "el arancel está en mayúsculas")
chk("Ver informe" not in t, "sin informe firmado no hay botón para verlo")

# --- otra persona no entra con esa clave
otro = requests.Session()
r = otro.get(P + "/paciente"); cs = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
chk("incorrectos" in otro.post(P + "/paciente", data={"csrf": cs, "rut": "22.222.222-2", "clave": clave}).text,
    "la clave no sirve con otro RUT")

# --- llegan las imágenes con el número de acceso
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": acc, "PatientID": "11111111-1",
                                                       "PatientName": "MUNOZ PEREZ^MARIA JOSE"})
ok = esperar(lambda: any(c["id"] == cita["id"] and c["pacs_estado"] == "imagenes"
                         for c in ag.get(A + f"/api/citas?fecha={hoy}").json()), 30)
chk(ok, "la agenda ve 'imágenes' cuando el estudio llega al PACS")
c = next(c for c in ag.get(A + f"/api/citas?fecha={hoy}").json() if c["id"] == cita["id"])
chk(c["estado"] == "atendido", "la cita pasa a 'atendido' sola")

# --- el radiólogo ve la orden de la agenda en el estudio
adm = requests.Session()
adm.post(P + "/login", data={"usuario": "admin", "clave": "clave-prueba-123"})
eid = "est-" + acc.lower()
t = adm.get(P + f"/estudio/{eid}").text
chk("Orden de agenda" in t and "Creatinina 0,9" in t and "DRA. ROJAS" in t, "el estudio muestra la orden y notas de recepción")
csrf_adm = re.search(r'name="csrf" value="([^"]+)"', t).group(1)

# --- botón «Ver en el PACS» de la agenda
chk(esperar(lambda: ag.get(A + "/api/me").json().get("pacs_publico") == "http://localhost:8000", 30),
    "la agenda aprende la dirección del PACS (botón «Ver en el PACS»)")
r = adm.get(P + f"/agenda/{acc}")
chk("/ficha/" in r.url and "Ficha del paciente" in r.text and f"/estudio/{eid}" in r.text and "desde la agenda" in r.text,
    "«Ver en el PACS» abre la ficha del paciente (por RUT) marcando el examen de la cita")
r = adm.get(P + "/agenda/NOEXISTE123")
chk("No se encontró el examen" in r.text, "un número de acceso desconocido avisa y vuelve a la lista")
r = requests.get(P + f"/agenda/{acc}")
chk("/login" in r.url, "sin sesión pide iniciar sesión primero")

# --- firmar el informe
r = adm.post(P + f"/estudio/{eid}/firmar", json={"cuerpo": "HALLAZGOS:\nSin alteraciones.\n\nIMPRESIÓN:\nNormal."},
             headers={"X-CSRF": csrf_adm})
chk(r.ok, "radiólogo firma el informe")
ok = esperar(lambda: any(c["id"] == cita["id"] and c["pacs_estado"] == "informado"
                         for c in ag.get(A + f"/api/citas?fecha={hoy}").json()), 30)
chk(ok, "la agenda ve 'informado'")

t = pac.get(P + "/paciente/examenes").text
chk("Informe disponible" in t and "Ver informe" in t, "el paciente ve 'Informe disponible'")
m = re.search(r'href="(/paciente/ver/\d+)"', t)
r = pac.get(P + m.group(1), allow_redirects=True)
chk(r.ok and "/r/" in r.url and "Clave de acceso" not in r.text, "abre su informe sin pedir otra clave")
chk(pac.get(r.url.rstrip("/") + "/informe.pdf").content[:4] == b"%PDF", "descarga el PDF")
ok = esperar(lambda: any(c["id"] == cita["id"] and c["pacs_estado"] == "entregado"
                         for c in ag.get(A + f"/api/citas?fecha={hoy}").json()), 30)
chk(ok, "la agenda ve 'entregado' cuando el paciente lo abre")
chk("Portal del paciente" in adm.get(P + f"/estudio/{eid}").text, "entrega registrada en el PACS")

# --- nueva clave: la anterior deja de servir
nueva = ag.post(A + f"/api/citas/{cita['id']}/clave").json()["clave"]
chk(nueva != clave, "recepción genera clave nueva")
time.sleep(2)
s3 = requests.Session(); r = s3.get(P + "/paciente"); cs = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
chk("incorrectos" in s3.post(P + "/paciente", data={"csrf": cs, "rut": "11111111-1", "clave": clave}).text, "la clave anterior ya no sirve")
r = s3.get(P + "/paciente"); cs = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
chk("Ver informe" in s3.post(P + "/paciente", data={"csrf": cs, "rut": "11111111-1", "clave": nueva}).text, "la clave nueva sí sirve")

# --- eliminar una cita la quita del PACS
c4 = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "15:00", "equipo": "tc", "paciente": "Borrar", "rut": "11111111-1",
                                     "prevision": "Particular", "examenes": [{"nombre": "TC cerebro", "valor": 1000}]}).json()
time.sleep(1.5)
chk(ag.delete(A + f"/api/citas/{c4['id']}").ok, "eliminar cita")
time.sleep(1.5)
chk("TC cerebro" not in s3.get(P + "/paciente/examenes").text, "la cita eliminada desaparece del portal")

# --- sin worklist: el tecnólogo escribe los datos a mano (otro número de acceso)
def cita_de(id_):
    return next((c for c in ag.get(A + f"/api/citas?fecha={hoy}").json() if c["id"] == id_), {})

c5 = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "16:00", "equipo": "tc", "paciente": "Pedro Sin Worklist",
                                     "rut": "15.555.555-5", "prevision": "Particular", "examenes": [{"nombre": "TC abdomen", "valor": 1000}]}).json()
ag.post(A + f"/api/citas/{c5['id']}/pago", json={"medio": "Efectivo"})
time.sleep(1.5)
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "MANUAL77", "PatientID": "15555555-5",
                                                       "PatientName": "SIN WORKLIST^PEDRO"})
chk(esperar(lambda: cita_de(c5["id"]).get("pacs_estado") == "imagenes", 30), "sin worklist: se une sola por RUT + fecha + equipo")
t = adm.get(P + "/estudio/est-manual77").text
chk("Orden de agenda" in t and c5["accesion"] in t, "el estudio unido por RUT muestra su cita")

# Dos ecografías del mismo paciente el mismo día: no se adivina
e1 = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "17:00", "equipo": "eco", "paciente": "Ana Doble", "rut": "16.666.666-6",
                                     "prevision": "Particular", "examenes": [{"nombre": "Eco abdominal", "valor": 1000}]}).json()
e2 = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "17:20", "equipo": "eco", "paciente": "Ana Doble", "rut": "16.666.666-6",
                                     "prevision": "Particular", "examenes": [{"nombre": "Eco renal", "valor": 1000}]}).json()
time.sleep(1.5)
r = requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": "DOBLE1", "PatientID": "16666666-6",
                                                           "PatientName": "DOBLE^ANA", "StudyDescription": "ECO RENAL", "Modality": "US"})
time.sleep(8)
chk(not cita_de(e1["id"]).get("pacs_estado") and not cita_de(e2["id"]).get("pacs_estado"), "dos exámenes iguales el mismo día: no se une solo")
t = adm.get(P + "/estudio/est-doble1").text
chk("Sin cita de agenda" in t and t.count('name="accesion"') >= 2 and "mismo RUT" in t, "el estudio ofrece las citas para unir a mano")
cs = re.search(r'name="csrf" value="([^"]+)"', t).group(1)
r = adm.post(P + "/estudio/est-doble1/orden", data={"csrf": cs, "accesion": e2["accesion"]})
chk("Estudio unido a la cita" in r.text, "unir a mano con la cita correcta")
chk(esperar(lambda: cita_de(e2["id"]).get("pacs_estado") == "imagenes", 30) and not cita_de(e1["id"]).get("pacs_estado"),
    "la agenda ve el avance solo en la cita unida")
r = adm.post(P + "/estudio/est-doble1/orden", data={"csrf": cs, "accion": "quitar"})
chk("quedó separado" in r.text, "separar si se unió mal")
chk(esperar(lambda: not cita_de(e2["id"]).get("pacs_estado"), 30), "al separar, la agenda vuelve a «sin imágenes»")

# --- borrar el examen del PACS: la cita vuelve a quedar sin imágenes
r = adm.post(P + "/estudio/est-doble1/orden", data={"csrf": cs, "accesion": e2["accesion"]})
chk(esperar(lambda: cita_de(e2["id"]).get("pacs_estado") == "imagenes", 30), "se vuelve a unir")
r = adm.post(P + "/estudio/est-doble1/borrar", data={"csrf": cs, "confirmar": "BORRAR", "motivo": "duplicado"})
chk("Examen borrado" in r.text and "vuelve a quedar sin imágenes" in r.text, "borrar el examen unido a una cita")
chk(esperar(lambda: not cita_de(e2["id"]).get("pacs_estado"), 30), "la agenda vuelve a mostrar la cita «sin imágenes»")

# --- médico solicitante elegido en la agenda: recibe acceso al examen cuando llegan las imágenes
t = adm.get(P + "/admin/usuarios").text
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', t))
cs = re.search(r'name="csrf" value="([^"]+)"', t).group(1)
adm.post(P + "/admin/usuarios", data={"csrf": cs, "usuario": "drmed", "nombre": "Dra. Paula Méndez",
                                      "perfil_id": perf["Médicos solicitantes"], "clave": "clave-larga-123", "activo": "on"})
time.sleep(61)  # la agenda guarda la lista de médicos 1 minuto
meds = ag.get(A + "/api/medicos").json()
chk(any(m["nombre"] == "Dra. Paula Méndez" for m in meds), "la agenda trae la lista de médicos del centro desde el PACS")
mid = next(m["id"] for m in meds if m["nombre"] == "Dra. Paula Méndez")
cm = ag.post(A + "/api/citas", json={"fecha": hoy, "hora": "16:00", "equipo": "tc", "paciente": "Medico Prueba", "rut": "17.777.777-7",
                                     "prevision": "Particular", "medico": "Dra. Paula Méndez", "medico_id": mid,
                                     "examenes": [{"nombre": "TC de cerebro", "valor": 1000}]}).json()
chk(cm["medico_id"] == mid and cm["medico"] == "DRA. PAULA MÉNDEZ", "la cita guarda el médico elegido de la lista")
time.sleep(1.5)
requests.post("http://localhost:8042/dev/llega", json={"AccessionNumber": cm["accesion"], "PatientID": "17777777-7",
                                                       "PatientName": "PRUEBA^MEDICO", "Modality": "CT"})
eidm = "est-" + cm["accesion"].lower()
chk(esperar(lambda: cita_de(cm["id"]).get("pacs_estado") == "imagenes", 30), "llegan las imágenes y se une a la cita")
med = requests.Session(); med.post(P + "/login", data={"usuario": "drmed", "clave": "clave-larga-123"})
chk(med.get(P + f"/estudio/{eidm}").status_code == 200, "el médico de la cita ve el examen con su usuario, sin asignar nada")
t = adm.get(P + f"/estudio/{eidm}").text
chk("<span>Solicitante</span>DRA. PAULA MÉNDEZ" in t and "agenda</small>" in t, "queda como solicitante y con acceso «agenda»")
# se cambia por un médico escrito a mano: pierde el acceso
ag.put(A + f"/api/citas/{cm['id']}", json={**{k: cm[k] for k in ("fecha", "hora", "equipo", "paciente", "rut", "prevision")},
                                           "medico": "Dr. Externo Soto", "medico_id": 0, "estado": "atendido",
                                           "examenes": [{"nombre": "TC de cerebro", "valor": 1000}]})
chk(esperar(lambda: med.get(P + f"/estudio/{eidm}").status_code == 403, 20), "al cambiar el médico en la agenda, el anterior pierde el acceso")
chk("<span>Solicitante</span>DR. EXTERNO SOTO" in adm.get(P + f"/estudio/{eidm}").text, "y el nuevo médico escrito queda como solicitante")

# --- bloqueo por intentos
s4 = requests.Session()
for _ in range(9):
    r = s4.get(P + "/paciente"); cs = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
    r = s4.post(P + "/paciente", data={"csrf": cs, "rut": "33333333-3", "clave": "MALA"})
chk(r.status_code == 429, "bloqueo tras muchos intentos fallidos")

print("\nFALLAS:", fallas)
sys.exit(1 if fallas else 0)
