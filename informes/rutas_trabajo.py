"""Sesión, lista de trabajo, acciones masivas, filtros guardados, exportación y cola de dictados."""
import csv
import io
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from flask import Response, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from core import (ESTADOS, MODALIDADES, app, csrf_token, dt_estudio, fmt_duracion, ip, login_requerido,
                  pacs, radiologos, registrar, usuario_actual, SQL_USUARIO)
from db import ahora, ajuste, auditar, get_db
from orthanc import fecha_dicom
from permisos import puede, solo_solicitados, ve_estudio
from servicios import asignar, validar_informe


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
    if usuario_actual():
        registrar("logout")
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
            registrar("cambio_clave")
            flash("Clave actualizada.", "ok")
            return redirect(url_for("cuenta"))
    return render_template("cuenta.html")


# ------------------------------------------------------------------ armado de la lista
def _en_lotes(ids, sql, extra=()):
    """Ejecuta sql con 'IN (...)' en lotes de 500 ids."""
    db, filas = get_db(), []
    for i in range(0, len(ids), 500):
        lote = ids[i:i + 500]
        filas += db.execute(sql.replace("(?)", "(" + ",".join("?" * len(lote)) + ")"),
                            list(extra) + lote).fetchall()
    return filas


def estado_de(inf, dictados_pend, u):
    if inf and inf["estado"] in ("por_validar", "firmado"):
        estado = inf["estado"]
    elif dictados_pend:
        estado = "dictado"
    elif inf:
        estado = "borrador"
    else:
        estado = "pendiente"
    # Quien no ve borradores solo distingue "validado" de "pendiente"
    if estado not in ("firmado", "pendiente") and not puede(u, "ver_borrador"):
        estado = "pendiente"
    return estado


