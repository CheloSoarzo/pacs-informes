"""Núcleo de la app: configuración, sesión, permisos y utilidades compartidas por las rutas."""
import os
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

import requests
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from db import ahora, ajuste, auditar, get_db, init_db
from orthanc import Orthanc, OrthancError, edad, fecha_dicom
from pdf import generar_pdf
from permisos import CATALOGO, puede, ve_estudio

app = Flask(__name__)
app.config.from_object(Config)
# Proxies delante de la app: 1 con docker-compose (Caddy), 2 en Render (Render + Caddy)
_HOPS = int(os.environ.get("PROXY_HOPS", "1") or 1)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_HOPS, x_proto=_HOPS, x_host=_HOPS)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=Config.SECURE_COOKIES,
    SESSION_COOKIE_DOMAIN=Config.COOKIE_DOMAIN,
    # Cada PACS del servidor tiene su propia cookie de sesión (no se mezclan los logins)
    SESSION_COOKIE_NAME=f"sesion_{Config.INSTANCIA}" if Config.INSTANCIA else "session",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=60 * 1024 * 1024,
)
init_db(app)

pacs = Orthanc(Config.ORTHANC_URL, Config.ORTHANC_USER, Config.ORTHANC_PASSWORD)

MODALIDADES = [("", "Todas"), ("CT", "TC"), ("CR\\DX", "Rayos X"), ("US", "Ecografía"),
               ("MR", "RM"), ("MG", "Mamografía")]

ESTADOS = {
    "pendiente": "Pendiente",
    "dictado": "Por transcribir",
    "borrador": "Borrador",
    "por_validar": "Por validar",
    "firmado": "Validado",
}

TIPOS_ENTREGA = {
    "retiro": "Retiro presencial",
    "cd": "CD grabado",
    "placas": "Placas impresas",
    "impreso": "Informe impreso",
    "email": "Correo electrónico",
    "otro": "Otro",
}

SQL_USUARIO = """SELECT u.*, p.clave AS perfil_clave, p.nombre AS perfil_nombre, p.permisos,
                        p.solo_solicitados
                 FROM usuarios u LEFT JOIN perfiles p ON p.id = u.perfil_id"""


# ------------------------------------------------------------------ sesión y permisos
def ip():
    return request.remote_addr or ""


def usuario_actual():
    if "uid" not in session:
        return None
    if "user" not in g:
        g.user = get_db().execute(SQL_USUARIO + " WHERE u.id=? AND u.activo=1", (session["uid"],)).fetchone()
    return g.user


def usuario_por_id(uid):
    return get_db().execute(SQL_USUARIO + " WHERE u.id=?", (uid,)).fetchone() if uid else None


def login_requerido(permiso=None):
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            u = usuario_actual()
            if not u:
                if request.path.startswith("/api/") or request.is_json:
                    return jsonify(error="Sesión expirada"), 401
                return redirect(url_for("login", next=request.full_path.rstrip("?")))
            if permiso and not puede(u, permiso):
                abort(403)
            return f(*a, **kw)
        return w
    return deco


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def registrar(accion, detalle=""):
    u = usuario_actual()
    auditar(u["usuario"] if u else "anónimo", accion, detalle, ip())


@app.before_request
def verificar_csrf():
    if request.method == "POST" and not request.path.startswith("/r/"):
        enviado = request.headers.get("X-CSRF") or request.form.get("csrf")
        if not enviado or enviado != session.get("csrf"):
            if request.endpoint == "login":
                return  # el login genera el token tras validar
            abort(400, "Token de seguridad inválido. Recarga la página.")


@app.context_processor
def contexto():
    u = usuario_actual()

    def n_dictados():
        if not u or not puede(u, "transcribir"):
            return 0
        return get_db().execute("SELECT COUNT(*) FROM dictados WHERE estado IN ('pendiente','tomado')").fetchone()[0]

    return {"usuario": u, "csrf_token": csrf_token, "centro": Config.CENTRO_NOMBRE, "n_dictados": n_dictados,
            "puede": lambda accion: puede(u, accion), "ESTADOS": ESTADOS, "TIPOS_ENTREGA": TIPOS_ENTREGA,
            "PACS_PUBLIC_URL": Config.PACS_PUBLIC_URL, "CATALOGO": CATALOGO}


