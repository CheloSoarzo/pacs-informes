"""Prueba automática de todo el flujo. Requiere: dev/mock_orthanc.py y dev/smtp_falso.py corriendo, y la app en
localhost:8000 con ADMIN_PASSWORD=clave-prueba-123 LINK_REQUIERE_CLAVE=true SMTP_HOST=127.0.0.1 SMTP_PORT=2525
SMTP_STARTTLS=false SMTP_FROM=informes@demo.cl, sobre una base de datos vacía."""
import re, os, glob, subprocess, requests
B = "http://localhost:8000"; fallas = 0
def chk(c, m):
    global fallas; fallas += (not c); print(("OK   " if c else "FALLA ") + m)
def sesion(u, c="clave-larga-123"):
    s = requests.Session(); s.post(B + "/login", data={"usuario": u, "clave": c})
    t = s.get(B + "/").text; m = re.search(r'name="csrf" value="([^"]+)"', t); s.csrf = m.group(1) if m else ""; return s
def H(s): return {"X-CSRF": s.csrf}
def n_filas(t): return t.count('class="paciente"')

adm = sesion("admin", "clave-prueba-123")
t = adm.get(B + "/admin/perfiles").text
chk("Transcripcionistas - Avanzado" in t and "Médicos de Consultas" in t and "Tecnólogos de Ecografías" in t, "10 perfiles iniciales (como pacs4me)")
perf = dict((n, int(i)) for i, n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/admin/usuarios").text))
def crear(u, nombre, perfil, **kw):
    d = {"csrf": adm.csrf, "usuario": u, "nombre": nombre, "perfil_id": perf[perfil], "clave": "clave-larga-123", "activo": "on"}; d.update(kw)
    return adm.post(B + "/admin/usuarios", data=d).text
for args in [("rad", "Dr. Andrés López", "Radiólogos"), ("rad2", "Dra. Paula Reyes", "Radiólogos"),
             ("trans", "Carla Muñoz", "Transcripcionistas"), ("tec", "TM Daniela Fuentes", "Tecnólogos"),
             ("sec", "Ana Secretaría", "Secretarias de Recepción"), ("ent", "Pedro Entrega", "Entrega de Exámenes")]:
    chk("Usuario guardado" in crear(*args), "crear " + args[0])
chk("solo ve sus exámenes" in crear("der0", "X", "Médicos de Consultas"), "médico de consulta sin nombre rechazado")
chk("Usuario guardado" in crear("der", "Dra. Carolina Rojas", "Médicos de Consultas", filtro_solicitante="CAROLINA ROJAS"), "crear derivador")
chk("Usuario guardado" in crear("radsur", "Dr. Sur", "Radiólogos", centros="CENTRO SUR"), "crear radiólogo Centro Sur")
S = {u: sesion(u) for u in ["rad", "rad2", "trans", "tec", "sec", "ent", "der", "radsur"]}

for u, s in S.items(): chk(s.get(B + "/").status_code == 200, f"lista abre para {u}")
chk(n_filas(S["der"].get(B + "/").text) == 1, "derivador ve solo el examen que solicitó")
chk(n_filas(S["radsur"].get(B + "/").text) == 1, "radiólogo Centro Sur ve 1")

# --- asignación masiva + urgencia
r = adm.post(B + "/estudios/acciones", data={"csrf": adm.csrf, "accion": "asignar", "ids": ["est-ct", "est-rx"], "radiologo_id": "", "volver": ""})
rid = re.search(r'<option value="(\d+)">Dr\. Andrés López</option>', adm.get(B + "/").text).group(1)
r = adm.post(B + "/estudios/acciones", data={"csrf": adm.csrf, "accion": "asignar", "ids": ["est-ct", "est-rx"], "radiologo_id": rid, "volver": ""})
chk("2 estudio(s) asignados" in r.text, "asignación masiva")
r = adm.post(B + "/estudios/acciones", data={"csrf": adm.csrf, "accion": "urgente", "ids": ["est-ct"], "volver": ""})
chk("URGENTE" in r.text and "fila-urgente" in r.text, "marcar urgente")
chk(S["trans"].post(B + "/estudios/acciones", data={"csrf": S["trans"].csrf, "accion": "asignar", "ids": ["est-ct"], "radiologo_id": rid}).status_code == 403, "transcripcionista no asigna")
t = S["rad"].get(B + "/?asignado=mio").text
chk(n_filas(t) == 2 and t.index("JUAN ANDRES") < t.index("CATALINA"), "rad ve sus 2 asignados, urgente primero")

# --- dictado de audio
audio = b"\x1aE\xdf\xa3" + os.urandom(4000)
r = S["rad"].post(B + "/estudio/est-ct/dictados", files={"audio": ("d.webm", audio, "audio/webm")}, data={"duracion": "42.5", "nota": "ojo lateralidad"}, headers=H(S["rad"]))
chk(r.status_code == 200, "radiólogo sube dictado")
chk(S["trans"].post(B + "/estudio/est-ct/dictados", files={"audio": ("d.webm", audio, "audio/webm")}, headers=H(S["trans"])).status_code == 403, "transcripcionista no puede dictar")
did = r.json()["id"]
t = S["trans"].get(B + "/").text
chk("Por transcribir" in t and '<span class="contador">1</span>' in t, "lista y contador muestran dictado pendiente")
t = S["trans"].get(B + "/dictados").text
chk("JUAN ANDRES" in t and "0:42" in t and "URGENTE" in t, "cola de dictados")
r = S["trans"].get(B + f"/dictados/{did}/audio", headers={"Range": "bytes=0-99"})
chk(r.status_code == 206 and len(r.content) == 100, "audio con saltos (Range) para transcribir")
chk(S["sec"].get(B + f"/dictados/{did}/audio").status_code == 403, "secretaría no escucha audio")
chk(S["der"].get(B + f"/dictados/{did}/audio").status_code == 403, "derivador no escucha audio")
chk("ojo lateralidad" in S["trans"].get(B + "/estudio/est-ct").text, "nota del dictado visible")

# --- transcripción
S["trans"].post(B + f"/dictados/{did}/tomar", data={"csrf": S["trans"].csrf})
chk("Carla Muñoz" in S["trans"].get(B + "/dictados").text, "dictado tomado por transcripcionista")
cuerpo = "TÉCNICA:\nTC de cerebro sin contraste.\n\nHALLAZGOS:\nSin hemorragia.\n\nIMPRESIÓN:\nNormal."
r = S["trans"].post(B + "/estudio/est-ct/guardar", json={"cuerpo": cuerpo}, headers=H(S["trans"])); chk(r.ok, "transcripcionista guarda borrador")
chk(S["trans"].post(B + "/estudio/est-ct/firmar", json={"cuerpo": cuerpo}, headers=H(S["trans"])).status_code == 403, "transcripcionista no firma")
r = S["trans"].post(B + "/estudio/est-ct/enviar-validacion", json={"cuerpo": cuerpo}, headers=H(S["trans"])); chk(r.ok, "enviar a validación")
chk("Por validar" in S["rad"].get(B + "/?estado=por_validar&asignado=mio").text, "rad ve por validar")
chk("Por validar" not in S["sec"].get(B + "/").text, "secretaría no ve estados internos")
t = S["der"].get(B + "/estudio/est-ct").text
chk("Sin hemorragia" not in t, "derivador no ve borrador")

# --- validación en lote
r = S["rad"].post(B + "/estudios/acciones", data={"csrf": S["rad"].csrf, "accion": "validar", "ids": ["est-ct", "est-rx"], "confirmado": "1"})
chk("1 informe(s) validados" in r.text and "1 omitido" in r.text, "validación en lote (omite los no listos)")
chk(S["trans"].post(B + "/estudios/acciones", data={"csrf": S["trans"].csrf, "accion": "validar", "ids": ["est-ct"], "confirmado": "1"}).status_code == 403, "transcripcionista no valida en lote")
t = S["rad"].get(B + "/estudio/est-ct").text
chk("transcrito por Carla Muñoz" in t and "Validado por" in t, "informe validado con transcriptor")
inf = re.search(r'/informe/(\d+)\.pdf', t).group(1)
open("/tmp/informe_prueba.pdf", "wb").write(S["rad"].get(B + f"/informe/{inf}.pdf").content)
txt = subprocess.run(["pdftotext", "/tmp/informe_prueba.pdf", "-"], capture_output=True, text=True).stdout
chk("Transcripción: CM" in txt and "Andrés López" in txt, "PDF con firma e iniciales de transcripción")
t = S["der"].get(B + "/estudio/est-ct").text
chk("Sin hemorragia" in t and "Entrega al paciente" not in t, "derivador ve informe validado, sin entrega")
chk("Transcrito" in S["trans"].get(B + "/dictados?ver=hechos").text, "dictado aparece como transcrito")

# --- contacto + correo
S["sec"].post(B + "/estudio/est-ct/contacto", data={"csrf": S["sec"].csrf, "email": "paciente@correo.cl", "telefono": "912345678"})
antes = len(glob.glob("/tmp/correos_prueba/*.eml"))
r = S["sec"].post(B + "/estudio/est-ct/email", data={"csrf": S["sec"].csrf, "para": "paciente@correo.cl"})
chk("Informe enviado a paciente@correo.cl" in r.text, "secretaría envía correo")
correos = sorted(glob.glob("/tmp/correos_prueba/*.eml"))
eml = open(correos[-1]).read() if len(correos) > antes else ""
chk("paciente@correo.cl" in eml and "application/pdf" in eml and "/r/" in eml, "correo con link y PDF adjunto")
r = S["sec"].post(B + "/estudio/est-ct/email", data={"csrf": S["sec"].csrf, "para": "malo"})
chk("no es válida" in r.text, "correo inválido rechazado")
chk(S["tec"].post(B + "/estudio/est-ct/email", data={"csrf": S["tec"].csrf, "para": "a@b.cl"}).status_code == 403, "tecnólogo no envía correo")
chk('data-tel="912345678"' in S["sec"].get(B + "/estudio/est-ct").text, "teléfono para WhatsApp")

# --- envío automático al validar
adm.post(B + "/admin/ajustes", data={"csrf": adm.csrf, "correo_asunto": "Informe {centro}", "correo_cuerpo": "Hola {paciente}: {link} {linea_clave}", "envio_auto_email": "on", "horas_alerta": "24", "horas_alerta_urgente": "2", "webhook_url": ""})
S["sec"].post(B + "/estudio/est-us/contacto", data={"csrf": S["sec"].csrf, "email": "maria@correo.cl", "telefono": ""})
antes = len(glob.glob("/tmp/correos_prueba/*.eml"))
r = S["rad"].post(B + "/estudio/est-us/firmar", json={"cuerpo": "HALLAZGOS:\nHígado normal.\n\nIMPRESIÓN:\nNormal."}, headers=H(S["rad"]))
chk(r.ok and "maria@correo.cl" in r.json().get("aviso", ""), "correo automático al validar")
chk(len(glob.glob("/tmp/correos_prueba/*.eml")) == antes + 1, "correo automático recibido")
chk("Hola MARIA JOSE GONZALEZ" in open(sorted(glob.glob("/tmp/correos_prueba/*.eml"))[-1]).read(), "plantilla de correo personalizada")

# --- entregas
r = S["sec"].post(B + "/estudio/est-ct/entregas", data={"csrf": S["sec"].csrf, "tipo": "retiro", "detalle": "retira su hija"})
chk("Entrega registrada" in r.text and "retira su hija" in r.text, "registrar retiro")
S["sec"].post(B + "/estudio/est-ct/entregas", data={"csrf": S["sec"].csrf, "tipo": "cd", "detalle": ""})
eid = re.findall(r'/entregas/(\d+)/anular', S["ent"].get(B + "/estudio/est-ct").text)[0]
chk(S["sec"].post(B + f"/entregas/{eid}/anular", data={"csrf": S["sec"].csrf}).status_code == 403, "secretaría no anula entregas")
chk("anulada por Pedro Entrega" in S["ent"].post(B + f"/entregas/{eid}/anular", data={"csrf": S["ent"].csrf}).text, "entrega anulada (queda registro)")
chk("📦" in S["sec"].get(B + "/").text, "lista marca estudios entregados")

# --- notas
r = S["tec"].post(B + "/estudio/est-ct/notas", data={"csrf": S["tec"].csrf, "texto": "Paciente con prótesis de cadera"}, files={"archivo": ("orden.pdf", b"%PDF-1.4 orden", "application/pdf")})
chk("Paciente con prótesis de cadera" in r.text and "orden.pdf" in r.text, "nota con adjunto")
t = S["trans"].get(B + "/estudio/est-ct").text
nid = re.search(r'/notas/(\d+)/archivo', t).group(1)
chk(S["trans"].get(B + f"/notas/{nid}/archivo").content.startswith(b"%PDF"), "descargar adjunto")
chk(S["sec"].post(B + f"/notas/{nid}/eliminar", data={"csrf": S["sec"].csrf}).status_code == 403, "no borra nota ajena")
chk(S["tec"].post(B + "/estudio/est-ct/notas", data={"csrf": S["tec"].csrf, "texto": ""}, files={"archivo": ("x.exe", b"MZ", "application/octet-stream")}).text.count("Formato no permitido") == 1, "adjunto peligroso rechazado")
chk("📝 1" in S["trans"].get(B + "/").text, "lista muestra notas")
chk(S["der"].post(B + "/estudio/est-ct/notas", data={"csrf": S["der"].csrf, "texto": "x"}).status_code == 403, "derivador sin notas")

# --- frases
r = adm.post(B + "/admin/frases", data={"csrf": adm.csrf, "tipo": "hallazgo", "titulo": "Sinusopatía", "atajo": ".sinus", "texto": "Engrosamiento mucoso del seno ___.", "modalidad": "CT"})
chk("Frases actualizadas" in r.text and ".sinus" in r.text, "crear frase con atajo")
t = S["rad"].get(B + "/estudio/est-rx").text
chk('"atajo": "torax"' in t and "sinus" not in t, "frases filtradas por modalidad")
chk(S["trans"].get(B + "/admin/frases").status_code == 403 and S["rad"].get(B + "/admin/frases").status_code == 200, "permiso de frases")

# --- historial
t = S["rad"].get(B + "/estudio/est-ct").text
chk("Otros estudios del paciente" in t and "est-ct-previo" in t, "historial del paciente")

# --- perfiles editables
t = adm.get(B + "/admin/perfiles").text
pid = re.search(r'name="id" value="(\d+)">\s*<div class="grid2">\s*<div><label for="p-nombre-\d+">Nombre</label><input type="text" id="p-nombre-\d+" name="nombre" value="Transcripcionistas"', t).group(1)
r = adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "id": pid, "nombre": "Transcripcionistas", "descripcion": "x", "permisos": ["notas", "dictados_escuchar", "transcribir", "ver_borrador", "redactar", "visor"]})
chk("guardado" in r.text, "editar permisos de perfil")
chk("Abrir en visor OHIF" in S["trans"].get(B + "/estudio/est-ct").text, "nuevo permiso aplica al instante")
chk("No se puede eliminar" in adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "borrar": pid}).text, "no borra perfil en uso")
chk("Perfil duplicado" in adm.post(B + "/admin/perfiles", data={"csrf": adm.csrf, "duplicar": pid}).text, "duplicar perfil")
chk(S["rad"].get(B + "/admin/perfiles").status_code == 403, "radiólogo no administra perfiles")

