"""Pantalla de estudio: informe, flujo dictado → transcripción → validación, asignación, notas y entregas."""
import mimetypes
import os
import sqlite3
import threading
from datetime import datetime

import requests
from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from config import Config
from core import (app, estudio_visible, guardar_meta, informe_por_estudio, link_vigente, login_requerido,
                  meta_de, pacs, radiologos, registrar, url_link, usuario_actual, usuario_por_id, ve_informe)
from correo import configurado as correo_configurado
from db import ahora, get_db
from pdf import generar_pdf
from permisos import puede, ve_estudio
from servicios import asignar, enviar_correo_informe, registrar_entrega, validar_informe

EXT_AUDIO = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
             "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/aac": ".aac"}
EXT_ADJUNTO = (".pdf", ".jpg", ".jpeg", ".png", ".txt", ".doc", ".docx")


def _volver(estudio_id, ancla=""):
    return redirect(url_for("estudio", estudio_id=estudio_id) + (f"#{ancla}" if ancla else ""))


# ------------------------------------------------------------------ pantalla del estudio
@app.route("/estudio/<estudio_id>")
@login_requerido()
def estudio(estudio_id):
    u = usuario_actual()
    est = estudio_visible(estudio_id)
    db = get_db()
    inf = informe_por_estudio(estudio_id)
    meta = meta_de(estudio_id)
    series = pacs.series(estudio_id)
    mods = set(est["modalidad"].split("/")) if est["modalidad"] else set()

    def aplica(modalidad_campo):
        return not modalidad_campo or not mods or bool(mods & set(modalidad_campo.split(",")))

    plantillas = [p for p in db.execute("SELECT * FROM plantillas WHERE activo=1 ORDER BY nombre").fetchall()
                  if aplica(p["modalidad"])]
    frases = [dict(f) for f in db.execute("SELECT * FROM frases WHERE activo=1 ORDER BY tipo DESC, titulo")
              .fetchall() if aplica(f["modalidad"])]
    dictados = db.execute(
        "SELECT d.*, uc.nombre AS dictado_por, ut.nombre AS transcriptor FROM dictados d "
        "LEFT JOIN usuarios uc ON uc.id=d.creado_por LEFT JOIN usuarios ut ON ut.id=d.transcriptor_id "
        "WHERE d.estudio_id=? AND d.estado!='anulado' ORDER BY d.id", (estudio_id,)).fetchall()
    notas = db.execute("SELECT n.*, uu.nombre AS autor FROM notas n LEFT JOIN usuarios uu ON uu.id=n.usuario_id "
                       "WHERE n.estudio_id=? AND n.eliminado=0 ORDER BY n.id", (estudio_id,)).fetchall() \
        if puede(u, "notas") else []
    entregas = db.execute("SELECT e.*, uu.nombre AS usuario, ua.nombre AS anulado_por_nombre FROM entregas e "
                          "LEFT JOIN usuarios uu ON uu.id=e.usuario_id LEFT JOIN usuarios ua ON ua.id=e.anulado_por "
                          "WHERE e.estudio_id=? ORDER BY e.id DESC", (estudio_id,)).fetchall()
    link = link_vigente(inf["id"]) if inf else None
    versiones = db.execute(
        "SELECT v.*, uu.nombre AS firmante FROM informe_versiones v LEFT JOIN usuarios uu ON uu.id=v.firmado_por "
        "WHERE informe_id=? ORDER BY version DESC", (inf["id"],)).fetchall() if inf else []
    firmante = usuario_por_id(inf["firmado_por"]) if inf and inf["firmado_por"] else None
    transcriptor = usuario_por_id(inf["transcrito_por"]) if inf and inf["transcrito_por"] else None
    radiologo = usuario_por_id(meta["radiologo_id"]) if meta["radiologo_id"] else None

    # Otros estudios del mismo paciente (para comparar)
    historial = []
    if est["paciente_id"]:
        previos = [e for e in pacs.estudios_de_paciente(est["paciente_id"]) if e["id"] != estudio_id and ve_estudio(u, e)]
        estados = {r["estudio_id"]: r["estado"] for r in db.execute(
            f"SELECT estudio_id, estado FROM informes WHERE estudio_id IN ({','.join('?' * len(previos))})",
            [e["id"] for e in previos]).fetchall()} if previos else {}
        for e in previos[:20]:
            e["estado"] = estados.get(e["id"], "pendiente")
            if e["estado"] != "firmado" and not puede(u, "ver_borrador"):
                e["estado"] = "pendiente"
            historial.append(e)

    registrar("abrir_estudio", f"{estudio_id} {est['paciente_id']}")
    return render_template(
        "estudio.html", est=est, inf=inf if (inf and (ve_informe(u, inf) or puede(u, "redactar"))) else None,
        meta=meta, radiologo=radiologo, radiologos=radiologos(), series=series, plantillas=plantillas,
        frases=frases, dictados=dictados, notas=notas, entregas=entregas, link=link, url_link=url_link(link),
        versiones=versiones, firmante=firmante, transcriptor=transcriptor, historial=historial,
        visor_ohif=f"{Config.PACS_PUBLIC_URL}/ohif/viewer?StudyInstanceUIDs={est['uid']}",
        visor_stone=f"{Config.PACS_PUBLIC_URL}/stone-webviewer/index.html?study={est['uid']}",
        whisper=bool(Config.WHISPER_URL), requiere_clave=Config.LINK_REQUIERE_CLAVE,
        correo_ok=correo_configurado())