@app.after_request
def cabeceras(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith("/r/"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(OrthancError)
def error_pacs(e):
    if request.is_json or request.path.startswith("/api/"):
        return jsonify(error=str(e)), 502
    return render_template("error.html", mensaje=str(e)), 502


# ------------------------------------------------------------------ formato
def fmt_fecha_hora(ts):
    if not ts:
        return ""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return ts


def fmt_duracion(horas):
    if horas is None:
        return ""
    minutos = int(horas * 60)
    if minutos < 60:
        return f"{max(minutos, 0)} min"
    if minutos < 48 * 60:
        return f"{minutos // 60} h {minutos % 60:02d}"
    return f"{minutos // 1440} d {(minutos % 1440) // 60} h"


def fmt_mmss(seg):
    seg = int(seg or 0)
    return f"{seg // 60}:{seg % 60:02d}"


app.jinja_env.filters["fh"] = fmt_fecha_hora
app.jinja_env.filters["dur"] = fmt_duracion
app.jinja_env.filters["mmss"] = fmt_mmss


def iniciales(nombre):
    partes = [p for p in (nombre or "").replace(".", " ").split() if p[:1].isalpha() and p.lower() not in ("dr", "dra", "tm")]
    return "".join(p[0].upper() for p in partes[:3])


app.jinja_env.filters["iniciales"] = iniciales


def dt_estudio(fecha_raw, hora_raw):
    """Fecha/hora DICOM → datetime (o None)."""
    try:
        return datetime.strptime((fecha_raw or "")[:8] + ((hora_raw or "") + "000000")[:6], "%Y%m%d%H%M%S")
    except ValueError:
        return None


# ------------------------------------------------------------------ estudios e informes
def estudio_visible(estudio_id):
    """Devuelve el estudio si el usuario actual puede verlo; si no, 404/403."""
    cache = g.setdefault("estudios", {})
    if estudio_id not in cache:
        cache[estudio_id] = pacs.estudio(estudio_id)
    est = cache[estudio_id]
    if not est:
        abort(404)
    if not ve_estudio(usuario_actual(), est):
        abort(403)
    return est


def informe_por_estudio(estudio_id):
    return get_db().execute("SELECT * FROM informes WHERE estudio_id=?", (estudio_id,)).fetchone()


def meta_de(estudio_id):
    fila = get_db().execute("SELECT * FROM estudio_meta WHERE estudio_id=?", (estudio_id,)).fetchone()
    return dict(fila) if fila else {"estudio_id": estudio_id, "radiologo_id": None, "asignado_por": None,
                                    "asignado_en": None, "prioridad": "normal",
                                    "paciente_email": "", "paciente_telefono": ""}


def guardar_meta(estudio_id, **campos):
    db = get_db()
    db.execute("INSERT OR IGNORE INTO estudio_meta (estudio_id) VALUES (?)", (estudio_id,))
    if campos:
        db.execute(f"UPDATE estudio_meta SET {', '.join(k + '=?' for k in campos)} WHERE estudio_id=?",
                   list(campos.values()) + [estudio_id])
    db.commit()


def ve_informe(u, inf):
    """¿Puede ver el texto de este informe? (borradores solo con permiso)."""
    return bool(inf) and (inf["estado"] == "firmado" or puede(u, "ver_borrador"))


def datos_pdf(inf):
    """Mezcla datos guardados del informe para el PDF."""
    d = dict(inf)
    d["fecha"] = fecha_dicom(inf["fecha_estudio"])
    d["edad"] = edad(inf["paciente_fnac"], inf["fecha_estudio"])
    d["firmado_en_txt"] = fmt_fecha_hora(inf["firmado_en"])
    t = usuario_por_id(inf["transcrito_por"]) if inf["transcrito_por"] else None
    d["transcriptor_iniciales"] = iniciales(t["nombre"]) if t else ""
    return d


def firmante_de(inf):
    if not inf["firmado_por"] or inf["estado"] != "firmado":
        return None
    u = usuario_por_id(inf["firmado_por"])
    return dict(u) if u else None


def centro_info():
    return {"nombre": Config.CENTRO_NOMBRE, "direccion": Config.CENTRO_DIRECCION,
            "telefono": Config.CENTRO_TELEFONO, "email": Config.CENTRO_EMAIL}


def logo_path():
    p = os.path.join(Config.DATA_DIR, "logo.png")
    return p if os.path.exists(p) else None


def link_vigente(inf_id):
    return get_db().execute(
        "SELECT * FROM links WHERE informe_id=? AND revocado=0 AND expira>? ORDER BY id DESC LIMIT 1",
        (inf_id, ahora())).fetchone()


def url_link(link):
    return f"{Config.PUBLIC_BASE_URL}/r/{link['token']}" if link else ""


def nuevo_link(inf_id):
    token = secrets.token_urlsafe(24)
    clave = f"{secrets.randbelow(10**6):06d}" if Config.LINK_REQUIERE_CLAVE else ""
    expira = (datetime.now() + timedelta(days=Config.LINK_DIAS_VIGENCIA)).strftime("%Y-%m-%d %H:%M:%S")
    u = usuario_actual()
    db = get_db()
    db.execute("INSERT INTO links (token, informe_id, clave, creado_por, creado, expira) VALUES (?,?,?,?,?,?)",
               (token, inf_id, clave, u["id"] if u else None, ahora(), expira))
    db.commit()
    registrar("crear_link", f"informe {inf_id}")
    return token, clave


def pdf_de(inf, con_qr=True):
    link = link_vigente(inf["id"]) if con_qr else None
    return generar_pdf(datos_pdf(inf), firmante_de(inf), centro_info(), url_link(link) or None, logo_path())


def nombre_archivo(inf):
    base = f"Informe_{inf['paciente_nombre'] or 'paciente'}_{inf['fecha_estudio'] or ''}"
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in base) + ".pdf"


def radiologos():
    """Usuarios activos que pueden firmar (para asignar estudios)."""
    filas = get_db().execute(SQL_USUARIO + " WHERE u.activo=1 ORDER BY u.nombre").fetchall()
    return [f for f in filas if puede(f, "firmar")]


# ------------------------------------------------------------------ aviso externo al validar
def webhook_validado(inf):
    url = ajuste("webhook_url").strip()
    if not url:
        return
    link = link_vigente(inf["id"])
    datos = {"evento": "informe_validado", "estudio_uid": inf["estudio_uid"], "accession": inf["accession"],
             "paciente_id": inf["paciente_id"], "paciente_nombre": inf["paciente_nombre"],
             "fecha_estudio": inf["fecha_estudio"], "validado_en": inf["firmado_en"], "version": inf["version"],
             "link_paciente": url_link(link)}

    def enviar():
        try:
            requests.post(url, json=datos, timeout=10)
        except requests.RequestException:
            pass
    threading.Thread(target=enviar, daemon=True).start()
