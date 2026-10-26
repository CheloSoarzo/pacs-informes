"""Links para pacientes, PDF, imágenes y descargas."""
import secrets

from flask import Response, abort, flash, jsonify, redirect, render_template, request, session, stream_with_context, url_for

from core import (app, centro_info, datos_pdf, estudio_visible, firmante_de, login_requerido, nombre_archivo,
                  nuevo_link, pacs, pdf_de, registrar, url_link, usuario_actual, ve_informe)
from db import ahora, auditar, get_db
from permisos import restringido


# ------------------------------------------------------------------ PDF interno
@app.route("/informe/<int:inf_id>.pdf")
@login_requerido()
def informe_pdf(inf_id):
    inf = get_db().execute("SELECT * FROM informes WHERE id=?", (inf_id,)).fetchone()
    if not inf:
        abort(404)
    estudio_visible(inf["estudio_id"])
    if not ve_informe(usuario_actual(), inf):
        abort(404)
    return Response(pdf_de(inf), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre_archivo(inf)}"'})


# ------------------------------------------------------------------ gestión de links
@app.route("/informe/<int:inf_id>/link", methods=["POST"])
@login_requerido("links")
def crear_link(inf_id):
    inf = get_db().execute("SELECT * FROM informes WHERE id=?", (inf_id,)).fetchone()
    if not inf or inf["estado"] != "firmado":
        return jsonify(error="El informe debe estar validado."), 400
    estudio_visible(inf["estudio_id"])
    db = get_db()
    db.execute("UPDATE links SET revocado=1 WHERE informe_id=? AND revocado=0", (inf_id,))
    db.commit()
    token, clave = nuevo_link(inf_id)
    return jsonify(url=url_link({"token": token}), clave=clave)


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
    registrar("revocar_link", f"informe {inf_id}")
    flash("Link desactivado. El paciente ya no podrá abrirlo.", "ok")
    return redirect(url_for("estudio", estudio_id=inf["estudio_id"]) + "#entrega")


# ------------------------------------------------------------------ página del paciente
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
            auditar("paciente", "clave_link_fallida", f"link {link['id']}", request.remote_addr or "")
            flash("Clave incorrecta.", "error")
        return render_template("publico_clave.html", token=token)
    db = get_db()
    db.execute("UPDATE links SET accesos=accesos+1, ultimo_acceso=? WHERE id=?", (ahora(), link["id"]))
    db.commit()
    auditar("paciente", "abrir_link", f"link {link['id']} informe {inf['id']}", request.remote_addr or "")
    return render_template("publico.html", token=token, inf=inf, d=datos_pdf(inf), firmante=firmante_de(inf),
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
    auditar("paciente", "descargar_zip", f"link {link['id']}", request.remote_addr or "")
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
    registrar("descargar_zip", estudio_id)
    return _zip(estudio_id, est["paciente_nombre"])
