"""Prueba de la agenda: cargar un respaldo (reemplaza los datos) y que el PACS se ponga al día.
Requiere la agenda en :3000 (ADMIN_CLAVE=clave-agenda-123) conectada a la app del PACS en :8000 (base nueva).

    python dev/prueba_agenda_respaldo.py <carpeta DATA_DIR de la agenda> <archivo informes.db del PACS>
"""
import os
import sqlite3
import sys
import time
from datetime import date

import requests

A = "http://localhost:3000"
fallas = 0
DATA, PACSDB = sys.argv[1], sys.argv[2]


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def login():
    s = requests.Session()
    s.post(A + "/api/login", json={"usuario": "admin", "clave": "clave-agenda-123"})
    return s


hoy = date.today().isoformat()
ag = login()
nueva = lambda h, p: ag.post(A + "/api/citas", json={"fecha": hoy, "hora": h, "equipo": "tc", "paciente": p, "rut": "11.111.111-1",
                                                     "prevision": "Particular", "examenes": [{"nombre": "TC", "valor": 1000}]}).json()
c1, c2 = nueva("09:00", "Paciente Uno"), nueva("09:30", "Paciente Dos")
resp = ag.get(A + "/api/respaldo")
chk(resp.ok and resp.content[:15] == b"SQLite format 3", "se descarga el respaldo")
c3 = nueva("10:00", "Paciente Tres (después del respaldo)")
time.sleep(2)

r = ag.post(A + "/api/respaldo/revisar", data=b"esto no es una base de datos", headers={"Content-Type": "application/octet-stream"})
chk(r.status_code == 400 and "no es un respaldo" in r.json().get("error", ""), "rechaza un archivo que no es respaldo")
r = ag.post(A + "/api/respaldo/revisar", data=resp.content, headers={"Content-Type": "application/octet-stream"}).json()
chk(r["actual"]["citas"] == 3 and r["respaldo"]["citas"] == 2, f"compara: ahora {r['actual']['citas']} citas, respaldo {r['respaldo']['citas']}")
tok = r["token"]
chk(ag.post(A + f"/api/respaldo/restaurar?token={tok}&confirmar=no").status_code == 400, "sin escribir RESTAURAR no hace nada")
chk(len(ag.get(A + f"/api/citas?fecha={hoy}").json()) == 3, "todo sigue igual")
d = ag.post(A + f"/api/respaldo/restaurar?token={tok}&confirmar=restaurar").json()
chk(d.get("ok") and d["citas"] == 2, "restaura el respaldo")
chk(ag.get(A + f"/api/citas?fecha={hoy}").status_code == 401, "hay que volver a iniciar sesión")
ag = login()
pacs = {c["paciente"] for c in ag.get(A + f"/api/citas?fecha={hoy}").json()}
chk(pacs == {"Paciente Uno", "Paciente Dos"}, "quedan las citas del respaldo")
chk(any(n.startswith("antes-de-restaurar-") for n in os.listdir(os.path.join(DATA, "respaldos"))), "guardó una copia de lo anterior")
n4 = nueva("11:00", "Paciente Cuatro")
chk(n4["id"] > c3["id"] and n4["accesion"] not in (c1["accesion"], c2["accesion"], c3["accesion"]),
    "las citas nuevas no reutilizan números ya usados en esta agenda")
# el PACS borra la orden de la cita que ya no existe
ok = False
for _ in range(30):
    con = sqlite3.connect(PACSDB)
    f = con.execute("SELECT eliminada FROM ordenes WHERE accesion=?", (c3["accesion"],)).fetchone()
    con.close()
    if f and f[0] == 1:
        ok = True
        break
    time.sleep(1)
chk(ok, "el PACS elimina la orden de la cita que no estaba en el respaldo")

# Respaldo de OTRA agenda: una cita con el mismo número pero otro paciente recibe un número de acceso nuevo
import tempfile
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=DATA); tmp.write(resp.content); tmp.close()
con = sqlite3.connect(tmp.name)
con.execute("UPDATE citas SET rut='22.222.222-2', paciente='Otro Centro' WHERE id=?", (c1["id"],)); con.commit(); con.close()
r = ag.post(A + "/api/respaldo/revisar", data=open(tmp.name, "rb").read(), headers={"Content-Type": "application/octet-stream"}).json()
ag.post(A + f"/api/respaldo/restaurar?token={r['token']}&confirmar=RESTAURAR")
ag = login()
cs = {c["paciente"]: c for c in ag.get(A + f"/api/citas?fecha={hoy}").json()}
chk(cs["Otro Centro"]["accesion"] == c1["accesion"] + "R1", f"cita de otro paciente con el mismo número: acceso nuevo ({cs['Otro Centro']['accesion']})")
chk(cs["Paciente Dos"]["accesion"] == c2["accesion"], "la cita que es la misma conserva su número")
print("\nFALLAS:", fallas)