# --- rectificar / invalidar
r = S["rad2"].post(B + "/estudio/est-ct/reabrir", data={"csrf": S["rad2"].csrf, "motivo": "x"})
chk("Solo quien validó" in r.text, "rad2 no rectifica informe ajeno")
r = adm.post(B + "/estudio/est-ct/invalidar", data={"csrf": adm.csrf, "motivo": "paciente equivocado"})
chk("Informe invalidado" in r.text, "invalidar informe")
link = re.search(r'/r/([\w-]+)', S["sec"].get(B + "/estudio/est-us").text)
chk(S["rad"].post(B + "/estudio/est-us/invalidar", data={"csrf": S["rad"].csrf, "motivo": "x"}).status_code == 403, "radiólogo sin permiso de invalidar")
t = adm.get(B + "/estudio/est-ct").text
chk("INVALIDADO: paciente equivocado" in t, "historial registra invalidación")

# --- paciente (link público con clave) sobre est-us
tok = link.group(1); p = requests.Session()
t = S["sec"].get(B + "/estudio/est-us").text; clave = re.search(r'id="link-clave">(\d{6})', t).group(1)
p.post(B + "/r/" + tok, data={"clave": clave})
chk("Hígado normal" in p.get(B + "/r/" + tok).text, "paciente abre informe con clave")

