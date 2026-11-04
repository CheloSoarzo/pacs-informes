"""Prueba: nodo DICOM para Horos (archivo .plist) y botón «Abrir en Horos / OsiriX».
Requiere el PACS simulado y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva)."""
import io
import plistlib
import re
import zipfile

import requests

B = "http://localhost:8000"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def sesion(u, c="clave-larga-123"):
    s = requests.Session()
    s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text
    m = re.search(r'name="csrf" value="([^"]+)"', t)
    s.csrf = m.group(1) if m else ""
    return s


adm = sesion("admin", "clave-prueba-123")
t = adm.get(B + "/admin/equipos").text
chk("Horos / OsiriX" in t and "/admin/equipos/horos.plist" in t, "la pantalla de equipos tiene la sección Horos con el archivo")
r = adm.get(B + "/admin/equipos/horos.plist")
nodos = plistlib.loads(r.content)
n = nodos[0]
chk(r.ok and "attachment" in r.headers.get("Content-Disposition", "") and isinstance(nodos, list), "descarga un .plist (lista de nodos)")
chk(n["AETitle"] == "PACS" and n["Port"] == 46429 and n["Address"] == "203.0.113.50", f"datos del PACS: {n['Address']}:{n['Port']} {n['AETitle']}")
chk(n["retrieveMode"] == 1 and n["QR"] and n["Send"] and n["Activated"], "recuperación C-GET, búsqueda y envío activados")

perf = dict((nm, int(i)) for i, nm in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
for u, nm, p in [("tec", "TM Daniela", "Tecnólogos"), ("trans", "Carla Muñoz", "Transcripcionistas")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": nm, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
tec, trans = sesion("tec"), sesion("trans")
chk(tec.get(B + "/admin/equipos/horos.plist").status_code == 403, "el archivo de nodos es solo para quien administra equipos")
t = tec.get(B + "/estudio/est-ct").text
chk('data-horos="horos"' in t and 'data-horos="osirix"' in t, "el estudio tiene «Abrir en Horos» y «OsiriX»")
chk(trans.post(B + "/estudio/est-ct/horos", headers={"X-CSRF": trans.csrf}).status_code == 403, "sin permiso de descarga: 403")
d = tec.post(B + "/estudio/est-ct/horos", headers={"X-CSRF": tec.csrf}).json()
chk(d["horos"].startswith("horos://?methodName=DownloadURL&Display=YES&URL='http") and d["osirix"].startswith("osirix://"),
    f"arma el enlace para Horos: {d['horos'][:60]}…")
z = requests.get(d["zip"])   # Horos lo descarga sin la sesión del navegador
chk(z.ok and zipfile.is_zipfile(io.BytesIO(z.content)), "el enlace temporal entrega el ZIP del estudio sin sesión")
requests.get(d["zip"]); requests.get(d["zip"])
chk(requests.get(d["zip"]).status_code == 404, "el enlace sirve pocas veces (luego 404)")
chk(requests.get(B + "/dl/" + "x" * 32 + ".zip").status_code == 404, "un enlace inventado no sirve")
chk("abrir_en_horos" in adm.get(B + "/admin/auditoria").text, "queda en la auditoría")
print("\nFALLAS:", fallas)