# ------------------------------------------------------------------ informe
def _crear_o_actualizar(estudio_id, cuerpo, solicitante):
    u = usuario_actual()
    db = get_db()
    est = estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    if inf and inf["estado"] == "firmado":
        return None, "El informe ya está validado. Rectifícalo para modificarlo."
    if not inf:
        db.execute(
            """INSERT INTO informes (estudio_id, estudio_uid, paciente_nombre, paciente_id, paciente_fnac,
               paciente_sexo, fecha_estudio, hora_estudio, modalidad, descripcion, accession, institucion,
               solicitante, cuerpo, creado_por, creado, actualizado_por, actualizado)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (estudio_id, est["uid"], est["paciente_nombre"], est["paciente_id"], est["paciente_fnac_raw"],
             est["paciente_sexo"], est["fecha_raw"], est["hora_raw"], est["modalidad"], est["descripcion"],
             est["accession"], est["institucion"], solicitante if solicitante is not None else est["solicitante"],
             cuerpo, u["id"], ahora(), u["id"], ahora()))
    else:
        campos, vals = ["cuerpo=?", "actualizado_por=?", "actualizado=?"], [cuerpo, u["id"], ahora()]
        if solicitante is not None:
            campos.append("solicitante=?")
            vals.append(solicitante)
        db.execute(f"UPDATE informes SET {', '.join(campos)} WHERE estudio_id=?", vals + [estudio_id])
    # Quien transcribe "toma" los dictados pendientes del estudio
    if puede(u, "transcribir"):
        db.execute("UPDATE dictados SET estado='tomado', transcriptor_id=?, tomado_en=? "
                   "WHERE estudio_id=? AND estado='pendiente'", (u["id"], ahora(), estudio_id))
    db.commit()
    return informe_por_estudio(estudio_id), None


@app.route("/estudio/<estudio_id>/guardar", methods=["POST"])
@login_requerido("redactar")
def guardar(estudio_id):
    data = request.get_json(silent=True) or {}
    inf, err = _crear_o_actualizar(estudio_id, data.get("cuerpo", ""), data.get("solicitante"))
    if err:
        return jsonify(error=err), 409
    return jsonify(ok=True, guardado=datetime.now().strftime("%H:%M:%S"), estado=inf["estado"])


@app.route("/estudio/<estudio_id>/enviar-validacion", methods=["POST"])
@login_requerido("redactar")
def enviar_validacion(estudio_id):
    data = request.get_json(silent=True) or {}
    cuerpo = data.get("cuerpo", "")
    if len(cuerpo.strip()) < 10:
        return jsonify(error="El informe está vacío."), 400
    inf, err = _crear_o_actualizar(estudio_id, cuerpo, data.get("solicitante"))
    if err:
        return jsonify(error=err), 409
    u = usuario_actual()
    db = get_db()
    db.execute("UPDATE informes SET estado='por_validar', transcrito_por=?, transcrito_en=? WHERE id=?",
               (u["id"], ahora(), inf["id"]))
    db.execute("UPDATE dictados SET estado='transcrito', transcriptor_id=COALESCE(transcriptor_id, ?), "
               "transcrito_en=? WHERE estudio_id=? AND estado IN ('pendiente','tomado')",
               (u["id"], ahora(), estudio_id))
    db.commit()
    registrar("enviar_validacion", f"informe {inf['id']} {inf['paciente_id']}")
    return jsonify(ok=True)


@app.route("/estudio/<estudio_id>/firmar", methods=["POST"])
@login_requerido("firmar")
def firmar(estudio_id):
    data = request.get_json(silent=True) or {}
    cuerpo = data.get("cuerpo", "")
    if len(cuerpo.strip()) < 10:
        return jsonify(error="El informe está vacío."), 400
    inf, err = _crear_o_actualizar(estudio_id, cuerpo, data.get("solicitante"))
    if err:
        return jsonify(error=err), 409
    inf, aviso = validar_informe(inf)
    return jsonify(ok=True, aviso=aviso)


def _rectificar(estudio_id, invalidar):
    estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    if not inf or inf["estado"] != "firmado":
        abort(400)
    u = usuario_actual()
    if invalidar:
        if not puede(u, "invalidar"):
            abort(403)
    elif not (puede(u, "rectificar_todos") or (puede(u, "rectificar") and inf["firmado_por"] == u["id"])):
        flash("Solo quien validó el informe, o alguien con permiso para rectificar informes de otros, "
              "puede rectificarlo.", "error")
        return _volver(estudio_id)
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Indica el motivo.", "error")
        return _volver(estudio_id)
    db = get_db()
    db.execute(
        """INSERT INTO informe_versiones (informe_id, version, cuerpo, firmado_por, firmado_en,
           reabierto_por, reabierto_en, motivo) VALUES (?,?,?,?,?,?,?,?)""",
        (inf["id"], inf["version"], inf["cuerpo"], inf["firmado_por"], inf["firmado_en"], u["id"], ahora(),
         ("INVALIDADO: " if invalidar else "") + motivo))
    db.execute("UPDATE informes SET estado='borrador', version=version+1, firmado_por=NULL, firmado_en=NULL "
               "WHERE id=?", (inf["id"],))
    if invalidar:
        db.execute("UPDATE links SET revocado=1 WHERE informe_id=? AND revocado=0", (inf["id"],))
    db.commit()
    registrar("invalidar" if invalidar else "rectificar", f"informe {inf['id']} v{inf['version']}: {motivo}")
    flash("Informe invalidado: se retiró y el link del paciente quedó desactivado." if invalidar else
          "Informe reabierto. Al validarlo de nuevo quedará como nueva versión.", "ok")
    return _volver(estudio_id)


@app.route("/estudio/<estudio_id>/reabrir", methods=["POST"])
@login_requerido()
def reabrir(estudio_id):
    return _rectificar(estudio_id, invalidar=False)


@app.route("/estudio/<estudio_id>/invalidar", methods=["POST"])
@login_requerido("invalidar")
def invalidar(estudio_id):
    return _rectificar(estudio_id, invalidar=True)


@app.route("/estudio/<estudio_id>/vista-previa.pdf", methods=["POST"])
@login_requerido("redactar")
def vista_previa(estudio_id):
    """PDF de borrador con el texto actual del editor (sin guardar)."""
    from core import centro_info, logo_path
    from flask import Response
    est = estudio_visible(estudio_id)
    d = dict(est)
    d["cuerpo"] = request.form.get("cuerpo", "")
    d["solicitante"] = request.form.get("solicitante", est["solicitante"])
    pdf = generar_pdf(d, None, centro_info(), None, logo_path())
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="borrador.pdf"'})


# ------------------------------------------------------------------ asignación y datos del paciente
@app.route("/estudio/<estudio_id>/asignar", methods=["POST"])
@login_requerido("asignar")
def asignar_estudio(estudio_id):
    estudio_visible(estudio_id)
    rid = request.form.get("radiologo_id", "")
    rid = int(rid) if rid.isdigit() else None
    if rid and rid not in {r["id"] for r in radiologos()}:
        abort(400)
    asignar(estudio_id, rid)
    prioridad = request.form.get("prioridad")
    if prioridad in ("normal", "urgente"):
        guardar_meta(estudio_id, prioridad=prioridad)
    flash("Asignación actualizada.", "ok")
    return _volver(estudio_id)


@app.route("/estudio/<estudio_id>/tomar", methods=["POST"])
@login_requerido("firmar")
def tomar_estudio(estudio_id):
    estudio_visible(estudio_id)
    asignar(estudio_id, usuario_actual()["id"])
    flash("El estudio quedó asignado a ti.", "ok")
    return _volver(estudio_id)


@app.route("/estudio/<estudio_id>/contacto", methods=["POST"])
@login_requerido("datos_contacto")
def contacto(estudio_id):
    estudio_visible(estudio_id)
    guardar_meta(estudio_id, paciente_email=request.form.get("email", "").strip()[:120],
                 paciente_telefono=request.form.get("telefono", "").strip()[:30])
    registrar("contacto", estudio_id)
    flash("Datos de contacto guardados.", "ok")
    return _volver(estudio_id, "entrega")


# ------------------------------------------------------------------ dictados de audio
def _transcribir_auto(dictado_id, ruta, mime, db_path):
    """Borrador automático con Whisper (en segundo plano). Si falla, el dictado sigue igual."""
    try:
        headers = {"Authorization": f"Bearer {Config.WHISPER_API_KEY}"} if Config.WHISPER_API_KEY else {}
        with open(ruta, "rb") as fh:
            r = requests.post(f"{Config.WHISPER_URL}/v1/audio/transcriptions", headers=headers,
                              files={"file": (os.path.basename(ruta), fh, mime)},
                              data={"model": Config.WHISPER_MODEL, "language": "es",
                                    "prompt": "Informe radiológico. Hallazgos. Impresión."}, timeout=600)
        r.raise_for_status()
        texto = r.json().get("text", "").strip()
        con = sqlite3.connect(db_path, timeout=30)
        con.execute("UPDATE dictados SET texto_auto=? WHERE id=?", (texto, dictado_id))
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001 - el borrador automático es opcional
        pass


@app.route("/estudio/<estudio_id>/dictados", methods=["POST"])
@login_requerido("dictar")
def subir_dictado(estudio_id):
    est = estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    if inf and inf["estado"] == "firmado":
        return jsonify(error="El informe ya está validado."), 409
    audio = request.files.get("audio")
    if not audio:
        return jsonify(error="No llegó el audio."), 400
    mime = (audio.mimetype or "audio/webm").split(";")[0]
    if not mime.startswith("audio/"):
        return jsonify(error="El archivo no es audio."), 400
    try:
        duracion = float(request.form.get("duracion", "0") or 0)
    except ValueError:
        duracion = 0
    u = usuario_actual()
    db = get_db()
    cur = db.execute(
        """INSERT INTO dictados (estudio_id, paciente_nombre, paciente_id, descripcion, modalidad, fecha_estudio,
           institucion, solicitante, archivo, mime, duracion, creado_por, creado, nota)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (estudio_id, est["paciente_nombre"], est["paciente_id"], est["descripcion"], est["modalidad"],
         est["fecha_raw"], est["institucion"], est["solicitante"], "", mime, duracion, u["id"], ahora(),
         request.form.get("nota", "")[:300]))
    did = cur.lastrowid
    nombre = f"{did}{EXT_AUDIO.get(mime, '.webm')}"
    ruta = os.path.join(Config.DATA_DIR, "dictados", nombre)
    audio.save(ruta)
    db.execute("UPDATE dictados SET archivo=? WHERE id=?", (nombre, did))
    # Si nadie está asignado, el estudio queda asignado a quien dictó
    if not meta_de(estudio_id).get("radiologo_id") and puede(u, "firmar"):
        asignar(estudio_id, u["id"])
    db.commit()
    registrar("dictado", f"{estudio_id} dictado {did} ({duracion:.0f}s)")
    if Config.WHISPER_URL:
        threading.Thread(target=_transcribir_auto, daemon=True,
                         args=(did, ruta, mime, os.path.join(Config.DATA_DIR, "informes.db"))).start()
    return jsonify(ok=True, id=did)