def armar_lista(args):
    """Busca en el PACS y completa cada estudio con su estado de trabajo. Devuelve (estudios, filtros)."""
    u = usuario_actual()
    hoy = date.today()
    f = {
        "desde": args.get("desde") or (hoy - timedelta(days=6)).isoformat(),
        "hasta": args.get("hasta") or hoy.isoformat(),
        "q": args.get("q", "").strip(),
        "modalidad": args.get("modalidad", ""),
        "estado": args.get("estado", ""),
        "asignado": args.get("asignado", ""),
        "prioridad": args.get("prioridad", ""),
    }
    # Si se busca un paciente, no limitar por fecha
    d = "" if f["q"] else f["desde"].replace("-", "")
    h = "" if f["q"] else f["hasta"].replace("-", "")
    solicitante = ""
    if solo_solicitados(u):
        palabras = (u["filtro_solicitante"] or "").replace("^", " ").split()
        solicitante = max(palabras, key=len) if palabras else "SIN-FILTRO-CONFIGURADO"
    estudios = [e for e in pacs.buscar_estudios(d, h, f["q"], f["modalidad"], solicitante=solicitante)
                if ve_estudio(u, e)]

    ids = [e["id"] for e in estudios]
    informes = {r["estudio_id"]: r for r in _en_lotes(
        ids, "SELECT i.*, uf.nombre AS firmante FROM informes i LEFT JOIN usuarios uf ON uf.id=i.firmado_por "
             "WHERE i.estudio_id IN (?)")}
    metas = {r["estudio_id"]: r for r in _en_lotes(
        ids, "SELECT m.*, ur.nombre AS radiologo FROM estudio_meta m LEFT JOIN usuarios ur ON ur.id=m.radiologo_id "
             "WHERE m.estudio_id IN (?)")}
    dictados = {}
    for r in _en_lotes(ids, "SELECT estudio_id, estado, transcriptor_id FROM dictados "
                            "WHERE estado IN ('pendiente','tomado') AND estudio_id IN (?)"):
        dictados.setdefault(r["estudio_id"], []).append(r)
    notas = {r[0]: r[1] for r in _en_lotes(
        ids, "SELECT estudio_id, COUNT(*) FROM notas WHERE eliminado=0 AND estudio_id IN (?) GROUP BY estudio_id")}
    entregas = {r[0]: r[1] for r in _en_lotes(
        ids, "SELECT estudio_id, COUNT(*) FROM entregas WHERE anulado=0 AND detalle NOT LIKE 'ERROR%' "
             "AND estudio_id IN (?) GROUP BY estudio_id")}

    ahora_dt = datetime.now()
    h_alerta = float(ajuste("horas_alerta") or 24)
    h_urg = float(ajuste("horas_alerta_urgente") or 2)
    for e in estudios:
        inf, m = informes.get(e["id"]), metas.get(e["id"])
        e["estado"] = estado_de(inf, dictados.get(e["id"]), u)
        e["firmante"] = inf["firmante"] if inf and inf["estado"] == "firmado" else ""
        e["informe_id"] = inf["id"] if inf else None
        e["radiologo_id"] = m["radiologo_id"] if m else None
        e["radiologo"] = m["radiologo"] if m else ""
        e["urgente"] = bool(m and m["prioridad"] == "urgente")
        e["notas"] = notas.get(e["id"], 0)
        e["entregas"] = entregas.get(e["id"], 0)
        llegada = dt_estudio(e["fecha_raw"], e["hora_raw"])
        e["llegada"] = llegada
        e["alerta"] = ""
        e["horas"] = None
        if inf and inf["estado"] == "firmado":
            fin = datetime.strptime(inf["firmado_en"], "%Y-%m-%d %H:%M:%S")
            e["horas"] = (fin - llegada).total_seconds() / 3600 if llegada else None
        else:
            desde = llegada
            if m and m["asignado_en"]:
                desde = datetime.strptime(m["asignado_en"], "%Y-%m-%d %H:%M:%S")
            if desde:
                e["horas"] = max((ahora_dt - desde).total_seconds() / 3600, 0)
                limite = h_urg if e["urgente"] else h_alerta
                e["alerta"] = "rojo" if e["horas"] > limite else ("amarillo" if e["horas"] > limite / 2 else "")

    # Filtros que no puede aplicar el PACS
    if f["estado"]:
        estudios = [e for e in estudios if e["estado"] == f["estado"]]
    if f["asignado"] == "mio":
        estudios = [e for e in estudios if e["radiologo_id"] == u["id"]]
    elif f["asignado"] == "sin":
        estudios = [e for e in estudios if not e["radiologo_id"]]
    elif f["asignado"].isdigit():
        estudios = [e for e in estudios if e["radiologo_id"] == int(f["asignado"])]
    if f["prioridad"] == "urgente":
        estudios = [e for e in estudios if e["urgente"]]

    # Urgentes sin validar primero; luego por fecha (más reciente arriba)
    estudios.sort(key=lambda e: (not (e["urgente"] and e["estado"] != "firmado"),
                                 -(e["llegada"].timestamp() if e["llegada"] else 0)))
    return estudios, f


@app.route("/")
@login_requerido()
def lista():
    estudios, f = armar_lista(request.args)
    u = usuario_actual()
    conteo = {k: sum(1 for e in estudios if e["estado"] == k) for k in ESTADOS}
    filtros = get_db().execute("SELECT * FROM filtros WHERE usuario_id=? ORDER BY nombre", (u["id"],)).fetchall()
    query_actual = urlencode({k: v for k, v in request.args.items() if v and k != "refresco"})
    query_sin_estado = urlencode({k: v for k, v in request.args.items() if v and k not in ("refresco", "estado")})
    return render_template("lista.html", estudios=estudios, f=f, modalidades=MODALIDADES, conteo=conteo,
                           radiologos=radiologos(), filtros=filtros, query_actual=query_actual,
                           query_sin_estado=query_sin_estado)


@app.route("/estudios/acciones", methods=["POST"])
@login_requerido()
def acciones_masivas():
    u = usuario_actual()
    ids = request.form.getlist("ids")
    accion = request.form.get("accion", "")
    volver = request.form.get("volver", "")
    destino = url_for("lista") + ("?" + volver if volver else "")
    if not ids:
        flash("Selecciona al menos un estudio.", "error")
        return redirect(destino)

    visibles = []
    for eid in ids:
        est = pacs.estudio(eid)
        if est and ve_estudio(u, est):
            visibles.append(eid)

    if accion in ("asignar", "urgente", "normal"):
        if not puede(u, "asignar"):
            abort(403)
        if accion == "asignar":
            rid = request.form.get("radiologo_id", "")
            rid = int(rid) if rid.isdigit() else None
            if rid and rid not in {r["id"] for r in radiologos()}:
                abort(400)
            for eid in visibles:
                asignar(eid, rid)
            flash(f"{len(visibles)} estudio(s) {'asignados' if rid else 'sin radiólogo asignado'}.", "ok")
        else:
            from core import guardar_meta
            for eid in visibles:
                guardar_meta(eid, prioridad="urgente" if accion == "urgente" else "normal")
            registrar("prioridad_" + accion, ",".join(visibles))
            flash(f"{len(visibles)} estudio(s) marcados como {'urgentes' if accion == 'urgente' else 'normales'}.", "ok")

    elif accion == "validar":
        if not (puede(u, "firmar") and puede(u, "firmar_lote")):
            abort(403)
        if request.form.get("confirmado") != "1":
            flash("Confirma la validación en lote.", "error")
            return redirect(destino)
        db = get_db()
        hechos, omitidos = 0, 0
        for eid in visibles:
            inf = db.execute("SELECT * FROM informes WHERE estudio_id=?", (eid,)).fetchone()
            if not inf or inf["estado"] != "por_validar" or len((inf["cuerpo"] or "").strip()) < 10:
                omitidos += 1
                continue
            validar_informe(inf)
            hechos += 1
        msg = f"{hechos} informe(s) validados."
        if omitidos:
            msg += f" {omitidos} omitido(s): solo se validan informes en estado «Por validar»."
        flash(msg, "ok" if hechos else "error")
    else:
        abort(400)
    return redirect(destino)


# ------------------------------------------------------------------ filtros guardados
@app.route("/filtros", methods=["POST"])
@login_requerido()
def guardar_filtro():
    nombre = request.form.get("nombre", "").strip()[:40]
    query = request.form.get("query", "")
    if nombre:
        db = get_db()
        db.execute("INSERT INTO filtros (usuario_id, nombre, query) VALUES (?,?,?)",
                   (usuario_actual()["id"], nombre, query))
        db.commit()
        flash(f"Filtro «{nombre}» guardado.", "ok")
    return redirect(url_for("lista") + ("?" + query if query else ""))


@app.route("/filtros/<int:fid>/borrar", methods=["POST"])
@login_requerido()
def borrar_filtro(fid):
    db = get_db()
    db.execute("DELETE FROM filtros WHERE id=? AND usuario_id=?", (fid, usuario_actual()["id"]))
    db.commit()
    return redirect(url_for("lista"))


# ------------------------------------------------------------------ exportar
@app.route("/estudios/exportar.csv")
@login_requerido("exportar")
def exportar():
    estudios, _ = armar_lista(request.args)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Fecha", "Hora", "Paciente", "RUT/ID", "Edad", "Sexo", "Modalidad", "Examen", "N° examen",
                "Centro", "Solicitante", "Estado", "Radiólogo asignado", "Validado por", "Urgente",
                "Horas (sin informar / hasta validar)", "Entregas"])
    for e in estudios:
        w.writerow([e["fecha"], e["hora"], e["paciente_nombre"], e["paciente_id"], e["edad"], e["paciente_sexo"],
                    e["modalidad"], e["descripcion"], e["accession"], e["institucion"], e["solicitante"],
                    ESTADOS.get(e["estado"], e["estado"]), e["radiologo"], e["firmante"],
                    "Sí" if e["urgente"] else "", f"{e['horas']:.1f}".replace(".", ",") if e["horas"] is not None else "",
                    e["entregas"]])
    registrar("exportar", f"{len(estudios)} estudios")
    nombre = f"estudios_{date.today().isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ------------------------------------------------------------------ cola de dictados
@app.route("/dictados")
@login_requerido()
def cola_dictados():
    u = usuario_actual()
    if not (puede(u, "transcribir") or puede(u, "dictar") or puede(u, "dictados_escuchar")):
        abort(403)
    ver = request.args.get("ver", "pendientes")
    db = get_db()
    sql = ("SELECT d.*, uc.nombre AS dictado_por, ut.nombre AS transcriptor, m.prioridad, "
           "i.estado AS estado_informe FROM dictados d "
           "LEFT JOIN usuarios uc ON uc.id=d.creado_por LEFT JOIN usuarios ut ON ut.id=d.transcriptor_id "
           "LEFT JOIN estudio_meta m ON m.estudio_id=d.estudio_id LEFT JOIN informes i ON i.estudio_id=d.estudio_id ")
    if ver == "mios":
        filas = db.execute(sql + "WHERE d.creado_por=? AND d.estado!='anulado' ORDER BY d.id DESC LIMIT 300",
                           (u["id"],)).fetchall()
    elif ver == "hechos":
        filas = db.execute(sql + "WHERE d.estado='transcrito' ORDER BY d.transcrito_en DESC LIMIT 300").fetchall()
    else:
        filas = db.execute(sql + "WHERE d.estado IN ('pendiente','tomado') "
                                 "ORDER BY (m.prioridad='urgente') DESC, d.id ASC").fetchall()
    ahora_dt = datetime.now()
    dictados = []
    for d in filas:
        if not ve_estudio(u, {"institucion": d["institucion"], "solicitante": d["solicitante"]}):
            continue
        x = dict(d)
        x["espera"] = fmt_duracion((ahora_dt - datetime.strptime(d["creado"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600)
        dictados.append(x)
    return render_template("dictados.html", dictados=dictados, ver=ver)


# ------------------------------------------------------------------ estadísticas
@app.route("/estadisticas")
@login_requerido("estadisticas")
def estadisticas():
    hoy = date.today()
    desde = request.args.get("desde") or hoy.replace(day=1).isoformat()
    hasta = request.args.get("hasta") or hoy.isoformat()
    u = usuario_actual()
    estudios = [e for e in pacs.buscar_estudios(desde.replace("-", ""), hasta.replace("-", ""), limite=20000)
                if ve_estudio(u, e)]
    por_mod, por_centro, por_dia = {}, {}, {}
    for e in estudios:
        for m in (e["modalidad"] or "OT").split("/"):
            por_mod[m] = por_mod.get(m, 0) + 1
        c = e["institucion"] or "(sin centro)"
        por_centro[c] = por_centro.get(c, 0) + 1
        por_dia[e["fecha_raw"]] = por_dia.get(e["fecha_raw"], 0) + 1

    ids = [e["id"] for e in estudios]
    db = get_db()
    informes = _en_lotes(ids, "SELECT i.*, uf.nombre AS firmante, ut.nombre AS transcriptor FROM informes i "
                              "LEFT JOIN usuarios uf ON uf.id=i.firmado_por "
                              "LEFT JOIN usuarios ut ON ut.id=i.transcrito_por WHERE i.estudio_id IN (?)")
    llegada = {e["id"]: dt_estudio(e["fecha_raw"], e["hora_raw"]) for e in estudios}
    validados = [i for i in informes if i["estado"] == "firmado"]
    por_rad, tiempos = {}, []
    for i in validados:
        r = por_rad.setdefault(i["firmante"] or "?", {"n": 0, "horas": []})
        r["n"] += 1
        ll = llegada.get(i["estudio_id"])
        if ll:
            h = (datetime.strptime(i["firmado_en"], "%Y-%m-%d %H:%M:%S") - ll).total_seconds() / 3600
            if h >= 0:
                r["horas"].append(h)
                tiempos.append(h)
    por_trans = {}
    for i in informes:
        if i["transcriptor"]:
            por_trans[i["transcriptor"]] = por_trans.get(i["transcriptor"], 0) + 1
    dict_pend = db.execute("SELECT COUNT(*) FROM dictados WHERE estado IN ('pendiente','tomado')").fetchone()[0]

    def mediana(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None

    resumen = {
        "estudios": len(estudios), "validados": len(validados),
        "pendientes": len(estudios) - len(validados), "dictados_pendientes": dict_pend,
        "mediana_horas": mediana(tiempos),
    }
    radiologos_tabla = sorted(({"nombre": k, "n": v["n"], "mediana": mediana(v["horas"])}
                               for k, v in por_rad.items()), key=lambda x: -x["n"])
    return render_template("estadisticas.html", desde=desde, hasta=hasta, resumen=resumen,
                           por_mod=sorted(por_mod.items(), key=lambda x: -x[1]),
                           por_centro=sorted(por_centro.items(), key=lambda x: -x[1]),
                           por_dia=[(fecha_dicom(k), v) for k, v in sorted(por_dia.items())], radiologos=radiologos_tabla,
                           transcriptores=sorted(por_trans.items(), key=lambda x: -x[1]))
