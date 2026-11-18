"""v1.1.70: «Guardar como pendiente» (Pendiente de consulta) y deshacer/rehacer en el editor del informe.
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


adm = sesion("admin", "clave-prueba-123")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": "trans", "nombre": "Carla Muñoz",
                                      "perfil_id": perf["Transcripcionistas"], "clave": "clave-larga-123", "activo": "on"})
tr = sesion("trans")
H = {"X-CSRF": tr.csrf}
texto = "Hígado de tamaño normal. Lesión hipoecogénica de 8 mm en segmento VI."
r = tr.post(B + "/estudio/est-us/pendiente", json={"cuerpo": "corto"}, headers=H)
chk(r.status_code == 400, "pendiente con informe vacío: no deja")
r = tr.post(B + "/estudio/est-us/pendiente", json={"cuerpo": texto}, headers=H)
chk(r.ok, "guardar como pendiente")
t = tr.get(B + "/estudio/est-us").text
chk("Pendiente de consulta" in t and texto[:20] in t, "el estudio muestra «Pendiente de consulta» y el texto guardado")
l = tr.get(B + "/?desde=2000-01-01").text
chk("estado-consulta" in l and "Retomar" in l, "la lista tiene el filtro y el botón «Retomar»")
chk("est-us" in tr.get(B + "/?desde=2000-01-01&estado=consulta").text, "filtro «Pendiente de consulta» lo muestra")
tr.post(B + "/estudio/est-us/guardar", json={"cuerpo": texto + " Sin otros hallazgos."}, headers=H)
chk("Pendiente de consulta" in tr.get(B + "/estudio/est-us").text, "el guardado automático no le quita el estado")
r = tr.post(B + "/estudio/est-us/enviar-validacion", json={"cuerpo": texto.replace("8 mm", "18 mm")}, headers=H)
chk(r.ok and "Por validar" in tr.get(B + "/estudio/est-us").text, "aclarada la duda, se termina como siempre (Por validar)")

# Navegador: botón «Guardar como pendiente» y deshacer / rehacer
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 950})
    pg.goto(B + "/login"); pg.fill("input[name=usuario]", "trans"); pg.fill("input[name=clave]", "clave-larga-123")
    pg.click("button[type=submit]")
    pg.goto(B + "/estudio/est-ct"); pg.wait_for_timeout(800)
    ed = pg.locator(".editor-rico.principal")
    chk(pg.locator(".f-deshacer").is_disabled(), "deshacer empieza desactivado")
    ed.click(); pg.keyboard.type("Primera frase."); pg.wait_for_timeout(900)
    pg.keyboard.type(" Segunda frase."); pg.wait_for_timeout(900)
    pg.select_option("#plantilla", index=1) if pg.locator("#plantilla option").count() > 1 else None
    pg.click(".f-deshacer"); pg.wait_for_timeout(200)
    chk(ed.inner_text().strip() == "Primera frase.", "deshacer (botón) quita la última frase")
    pg.click(".f-rehacer"); pg.wait_for_timeout(200)
    chk(ed.inner_text().strip() == "Primera frase. Segunda frase.", "rehacer la vuelve a poner")
    ed.click(); pg.keyboard.press("Control+z"); pg.wait_for_timeout(200)
    chk(ed.inner_text().strip() == "Primera frase.", "Ctrl+Z también deshace")
    pg.keyboard.press("Control+y"); pg.wait_for_timeout(200)
    chk(ed.inner_text().strip() == "Primera frase. Segunda frase.", "Ctrl+Y rehace")
    pg.wait_for_timeout(3200)
    chk("Guardado" in pg.inner_text("#estado-guardado"), "lo deshecho/rehecho se guarda solo")
    pg.locator(".barra-formato").first.screenshot(path="/tmp/p70/barra.png")
    pg.click("#btn-pendiente"); pg.wait_for_timeout(300)
    pg.locator(".botones-informe").screenshot(path="/tmp/p70/botones.png")
    pg.locator("#confirmar").screenshot(path="/tmp/p70/confirmar.png")
    pg.click("#c-si"); pg.wait_for_timeout(1500)
    chk("Pendiente de consulta" in pg.inner_text("body"), "en el navegador queda «Pendiente de consulta»")
    pg.goto(B + "/?desde=2000-01-01&estado=consulta"); pg.wait_for_timeout(800)
    pg.screenshot(path="/tmp/p70/lista.png")
    b.close()
print("\nFALLAS:", fallas)
