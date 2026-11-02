"""Prueba: ficha del paciente (todos sus exámenes por RUT).
Requiere el PACS simulado y la app en localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 (base nueva)."""
import re

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
t = adm.get(B + "/estudio/est-ct").text
chk("Ficha del paciente" in t, "la ficha del estudio tiene el botón «Ficha del paciente»")
href = re.search(r'href="(/ficha/[^"]+)"', t).group(1)
t = adm.get(B + href).text
ids = set(re.findall(r'href="/estudio/([^"]+)"', t))
chk(len(ids) >= 2 and "est-ct" in ids, f"la ficha muestra todos los exámenes del paciente ({len(ids)})")
chk("MARIA JOSE" not in t and "CATALINA" not in t, "no muestra exámenes de otros pacientes")
t2 = adm.get(B + "/ficha/12345678-5").text
chk(set(re.findall(r'href="/estudio/([^"]+)"', t2)) == ids, "funciona con el RUT con o sin puntos")
chk(adm.get(B + "/ficha/99999999-9").url.rstrip("/") == B, "un RUT sin exámenes vuelve a la lista con aviso")

perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": "rad", "nombre": "Dr. Andrés López",
                                      "perfil_id": perf["Radiólogos"], "clave": "clave-larga-123", "activo": "on"})
rad = sesion("rad")
rid = re.search(r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-ct").text).group(1)
adm.post(B + "/estudio/est-ct/asignar", data={"csrf": adm.csrf, "radiologo_id": rid, "prioridad": "normal"})
t = rad.get(B + href).text
chk(set(re.findall(r'href="/estudio/([^"]+)"', t)) == {"est-ct"}, "el radiólogo ve en la ficha solo lo que tiene asignado")
print("\nFALLAS:", fallas)
