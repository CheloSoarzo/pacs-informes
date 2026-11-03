"""Integración con la agenda: órdenes, worklist DICOM, estados y portal del paciente.

La agenda envía cada cita como una "orden" con su número de acceso (AccessionNumber). Con eso:
  - se escribe la lista de trabajo (worklist) que leen los equipos: el tecnólogo elige al paciente
    en el equipo y el estudio llega al PACS con el mismo número de acceso;
  - el estudio queda unido a la cita, y la agenda ve el avance (imágenes, informado, entregado);
  - el paciente entra con su RUT y la clave que le dieron al pagar, y ve sus exámenes.

Variables:
  INTEGRACION_TOKEN   clave compartida con la agenda (la misma en ambos lados). Vacía = desactivada.
  WORKLIST_DIR        carpeta compartida con Orthanc (plugin Worklists). Vacía = sin worklist.
  MWL_MODALIDAD_TC / _RX / _ECO   modalidad DICOM de cada equipo (CT, CR, US por defecto)
  MWL_AET_TC / _RX / _ECO         AE Title del equipo, si se quiere que cada uno vea solo lo suyo
  MWL_INCLUIR         "llegados" (por defecto: pagados o marcados como llegó) o "todos"
  MWL_CHARSET         juego de caracteres del worklist (ISO_IR 100 = latín, sirve para tildes y ñ)
"""
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for

from config import Config
from core import app, centro_info, link_vigente, login_requerido, nuevo_link, pacs, registrar, usuario_actual
from db import ahora, auditar, get_db

TOKEN = os.environ.get("INTEGRACION_TOKEN", "").strip()
WORKLIST_DIR = os.environ.get("WORKLIST_DIR", "").strip()
MODALIDAD = {"tc": os.environ.get("MWL_MODALIDAD_TC", "CT") or "CT",
             "rx": os.environ.get("MWL_MODALIDAD_RX", "CR") or "CR",
             "eco": os.environ.get("MWL_MODALIDAD_ECO", "US") or "US"}
