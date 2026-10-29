"""Prueba: corregir datos del paciente y visor dentro del informe.

Requiere dev/mock_orthanc.py y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva).
"""
import re
import requests

B = "http://localhost:8000"; M = "http://localhost:8042"; fallas = 0


def chk(c, m):
    global fallas; fallas += (not c); print(("OK   " if c else "FALLA ") + m)


def sesion(u, c):
    s = requests.Session(); s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text; m = re.search(r'name="csrf" value="([^"]+)"', t); s.csrf = m.group(1) if m else ""
    return s


adm = sesion("admin", "clave-prueba-123")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
for u, perfil in (("tec1", "Tecnólogos"), ("rad1", "Radiólogos"), ("der1", "Médicos de Consultas")):
    d = {"csrf": adm.csrf, "usuario": u, "nombre": u.upper(), "perfil_id": perf[perfil], "clave": "clave-larga-123", "activo": "on"}
    if perfil == "Médicos de Consultas":
        d["filtro_solicitante"] = "ROJAS"
    adm.post(B + "/admin/usuarios", data=d)
tec, rad, der = sesion("tec1", "clave-larga-123"), sesion("rad1", "clave-larga-123"), sesion("der1", "clave-larga-123")

# --- lista: doble clic solo para quien puede corregir
lt = tec.get(B + "/").text
chk('data-estudio="est-ct"' in lt and 'dlg-paciente' in lt, "tecnólogo: filas editables y ventana en la lista")
lr = rad.get(B + "/").text
chk('data-estudio=' not in lr and 'dlg-paciente' not in lr and 'data-url="/estudio/est-ct"' in lr,
    "radiólogo: sin edición (doble clic abre el estudio)")

# --- datos actuales
r = tec.get(B + "/estudio/est-ct/datos")
d = r.json()["datos"] if r.ok else {}
chk(d.get("apellidos") == "PEREZ SOTO" and d.get("nombres") == "JUAN ANDRES" and d.get("rut") == "12.345.678-5"
    and d.get("fnac") == "1970-03-12" and d.get("sexo") == "M", "GET datos editables")
chk(rad.get(B + "/estudio/est-ct/datos").status_code == 403, "radiólogo no puede corregir (403)")

# --- informe + nota antes de corregir
rad.post(B + "/estudio/est-ct/guardar", json={"cuerpo": "Informe de prueba"}, headers={"X-CSRF": rad.csrf})
tec.post(B + "/estudio/est-ct/notas", data={"csrf": tec.csrf, "texto": "nota previa"})

# --- validación
r = tec.post(B + "/estudio/est-ct/datos", data={"csrf": tec.csrf, "apellidos": "", "rut": "", "sexo": "M"})
chk("Falta el apellido" in r.text, "rechaza apellido vacío")
r = tec.post(B + "/estudio/est-ct/datos", data={"csrf": tec.csrf, **d, "fnac": "2999-01-01"})
chk("fecha de nacimiento no es válida" in r.text, "rechaza fecha futura")
r = tec.post(B + "/estudio/est-ct/datos", data={"csrf": tec.csrf, **d})
chk("No hubo cambios" in r.text, "sin cambios no toca el PACS")

# --- corregir nombre (mismo RUT: mismo ID)
d2 = dict(d, nombres="Juan Andrés", descripcion="tc de cerebro")
r = tec.post(B + "/estudio/est-ct/datos", data={"csrf": tec.csrf, **d2})
e = requests.get(M + "/studies/est-ct").json()
chk(r.url.endswith("/estudio/est-ct") and e["PatientMainDicomTags"]["PatientName"] == "PEREZ SOTO^JUAN ANDRÉS"
    and e["MainDicomTags"]["StudyDescription"] == "TC DE CEREBRO", "corrige nombre y examen en el PACS (mayúsculas)")

# --- corregir RUT: el PACS cambia el ID y todo se mueve
d3 = dict(d2, rut="11.111.111-1", fnac="1971-04-01")
r = tec.post(B + "/estudio/est-ct/datos", data={"csrf": tec.csrf, **d3})
chk(r.url.endswith("/estudio/est-ct-mod"), "cambia RUT → redirige al ID nuevo")
chk(requests.get(M + "/studies/est-ct").status_code == 404, "copia anterior borrada del PACS")
t = rad.get(B + "/estudio/est-ct-mod").text
chk("Informe de prueba" in t, "el informe siguió al estudio")
chk("nota previa" in tec.get(B + "/estudio/est-ct-mod").text, "las notas siguieron al estudio")
chk("11.111.111-1" in t and "JUAN ANDRÉS PEREZ SOTO" in t, "pantalla muestra los datos nuevos")
pdf = rad.get(B + "/estudio/est-ct-mod").text
aud = adm.get(B + "/admin/auditoria").text
chk("editar_paciente" in aud and "12.345.678-5" in aud and "11.111.111-1" in aud, "auditoría con dato anterior y nuevo")

# --- visor dentro del informe
chk('data-modo="ohif"' in t and 'data-ohif="' in t and "/ohif/viewer?StudyInstanceUIDs=1.2.826.0.1.1" in t,
    "pestaña visor diagnóstico con el estudio")
chk('data-defecto="ohif"' in t, "radiólogo parte en el visor diagnóstico")
tt = tec.get(B + "/estudio/est-us").text
chk('data-defecto="rapida"' in tt and 'data-editar-paciente="est-us"' in tt, "tecnólogo: vista rápida y botón Corregir datos")
chk('data-editar-paciente' not in rad.get(B + "/estudio/est-us").text, "radiólogo sin botón Corregir datos")

print("FALLAS:", fallas)
