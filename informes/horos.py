"""Horos / OsiriX: archivo de nodos DICOM listo para importar, y botón «Abrir en Horos / OsiriX».

1) /admin/equipos/horos.plist — ubicación (nodo DICOM) del PACS con sus datos, para importar en
   Horos u OsiriX (Preferencias → Ubicaciones/Locations → Importar). Recuperación por C-GET, que funciona
   aunque el computador con Horos esté detrás de un router (no necesita que el PACS le abra conexión).

2) Botón en el estudio: crea un enlace de descarga temporal (15 minutos) del estudio en ZIP y abre
   horos:// u osirix:// con «DownloadURL»: Horos/OsiriX baja el ZIP, lo importa y abre el estudio.
"""
import plistlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import Response, abort, jsonify, request

from config import Config
from core import app, estudio_visible, login_requerido, registrar, usuario_actual
from db import ahora, get_db

MINUTOS_ENLACE = 15
USOS_ENLACE = 3


def _host_pacs():
    """Dirección del servidor para los equipos (la misma que se muestra en la pantalla de equipos)."""
    return Config.SERVIDOR_IP_PUBLICA or urlsplit(Config.PUBLIC_BASE_URL).hostname or ""


def _tabla():
    get_db().execute("""CREATE TABLE IF NOT EXISTS descargas_temp (
        token TEXT PRIMARY KEY, estudio_id TEXT NOT NULL, usuario_id INTEGER, expira TEXT NOT NULL,
        usos INTEGER NOT NULL DEFAULT 0)""")


@app.route("/admin/equipos/horos.plist")
@login_requerido("equipos")
def horos_plist():
    nombre = Config.CENTRO_NOMBRE or "PACS"
    nodo = {
        "Description": f"PACS {nombre}"[:64],
        "AETitle": Config.PACS_AET,
        "Address": _host_pacs(),
        "Port": int(Config.DICOM_PUERTO_PUBLICO or 4242),
        "QR": True,                 # buscar y recuperar (Query/Retrieve)
        "Send": True,               # enviar estudios al PACS
        "Activated": True,
        "retrieveMode": 1,          # 0 = C-MOVE, 1 = C-GET, 2 = WADO
        "TransferSyntax": 0,        # Explicit Little Endian
        "TLSEnabled": False,
    }
    registrar("descargar_nodos_horos", nodo["Address"])
    datos = plistlib.dumps([nodo], fmt=plistlib.FMT_XML)
    archivo = "".join(c if c.isalnum() else "_" for c in nombre) + "_DICOMNodes.plist"
    return Response(datos, mimetype="application/x-plist",
                    headers={"Content-Disposition": f'attachment; filename="{archivo}"'})


@app.route("/estudio/<estudio_id>/horos", methods=["POST"])
@login_requerido("descargar")
def horos_enlace(estudio_id):
    """Enlace temporal del ZIP del estudio, para que Horos/OsiriX lo descargue por su cuenta."""
    est = estudio_visible(estudio_id)
    _tabla()
    token = secrets.token_urlsafe(24)
    expira = (datetime.now() + timedelta(minutes=MINUTOS_ENLACE)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    db.execute("DELETE FROM descargas_temp WHERE expira < ?", (ahora(),))
    db.execute("INSERT INTO descargas_temp (token, estudio_id, usuario_id, expira) VALUES (?,?,?,?)",
               (token, estudio_id, usuario_actual()["id"], expira))
    db.commit()
    registrar("abrir_en_horos", f"{est['paciente_nombre']} {est['paciente_id']} · {est['fecha']}")
    zip_url = f"{Config.PUBLIC_BASE_URL}/dl/{token}.zip"
    consulta = f"?methodName=DownloadURL&Display=YES&URL='{zip_url}'"
    return jsonify(horos="horos://" + consulta, osirix="osirix://" + consulta, zip=zip_url)


@app.route("/dl/<token>.zip")
def descarga_temporal(token):
    """ZIP del estudio con un enlace temporal (lo usa Horos/OsiriX, que no tiene la sesión del navegador)."""
    if not (16 <= len(token) <= 64):
        abort(404)
    _tabla()
    db = get_db()
    d = db.execute("SELECT * FROM descargas_temp WHERE token=?", (token,)).fetchone()
    if not d or d["expira"] < ahora() or d["usos"] >= USOS_ENLACE:
        abort(404)
    db.execute("UPDATE descargas_temp SET usos=usos+1 WHERE token=?", (token,))
    db.commit()
    from rutas_publico import _zip
    from db import auditar
    auditar(f"usuario#{d['usuario_id']}", "descarga_horos", d["estudio_id"],
            request.headers.get("X-Forwarded-For", request.remote_addr or ""))
    return _zip(d["estudio_id"], "estudio")
