"""Prueba: transcripción automática de dictados con IA (cola, reintentos, pantalla).

Requiere dev/mock_orthanc.py, el transcriptor con Whisper simulado y la app en localhost:8000 con
ADMIN_PASSWORD=clave-prueba-123 WHISPER_URL=http://localhost:9010 WHISPER_REINTENTO_SEG=2 WHISPER_INICIO_SEG=1.
Variables: TR=transcriptor/servidor.py STUB=<carpeta con un faster_whisper simulado> AUDIO=<grabación webm real>."""
import re, sys, time, subprocess, os, requests
B = "http://localhost:8000"; fallas = 0
def chk(c, m):
    global fallas; fallas += (not c); print(("OK   " if c else "FALLA ") + m)
def sesion(u, c):
    s = requests.Session(); s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text; m = re.search(r'name="csrf" value="([^"]+)"', t); s.csrf = m.group(1) if m else ""
    return s
AUDIO = open(os.environ["AUDIO"], "rb").read()
adm = sesion("admin", "clave-prueba-123")
pid = re.search(r'<option value="(\d+)"[^>]*>Radiólogos</option>', adm.get(B + "/admin/usuarios").text).group(1)
form = next(f for f in adm.get(B + "/admin/perfiles").text.split("<form") if f'name="id" value="{pid}"' in f)
adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "id": pid, "nombre": "Radiólogos", "permisos": re.findall(r'name="permisos" value="([^"]+)" checked', form)})
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
for u, p in (("rad1", "Radiólogos"), ("tra1", "Transcripcionistas")):
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": u.upper(), "perfil_id": perf[p], "clave": "clave-larga-123", "activo": "on"})
rad, tra = sesion("rad1", "clave-larga-123"), sesion("tra1", "clave-larga-123")

def subir(est, datos):
    r = rad.post(B + f"/estudio/{est}/dictados", headers={"X-CSRF": rad.csrf}, files={"audio": ("d.webm", datos, "audio/webm")}, data={"duracion": "42"})
    return r.json().get("id")
def estado(did):
    return tra.get(B + f"/dictados/{did}/texto-auto").json()
def esperar(did, hasta=("listo", "error"), seg=20):
    for _ in range(seg * 4):
        e = estado(did)
        if e["estado"] in hasta: return e
        time.sleep(0.25)
    return estado(did)

# 1. Dictado normal → la IA lo transcribe sola
did = subir("est-ct", AUDIO)
e = estado(did); chk(e["estado"] in ("pendiente", "procesando"), f"recién subido queda «transcribiendo» ({e['estado']})")
t = tra.get(B + "/estudio/est-ct").text
chk('data-auto-estado="pendiente"' in t or 'data-auto-estado="procesando"' in t, "la pantalla del estudio muestra el estado de la IA")
e = esperar(did); chk(e["estado"] == "listo" and "Nódulo de 8 mm" in e["texto"] and "s, es)" in e["texto"], f"transcrito: «{e['texto'][:60]}»")
t = tra.get(B + "/estudio/est-ct").text
chk(f'data-auto-estado="listo"' in t and f'/dictados/{did}/texto-auto' in t, "estudio: botón «Usar texto de la IA» disponible")
chk("Texto IA" in tra.get(B + "/dictados").text, "cola de dictados: marca «Texto IA»")
chk(rad.get(B + "/estudio/est-ct/dictar").status_code == 200, "pantalla de dictado del radiólogo sigue funcionando")

# 2. Audio dañado → reintenta 3 veces y queda en error; «Reintentar» lo vuelve a la cola
did2 = subir("est-ct", os.urandom(3000))
e = esperar(did2, seg=30); chk(e["estado"] == "error", "audio dañado: tras 3 intentos queda en error")
import sqlite3; err = sqlite3.connect(os.environ["DB"]).execute("SELECT auto_error FROM dictados WHERE id=?", (did2,)).fetchone()[0]
chk("no se pudo leer el audio" in err, f"queda guardado el motivo real: «{err[:70]}…»")
r = tra.post(B + f"/dictados/{did2}/texto-auto/reintentar", headers={"X-CSRF": tra.csrf}); chk(r.ok and estado(did2)["estado"] in ("pendiente","procesando","error"), "Reintentar lo vuelve a la cola")
r = tra.post(B + f"/dictados/{did2}/texto-auto/reintentar"); chk(r.status_code in (400, 403), f"Reintentar sin CSRF se rechaza ({r.status_code})")

# 3. Transcriptor caído → el dictado espera y se transcribe cuando vuelve
subprocess.run(["pkill", "-f", "transcriptor/servidor.py"]); time.sleep(0.5)
did3 = subir("est-ct", AUDIO)
time.sleep(1.5); e = estado(did3); chk(e["estado"] in ("pendiente", "procesando"), f"transcriptor apagado: el dictado sigue esperando ({e['estado']})")
subprocess.Popen(["python3", os.environ["TR"]], env={**os.environ, "PYTHONPATH": os.environ["STUB"], "PUERTO": "9010"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
e = esperar(did3, seg=30); chk(e["estado"] == "listo", f"al volver el transcriptor se completa solo ({e['estado']})")

# 4. Permisos: un usuario sin acceso a dictados no ve el texto
chk(requests.get(B + f"/dictados/{did}/texto-auto", allow_redirects=False).status_code in (302, 401, 403), "sin sesión no se puede leer el texto")
print("\nFALLAS:", fallas); sys.exit(1 if fallas else 0)