# --- exportar / estadísticas / filtros
r = S["sec"].get(B + "/estudios/exportar.csv")
chk(r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf") and "Radiólogo asignado" in r.text, "exportar a Excel (CSV)")
chk(S["tec"].get(B + "/estudios/exportar.csv").status_code == 403, "tecnólogo no exporta")
t = adm.get(B + "/estadisticas").text
chk("Validados" in t and "Carla Muñoz" in t and "Dr. Andrés López" in t, "estadísticas con radiólogos y transcripcionistas")
chk(S["trans"].get(B + "/estadisticas").status_code == 403, "transcripcionista sin estadísticas")
r = S["rad"].post(B + "/filtros", data={"csrf": S["rad"].csrf, "nombre": "Mis TC", "query": "modalidad=CT&asignado=mio"})
chk("Mis TC" in r.text, "guardar filtro")
chk("Mis TC" not in S["rad2"].get(B + "/").text, "filtro es personal")

# --- acceso al PACS (forward_auth)
def pa(s, m, uri): return s.get(B + "/api/auth/pacs", headers={"X-Forwarded-Method": m, "X-Forwarded-Uri": uri}).status_code
chk(pa(S["tec"], "POST", "/instances") == 200, "tecnólogo sube al PACS")
chk(pa(S["rad"], "POST", "/instances") == 403, "radiólogo no sube al PACS")
chk(pa(S["rad"], "DELETE", "/studies/x") == 403 and pa(adm, "DELETE", "/studies/x") == 200, "solo admin borra en PACS")
chk(pa(S["sec"], "GET", "/ohif/viewer") == 403, "secretaría sin visor")
chk(pa(S["der"], "GET", "/dicom-web/studies/1.2.826.0.1.1/metadata") == 200 and pa(S["der"], "GET", "/dicom-web/studies/1.2.826.0.1.2/metadata") == 403, "derivador solo sus estudios en el visor")

# --- varias pantallas
for u in ["/admin/usuarios", "/admin/perfiles", "/admin/plantillas", "/admin/frases", "/admin/ajustes", "/admin/auditoria?q=firmar", "/dictados?ver=mios", "/cuenta"]:
    chk(adm.get(B + u).status_code == 200, "página " + u)
t = adm.get(B + "/admin/auditoria").text
chk(all(a in t for a in ["dictado", "enviar_validacion", "firmar", "invalidar", "entrega_email", "nota"]), "auditoría registra el flujo")
print("FALLAS:", fallas)