def _dictado_visible(did):
    d = get_db().execute("SELECT * FROM dictados WHERE id=?", (did,)).fetchone()
    if not d:
        abort(404)
    u = usuario_actual()
    if not ve_estudio(u, {"institucion": d["institucion"], "solicitante": d["solicitante"]}):
        abort(403)
    return d


@app.route("/dictados/<int:did>/audio")
@login_requerido()
def audio_dictado(did):
    d = _dictado_visible(did)
    u = usuario_actual()
    if not (puede(u, "dictados_escuchar") or d["creado_por"] == u["id"]):
        abort(403)
    ruta = os.path.join(Config.DATA_DIR, "dictados", d["archivo"])
    if not d["archivo"] or not os.path.exists(ruta):
        abort(404)
    return send_file(ruta, mimetype=d["mime"] or mimetypes.guess_type(ruta)[0], conditional=True)


@app.route("/dictados/<int:did>/texto-auto")
@login_requerido("dictados_escuchar")
def texto_auto(did):
    d = _dictado_visible(did)
    return jsonify(texto=d["texto_auto"] or "")


@app.route("/dictados/<int:did>/tomar", methods=["POST"])
@login_requerido("transcribir")
def tomar_dictado(did):
    d = _dictado_visible(did)
    u = usuario_actual()
    if d["estado"] in ("pendiente", "tomado"):
        db = get_db()
        db.execute("UPDATE dictados SET estado='tomado', transcriptor_id=?, tomado_en=? WHERE id=?",
                   (u["id"], ahora(), did))
        db.commit()
        registrar("tomar_dictado", str(did))
    return redirect(url_for("estudio", estudio_id=d["estudio_id"]) + "#informe")


