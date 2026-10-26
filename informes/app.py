"""App de informes radiológicos sobre Orthanc.

Flujo: el equipo envía el estudio al PACS → aparece en la lista → el radiólogo abre
las imágenes, dicta o escribe el informe → lo firma → se genera PDF y link para el paciente.
"""
import os
import re
import secrets
import time
from urllib.parse import parse_qs, quote, urlsplit
from datetime import date, datetime, timedelta
from functools import wraps

import requests
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, session, stream_with_context, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from db import ahora, auditar, get_db, init_db
from orthanc import Orthanc, OrthancError, edad, fecha_dicom
from pdf import generar_pdf
from permisos import DESCRIPCION_ROLES, ROLES, puede, restringido, ve_estudio

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
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,
)
init_db(app)

pacs = Orthanc(Config.ORTHANC_URL, Config.ORTHANC_USER, Config.ORTHANC_PASSWORD)

MODALIDADES = [("", "Todas"), ("CT", "TC"), ("CR\\DX", "Rayos X"), ("US", "Ecografía"),
               ("MR", "RM"), ("MG", "Mamografía")]


# ------------------------------------------------------------------ utilidades
def ip():
    return request.remote_addr or ""


def usuario_actual():
    if "uid" not in session:
        return None
    if "user" not in g:
        g.user = get_db().execute(
            "SELECT * FROM usuarios WHERE id=? AND activo=1", (session["uid"],)).fetchone()
    return g.user


def login_requerido(permiso=None):
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            u = usuario_actual()
            if not u:
                if request.path.startswith("/api/") or request.is_json:
                    return jsonify(error="Sesión expirada"), 401
                return redirect(url_for("login", next=request.path))
            if permiso and not puede(u, permiso):
                abort(403)
            return f(*a, **kw)
        return w
    return deco


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


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
    return {"usuario": usuario_actual(), "csrf_token": csrf_token, "centro": Config.CENTRO_NOMBRE,
            "ROLES": ROLES, "DESCRIPCION_ROLES": DESCRIPCION_ROLES,
            "puede": lambda accion: puede(usuario_actual(), accion),
            "PACS_PUBLIC_URL": Config.PACS_PUBLIC_URL}