AET_EQUIPO = {k: (os.environ.get(f"MWL_AET_{k.upper()}", "") or "").strip()[:16] for k in ("tc", "rx", "eco")}
MWL_INCLUIR = (os.environ.get("MWL_INCLUIR", "llegados") or "llegados").strip().lower()
MWL_CHARSET = os.environ.get("MWL_CHARSET", "ISO_IR 100") or "ISO_IR 100"
MWL_DIAS = int(os.environ.get("MWL_DIAS", "1") or 1)          # hoy y los N días siguientes
PORTAL_MINUTOS = int(os.environ.get("PORTAL_MINUTOS", "60") or 60)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ordenes (
    accesion TEXT PRIMARY KEY,
    cita_id INTEGER,
    fecha TEXT NOT NULL, hora TEXT DEFAULT '', duracion INTEGER DEFAULT 15,
    equipo TEXT DEFAULT '', modalidad TEXT DEFAULT '',
    paciente TEXT DEFAULT '', rut TEXT DEFAULT '', rut_norm TEXT DEFAULT '', fecha_nac TEXT DEFAULT '',
    email TEXT DEFAULT '', telefono TEXT DEFAULT '', medico TEXT DEFAULT '', prevision TEXT DEFAULT '',
    examen TEXT DEFAULT '', examenes TEXT DEFAULT '', notas TEXT DEFAULT '',
    estado_agenda TEXT DEFAULT '', pagado INTEGER NOT NULL DEFAULT 0, eliminada INTEGER NOT NULL DEFAULT 0,
    estudio_id TEXT DEFAULT '',                  -- estudio de Orthanc unido por número de acceso
    visto_portal TEXT DEFAULT '',                -- primera vez que el paciente abrió el informe en línea
    recibido TEXT NOT NULL, actualizado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ordenes_rut ON ordenes(rut_norm);
CREATE INDEX IF NOT EXISTS ix_ordenes_fecha ON ordenes(fecha);
CREATE INDEX IF NOT EXISTS ix_ordenes_estudio ON ordenes(estudio_id);

-- Clave del paciente para el portal (la crea la agenda al pagar; aquí solo llega el hash)
CREATE TABLE IF NOT EXISTS portal_pacientes (
    rut_norm TEXT PRIMARY KEY,
    clave_hash TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
"""


def preparar_db():
    import sqlite3
    path = os.path.join(Config.DATA_DIR, "informes.db")
    con = sqlite3.connect(path, timeout=30)
    con.executescript(SCHEMA)
    # borrado_en: el estudio unido se borró del PACS; la agenda vuelve a «sin imágenes» hasta que llegue otro
    if "borrado_en" not in {r[1] for r in con.execute("PRAGMA table_info(ordenes)")}:
        con.execute("ALTER TABLE ordenes ADD COLUMN borrado_en TEXT DEFAULT ''")
    # Exámenes siempre en MAYÚSCULAS (así se ven en los equipos); corrige órdenes antiguas
    for acc, ex, exs in con.execute("SELECT accesion, examen, examenes FROM ordenes").fetchall():
        try:
            lista = json.loads(exs or "[]")
            nueva = json.dumps([dict(e, nombre=str(e.get("nombre") or "").upper()) for e in lista], ensure_ascii=False)
        except (ValueError, AttributeError):
            nueva = exs
        if (ex or "") != (ex or "").upper() or (exs or "[]") != nueva:
            con.execute("UPDATE ordenes SET examen=?, examenes=? WHERE accesion=?", ((ex or "").upper(), nueva, acc))
    con.commit()
    con.close()


preparar_db()


# ------------------------------------------------------------------ utilidades
def norm_rut(rut):
    return re.sub(r"[^0-9kK]", "", str(rut or "")).upper()


def rut_con_guion(rut_norm):
    return f"{rut_norm[:-1]}-{rut_norm[-1]}" if len(rut_norm) >= 2 else rut_norm


def rut_visible(rut_norm):
    """12345678K → 12.345.678-K"""
    if len(rut_norm) < 2:
        return rut_norm
    cuerpo = rut_norm[:-1]
    return f"{int(cuerpo):,}".replace(",", ".") + "-" + rut_norm[-1] if cuerpo.isdigit() else rut_con_guion(rut_norm)


def verificar_clave_agenda(clave, guardada):
    """Verifica una clave con el formato de la agenda (Node): scrypt$<sal>$<hash hex>."""
    try:
        tipo, sal, h = str(guardada).split("$")
    except ValueError:
        return False
    if tipo != "scrypt" or not sal or not h:
        return False
    calc = hashlib.scrypt(str(clave).encode(), salt=sal.encode(), n=16384, r=8, p=1, dklen=64, maxmem=64 * 1024 * 1024)
    return hmac.compare_digest(calc.hex(), h.lower())


def _token_ok():
    enviado = request.headers.get("Authorization", "")
    enviado = enviado[7:] if enviado.startswith("Bearer ") else ""
    return bool(TOKEN) and secrets.compare_digest(enviado, TOKEN)


def _txt(v, n):
    return str(v if v is not None else "").strip()[:n]


# ------------------------------------------------------------------ API para la agenda
@app.route("/api/integracion/ordenes", methods=["POST"])
def recibir_ordenes():
    if not _token_ok():
        abort(404)
    datos = request.get_json(silent=True) or {}
    db = get_db()
    n = 0
    for o in (datos.get("ordenes") or [])[:200]:
        acc = _txt(o.get("accesion"), 16)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,16}", acc):
            continue
        rut_n = norm_rut(o.get("rut_norm") or o.get("rut"))
        examenes = o.get("examenes") if isinstance(o.get("examenes"), list) else []
        fila = {
            "accesion": acc, "cita_id": int(o.get("cita_id") or 0) or None,
            "fecha": _txt(o.get("fecha"), 10), "hora": _txt(o.get("hora"), 5), "duracion": int(o.get("duracion") or 15),
            "equipo": _txt(o.get("equipo"), 5), "modalidad": MODALIDAD.get(_txt(o.get("equipo"), 5), ""),
            "paciente": _txt(o.get("paciente"), 120), "rut": _txt(o.get("rut"), 20), "rut_norm": rut_n,
            "fecha_nac": _txt(o.get("fecha_nac"), 10), "email": _txt(o.get("email"), 120),
            "telefono": _txt(o.get("telefono"), 30), "medico": _txt(o.get("medico"), 120),
            "prevision": _txt(o.get("prevision"), 40), "examen": _txt(o.get("examen"), 600).upper(),
            "examenes": json.dumps([{"codigo": _txt(e.get("codigo"), 20), "nombre": _txt(e.get("nombre"), 160).upper()}
                                    for e in examenes[:10] if isinstance(e, dict)], ensure_ascii=False),
            "notas": _txt(o.get("notas"), 2000), "estado_agenda": _txt(o.get("estado"), 20),
            "pagado": 1 if o.get("pagado") else 0, "eliminada": 1 if o.get("eliminada") else 0, "ts": ahora(),
        }
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fila["fecha"]):
            continue
        db.execute("""INSERT INTO ordenes (accesion, cita_id, fecha, hora, duracion, equipo, modalidad, paciente, rut, rut_norm,
                fecha_nac, email, telefono, medico, prevision, examen, examenes, notas, estado_agenda, pagado, eliminada,
                recibido, actualizado)
            VALUES (:accesion,:cita_id,:fecha,:hora,:duracion,:equipo,:modalidad,:paciente,:rut,:rut_norm,:fecha_nac,:email,
                :telefono,:medico,:prevision,:examen,:examenes,:notas,:estado_agenda,:pagado,:eliminada,:ts,:ts)
            ON CONFLICT(accesion) DO UPDATE SET cita_id=:cita_id, fecha=:fecha, hora=:hora, duracion=:duracion, equipo=:equipo,
                modalidad=:modalidad, paciente=:paciente, rut=:rut, rut_norm=:rut_norm, fecha_nac=:fecha_nac, email=:email,
                telefono=:telefono, medico=:medico, prevision=:prevision, examen=:examen, examenes=:examenes, notas=:notas,
                estado_agenda=:estado_agenda, pagado=:pagado, eliminada=:eliminada, actualizado=:ts""", fila)
        clave = _txt(o.get("clave_hash"), 300)
        if rut_n and clave.startswith("scrypt$"):
            db.execute("""INSERT INTO portal_pacientes (rut_norm, clave_hash, actualizado) VALUES (?,?,?)
                ON CONFLICT(rut_norm) DO UPDATE SET clave_hash=excluded.clave_hash, actualizado=excluded.actualizado""",
                       (rut_n, clave, ahora()))
        n += 1
    db.commit()
    _worklist_seguro()
    return jsonify(ok=True, recibidas=n)


@app.route("/api/integracion/estados")
def estados_para_agenda():
    """Avance de cada examen (imágenes recibidas, informado, entregado) para la agenda."""
    if not _token_ok():
        abort(404)
    desde = request.args.get("desde", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", desde):
        desde = (date.today() - timedelta(days=60)).isoformat()
    unir_estudios()
    _worklist_seguro()
    out = [{"accesion": o["accesion"], "estado": e, "detalle": d}
           for o, e, d in (estado_de(o) for o in get_db().execute(
               "SELECT * FROM ordenes WHERE fecha>=? AND eliminada=0 AND estudio_id!=''", (desde,)).fetchall())
           if e]
    # Exámenes cuyo estudio se borró del PACS: la agenda debe volver a mostrarlos sin imágenes
    out += [{"accesion": o["accesion"], "estado": "sin_imagenes", "detalle": {}}
            for o in get_db().execute("SELECT accesion FROM ordenes WHERE fecha>=? AND eliminada=0 AND estudio_id='' "
                                      "AND COALESCE(borrado_en,'')!=''", (desde,)).fetchall()]
    # pacs_url: la agenda la usa para el botón «Ver en el PACS»
    return jsonify(estados=out, pacs_url=Config.PUBLIC_BASE_URL)


# Modalidades DICOM que puede enviar cada equipo de la agenda
FAMILIA = {"tc": {"CT"}, "rx": {"CR", "DX", "DR", "RF", "OT"}, "eco": {"US"}}


def _unir(db, o, estudio_id, como):
    db.execute("UPDATE ordenes SET estudio_id=? WHERE accesion=?", (estudio_id, o["accesion"]))
    # Correo y teléfono del paciente para enviarle el informe
    db.execute("INSERT OR IGNORE INTO estudio_meta (estudio_id) VALUES (?)", (estudio_id,))
    db.execute("""UPDATE estudio_meta SET paciente_email=CASE WHEN COALESCE(paciente_email,'')='' THEN ? ELSE paciente_email END,
        paciente_telefono=CASE WHEN COALESCE(paciente_telefono,'')='' THEN ? ELSE paciente_telefono END WHERE estudio_id=?""",
               (o["email"], o["telefono"], estudio_id))
    db.execute("INSERT INTO auditoria (ts, usuario, accion, detalle, ip) VALUES (?,?,?,?,?)",
               (ahora(), "sistema" if como != "manual" else "", "unir_orden", f"{o['accesion']} → {estudio_id} ({como})", ""))


def _estudios_del_dia(fecha, rut_norm):
    """Estudios de Orthanc de esa fecha cuyo PatientID es ese RUT (con o sin puntos y guion)."""
    if len(rut_norm) < 7:
        return []
    res = pacs.post_json("/tools/find", {
        "Level": "Study", "Expand": True, "CaseSensitive": False, "Limit": 20,
        "Query": {"StudyDate": re.sub(r"\D", "", fecha), "PatientID": "*" + "*".join(rut_norm) + "*"},
        "RequestedTags": ["ModalitiesInStudy"]}) or []
    out = []
    for e in res:
        if norm_rut(e.get("PatientMainDicomTags", {}).get("PatientID", "")) != rut_norm:
            continue
        mods = (e.get("RequestedTags", {}).get("ModalitiesInStudy") or e.get("MainDicomTags", {}).get("ModalitiesInStudy") or "")
        out.append({"id": e["ID"], "mods": {m for m in mods.split("\\") if m and m not in ("SR", "PR", "KO")}})
    return out


def unir_estudios():
    """Une las órdenes recientes con su estudio en Orthanc.

    1) Por número de acceso (cuando el equipo usó el worklist o el tecnólogo lo escribió).
    2) Sin worklist: por RUT + fecha + tipo de equipo, solo si hay una única coincidencia posible.
       Si el paciente tiene dos exámenes del mismo tipo ese día, no se adivina: se une a mano en el estudio."""
    db = get_db()
    hace = (date.today() - timedelta(days=7)).isoformat()
    pendientes = db.execute("""SELECT * FROM ordenes WHERE estudio_id='' AND eliminada=0 AND fecha>=? AND fecha<=?
        AND estado_agenda NOT IN ('cancelado','ausente')""", (hace, date.today().isoformat())).fetchall()
    usados = {r[0] for r in db.execute("SELECT estudio_id FROM ordenes WHERE estudio_id!='' AND eliminada=0")}
    try:
        for o in pendientes[:200]:
            res = pacs.post_json("/tools/find", {"Level": "Study", "Query": {"AccessionNumber": o["accesion"]}, "Limit": 2}) or []
            estudio_id = (res[0] if isinstance(res[0], str) else res[0].get("ID", "")) if res else ""
            if estudio_id and estudio_id not in usados:
                _unir(db, o, estudio_id, "número de acceso")
                usados.add(estudio_id)
        # Sin worklist: RUT + fecha + equipo
        restantes = db.execute("""SELECT * FROM ordenes WHERE estudio_id='' AND eliminada=0 AND fecha>=? AND fecha<=?
            AND estado_agenda NOT IN ('cancelado','ausente') AND rut_norm!=''""", (hace, date.today().isoformat())).fetchall()
        grupos = {}
        for o in restantes:
            grupos.setdefault((o["rut_norm"], o["fecha"], o["equipo"]), []).append(o)
        for (rut, fecha, equipo), ordenes in grupos.items():
            if len(ordenes) != 1:
                continue        # dos exámenes iguales el mismo día: se une a mano
            fam = FAMILIA.get(equipo, set())
            cand = [e for e in _estudios_del_dia(fecha, rut) if e["id"] not in usados and (e["mods"] & fam or not e["mods"])]
            if len(cand) == 1:
                _unir(db, ordenes[0], cand[0]["id"], "RUT y fecha")
                usados.add(cand[0]["id"])
    except Exception as e:  # noqa: BLE001  (si el PACS no responde, se reintenta en la próxima consulta)
        app.logger.warning("No se pudieron unir estudios con la agenda: %s", e)
    db.commit()


def estado_de(o):
    """('imagenes'|'informado'|'entregado'|'', detalle) de una orden."""
    if not o["estudio_id"]:
        return o, "", {}
    db = get_db()
    inf = db.execute("SELECT id, estado, firmado_en FROM informes WHERE estudio_id=?", (o["estudio_id"],)).fetchone()
    if not inf or inf["estado"] != "firmado":
        return o, "imagenes", {}
    entregado = o["visto_portal"] or db.execute(
        "SELECT creado FROM entregas WHERE estudio_id=? AND anulado=0 ORDER BY id LIMIT 1", (o["estudio_id"],)).fetchone()
    if not entregado:
        acc = db.execute("SELECT ultimo_acceso FROM links WHERE informe_id=? AND accesos>0 ORDER BY id LIMIT 1",
                         (inf["id"],)).fetchone()
        entregado = acc["ultimo_acceso"] if acc else ""
    return o, ("entregado" if entregado else "informado"), {"firmado_en": inf["firmado_en"] or ""}


def orden_de_estudio(estudio_id, accession=""):
    """Orden de la agenda que corresponde a un estudio (para mostrarla en la pantalla del estudio)."""
    db = get_db()
    o = db.execute("SELECT * FROM ordenes WHERE estudio_id=? AND eliminada=0", (estudio_id,)).fetchone()
    if not o and accession:
        o = db.execute("SELECT * FROM ordenes WHERE accesion=? AND eliminada=0 AND estudio_id=''", (accession,)).fetchone()
        if o:
            _unir(db, o, estudio_id, "número de acceso")
            db.commit()
            o = db.execute("SELECT * FROM ordenes WHERE accesion=?", (accession,)).fetchone()
    return o


def candidatas(est):
    """Citas de la agenda que podrían ser de este estudio (para unir a mano): mismo RUT primero, ±3 días."""
    f = est.get("fecha_raw") or ""
    if len(f) < 8:
        return []
    d = date(int(f[:4]), int(f[4:6]), int(f[6:8]))
    rut = norm_rut(est.get("paciente_id"))
    filas = get_db().execute("""SELECT * FROM ordenes WHERE estudio_id='' AND eliminada=0 AND fecha BETWEEN ? AND ?
        AND estado_agenda NOT IN ('cancelado') ORDER BY (rut_norm=?) DESC, ABS(julianday(fecha)-julianday(?)), hora LIMIT 15""",
                             ((d - timedelta(days=3)).isoformat(), (d + timedelta(days=3)).isoformat(), rut, d.isoformat())).fetchall()
    return [dict(o, mismo_rut=(o["rut_norm"] == rut and bool(rut))) for o in filas]


@app.route("/agenda/<accesion>")
@login_requerido()
def desde_agenda(accesion):
    """Botón «Ver en el PACS» de la agenda: abre la ficha del paciente (todos sus exámenes), marcando ese examen."""
    db = get_db()
    o = db.execute("SELECT rut_norm, estudio_id FROM ordenes WHERE accesion=? AND eliminada=0", (accesion,)).fetchone()
    if o and not o["estudio_id"]:
        unir_estudios()
        o = db.execute("SELECT rut_norm, estudio_id FROM ordenes WHERE accesion=? AND eliminada=0", (accesion,)).fetchone()
    if o and len(o["rut_norm"] or "") >= 7:
        return redirect(url_for("ficha_paciente", rut=rut_con_guion(o["rut_norm"]), acc=accesion))
    if o and o["estudio_id"]:
        return redirect(url_for("estudio", estudio_id=o["estudio_id"]))
    flash(f"No se encontró el examen N° {accesion} en el PACS.", "error")
    return redirect(url_for("lista", q=accesion, desde="2000-01-01"))


@app.route("/ficha/<rut>")
@login_requerido()
def ficha_paciente(rut):
    """Ficha del paciente: todos sus exámenes en el PACS (de cualquier fecha) y sus citas de la agenda,
    unidos por el RUT. Cada usuario ve solo los exámenes que su perfil le permite."""
    from permisos import restringido
    from rutas_trabajo import armar_lista
    rn = norm_rut(rut)
    if len(rn) < 7:
        abort(404)
    u = usuario_actual()
    estudios, _ = armar_lista({"q": rut_con_guion(rn)})
    estudios = [e for e in estudios if norm_rut(e["paciente_id"]) == rn]
    estudios.sort(key=lambda e: -(e["llegada"].timestamp() if e["llegada"] else 0))
    db = get_db()
    ordenes = db.execute("SELECT * FROM ordenes WHERE rut_norm=? AND eliminada=0 ORDER BY fecha DESC, hora DESC",
                         (rn,)).fetchall()
    por_estudio = {o["estudio_id"]: o for o in ordenes if o["estudio_id"]}
    for e in estudios:
        o = por_estudio.get(e["id"])
        e["accesion_agenda"] = o["accesion"] if o else ""
    # Citas de la agenda que aún no tienen imágenes (solo para quien ve todo el archivo)
    citas = [] if restringido(u) else [o for o in ordenes if not o["estudio_id"]
                                       and (o["estado_agenda"] or "") not in ("cancelado",)]
    base = estudios[0] if estudios else None
    o0 = ordenes[0] if ordenes else None
    paciente = {
        "nombre": (base["paciente_nombre"] if base else "") or (o0["paciente"] if o0 else "") or "(sin nombre)",
        "rut": (base["paciente_id"] if base else "") or rut_con_guion(rn),
        "edad": base["edad"] if base else "",
        "sexo": base["paciente_sexo"] if base else "",
        "fnac": (base["paciente_fnac"] if base else "") or (
            "/".join(reversed(o0["fecha_nac"].split("-"))) if o0 and o0["fecha_nac"] else ""),
        "telefono": "" if restringido(u) else next((o["telefono"] for o in ordenes if o["telefono"]), ""),
        "email": "" if restringido(u) else next((o["email"] for o in ordenes if o["email"]), ""),
    }
    if not estudios and not citas:
        flash("No hay exámenes de ese RUT que puedas ver.", "error")
        return redirect(url_for("lista"))
    registrar("ver_ficha", rut_con_guion(rn))
    return render_template("ficha.html", p=paciente, estudios=estudios, citas=citas, marcar=request.args.get("acc", ""))


@app.route("/estudio/<estudio_id>/orden", methods=["POST"])
@login_requerido("editar_paciente")
def unir_orden(estudio_id):
    from core import estudio_visible
    estudio_visible(estudio_id)
    db = get_db()
    if request.form.get("accion") == "quitar":
        # borrado_en: la agenda vuelve a mostrar la cita «sin imágenes»
        db.execute("UPDATE ordenes SET estudio_id='', borrado_en=? WHERE estudio_id=?", (ahora(), estudio_id))
        db.commit()
        registrar("separar_orden", estudio_id)
        flash("El estudio quedó separado de la cita de la agenda.", "ok")
    else:
        acc = request.form.get("accesion", "")
        o = db.execute("SELECT * FROM ordenes WHERE accesion=? AND eliminada=0", (acc,)).fetchone()
        if not o:
            abort(404)
        if o["estudio_id"]:
            flash("Esa cita ya está unida a otro estudio.", "error")
        elif db.execute("SELECT 1 FROM ordenes WHERE estudio_id=? AND eliminada=0", (estudio_id,)).fetchone():
            flash("Este estudio ya está unido a una cita.", "error")
        else:
            _unir(db, o, estudio_id, "manual")
            db.commit()
            registrar("unir_orden", f"{acc} → {estudio_id}")
            flash(f"Estudio unido a la cita {acc} de la agenda.", "ok")
    return redirect(url_for("estudio", estudio_id=estudio_id))


app.jinja_env.globals["orden_agenda"] = lambda est: orden_de_estudio(est["id"], est.get("accession", "")) if est else None
app.jinja_env.globals["citas_candidatas"] = lambda est: candidatas(est) if est and TOKEN else []


# ------------------------------------------------------------------ worklist DICOM
def _uid_estudio(accesion):
    """StudyInstanceUID fijo para cada número de acceso (prefijo 2.25 + UUID)."""
    return "2.25." + str(uuid.uuid5(uuid.NAMESPACE_URL, f"pacs-informes/{Config.INSTANCIA}/{accesion}").int)


def _fecha(d):
    return re.sub(r"\D", "", d or "")[:8]


def _worklist_seguro():
    """El worklist nunca debe frenar la recepción de órdenes: si falla, queda en el registro y se reintenta."""
    try:
        escribir_worklist()
    except Exception as e:  # noqa: BLE001
        app.logger.warning("No se pudo escribir el worklist: %s", e)


def escribir_worklist():
    """Reescribe los archivos .wl de la carpeta del worklist (hoy y los días siguientes)."""
    if not WORKLIST_DIR:
        return
    try:
        from pydicom.dataset import Dataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian
    except ImportError:
        app.logger.warning("Falta pydicom: el worklist está desactivado")
        return
    os.makedirs(WORKLIST_DIR, exist_ok=True)
    hoy = date.today()
    hasta = (hoy + timedelta(days=MWL_DIAS)).isoformat()
    filtro = "" if MWL_INCLUIR == "todos" else "AND (pagado=1 OR estado_agenda IN ('llego','atendido'))"
    filas = get_db().execute(f"""SELECT * FROM ordenes WHERE eliminada=0 AND estudio_id='' AND fecha>=? AND fecha<=?
        AND estado_agenda NOT IN ('cancelado','ausente') {filtro} ORDER BY fecha, hora""", (hoy.isoformat(), hasta)).fetchall()
    with open(os.path.join(WORKLIST_DIR, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        deseados = set()
        for o in filas:
            nombre = f"{o['accesion']}.wl"
            deseados.add(nombre)
            ds = Dataset()
            ds.SpecificCharacterSet = MWL_CHARSET
            ds.AccessionNumber = o["accesion"]
            ds.PatientName = (o["paciente"] or "").upper().replace("^", " ")
            ds.PatientID = rut_con_guion(o["rut_norm"]) if o["rut_norm"] else o["accesion"]
            ds.PatientBirthDate = _fecha(o["fecha_nac"])
            ds.PatientSex = ""
            ds.ReferringPhysicianName = (o["medico"] or "").upper().replace("^", " ")
            ds.RequestedProcedureID = o["accesion"]
            ds.RequestedProcedureDescription = (o["examen"] or "").upper()[:64]
            ds.StudyInstanceUID = _uid_estudio(o["accesion"])
            ds.InstitutionName = (Config.CENTRO_NOMBRE or "")[:64]
            sps = Dataset()
            sps.Modality = o["modalidad"] or MODALIDAD.get(o["equipo"], "OT")
            sps.ScheduledStationAETitle = AET_EQUIPO.get(o["equipo"], "")
            sps.ScheduledProcedureStepStartDate = _fecha(o["fecha"])
            sps.ScheduledProcedureStepStartTime = re.sub(r"\D", "", o["hora"] or "")[:4] + "00"
            sps.ScheduledProcedureStepDescription = (o["examen"] or "").upper()[:64]
            sps.ScheduledProcedureStepID = o["accesion"]
            sps.ScheduledPerformingPhysicianName = ""
            ds.ScheduledProcedureStepSequence = [sps]
            meta = FileMetaDataset()
            meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.31"
            meta.MediaStorageSOPInstanceUID = _uid_estudio(o["accesion"] + "-wl")
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds.file_meta = meta
            ds.preamble = b"\0" * 128
            tmp = os.path.join(WORKLIST_DIR, "." + nombre + ".tmp")
            try:
                ds.save_as(tmp, enforce_file_format=True)
            except TypeError:  # pydicom < 3
                ds.is_little_endian, ds.is_implicit_VR = True, False
                ds.save_as(tmp, write_like_original=False)
            os.replace(tmp, os.path.join(WORKLIST_DIR, nombre))
        for f in os.listdir(WORKLIST_DIR):
            if f.endswith(".wl") and f not in deseados:
                try:
                    os.remove(os.path.join(WORKLIST_DIR, f))
                except OSError:
                    pass


# ------------------------------------------------------------------ portal del paciente
def _rut_paciente():
    rut, hasta = session.get("paciente_rut"), session.get("paciente_hasta", "")
    if not rut or hasta < ahora():
        session.pop("paciente_rut", None)
        return None
    return rut


@app.route("/paciente", methods=["GET", "POST"])
def portal_ingreso():
    if request.method == "GET" and _rut_paciente():
        return redirect(url_for("portal_examenes"))
    if request.method == "POST":
        rut = norm_rut(request.form.get("rut"))
        clave = re.sub(r"\s", "", request.form.get("clave", "")).upper()
        db = get_db()
        ipx = request.remote_addr or ""
        hace15 = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        fallos = db.execute("SELECT COUNT(*) FROM auditoria WHERE accion='portal_fallido' AND (usuario=? OR ip=?) AND ts>?",
                            (rut, ipx, hace15)).fetchone()[0]
        if fallos >= 8:
            flash("Demasiados intentos. Espera 15 minutos e inténtalo de nuevo.", "error")
            return render_template("paciente.html", modo="ingreso", c=centro_info()), 429
        p = db.execute("SELECT * FROM portal_pacientes WHERE rut_norm=?", (rut,)).fetchone() if rut else None
        if p and verificar_clave_agenda(clave, p["clave_hash"]):
            for k in [k for k in session if k.startswith("lk_")]:
                session.pop(k, None)
            session["paciente_rut"] = rut
            session["paciente_hasta"] = (datetime.now() + timedelta(minutes=PORTAL_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
            auditar(rut, "portal_ingreso", "", ipx)
            return redirect(url_for("portal_examenes"))
        auditar(rut or "?", "portal_fallido", "", ipx)
        flash("RUT o clave incorrectos. Revisa la hoja que te entregaron en recepción.", "error")
    return render_template("paciente.html", modo="ingreso", c=centro_info())


@app.route("/paciente/salir", methods=["POST"])
def portal_salir():
    for k in [k for k in session if k.startswith("paciente_") or k.startswith("lk_")]:
        session.pop(k, None)
    return redirect(url_for("portal_ingreso"))


def _examenes_de(rut):
    db = get_db()
    hoy = date.today().isoformat()
    out, vistos = [], set()
    for o in db.execute("""SELECT * FROM ordenes WHERE rut_norm=? AND eliminada=0 AND estado_agenda NOT IN ('cancelado','ausente')
            ORDER BY fecha DESC, hora DESC LIMIT 60""", (rut,)).fetchall():
        inf = db.execute("SELECT id, estado, firmado_en FROM informes WHERE estudio_id=?", (o["estudio_id"],)).fetchone() \
            if o["estudio_id"] else None
        if inf and inf["estado"] == "firmado":
            estado, txt = "listo", "Informe disponible"
        elif o["estudio_id"] or o["pagado"] or o["estado_agenda"] in ("llego", "atendido"):
            estado, txt = "proceso", "Examen realizado · informe en proceso"
        elif o["fecha"] >= hoy:
            estado, txt = "agendado", "Agendado"
        else:
            continue
        if o["estudio_id"]:
            vistos.add(o["estudio_id"])
        out.append({"fecha": o["fecha"], "hora": o["hora"], "examen": o["examen"] or "Examen", "estado": estado,
                    "estado_txt": txt, "informe_id": inf["id"] if inf and estado == "listo" else None,
                    "firmado_en": inf["firmado_en"] if inf else ""})
    # Informes validados del mismo RUT que no vinieron de la agenda (ej: exámenes anteriores)
    for inf in db.execute("""SELECT id, estudio_id, fecha_estudio, descripcion, firmado_en FROM informes WHERE estado='firmado'
            AND UPPER(REPLACE(REPLACE(REPLACE(paciente_id,'.',''),'-',''),' ',''))=? ORDER BY fecha_estudio DESC LIMIT 60""",
                          (rut,)).fetchall():
        if inf["estudio_id"] in vistos:
            continue
        f = inf["fecha_estudio"] or ""
        out.append({"fecha": f"{f[:4]}-{f[4:6]}-{f[6:8]}" if len(f) == 8 else f, "hora": "",
                    "examen": inf["descripcion"] or "Examen", "estado": "listo", "estado_txt": "Informe disponible",
                    "informe_id": inf["id"], "firmado_en": inf["firmado_en"] or ""})
    out.sort(key=lambda e: (e["fecha"], e["hora"]), reverse=True)
    return out


@app.route("/paciente/examenes")
def portal_examenes():
    rut = _rut_paciente()
    if not rut:
        return redirect(url_for("portal_ingreso"))
    o = get_db().execute("SELECT paciente FROM ordenes WHERE rut_norm=? ORDER BY fecha DESC LIMIT 1", (rut,)).fetchone()
    return render_template("paciente.html", modo="lista", c=centro_info(), examenes=_examenes_de(rut),
                           nombre=(o["paciente"] if o else ""), rut=rut_visible(rut))


@app.route("/paciente/ver/<int:inf_id>")
def portal_ver(inf_id):
    rut = _rut_paciente()
    if not rut:
        return redirect(url_for("portal_ingreso"))
    if not any(e["informe_id"] == inf_id for e in _examenes_de(rut)):
        abort(404)
    db = get_db()
    link = link_vigente(inf_id)
    if not link:
        nuevo_link(inf_id)
        link = link_vigente(inf_id)
    session[f"lk_{link['id']}"] = True     # ya se identificó con RUT y clave: no se le pide la clave del link
    inf = db.execute("SELECT estudio_id FROM informes WHERE id=?", (inf_id,)).fetchone()
    o = db.execute("SELECT accesion, visto_portal FROM ordenes WHERE estudio_id=? AND eliminada=0", (inf["estudio_id"],)).fetchone()
    primera = not (o and o["visto_portal"])
    if o and not o["visto_portal"]:
        db.execute("UPDATE ordenes SET visto_portal=? WHERE accesion=?", (ahora(), o["accesion"]))
    if primera and not db.execute("SELECT 1 FROM entregas WHERE estudio_id=? AND tipo='portal' AND anulado=0",
                                  (inf["estudio_id"],)).fetchone():
        db.execute("INSERT INTO entregas (estudio_id, tipo, detalle, usuario_id, creado) VALUES (?,?,?,?,?)",
                   (inf["estudio_id"], "portal", "Visto por el paciente en el portal", None, ahora()))
    db.commit()
    auditar(rut, "portal_ver_informe", f"informe {inf_id}", request.remote_addr or "")
    return redirect(url_for("publico", token=link["token"]))


@app.after_request
def _sin_cache_portal(resp):
    if request.path.startswith("/paciente"):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp
