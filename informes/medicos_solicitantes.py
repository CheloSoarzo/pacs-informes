"""Médicos solicitantes: se les da acceso a un examen desde su ficha (lista desplegable) y lo ven con su usuario.

Un médico solicitante es un usuario con un perfil marcado «solo ve los estudios que solicitó» (perfil
«Médicos solicitantes»). Ve un examen si:
  - alguien con permiso le dio acceso en la ficha del examen (tabla estudio_accesos), o
  - su nombre, como aparece en los exámenes (campo opcional del usuario), figura como médico solicitante.
"""
import re
import unicodedata

from flask import flash, redirect, request, url_for

from core import app, estudio_visible, login_requerido, registrar, usuario_actual, usuario_por_id
from db import ahora, get_db
from permisos import puede


def _normal(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z ]+", " ", t)


def medicos_solicitantes():
    """Usuarios activos con perfil de médico solicitante (para la lista desplegable)."""
    return get_db().execute(
        "SELECT u.id, u.nombre, u.usuario, u.filtro_solicitante FROM usuarios u JOIN perfiles p ON p.id=u.perfil_id "
        "WHERE u.activo=1 AND p.solo_solicitados=1 ORDER BY u.nombre").fetchall()


def accesos_de(estudio_id):
    return get_db().execute(
        "SELECT a.*, u.nombre, ua.nombre AS asignado_por_nombre FROM estudio_accesos a "
        "JOIN usuarios u ON u.id=a.usuario_id LEFT JOIN usuarios ua ON ua.id=a.asignado_por "
        "WHERE a.estudio_id=? ORDER BY a.asignado_en", (estudio_id,)).fetchall()


def sugerido(est, medicos, ya):
    """El médico de la lista cuyo nombre se parece al solicitante del examen (para dejarlo preseleccionado)."""
    sol = set(_normal(est.get("solicitante", "")).split())
    if not sol:
        return None
    mejor, puntos = None, 0
    for m in medicos:
        if m["id"] in ya:
            continue
        palabras = set(_normal(m["filtro_solicitante"] or m["nombre"]).split()) - {"DR", "DRA"}
        comunes = len(palabras & sol)
        if comunes >= 2 and comunes > puntos:
            mejor, puntos = m["id"], comunes
    return mejor


@app.context_processor
def _ctx_medicos():
    def medicos_del_estudio(est):
        acc = accesos_de(est["id"])
        meds = medicos_solicitantes() if puede(usuario_actual(), "acceso_solicitante") else []
        ya = {a["usuario_id"] for a in acc}
        return {"accesos": acc, "medicos": [m for m in meds if m["id"] not in ya], "sugerido": sugerido(est, meds, ya)}
    return {"medicos_del_estudio": medicos_del_estudio}


@app.route("/estudio/<estudio_id>/acceso-medico", methods=["POST"])
@login_requerido("acceso_solicitante")
def acceso_medico(estudio_id):
    est = estudio_visible(estudio_id)
    f = request.form
    db = get_db()
    accion = f.get("accion", "agregar")
    if accion in ("otro", "quitar_otro"):
        return _otro_medico(est, accion, f.get("nombre_otro", ""))
    try:
        mid = int(f.get("usuario_id") or 0)
    except ValueError:
        mid = 0
    m = usuario_por_id(mid) if mid else None
    if not m or not any(x["id"] == mid for x in medicos_solicitantes()):
        flash("Elige un médico solicitante de la lista.", "error")
        return redirect(url_for("estudio", estudio_id=estudio_id) + "#medicos")
    if accion == "quitar":
        db.execute("DELETE FROM estudio_accesos WHERE estudio_id=? AND usuario_id=?", (estudio_id, mid))
        db.commit()
        registrar("quitar_acceso_medico", f"{m['nombre']} · {est['paciente_nombre']} {est['paciente_id']} · {est['fecha']}")
        flash(f"{m['nombre']} ya no puede ver este examen.", "ok")
    else:
        db.execute("INSERT OR IGNORE INTO estudio_accesos (estudio_id, usuario_id, asignado_por, asignado_en) "
                   "VALUES (?,?,?,?)", (estudio_id, mid, usuario_actual()["id"], ahora()))
        db.commit()
        registrar("dar_acceso_medico", f"{m['nombre']} · {est['paciente_nombre']} {est['paciente_id']} · {est['fecha']}")
        flash(f"Listo: {m['nombre']} ya puede ver este examen con su usuario.", "ok")
    return redirect(url_for("estudio", estudio_id=estudio_id) + "#medicos")


def _otro_medico(est, accion, nombre):
    """Médico escrito a mano: queda como médico solicitante del examen (y del informe si aún no está validado),
    sin usuario ni acceso a las imágenes."""
    from core import guardar_meta, informe_por_estudio
    nombre = " ".join(nombre.split())[:64].upper() if accion == "otro" else ""
    if accion == "otro" and len(nombre) < 3:
        flash("Escribe el nombre del médico.", "error")
        return redirect(url_for("estudio", estudio_id=est["id"]) + "#medicos")
    anterior = (get_db().execute("SELECT solicitante_manual FROM estudio_meta WHERE estudio_id=?", (est["id"],))
                .fetchone() or [""])[0] or ""
    guardar_meta(est["id"], solicitante_manual=nombre)
    inf = informe_por_estudio(est["id"])
    aviso = ""
    if inf and inf["estado"] != "firmado":
        # el informe en curso toma el nuevo nombre si tenía el solicitante anterior (o el del DICOM)
        if (inf["solicitante"] or "") in ("", anterior, est.get("solicitante", "")):
            get_db().execute("UPDATE informes SET solicitante=? WHERE id=?", (nombre or est.get("solicitante", ""), inf["id"]))
            get_db().commit()
    elif inf:
        aviso = " El informe ya validado no cambia."
    registrar("otro_medico_solicitante", f"{nombre or '(quitado: ' + anterior + ')'} · {est['paciente_nombre']} · {est['fecha']}")
    flash((f"Médico solicitante «{nombre}» guardado (sin acceso a las imágenes)." if nombre
           else "Se quitó el médico escrito.") + aviso, "ok")
    return redirect(url_for("estudio", estudio_id=est["id"]) + "#medicos")
