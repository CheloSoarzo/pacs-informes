"""Administración: usuarios, perfiles, plantillas, frases, ajustes y auditoría."""
import os
import re
from datetime import date, timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from config import Config
from core import app, login_requerido, pacs, registrar, usuario_actual
from correo import configurado as correo_configurado
from db import ahora, get_db
from orthanc import OrthancError
from permisos import CATALOGO, TODOS

MODS_DISP = ["CT", "CR", "DX", "US", "MR", "MG"]


# ------------------------------------------------------------------ usuarios
@app.route("/admin/usuarios", methods=["GET", "POST"])
@login_requerido("usuarios")
def admin_usuarios():
    db = get_db()
    perfiles = db.execute("SELECT * FROM perfiles WHERE activo=1 ORDER BY nombre").fetchall()
    if request.method == "POST":
        f = request.form
        uid = f.get("id")
        perfil_id = int(f.get("perfil_id") or 0)
        perfil = next((p for p in perfiles if p["id"] == perfil_id), None)
        nombre = f.get("nombre", "").strip()
        if not perfil or not nombre:
            flash("Indica nombre y perfil.", "error")
            return redirect(url_for("admin_usuarios"))
        centros = ",".join(c.strip() for c in f.get("centros", "").split(",") if c.strip())
        filtro = f.get("filtro_solicitante", "").strip()
        if perfil["solo_solicitados"] and not filtro:
            flash(f"El perfil «{perfil['nombre']}» solo ve sus exámenes: indica el nombre del médico "
                  "como aparece en los exámenes.", "error")
            return redirect(url_for("admin_usuarios"))
        clave = f.get("clave", "")
        if clave and len(clave) < 10:
            flash("La clave debe tener al menos 10 caracteres.", "error")
            return redirect(url_for("admin_usuarios"))
        datos = (nombre, perfil_id, perfil["clave"], f.get("titulo", "").strip(), f.get("registro", "").strip(),
                 1 if f.get("activo") else 0, centros, filtro)
        if uid:
            if int(uid) == usuario_actual()["id"] and (perfil["clave"] != "admin" or not f.get("activo")):
                flash("No puedes quitarte tu propio acceso de administrador ni desactivarte.", "error")
                return redirect(url_for("admin_usuarios"))
            db.execute("UPDATE usuarios SET nombre=?, perfil_id=?, rol=?, titulo=?, registro=?, activo=?, centros=?, "
                       "filtro_solicitante=? WHERE id=?", datos + (uid,))
            if clave:
                db.execute("UPDATE usuarios SET clave_hash=? WHERE id=?", (generate_password_hash(clave), uid))
        else:
            usuario = f.get("usuario", "").strip().lower()
            if not re.fullmatch(r"[a-z0-9._-]{3,40}", usuario or "") or not clave:
                flash("Usuario (3 a 40 letras, números, punto o guion) y clave son obligatorios.", "error")
                return redirect(url_for("admin_usuarios"))
            if db.execute("SELECT 1 FROM usuarios WHERE lower(usuario)=?", (usuario,)).fetchone():
                flash("Ese usuario ya existe.", "error")
                return redirect(url_for("admin_usuarios"))
            cur = db.execute(
                "INSERT INTO usuarios (usuario, nombre, perfil_id, rol, titulo, registro, activo, centros, "
                "filtro_solicitante, clave_hash, creado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        registrar("editar_usuario", str(uid))
        flash("Usuario guardado.", "ok")
        return redirect(url_for("admin_usuarios"))

    usuarios = db.execute("SELECT u.*, p.nombre AS perfil_nombre FROM usuarios u "
                          "LEFT JOIN perfiles p ON p.id=u.perfil_id ORDER BY u.activo DESC, u.nombre").fetchall()
    hace = (date.today() - timedelta(days=90)).strftime("%Y%m%d")
    try:
        estudios = pacs.buscar_estudios(hace, "", limite=2000)
        centros = sorted({e["institucion"] for e in estudios if e["institucion"]})
        solicitantes = sorted({e["solicitante"] for e in estudios if e["solicitante"]})
    except OrthancError:
        centros, solicitantes = [], []
    return render_template("admin_usuarios.html", usuarios=usuarios, perfiles=perfiles,
                           centros_conocidos=centros, solicitantes_conocidos=solicitantes)


# ------------------------------------------------------------------ perfiles
@app.route("/admin/perfiles", methods=["GET", "POST"])
@login_requerido("usuarios")
def admin_perfiles():
    db = get_db()
    if request.method == "POST":
        f = request.form
        pid = f.get("id")
        if f.get("duplicar"):
            p = db.execute("SELECT * FROM perfiles WHERE id=?", (f["duplicar"],)).fetchone()
            if p:
                clave = f"{p['clave']}_copia_{int(db.execute('SELECT MAX(id) FROM perfiles').fetchone()[0]) + 1}"
                db.execute("INSERT INTO perfiles (clave, nombre, descripcion, permisos, solo_solicitados) "
                           "VALUES (?,?,?,?,?)", (clave, p["nombre"] + " (copia)", p["descripcion"],
                                                  p["permisos"], p["solo_solicitados"]))
                db.commit()
                flash("Perfil duplicado. Ajusta su nombre y permisos.", "ok")
            return redirect(url_for("admin_perfiles"))
        if f.get("borrar"):
            p = db.execute("SELECT * FROM perfiles WHERE id=?", (f["borrar"],)).fetchone()
            en_uso = db.execute("SELECT COUNT(*) FROM usuarios WHERE perfil_id=? AND activo=1",
                                (f["borrar"],)).fetchone()[0]
            if not p or p["clave"] == "admin":
                flash("Ese perfil no se puede eliminar.", "error")
            elif en_uso:
                flash(f"No se puede eliminar: {en_uso} usuario(s) activos usan ese perfil.", "error")
            else:
                db.execute("UPDATE perfiles SET activo=0 WHERE id=?", (f["borrar"],))
                db.commit()
                registrar("eliminar_perfil", p["nombre"])
                flash("Perfil eliminado.", "ok")
            return redirect(url_for("admin_perfiles"))

        nombre = f.get("nombre", "").strip()
        if not nombre:
            flash("El perfil necesita un nombre.", "error")
            return redirect(url_for("admin_perfiles"))
        permisos = ",".join(p for p in f.getlist("permisos") if p in TODOS)
        solo = 1 if f.get("solo_solicitados") else 0
        if pid:
            p = db.execute("SELECT * FROM perfiles WHERE id=?", (pid,)).fetchone()
            if not p:
                abort(404)
            if p["clave"] == "admin":
                permisos, solo = ",".join(TODOS), 0  # el perfil administrador siempre tiene todo
            db.execute("UPDATE perfiles SET nombre=?, descripcion=?, permisos=?, solo_solicitados=? WHERE id=?",
                       (nombre, f.get("descripcion", "").strip(), permisos, solo, pid))
        else:
            clave = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_")[:30] or "perfil"
            if db.execute("SELECT 1 FROM perfiles WHERE clave=?", (clave,)).fetchone():
                clave += f"_{int(db.execute('SELECT MAX(id) FROM perfiles').fetchone()[0]) + 1}"
            db.execute("INSERT INTO perfiles (clave, nombre, descripcion, permisos, solo_solicitados) "
                       "VALUES (?,?,?,?,?)", (clave, nombre, f.get("descripcion", "").strip(), permisos, solo))
        db.commit()
        registrar("editar_perfil", nombre)
        flash(f"Perfil «{nombre}» guardado.", "ok")
        return redirect(url_for("admin_perfiles"))

    perfiles = db.execute("SELECT p.*, (SELECT COUNT(*) FROM usuarios u WHERE u.perfil_id=p.id AND u.activo=1) "
                          "AS n_usuarios FROM perfiles p WHERE p.activo=1 ORDER BY p.nombre").fetchall()
    grupos = []
    for grupo, clave, desc in CATALOGO:
        if not grupos or grupos[-1][0] != grupo:
            grupos.append((grupo, []))
        grupos[-1][1].append((clave, desc))
    return render_template("admin_perfiles.html", perfiles=perfiles, grupos=grupos)


# ------------------------------------------------------------------ plantillas
@app.route("/admin/plantillas", methods=["GET", "POST"])
@login_requerido("plantillas")
def admin_plantillas():
    db = get_db()
    if request.method == "POST":
        f = request.form
        if f.get("borrar"):
            db.execute("UPDATE plantillas SET activo=0 WHERE id=?", (f["borrar"],))
        else:
            mods = ",".join(m for m in f.getlist("modalidad") if m)
            if f.get("id"):
                db.execute("UPDATE plantillas SET nombre=?, modalidad=?, cuerpo=? WHERE id=?",
                           (f["nombre"].strip(), mods, f["cuerpo"], f["id"]))
            else:
                db.execute("INSERT INTO plantillas (nombre, modalidad, cuerpo) VALUES (?,?,?)",
                           (f["nombre"].strip(), mods, f["cuerpo"]))
        db.commit()
        registrar("editar_plantillas")
        flash("Plantillas actualizadas.", "ok")
        return redirect(url_for("admin_plantillas"))
    plantillas = db.execute("SELECT * FROM plantillas WHERE activo=1 ORDER BY modalidad, nombre").fetchall()
    return render_template("admin_plantillas.html", plantillas=plantillas, mods_disp=MODS_DISP)


# ------------------------------------------------------------------ frases
@app.route("/admin/frases", methods=["GET", "POST"])
@login_requerido("frases")
def admin_frases():
    db = get_db()
    if request.method == "POST":
        f = request.form
        if f.get("borrar"):
            db.execute("UPDATE frases SET activo=0 WHERE id=?", (f["borrar"],))
        else:
            tipo = f.get("tipo") if f.get("tipo") in ("hallazgo", "conclusion") else "hallazgo"
            mods = ",".join(m for m in f.getlist("modalidad") if m)
            atajo = re.sub(r"[^a-z0-9ñ_]", "", f.get("atajo", "").strip().lower().lstrip("."))[:20]
            titulo, texto = f.get("titulo", "").strip(), f.get("texto", "").strip()
            if not titulo or not texto:
                flash("La frase necesita título y texto.", "error")
                return redirect(url_for("admin_frases"))
            if f.get("id"):
                db.execute("UPDATE frases SET tipo=?, modalidad=?, atajo=?, titulo=?, texto=? WHERE id=?",
                           (tipo, mods, atajo, titulo, texto, f["id"]))
            else:
                db.execute("INSERT INTO frases (tipo, modalidad, atajo, titulo, texto) VALUES (?,?,?,?,?)",
                           (tipo, mods, atajo, titulo, texto))
        db.commit()
        registrar("editar_frases")
        flash("Frases actualizadas.", "ok")
        return redirect(url_for("admin_frases"))
    frases = db.execute("SELECT * FROM frases WHERE activo=1 ORDER BY tipo DESC, modalidad, titulo").fetchall()
    return render_template("admin_frases.html", frases=frases, mods_disp=MODS_DISP)


# ------------------------------------------------------------------ ajustes
@app.route("/admin/ajustes", methods=["GET", "POST"])
@login_requerido("ajustes")
def admin_ajustes():
    db = get_db()
    claves = ["correo_asunto", "correo_cuerpo", "envio_auto_email", "webhook_url", "horas_alerta",
              "horas_alerta_urgente"]
    if request.method == "POST":
        f = request.form
        valores = {
            "correo_asunto": f.get("correo_asunto", "").strip()[:200],
            "correo_cuerpo": f.get("correo_cuerpo", "")[:5000],
            "envio_auto_email": "1" if f.get("envio_auto_email") else "0",
            "webhook_url": f.get("webhook_url", "").strip()[:300],
            "horas_alerta": str(max(1, min(720, int(f.get("horas_alerta") or 24)))),
            "horas_alerta_urgente": str(max(1, min(72, int(f.get("horas_alerta_urgente") or 2)))),
        }
        if valores["webhook_url"] and not valores["webhook_url"].startswith(("https://", "http://")):
            flash("La URL de aviso debe empezar con https://", "error")
            return redirect(url_for("admin_ajustes"))
        for k, v in valores.items():
            db.execute("INSERT INTO ajustes (clave, valor) VALUES (?,?) "
                       "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (k, v))
        db.commit()
        registrar("ajustes")
        flash("Ajustes guardados.", "ok")
        return redirect(url_for("admin_ajustes"))
    valores = {r["clave"]: r["valor"] for r in db.execute("SELECT * FROM ajustes").fetchall()}
    return render_template("admin_ajustes.html", v={k: valores.get(k, "") for k in claves},
                           correo_ok=correo_configurado(), smtp_host=Config.SMTP_HOST, sistema=_estado_sistema())


# ------------------------------------------------------------------ actualizar el sistema desde la web
# La app no maneja Docker (por seguridad): deja una solicitud en su carpeta de datos y el servidor
# (cron, `pacs revisar`) la ve en menos de un minuto, baja la última versión de GitHub y reinicia.
SOLICITUD = "actualizar.solicitud"


def _leer(nombre):
    try:
        with open(os.path.join(Config.DATA_DIR, nombre), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _estado_sistema():
    if not Config.ADMINISTRA_SERVIDOR:
        return None
    return {"version": _leer("version.txt"), "resultado": _leer("actualizacion.txt"),
            "pendiente": os.path.exists(os.path.join(Config.DATA_DIR, SOLICITUD))}


@app.route("/admin/actualizar", methods=["POST"])
@login_requerido("ajustes")
def admin_actualizar():
    u = usuario_actual()
    if not Config.ADMINISTRA_SERVIDOR or u["perfil_clave"] != "admin":
        abort(403)
    with open(os.path.join(Config.DATA_DIR, SOLICITUD), "w", encoding="utf-8") as f:
        f.write(f"{ahora()} {u['usuario']}\n")
    registrar("actualizar_sistema")
    flash("Actualización solicitada. En 1 a 3 minutos el sistema se reinicia con la última versión; "
          "durante ese rato puede no responder.", "ok")
    return redirect(url_for("admin_ajustes") + "#sistema")


# ------------------------------------------------------------------ auditoría
@app.route("/admin/auditoria")
@login_requerido("auditoria")
def admin_auditoria():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        filas = db.execute("SELECT * FROM auditoria WHERE usuario LIKE ? OR accion LIKE ? OR detalle LIKE ? "
                           "ORDER BY id DESC LIMIT 500", (f"%{q}%",) * 3).fetchall()
    else:
        filas = db.execute("SELECT * FROM auditoria ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("admin_auditoria.html", filas=filas, q=q)
