"""v1.1.79: «Cancelar borrador» en el informe y el editor vacío no crea borrador.
Usa mock_orthanc + app en :8000 con base vacía (ADMIN_PASSWORD=clave-prueba-123)."""
import re

import requests
from playwright.sync_api import sync_playwright

B = "http://localhost:8000"
fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


def sesion(u, c="clave-larga-123"):
    s = requests.Session()
    s.post(B + "/login", data={"usuario": u, "clave": c})
    s.csrf = re.search(r'name="csrf" value="([^"]+)"', s.get(B + "/").text).group(1)
    return s


def estado(s, est):
    l = s.get(B + "/?desde=2000-01-01").text
    m = re.search(r'/estudio/' + est + r'".*?estado-(\w+)', l, re.S)
    return m.group(1) if m else "?"


adm = sesion("admin", "clave-prueba-123")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": "trans", "nombre": "Carla Muñoz",
                                      "perfil_id": perf["Transcripcionistas"], "clave": "clave-larga-123", "activo": "on"})
tr = sesion("trans")
H = {"X-CSRF": tr.csrf}

# El guardado automático con el editor vacío no crea borrador
r = tr.post(B + "/estudio/est-us/guardar", json={"cuerpo": "<p><br></p>"}, headers=H)
chk(r.ok and "Cancelar borrador" not in tr.get(B + "/estudio/est-us").text, "editor vacío: no queda como borrador")
chk("Borrador" not in tr.get(B + "/estudio/est-us").text.split("Texto del informe")[0], "el estudio sigue pendiente")

# Con texto queda borrador y aparece el botón
tr.post(B + "/estudio/est-us/guardar", json={"cuerpo": "Prueba que no sirve."}, headers=H)
t = tr.get(B + "/estudio/est-us").text
chk("Cancelar borrador" in t, "con texto: queda borrador y aparece «Cancelar borrador»")
r = tr.post(B + "/estudio/est-us/cancelar-borrador", headers=H)
chk(r.ok, "cancelar el borrador")
t = tr.get(B + "/estudio/est-us").text
chk("Prueba que no sirve" not in t and "Cancelar borrador" not in t, "el texto se descartó y el estudio volvió a pendiente")
r = tr.post(B + "/estudio/est-us/cancelar-borrador", headers=H)
chk(r.status_code == 409, "cancelar sin borrador: no hace nada")

# Un informe pendiente de consulta o validado no se cancela
tr.post(B + "/estudio/est-us/pendiente", json={"cuerpo": "Hígado de tamaño normal, sin lesiones."}, headers=H)
r = tr.post(B + "/estudio/est-us/cancelar-borrador", headers=H)
chk(r.status_code == 409 and "Cancelar borrador" not in tr.get(B + "/estudio/est-us").text, "«Pendiente de consulta» no se cancela")

# Navegador: botón, confirmación y vuelta a pendiente
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 950})
    pg.goto(B + "/login"); pg.fill("input[name=usuario]", "trans"); pg.fill("input[name=clave]", "clave-larga-123")
    pg.click("button[type=submit]")
    pg.goto(B + "/estudio/est-ct"); pg.wait_for_timeout(800)
    ed = pg.locator(".editor-rico.principal")
    ed.click(); pg.keyboard.type("Texto de prueba."); pg.wait_for_timeout(3500)
    pg.reload(); pg.wait_for_timeout(800)
    chk(pg.locator("#btn-cancelar-borrador").is_visible(), "el botón aparece en el borrador")
    pg.click("#btn-cancelar-borrador"); pg.wait_for_timeout(200)
    pg.screenshot(path="/tmp/cancelar-borrador.png", full_page=False)
    pg.click("#c-si"); pg.wait_for_timeout(1500)
    chk(ed.inner_text().strip() == "" and not pg.locator("#btn-cancelar-borrador").count(), "tras confirmar, el informe queda vacío y sin botón")
    b.close()
print("FALLAS:", fallas)
