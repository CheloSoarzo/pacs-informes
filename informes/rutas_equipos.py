"""Equipos que envían estudios por internet (lista blanca DICOM).

Cada equipo se registra con su AE Title y la IP pública fija de su centro. Con eso:
  1. Orthanc solo acepta envíos (C-STORE) de equipos registrados y desde su IP (DicomCheckModalityHost).
  2. El script firewall/sincronizar_firewall.sh abre el puerto DICOM solo a esas IPs.
"""
import ipaddress
import re
import secrets

from flask import Response, abort, flash, redirect, render_template, request, url_for

from config import Config
from core import app, login_requerido, pacs, registrar
from db import ahora, get_db
from orthanc import OrthancError

PREFIJO = "equipo-"       # nombre de las modalidades que administra esta app dentro de Orthanc
RE_AET = re.compile(r"^[A-Za-z0-9_\-. ]{1,16}$")


def _validar_ip(texto):
    try:
        ip = ipaddress.ip_address(texto.strip())
    except ValueError:
        return None, "La IP no es válida. Usa el formato 200.29.15.195"
    if ip.version != 4:
        return None, "Usa una IP versión 4 (ej: 200.29.15.195)."
    if not ip.is_global:
        return None, ("Esa IP es privada (de la red interna). Necesitas la IP pública del centro: "
                      "desde un PC del centro abre https://ifconfig.me para verla.")
    return str(ip), None


def sincronizar():
    """Deja en Orthanc exactamente los equipos activos de la lista. Devuelve (n_ok, errores)."""
    equipos = get_db().execute("SELECT * FROM equipos WHERE activo=1").fetchall()
    deseados = {f"{PREFIJO}{e['id']}": e for e in equipos}
    errores = []
    try:
        actuales = pacs.modalidades()
    except OrthancError as e:
        return 0, [str(e)]
    for nombre in actuales:
        if nombre.startswith(PREFIJO) and nombre not in deseados:
            try:
                pacs.borrar_modalidad(nombre)
            except OrthancError as e:
                errores.append(f"{nombre}: {e}")
    ok = 0
    for nombre, e in deseados.items():
        a = actuales.get(nombre) or {}
        if a.get("AET") == e["aet"] and a.get("Host") == e["ip"] and a.get("AllowStore", True):
            ok += 1
            continue
        try:
            pacs.guardar_modalidad(nombre, e["aet"], e["ip"])
            ok += 1
        except OrthancError as err:
            errores.append(f"{e['nombre']}: {err}")
    return ok, errores


@app.route("/admin/equipos", methods=["GET", "POST"])
@login_requerido("equipos")
def admin_equipos():
    db = get_db()
    if request.method == "POST":
        f = request.form
        if f.get("sincronizar"):
            ok, errores = sincronizar()
            flash(f"{ok} equipo(s) activos en el PACS." + (" Errores: " + "; ".join(errores) if errores else ""),
                  "error" if errores else "ok")
            return redirect(url_for("admin_equipos"))
        if f.get("borrar"):
            e = db.execute("SELECT * FROM equipos WHERE id=?", (f["borrar"],)).fetchone()
            if e:
                db.execute("DELETE FROM equipos WHERE id=?", (e["id"],))
                db.commit()
                registrar("eliminar_equipo", f"{e['nombre']} {e['aet']} {e['ip']}")
                sincronizar()
                flash(f"Equipo «{e['nombre']}» eliminado: ya no puede enviar estudios.", "ok")
            return redirect(url_for("admin_equipos"))

        nombre = f.get("nombre", "").strip()[:60]
        aet = f.get("aet", "").strip().upper()
        ip, err = _validar_ip(f.get("ip", ""))
        if not nombre:
            err = "Ponle un nombre al equipo (ej: TC Rancagua)."
        elif not RE_AET.match(aet):
            err = "El AE Title debe tener de 1 a 16 caracteres: letras, números, guion o guion bajo."
        if err:
            flash(err, "error")
            return redirect(url_for("admin_equipos"))
        repetido = db.execute("SELECT nombre FROM equipos WHERE aet=? AND ip=? AND id!=?",
                              (aet, ip, int(f.get("id") or 0))).fetchone()
        if repetido:
            flash(f"Ya existe un equipo con ese AE Title e IP ({repetido['nombre']}). "
                  "Cada equipo de un mismo centro necesita un AE Title distinto.", "error")
            return redirect(url_for("admin_equipos"))
        datos = (nombre, f.get("centro", "").strip()[:60], aet, ip, 1 if f.get("activo") else 0,
                 f.get("notas", "").strip()[:300])
        if f.get("id"):
            db.execute("UPDATE equipos SET nombre=?, centro=?, aet=?, ip=?, activo=?, notas=? WHERE id=?",
                       datos + (f["id"],))
        else:
            db.execute("INSERT INTO equipos (nombre, centro, aet, ip, activo, notas, creado) VALUES (?,?,?,?,?,?,?)",
                       datos + (ahora(),))
        db.commit()
        registrar("editar_equipo", f"{nombre} {aet} {ip}")
        ok, errores = sincronizar()
        if errores:
            flash("Equipo guardado, pero no se pudo actualizar el PACS: " + "; ".join(errores), "error")
        else:
            flash(f"Equipo «{nombre}» guardado y habilitado en el PACS.", "ok")
        return redirect(url_for("admin_equipos"))

    equipos = db.execute("SELECT * FROM equipos ORDER BY activo DESC, centro, nombre").fetchall()
    try:
        en_pacs = pacs.modalidades()
        estado_pacs = None
    except OrthancError as e:
        en_pacs, estado_pacs = {}, str(e)
    return render_template("admin_equipos.html", equipos=equipos, en_pacs=en_pacs, estado_pacs=estado_pacs,
                           prefijo=PREFIJO, pacs_aet=Config.PACS_AET, puerto=Config.DICOM_PUERTO_PUBLICO,
                           ip_servidor=Config.SERVIDOR_IP_PUBLICA, firewall_token=bool(Config.FIREWALL_TOKEN),
                           token_sugerido=secrets.token_urlsafe(24))


@app.route("/api/equipos/ips")
def ips_firewall():
    """Lista de IPs autorizadas para el script de firewall del servidor (protegida con FIREWALL_TOKEN).
    También vuelve a sincronizar los equipos con Orthanc, por si el PACS se reinició."""
    token = request.headers.get("X-Token", "")
    if not Config.FIREWALL_TOKEN or not secrets.compare_digest(token, Config.FIREWALL_TOKEN):
        abort(404)
    sincronizar()
    ips = sorted({e["ip"] for e in get_db().execute("SELECT ip FROM equipos WHERE activo=1").fetchall()})
    return Response("\n".join(ips) + ("\n" if ips else ""), mimetype="text/plain")