@app.route("/dictados/<int:did>/anular", methods=["POST"])
@login_requerido()
def anular_dictado(did):
    d = _dictado_visible(did)
    u = usuario_actual()
    propio = d["creado_por"] == u["id"] and d["estado"] == "pendiente"
    if not (puede(u, "dictados_anular") or propio):
        abort(403)
    db = get_db()
    db.execute("UPDATE dictados SET estado='anulado' WHERE id=?", (did,))
    db.commit()
    registrar("anular_dictado", str(did))
    flash("Dictado anulado.", "ok")
    return _volver(d["estudio_id"])


# ------------------------------------------------------------------ notas y adjuntos
@app.route("/estudio/<estudio_id>/notas", methods=["POST"])
@login_requerido("notas")
def agregar_nota(estudio_id):
    estudio_visible(estudio_id)
    texto = request.form.get("texto", "").strip()[:2000]
    archivo = request.files.get("archivo")
    if not texto and not (archivo and archivo.filename):
        flash("Escribe una nota o adjunta un archivo.", "error")
        return _volver(estudio_id, "notas")
    db = get_db()
    cur = db.execute("INSERT INTO notas (estudio_id, usuario_id, texto, creado) VALUES (?,?,?,?)",
                     (estudio_id, usuario_actual()["id"], texto, ahora()))
    if archivo and archivo.filename:
        ext = os.path.splitext(archivo.filename)[1].lower()
        if ext not in EXT_ADJUNTO:
            db.rollback()
            flash("Formato no permitido. Usa PDF, JPG, PNG, TXT o Word.", "error")
            return _volver(estudio_id, "notas")
        nombre = f"nota_{cur.lastrowid}{ext}"
        archivo.save(os.path.join(Config.DATA_DIR, "adjuntos", nombre))
        db.execute("UPDATE notas SET archivo=?, archivo_nombre=? WHERE id=?",
                   (nombre, os.path.basename(archivo.filename)[:120], cur.lastrowid))
    db.commit()
    registrar("nota", estudio_id)
    return _volver(estudio_id, "notas")