@app.after_request
def cabeceras(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith("/r/"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    return resp


def fmt_fecha_hora(ts):
    if not ts:
        return ""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return ts


app.jinja_env.filters["fh"] = fmt_fecha_hora


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


def datos_pdf(inf):
    """Mezcla datos guardados del informe para el PDF."""
    d = dict(inf)
    d["fecha"] = fecha_dicom(inf["fecha_estudio"])
    d["edad"] = edad(inf["paciente_fnac"], inf["fecha_estudio"])
    d["firmado_en_txt"] = fmt_fecha_hora(inf["firmado_en"])
    return d


def firmante_de(inf):
    if not inf["firmado_por"] or inf["estado"] != "firmado":
        return None
    u = get_db().execute("SELECT * FROM usuarios WHERE id=?", (inf["firmado_por"],)).fetchone()
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


def pdf_de(inf, con_qr=True):
    link = link_vigente(inf["id"]) if con_qr else None
    url = f"{Config.PUBLIC_BASE_URL}/r/{link['token']}" if link else None
    return generar_pdf(datos_pdf(inf), firmante_de(inf), centro_info(), url, logo_path())


def nombre_archivo(inf):
    base = f"Informe_{inf['paciente_nombre'] or 'paciente'}_{inf['fecha_estudio'] or ''}"
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in base) + ".pdf"


@app.errorhandler(OrthancError)
def error_pacs(e):
    if request.is_json or request.path.startswith("/api/"):
        return jsonify(error=str(e)), 502
    return render_template("error.html", mensaje=str(e)), 502


# ------------------------------------------------------------------ sesión
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()
        clave = request.form.get("clave", "")
        db = get_db()
        hace15 = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        fallos = db.execute(
            "SELECT COUNT(*) FROM auditoria WHERE accion='login_fallido' AND (usuario=? OR ip=?) AND ts>?",
            (usuario, ip(), hace15)).fetchone()[0]
        if fallos >= 8:
            flash("Demasiados intentos. Espera 15 minutos.", "error")
            return render_template("login.html"), 429
        u = db.execute("SELECT * FROM usuarios WHERE lower(usuario)=? AND activo=1", (usuario,)).fetchone()
        if u and check_password_hash(u["clave_hash"], clave):
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            csrf_token()
            auditar(u["usuario"], "login", "", ip())
            destino = request.args.get("next") or "/"
            pacs_ok = destino.startswith(Config.PACS_PUBLIC_URL + "/")
            if not pacs_ok and (not destino.startswith("/") or destino.startswith("//")):
                destino = "/"
            return redirect(destino)
        auditar(usuario, "login_fallido", "", ip())
        flash("Usuario o clave incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    u = usuario_actual()
    if u:
        auditar(u["usuario"], "logout", "", ip())
    session.clear()
    return redirect(url_for("login"))


@app.route("/cuenta", methods=["GET", "POST"])
@login_requerido()
def cuenta():
    u = usuario_actual()
    if request.method == "POST":
        actual, nueva = request.form.get("actual", ""), request.form.get("nueva", "")
        if not check_password_hash(u["clave_hash"], actual):
            flash("La clave actual no es correcta.", "error")
        elif len(nueva) < 10:
            flash("La nueva clave debe tener al menos 10 caracteres.", "error")
        else:
            db = get_db()
            db.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (generate_password_hash(nueva), u["id"]))
            db.commit()
            auditar(u["usuario"], "cambio_clave", "", ip())
            flash("Clave actualizada.", "ok")
            return redirect(url_for("cuenta"))
    return render_template("cuenta.html")


# ------------------------------------------------------------------ lista de trabajo
@app.route("/")
@login_requerido()
def lista():
    hoy = date.today()
    desde = request.args.get("desde") or (hoy - timedelta(days=6)).isoformat()
    hasta = request.args.get("hasta") or hoy.isoformat()
    q = request.args.get("q", "").strip()
    modalidad = request.args.get("modalidad", "")
    estado = request.args.get("estado", "")

    # Si se busca un paciente, no limitar por fecha
    d = "" if q else desde.replace("-", "")
    h = "" if q else hasta.replace("-", "")
    u = usuario_actual()
    solicitante = ""
    if u["rol"] == "derivador":
        palabras = (u["filtro_solicitante"] or "").replace("^", " ").split()
        solicitante = max(palabras, key=len) if palabras else "SIN-FILTRO-CONFIGURADO"
    estudios = [e for e in pacs.buscar_estudios(d, h, q, modalidad, solicitante=solicitante)
                if ve_estudio(u, e)]

    db = get_db()
    ids = [e["id"] for e in estudios]
    informes = {}
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        filas = db.execute(
            f"SELECT i.*, u.nombre AS firmante FROM informes i LEFT JOIN usuarios u ON u.id=i.firmado_por "
            f"WHERE estudio_id IN ({','.join('?' * len(lote))})", lote).fetchall()
        informes.update({f["estudio_id"]: f for f in filas})
    for e in estudios:
        inf = informes.get(e["id"])
        e["estado"] = inf["estado"] if inf else "pendiente"
        e["firmante"] = inf["firmante"] if inf else ""
        # Quien no informa no ve borradores como tales
        if e["estado"] == "borrador" and not puede(u, "ver_borrador"):
            e["estado"] = "pendiente"
    if estado:
        estudios = [e for e in estudios if e["estado"] == estado]

    conteo = {k: sum(1 for e in estudios if e["estado"] == k) for k in ("pendiente", "borrador", "firmado")}
    return render_template("lista.html", estudios=estudios, desde=desde, hasta=hasta, q=q,
                           modalidad=modalidad, estado=estado, modalidades=MODALIDADES, conteo=conteo)


# ------------------------------------------------------------------ estudio / informe
@app.route("/estudio/<estudio_id>")
@login_requerido()
def estudio(estudio_id):
    est = estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    series = pacs.series(estudio_id)
    db = get_db()
    mods = set(est["modalidad"].split("/")) if est["modalidad"] else set()
    plantillas = [p for p in db.execute(
        "SELECT * FROM plantillas WHERE activo=1 ORDER BY nombre").fetchall()
        if not p["modalidad"] or not mods or mods & set(p["modalidad"].split(","))]
    link = link_vigente(inf["id"]) if inf else None
    versiones = db.execute(
        "SELECT v.*, u.nombre AS firmante FROM informe_versiones v LEFT JOIN usuarios u ON u.id=v.firmado_por "
        "WHERE informe_id=? ORDER BY version DESC", (inf["id"],)).fetchall() if inf else []
    firmante = firmante_de(inf) if inf else None
    visor_ohif = f"{Config.PACS_PUBLIC_URL}/ohif/viewer?StudyInstanceUIDs={est['uid']}"
    visor_stone = f"{Config.PACS_PUBLIC_URL}/stone-webviewer/index.html?study={est['uid']}"
    auditar(usuario_actual()["usuario"], "abrir_estudio", f"{estudio_id} {est['paciente_id']}", ip())
    return render_template("estudio.html", est=est, inf=inf, series=series, plantillas=plantillas,
                           link=link, versiones=versiones, firmante=firmante,
                           visor_ohif=visor_ohif, visor_stone=visor_stone,
                           whisper=bool(Config.WHISPER_URL), requiere_clave=Config.LINK_REQUIERE_CLAVE)


def _crear_o_actualizar(estudio_id, cuerpo, solicitante):
    u = usuario_actual()
    db = get_db()
    est = estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    if inf and inf["estado"] == "firmado":
        return None, "El informe ya está firmado. Reábrelo para modificarlo."
    if not inf:
        db.execute(
            """INSERT INTO informes (estudio_id, estudio_uid, paciente_nombre, paciente_id, paciente_fnac,
               paciente_sexo, fecha_estudio, modalidad, descripcion, accession, institucion, solicitante,
               cuerpo, creado_por, creado, actualizado_por, actualizado)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (estudio_id, est["uid"], est["paciente_nombre"], est["paciente_id"], est["paciente_fnac_raw"],
             est["paciente_sexo"], est["fecha_raw"], est["modalidad"], est["descripcion"], est["accession"],
             est["institucion"], solicitante if solicitante is not None else est["solicitante"],
             cuerpo, u["id"], ahora(), u["id"], ahora()))
    else:
        campos, vals = ["cuerpo=?", "actualizado_por=?", "actualizado=?"], [cuerpo, u["id"], ahora()]
        if solicitante is not None:
            campos.append("solicitante=?")
            vals.append(solicitante)
        db.execute(f"UPDATE informes SET {', '.join(campos)} WHERE estudio_id=?", vals + [estudio_id])
    db.commit()
    return informe_por_estudio(estudio_id), None


@app.route("/estudio/<estudio_id>/guardar", methods=["POST"])
@login_requerido("informar")
def guardar(estudio_id):
    data = request.get_json(silent=True) or {}
    inf, err = _crear_o_actualizar(estudio_id, data.get("cuerpo", ""), data.get("solicitante"))
    if err:
        return jsonify(error=err), 409
    return jsonify(ok=True, guardado=datetime.now().strftime("%H:%M:%S"))


@app.route("/estudio/<estudio_id>/firmar", methods=["POST"])
@login_requerido("informar")
def firmar(estudio_id):
    data = request.get_json(silent=True) or {}
    cuerpo = data.get("cuerpo", "")
    if len(cuerpo.strip()) < 10:
        return jsonify(error="El informe está vacío."), 400
    inf, err = _crear_o_actualizar(estudio_id, cuerpo, data.get("solicitante"))
    if err:
        return jsonify(error=err), 409
    u = usuario_actual()
    db = get_db()
    db.execute("UPDATE informes SET estado='firmado', firmado_por=?, firmado_en=? WHERE id=?",
               (u["id"], ahora(), inf["id"]))
    db.commit()
    auditar(u["usuario"], "firmar", f"informe {inf['id']} v{inf['version']} {inf['paciente_id']}", ip())
    # Crear link automáticamente al firmar
    if not link_vigente(inf["id"]):
        _nuevo_link(inf["id"])
    return jsonify(ok=True)


@app.route("/estudio/<estudio_id>/reabrir", methods=["POST"])
@login_requerido("informar")
def reabrir(estudio_id):
    estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    if not inf or inf["estado"] != "firmado":
        abort(400)
    u = usuario_actual()
    if u["rol"] != "admin" and inf["firmado_por"] != u["id"]:
        flash("Solo quien firmó el informe o un administrador puede reabrirlo.", "error")
        return redirect(url_for("estudio", estudio_id=estudio_id))
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Indica el motivo de la rectificación.", "error")
        return redirect(url_for("estudio", estudio_id=estudio_id))
    db = get_db()
    db.execute(
        """INSERT INTO informe_versiones (informe_id, version, cuerpo, firmado_por, firmado_en,
           reabierto_por, reabierto_en, motivo) VALUES (?,?,?,?,?,?,?,?)""",
        (inf["id"], inf["version"], inf["cuerpo"], inf["firmado_por"], inf["firmado_en"], u["id"], ahora(), motivo))
    db.execute("UPDATE informes SET estado='borrador', version=version+1, firmado_por=NULL, firmado_en=NULL "
               "WHERE id=?", (inf["id"],))
    db.commit()
    auditar(u["usuario"], "reabrir", f"informe {inf['id']} v{inf['version']}: {motivo}", ip())
    flash("Informe reabierto. Al firmarlo de nuevo quedará como nueva versión.", "ok")
    return redirect(url_for("estudio", estudio_id=estudio_id))


@app.route("/informe/<int:inf_id>.pdf")
@login_requerido()
def informe_pdf(inf_id):
    inf = get_db().execute("SELECT * FROM informes WHERE id=?", (inf_id,)).fetchone()
    if not inf:
        abort(404)
    estudio_visible(inf["estudio_id"])
    if inf["estado"] != "firmado" and not puede(usuario_actual(), "ver_borrador"):
        abort(404)
    pdf = pdf_de(inf)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre_archivo(inf)}"'})


@app.route("/estudio/<estudio_id>/vista-previa.pdf", methods=["POST"])
@login_requerido("informar")
def vista_previa(estudio_id):
    """PDF de borrador con el texto actual del editor (sin guardar)."""
    est = estudio_visible(estudio_id)
    d = dict(est)
    d["cuerpo"] = request.form.get("cuerpo", "")
    d["solicitante"] = request.form.get("solicitante", est["solicitante"])
    pdf = generar_pdf(d, None, centro_info(), None, logo_path())
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="borrador.pdf"'})


# ------------------------------------------------------------------ links para pacientes
def _nuevo_link(inf_id):
    token = secrets.token_urlsafe(24)
    clave = f"{secrets.randbelow(10**6):06d}" if Config.LINK_REQUIERE_CLAVE else ""
    expira = (datetime.now() + timedelta(days=Config.LINK_DIAS_VIGENCIA)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute("INSERT INTO links (token, informe_id, clave, creado_por, creado, expira) VALUES (?,?,?,?,?,?)",
               (token, inf_id, clave, usuario_actual()["id"], ahora(), expira))
    db.commit()
    auditar(usuario_actual()["usuario"], "crear_link", f"informe {inf_id}", ip())
    return token, clave


@app.route("/informe/<int:inf_id>/link", methods=["POST"])
@login_requerido("links")
def crear_link(inf_id):
    inf = get_db().execute("SELECT * FROM informes WHERE id=?", (inf_id,)).fetchone()
    if not inf or inf["estado"] != "firmado":
        return jsonify(error="El informe debe estar firmado."), 400
    estudio_visible(inf["estudio_id"])
    db = get_db()
    db.execute("UPDATE links SET revocado=1 WHERE informe_id=? AND revocado=0", (inf_id,))
    db.commit()
    token, clave = _nuevo_link(inf_id)
    return jsonify(url=f"{Config.PUBLIC_BASE_URL}/r/{token}", clave=clave)


@app.route("/informe/<int:inf_id>/revocar", methods=["POST"])
@login_requerido("links")
def revocar_link(inf_id):
    db = get_db()
    inf = db.execute("SELECT estudio_id FROM informes WHERE id=?", (inf_id,)).fetchone()
    if not inf:
        abort(404)
    estudio_visible(inf["estudio_id"])
    db.execute("UPDATE links SET revocado=1 WHERE informe_id=? AND revocado=0", (inf_id,))
    db.commit()
    auditar(usuario_actual()["usuario"], "revocar_link", f"informe {inf_id}", ip())
    flash("Link desactivado. El paciente ya no podrá abrirlo.", "ok")
    inf = db.execute("SELECT estudio_id FROM informes WHERE id=?", (inf_id,)).fetchone()
    return redirect(url_for("estudio", estudio_id=inf["estudio_id"]))


def _link_publico(token):
    link = get_db().execute("SELECT * FROM links WHERE token=?", (token,)).fetchone()
    if not link or link["revocado"] or link["expira"] < ahora():
        return None, None
    inf = get_db().execute("SELECT * FROM informes WHERE id=?", (link["informe_id"],)).fetchone()
    return link, inf


def _clave_ok(link):
    return not link["clave"] or session.get(f"lk_{link['id']}") is True


@app.route("/r/<token>", methods=["GET", "POST"])
def publico(token):
    link, inf = _link_publico(token)
    if not link:
        return render_template("publico_error.html"), 404
    if not _clave_ok(link):
        if request.method == "POST":
            if secrets.compare_digest(request.form.get("clave", "").strip(), link["clave"]):
                session[f"lk_{link['id']}"] = True
                return redirect(url_for("publico", token=token))
            auditar("paciente", "clave_link_fallida", f"link {link['id']}", ip())
            flash("Clave incorrecta.", "error")
        return render_template("publico_clave.html", token=token)
    db = get_db()
    db.execute("UPDATE links SET accesos=accesos+1, ultimo_acceso=? WHERE id=?", (ahora(), link["id"]))
    db.commit()
    auditar("paciente", "abrir_link", f"link {link['id']} informe {inf['id']}", ip())
    datos = datos_pdf(inf)
    return render_template("publico.html", token=token, inf=inf, d=datos, firmante=firmante_de(inf),
                           centro_info=centro_info())


@app.route("/r/<token>/informe.pdf")
def publico_pdf(token):
    link, inf = _link_publico(token)
    if not link or not _clave_ok(link) or inf["estado"] != "firmado":
        abort(404)
    return Response(pdf_de(inf), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre_archivo(inf)}"'})


@app.route("/r/<token>/series")
def publico_series(token):
    link, inf = _link_publico(token)
    if not link or not _clave_ok(link):
        abort(404)
    series = pacs.series(inf["estudio_id"])
    for s in series:
        s["instancias"] = pacs.instancias_ordenadas(s["id"])
    return jsonify(series=[s for s in series if s["instancias"] and s["modalidad"] not in ("SR", "PR", "KO")])


@app.route("/r/<token>/img/<instancia_id>")
def publico_img(token, instancia_id):
    link, inf = _link_publico(token)
    if not link or not _clave_ok(link) or not pacs.instancia_pertenece(instancia_id, inf["estudio_id"]):
        abort(404)
    return _imagen(instancia_id)


@app.route("/r/<token>/estudio.zip")
def publico_zip(token):
    link, inf = _link_publico(token)
    if not link or not _clave_ok(link):
        abort(404)
    auditar("paciente", "descargar_zip", f"link {link['id']}", ip())
    return _zip(inf["estudio_id"], inf["paciente_nombre"])


# ------------------------------------------------------------------ imágenes (uso interno)
def _imagen(instancia_id):
    r = pacs.preview(instancia_id)
    if r is None:
        abort(404)
    return Response(r.content, mimetype=r.headers.get("Content-Type", "image/png"),
                    headers={"Cache-Control": "private, max-age=3600"})


def _zip(estudio_id, nombre):
    r = pacs.archivo_zip(estudio_id)
    if r is None:
        abort(404)
    fn = "".join(c if c.isalnum() else "_" for c in (nombre or "estudio")) + "_DICOM.zip"
    return Response(stream_with_context(r.iter_content(1024 * 256)), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.route("/img/<instancia_id>")
@login_requerido()
def img(instancia_id):
    if restringido(usuario_actual()):
        estudio_id = pacs.estudio_de_instancia(instancia_id)
        if not estudio_id:
            abort(404)
        estudio_visible(estudio_id)
    return _imagen(instancia_id)


@app.route("/estudio/<estudio_id>/series")
@login_requerido()
def series_json(estudio_id):
    estudio_visible(estudio_id)
    series = pacs.series(estudio_id)
    for s in series:
        s["instancias"] = pacs.instancias_ordenadas(s["id"])
    return jsonify(series=[s for s in series if s["instancias"]])


@app.route("/estudio/<estudio_id>/zip")
@login_requerido("descargar")
def estudio_zip(estudio_id):
    est = estudio_visible(estudio_id)
    auditar(usuario_actual()["usuario"], "descargar_zip", estudio_id, ip())
    return _zip(estudio_id, est["paciente_nombre"])


# ------------------------------------------------------------------ dictado con Whisper (opcional)
@app.route("/api/transcribir", methods=["POST"])
@login_requerido("informar")
def transcribir():
    if not Config.WHISPER_URL:
        return jsonify(error="Dictado por servidor no configurado"), 400
    audio = request.files.get("audio")
    if not audio:
        return jsonify(error="Sin audio"), 400
    headers = {"Authorization": f"Bearer {Config.WHISPER_API_KEY}"} if Config.WHISPER_API_KEY else {}
    try:
        r = requests.post(
            f"{Config.WHISPER_URL}/v1/audio/transcriptions",
            headers=headers,
            files={"file": (audio.filename or "audio.webm", audio.stream, audio.mimetype or "audio/webm")},
            data={"model": Config.WHISPER_MODEL, "language": "es",
                  "prompt": "Informe radiológico. Hallazgos. Impresión. Parénquima, hipodensidad, "
                            "ecogenicidad, adenopatías, derrame pleural, milímetros."},
            timeout=180)
        r.raise_for_status()
        return jsonify(texto=r.json().get("text", "").strip())
    except requests.RequestException as e:
        return jsonify(error=f"Error de transcripción: {e}"), 502


# ------------------------------------------------------------------ administración
@app.route("/admin/usuarios", methods=["GET", "POST"])
@login_requerido("usuarios")
def admin_usuarios():
    db = get_db()
    if request.method == "POST":
        f = request.form
        uid = f.get("id")
        centros = ",".join(c.strip() for c in f.get("centros", "").split(",") if c.strip())
        datos = (f.get("nombre", "").strip(), f.get("rol", "radiologo"), f.get("titulo", "").strip(),
                 f.get("registro", "").strip(), 1 if f.get("activo") else 0, centros,
                 f.get("filtro_solicitante", "").strip())
        if datos[1] not in ROLES or not datos[0]:
            flash("Datos incompletos.", "error")
            return redirect(url_for("admin_usuarios"))
        if datos[1] == "derivador" and not datos[6]:
            flash("Para un médico derivador indica su nombre como aparece en los exámenes.", "error")
            return redirect(url_for("admin_usuarios"))
        clave = f.get("clave", "")
        if clave and len(clave) < 10:
            flash("La clave debe tener al menos 10 caracteres.", "error")
            return redirect(url_for("admin_usuarios"))
        if uid:
            db.execute("UPDATE usuarios SET nombre=?, rol=?, titulo=?, registro=?, activo=?, centros=?, "
                       "filtro_solicitante=? WHERE id=?", datos + (uid,))
            if clave:
                db.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (generate_password_hash(clave), uid))
        else:
            usuario = f.get("usuario", "").strip().lower()
            if not usuario or not clave:
                flash("Usuario y clave son obligatorios.", "error")
                return redirect(url_for("admin_usuarios"))
            if db.execute("SELECT 1 FROM usuarios WHERE lower(usuario)=?", (usuario,)).fetchone():
                flash("Ese usuario ya existe.", "error")
                return redirect(url_for("admin_usuarios"))
            cur = db.execute(
                "INSERT INTO usuarios (usuario, nombre, rol, titulo, registro, activo, centros, filtro_solicitante, "
                "clave_hash, creado) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (usuario,) + datos + (generate_password_hash(clave), ahora()))
            uid = cur.lastrowid
        firma = request.files.get("firma")
        if firma and firma.filename:
            if not firma.filename.lower().endswith((".png", ".jpg", ".jpeg")):
                flash("La firma debe ser PNG o JPG.", "error")
            else:
                ext = os.path.splitext(firma.filename)[1].lower()
                ruta = os.path.join(Config.DATA_DIR, "firmas", f"usuario_{int(uid)}{ext}")
                firma.save(ruta)
                db.execute("UPDATE usuarios SET firma_png=? WHERE id=?", (ruta, uid))
        db.commit()
        auditar(usuario_actual()["usuario"], "editar_usuario", str(uid), ip())
        flash("Usuario guardado.", "ok")
        return redirect(url_for("admin_usuarios"))
    usuarios = db.execute("SELECT * FROM usuarios ORDER BY activo DESC, nombre").fetchall()
    hace = (date.today() - timedelta(days=90)).strftime("%Y%m%d")
    try:
        centros = sorted({e["institucion"] for e in pacs.buscar_estudios(hace, "", limite=1000) if e["institucion"]})
    except OrthancError:
        centros = []
    return render_template("admin_usuarios.html", usuarios=usuarios, centros_conocidos=centros)


@app.route("/admin/plantillas", methods=["GET", "POST"])
@login_requerido("plantillas")
def admin_plantillas():
    db = get_db()
    if request.method == "POST":
        f = request.form
        if f.get("borrar"):
            db.execute("UPDATE plantillas SET activo=0 WHERE id=?", (f["borrar"],))
        else:
            mods = ",".join(m for m in request.form.getlist("modalidad") if m)
            if f.get("id"):
                db.execute("UPDATE plantillas SET nombre=?, modalidad=?, cuerpo=? WHERE id=?",
                           (f["nombre"].strip(), mods, f["cuerpo"], f["id"]))
            else:
                db.execute("INSERT INTO plantillas (nombre, modalidad, cuerpo) VALUES (?,?,?)",
                           (f["nombre"].strip(), mods, f["cuerpo"]))
        db.commit()
        flash("Plantillas actualizadas.", "ok")
        return redirect(url_for("admin_plantillas"))
    plantillas = db.execute("SELECT * FROM plantillas WHERE activo=1 ORDER BY modalidad, nombre").fetchall()
    return render_template("admin_plantillas.html", plantillas=plantillas,
                           mods_disp=["CT", "CR", "DX", "US", "MR", "MG"])


@app.route("/admin/auditoria")
@login_requerido("usuarios")
def admin_auditoria():
    filas = get_db().execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("admin_auditoria.html", filas=filas)


# ------------------------------------------------------------------ acceso al PACS (un solo login)
# Caddy pregunta aquí antes de dejar pasar cada petición a pacs.tucentro.cl (forward_auth).
# La sesión se comparte porque la cookie usa el dominio común (COOKIE_DOMAIN=.tucentro.cl).
POST_LECTURA = ("/tools/find", "/tools/lookup", "/tools/count-resources", "/tools/create-archive",
                "/dicom-web/")
POST_SUBIDA = POST_LECTURA + ("/tools/create-media", "/instances")
RE_UID_RUTA = re.compile(r"^/dicom-web/studies/([0-9.]+)(?:/|$)")
_cache_uid = {}


def _acceso_uid(u, uid):
    """¿Puede el usuario ver el estudio con este StudyInstanceUID? (cacheado 5 min)."""
    clave = (u["id"], uid)
    hit = _cache_uid.get(clave)
    if hit and hit[1] > time.time():
        return hit[0]
    est = pacs.estudio_por_uid(uid)
    ok = ve_estudio(u, est)
    if len(_cache_uid) > 20000:
        _cache_uid.clear()
    _cache_uid[clave] = (ok, time.time() + 300)
    auditar(u["usuario"], "visor_pacs" if ok else "visor_pacs_denegado",
            f"{uid} {est['paciente_id'] if est else ''}", request.headers.get("X-Forwarded-For", ip()))
    return ok


@app.route("/api/auth/pacs")
def auth_pacs():
    metodo = request.headers.get("X-Forwarded-Method", "GET").upper()
    uri = request.headers.get("X-Forwarded-Uri", "/")
    partes = urlsplit(uri)
    ruta, qs = partes.path, parse_qs(partes.query)
    u = usuario_actual()

    if not u:
        if metodo == "GET" and "text/html" in request.headers.get("Accept", ""):
            return redirect(f"{Config.PUBLIC_BASE_URL}/login?next=" + quote(Config.PACS_PUBLIC_URL + uri, safe=""))
        return "Sesión requerida", 401

    def ok():
        r = Response("ok")
        r.headers["X-Usuario"] = u["usuario"]
        return r

    def no(msg="Sin permiso"):
        return Response(msg, 403)

    if u["rol"] == "admin":
        return ok()
    if not (puede(u, "visor") or puede(u, "explorador_pacs")):
        return no("Tu perfil no tiene acceso al visor.")
    if metodo == "DELETE" or metodo == "PUT":
        return no("Solo un administrador puede modificar o borrar en el PACS.")
    lectura = metodo in ("GET", "HEAD", "OPTIONS")

    if not restringido(u):
        if lectura:
            return ok()
        permitidos = POST_SUBIDA if puede(u, "explorador_pacs") else POST_LECTURA
        return ok() if ruta.startswith(permitidos) else no()

    # Usuario restringido (derivador o limitado a ciertos centros): solo visor y solo sus estudios
    if not lectura:
        return no()
    if ruta.startswith(("/ohif/", "/stone-webviewer/")) or ruta in ("/system", "/plugins"):
        return ok()  # archivos del visor, sin datos de pacientes
    if ruta.startswith("/dicom-web/studies"):
        m = RE_UID_RUTA.match(ruta)
        uid = m.group(1) if m else (qs.get("StudyInstanceUID") or [""])[0]
        if uid and re.fullmatch(r"[0-9.]+", uid) and _acceso_uid(u, uid):
            return ok()
    return no("Solo puedes ver los estudios autorizados para tu usuario.")


@app.route("/salud")
def salud():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=os.environ.get("DEBUG") == "1")
