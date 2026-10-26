"""Control de acceso al PACS (un solo login) y dictado por voz con servidor Whisper."""
import re
import time
from urllib.parse import parse_qs, quote, urlsplit

import requests
from flask import Response, jsonify, redirect, request

from config import Config
from core import app, ip, login_requerido, pacs, usuario_actual
from db import auditar
from permisos import puede, restringido, ve_estudio

# Caddy pregunta aquí antes de dejar pasar cada petición al PACS (forward_auth).
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

    if puede(u, "pacs_borrar") and not restringido(u):
        return ok()
    if not (puede(u, "visor") or puede(u, "pacs_subir")):
        return no("Tu perfil no tiene acceso al visor.")
    if metodo in ("DELETE", "PUT"):
        return no("Tu perfil no puede modificar ni borrar en el PACS.")
    lectura = metodo in ("GET", "HEAD", "OPTIONS")

    if not restringido(u):
        if lectura:
            return ok()
        permitidos = POST_SUBIDA if puede(u, "pacs_subir") else POST_LECTURA
        return ok() if ruta.startswith(permitidos) else no()

    # Usuario restringido (médico de consulta o limitado a ciertos centros): solo visor y solo sus estudios
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


@app.route("/api/transcribir", methods=["POST"])
@login_requerido("redactar")
def transcribir():
    """Dictado en vivo al editor usando un servidor Whisper (opcional)."""
    if not Config.WHISPER_URL:
        return jsonify(error="Dictado por servidor no configurado"), 400
    audio = request.files.get("audio")
    if not audio:
        return jsonify(error="Sin audio"), 400
    headers = {"Authorization": f"Bearer {Config.WHISPER_API_KEY}"} if Config.WHISPER_API_KEY else {}
    try:
        r = requests.post(
            f"{Config.WHISPER_URL}/v1/audio/transcriptions", headers=headers,
            files={"file": (audio.filename or "audio.webm", audio.stream, audio.mimetype or "audio/webm")},
            data={"model": Config.WHISPER_MODEL, "language": "es",
                  "prompt": "Informe radiológico. Hallazgos. Impresión. Parénquima, hipodensidad, "
                            "ecogenicidad, adenopatías, derrame pleural, milímetros."},
            timeout=180)
        r.raise_for_status()
        return jsonify(texto=r.json().get("text", "").strip())
    except requests.RequestException as e:
        return jsonify(error=f"Error de transcripción: {e}"), 502


@app.route("/salud")
def salud():
    return "ok"
