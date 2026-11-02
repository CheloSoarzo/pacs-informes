"""Prueba: borrar un examen completo (administrador y TM; con confirmación extra si tiene informe validado).
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
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>',
                                               adm.get(B + "/admin/usuarios").text))
for u, n, p in [("rad", "Dr. Andrés López", "Radiólogos"), ("tec", "TM Daniela", "Tecnólogos"),
                ("trans", "Carla Muñoz", "Transcripcionistas"), ("sec", "Ana Secretaría", "Secretarias de Recepción")]:
    adm.post(B + "/admin/usuarios", data={"csrf": adm.csrf, "usuario": u, "nombre": n, "perfil_id": perf[p],
                                          "clave": "clave-larga-123", "activo": "on"})
S = {u: sesion(u) for u in ["rad", "tec", "trans", "sec"]}

chk("Borrar examen" in adm.get(B + "/estudio/est-us").text, "el administrador ve «Borrar examen»")
chk("Borrar examen" in S["tec"].get(B + "/estudio/est-us").text, "el TM ve «Borrar examen»")
for u in ("trans", "sec"):
    chk("Borrar examen" not in S[u].get(B + "/estudio/est-us").text, f"{u} no ve «Borrar examen»")


def borrar(s, eid, **datos):
    return s.post(B + f"/estudio/{eid}/borrar", data={"csrf": s.csrf, **datos})


chk(borrar(S["sec"], "est-us", confirmar="BORRAR", motivo="x").status_code == 403, "la secretaria no puede borrar")
chk("escribe BORRAR" in borrar(S["tec"], "est-us", confirmar="borar", motivo="prueba").text, "exige escribir BORRAR")
chk("por qué se borra" in borrar(S["tec"], "est-us", confirmar="BORRAR", motivo="").text, "exige el motivo")
chk(S["tec"].get(B + "/estudio/est-us").status_code == 200, "si falta algo, no se borra nada")

# Examen sin informe validado, con una nota: lo borra el TM
S["tec"].post(B + "/estudio/est-us/notas", data={"csrf": S["tec"].csrf, "texto": "nota de prueba"})
r = borrar(S["tec"], "est-us", confirmar="borrar", motivo="examen de prueba")
chk("Examen borrado" in r.text, "el TM borra un examen sin informe (BORRAR sin importar mayúsculas)")
chk(adm.get(B + "/estudio/est-us").status_code == 404, "el estudio ya no existe en el PACS")
chk("/estudio/est-us" not in adm.get(B + "/?desde=2000-01-01").text, "ya no aparece en la lista")
aud = adm.get(B + "/admin/auditoria").text
chk("borrar_examen" in aud and "examen de prueba" in aud and "sin informe" in aud, "queda en la auditoría con el motivo")

# Examen con informe validado: pide la casilla extra
rid = re.search(r'<option value="(\d+)"[^>]*>Dr\. Andrés López</option>', adm.get(B + "/estudio/est-ct").text).group(1)
adm.post(B + "/estudio/est-ct/asignar", data={"csrf": adm.csrf, "radiologo_id": rid, "prioridad": "normal"})
S["rad"].post(B + "/estudio/est-ct/firmar", headers={"X-CSRF": S["rad"].csrf},
              json={"cuerpo": "HALLAZGOS:\nSin alteraciones.\n\nIMPRESIÓN:\nNormal."})
t = adm.get(B + "/estudio/est-ct").text
chk("informe validado" in t and 'name="validado_ok"' in t, "con informe validado aparece la confirmación extra")
inf_id = re.search(r"/informe/(\d+)\.pdf", t).group(1)
link = adm.post(B + f"/informe/{inf_id}/link", headers={"X-CSRF": adm.csrf}).json()
chk("informe validado también se borra" in borrar(adm, "est-ct", confirmar="BORRAR", motivo="paciente equivocado").text
    or "casilla" in borrar(adm, "est-ct", confirmar="BORRAR", motivo="paciente equivocado").text,
    "sin la casilla no se borra un examen validado")
chk(adm.get(B + "/estudio/est-ct").status_code == 200, "sigue existiendo")
r = borrar(adm, "est-ct", confirmar="BORRAR", motivo="paciente equivocado", validado_ok="1")
chk("Examen borrado" in r.text, "con la casilla, el administrador lo borra")
chk(adm.get(B + f"/informe/{inf_id}.pdf").status_code in (403, 404), "el PDF del informe ya no existe")
pac = requests.get(link["url"])
chk(pac.status_code in (404, 410) or "no está disponible" in pac.text.lower() or "no existe" in pac.text.lower(),
    "el link del paciente deja de funcionar")
aud = adm.get(B + "/admin/auditoria").text
chk("informe VALIDADO" in aud and "Dr. Andrés López" in aud, "la auditoría registra que tenía informe validado y por quién")

# --- Desde la lista: tarro de basura al final de cada fila
t = adm.get(B + "/?desde=2000-01-01").text
chk('data-borrar="/estudio/est-rx/borrar"' in t and 'id="dlg-borrar"' in t, "la lista muestra el tarro de basura y la ventana de borrado")
chk("data-borrar" not in S["sec"].get(B + "/?desde=2000-01-01").text, "la secretaria no ve el tarro de basura")
chk("data-borrar" in S["tec"].get(B + "/?desde=2000-01-01").text, "el TM ve el tarro de basura")
r = adm.post(B + "/estudio/est-rx/borrar", data={"csrf": adm.csrf, "confirmar": "no", "motivo": "x", "volver": "lista",
                                                "siguiente": "/?desde=2000-01-01"})
chk(r.url.endswith("/?desde=2000-01-01") and "escribe BORRAR" in r.text, "un error desde la lista vuelve a la lista")
r = adm.post(B + "/estudio/est-rx/borrar", data={"csrf": adm.csrf, "confirmar": "BORRAR", "motivo": "duplicado",
                                                "volver": "lista", "siguiente": "//evil.com/"})
chk(r.url.rstrip("/") == B and "Examen borrado" in r.text, "borra desde la lista (y no redirige fuera del sistema)")
print("\nFALLAS:", fallas)