@app.route("/notas/<int:nid>/archivo")
@login_requerido("notas")
def archivo_nota(nid):
    n = get_db().execute("SELECT * FROM notas WHERE id=? AND eliminado=0", (nid,)).fetchone()
    if not n or not n["archivo"]:
        abort(404)
    estudio_visible(n["estudio_id"])
    return send_file(os.path.join(Config.DATA_DIR, "adjuntos", n["archivo"]), as_attachment=True,
                     download_name=n["archivo_nombre"] or n["archivo"])


@app.route("/notas/<int:nid>/eliminar", methods=["POST"])
@login_requerido("notas")
def eliminar_nota(nid):
    n = get_db().execute("SELECT * FROM notas WHERE id=?", (nid,)).fetchone()
    if not n:
        abort(404)
    estudio_visible(n["estudio_id"])
    u = usuario_actual()
    if n["usuario_id"] != u["id"] and not puede(u, "notas_eliminar"):
        abort(403)
    db = get_db()
    db.execute("UPDATE notas SET eliminado=1 WHERE id=?", (nid,))
    db.commit()
    registrar("eliminar_nota", str(nid))
    return _volver(n["estudio_id"], "notas")


# ------------------------------------------------------------------ entrega
@app.route("/estudio/<estudio_id>/entregas", methods=["POST"])
@login_requerido("entregas")
def nueva_entrega(estudio_id):
    from core import TIPOS_ENTREGA
    estudio_visible(estudio_id)
    tipo = request.form.get("tipo", "")
    if tipo not in TIPOS_ENTREGA or tipo == "email":
        abort(400)
    registrar_entrega(estudio_id, tipo, request.form.get("detalle", "").strip()[:200])
    flash("Entrega registrada.", "ok")
    return _volver(estudio_id, "entrega")


@app.route("/entregas/<int:eid>/anular", methods=["POST"])
@login_requerido("entregas_anular")
def anular_entrega(eid):
    e = get_db().execute("SELECT * FROM entregas WHERE id=?", (eid,)).fetchone()
    if not e:
        abort(404)
    estudio_visible(e["estudio_id"])
    db = get_db()
    db.execute("UPDATE entregas SET anulado=1, anulado_por=?, anulado_en=? WHERE id=?",
               (usuario_actual()["id"], ahora(), eid))
    db.commit()
    registrar("anular_entrega", str(eid))
    return _volver(e["estudio_id"], "entrega")


@app.route("/estudio/<estudio_id>/email", methods=["POST"])
@login_requerido("email")
def enviar_email(estudio_id):
    estudio_visible(estudio_id)
    inf = informe_por_estudio(estudio_id)
    if not inf or inf["estado"] != "firmado":
        flash("El informe debe estar validado para enviarlo.", "error")
        return _volver(estudio_id, "entrega")
    para = request.form.get("para", "").strip()
    if puede(usuario_actual(), "datos_contacto") and para:
        guardar_meta(estudio_id, paciente_email=para[:120])
    ok, msg = enviar_correo_informe(inf, para)
    flash(msg, "ok" if ok else "error")
    return _volver(estudio_id, "entrega")
