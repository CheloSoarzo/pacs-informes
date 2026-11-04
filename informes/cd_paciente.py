"""CD / DVD del paciente con visor incluido (como el «grabar CD» de Horos u OsiriX).

El PACS arma una imagen de disco (.iso) lista para grabar con lo que trae el computador
(Windows: clic derecho → «Grabar imagen de disco»; Mac: clic derecho → «Grabar imagen de disco en disco»).
El disco lleva:

    DICOMDIR + IMAGES/        imágenes originales (DICOM), para cualquier visor médico (Horos, RadiAnt, Weasis…)
    ABRIR_VISOR.HTML          visor que se abre con doble clic en cualquier navegador, sin instalar nada
    VISOR/                    imágenes de referencia (JPEG) y datos del visor
    INFORME.PDF               el informe validado (si existe)
    LEAME.TXT, AUTORUN.INF    instrucciones y nombre del disco

Como un estudio grande puede tardar, el disco se arma en segundo plano: la pantalla pide prepararlo, consulta
el avance y luego lo descarga. La imagen .iso se arma con xorriso (viene en la imagen Docker); si no está,
se entrega un .zip con el mismo contenido.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import abort, jsonify, render_template, request, send_file

from config import Config
from core import (app, centro_info, estudio_visible, informe_por_estudio, login_requerido, logo_path, pacs, pdf_de,
                  registrar, solicitante_de, usuario_actual)
from servicios import registrar_entrega

CD_MB = 700            # capacidad de un CD
DVD_MB = 4480          # capacidad de un DVD de 4,7 GB
MAX_IMG_SERIE = 500    # imágenes de referencia por serie en el visor (las DICOM van todas)
HORAS_GUARDADO = 6     # los discos preparados se borran después de este tiempo


def _base():
    p = os.path.join(Config.DATA_DIR, "tmp")
    os.makedirs(p, exist_ok=True)
    return p


def _ascii(texto, largo=None):
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9_\- ]+", "", t).strip()
    return t[:largo] if largo else t


def _version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return ""


def _tamano_mb(estudio_id):
    st = pacs.get_json(f"/studies/{estudio_id}/statistics") or {}
    mb = st.get("DiskSizeMB")
    if mb is None and st.get("DiskSize"):
        mb = int(st["DiskSize"]) / 1048576
    return float(mb or 0), int(st.get("CountInstances") or 0)


def _estado(trabajo, **cambios):
    """Lee (y opcionalmente actualiza) el estado de un disco en preparación. Se guarda en un archivo para que
    lo vean todos los procesos de la app."""
    ruta = os.path.join(trabajo, "estado.json")
    try:
        with open(ruta) as f:
            e = json.load(f)
    except (OSError, ValueError):
        e = {}
    if cambios:
        e.update(cambios)
        tmp = ruta + ".tmp"
        with open(tmp, "w") as f:
            json.dump(e, f)
        os.replace(tmp, ruta)
    return e


def _trabajo(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        abort(404)
    t = os.path.join(_base(), "cd-" + token)
    if not os.path.isdir(t):
        abort(404)
    return t


# ------------------------------------------------------------------ pantalla
@app.route("/estudio/<estudio_id>/cd")
@login_requerido("grabar_cd")
def cd_info(estudio_id):
    """Datos para la ventana «Grabar CD»: tamaño y si cabe en CD o necesita DVD."""
    est = estudio_visible(estudio_id)
    mb, n = _tamano_mb(estudio_id)
    total = mb * 1.1 + 15   # + visor de referencia e informe (aprox.)
    inf = informe_por_estudio(estudio_id)
    return jsonify(paciente=est["paciente_nombre"], fecha=est["fecha"], examen=est["descripcion"],
                   modalidad=est["modalidad"], imagenes=n, mb=round(total),
                   medio="CD" if total <= CD_MB else ("DVD" if total <= DVD_MB else "DVD doble capa"),
                   informe=bool(inf and inf["estado"] == "firmado"), iso=bool(shutil.which("xorriso")),
                   preparar=f"/estudio/{estudio_id}/cd/preparar")


@app.route("/estudio/<estudio_id>/cd/preparar", methods=["POST"])
@login_requerido("grabar_cd")
def cd_preparar(estudio_id):
    """Empieza a armar el disco en segundo plano. Devuelve la dirección para consultar el avance."""
    est = estudio_visible(estudio_id)
    limpiar_temporales()
    token = secrets.token_urlsafe(18)
    trabajo = os.path.join(_base(), "cd-" + token)
    carpeta = os.path.join(trabajo, "disco")
    os.makedirs(os.path.join(carpeta, "VISOR", "IMG"))
    u = usuario_actual()
    _estado(trabajo, usuario=u["id"], estudio=estudio_id, listo=False, error="", avance=0, paso="Preparando…")

    # Lo rápido se hace aquí (necesita la sesión y la base de datos): series, informe y textos del disco
    series, trabajos = [], []
    for k, s in enumerate(pacs.series(estudio_id), 1):
        ids = pacs.instancias_ordenadas(s["id"])
        if not ids:
            continue
        ids = ids[::max(1, -(-len(ids) // MAX_IMG_SERIE))]
        sub = f"S{k:02d}"
        os.makedirs(os.path.join(carpeta, "VISOR", "IMG", sub), exist_ok=True)
        archivos = []
        for j, iid in enumerate(ids, 1):
            rel = f"IMG/{sub}/I{j:04d}.JPG"
            archivos.append(rel)
            trabajos.append((iid, os.path.join(carpeta, "VISOR", rel)))
        series.append({"titulo": f"{s['modalidad']} · {s['descripcion'] or 'Serie ' + str(s['numero'] or k)}",
                       "imagenes": archivos})
    inf = informe_por_estudio(estudio_id)
    hay_informe = bool(inf and inf["estado"] == "firmado")
    if hay_informe:
        with open(os.path.join(carpeta, "INFORME.PDF"), "wb") as f:
            f.write(pdf_de(inf))
    c = centro_info()
    logo = logo_path()
    if logo:
        shutil.copy(logo, os.path.join(carpeta, "VISOR", "LOGO.PNG"))
    datos = {"centro": c.get("nombre", ""), "direccion": c.get("direccion", ""), "telefono": c.get("telefono", ""),
             "logo": bool(logo), "paciente": est["paciente_nombre"], "rut": est["paciente_id"],
             "fnac": est["paciente_fnac"], "sexo": est["paciente_sexo"], "fecha": est["fecha"], "hora": est["hora"],
             "examen": est["descripcion"], "modalidad": est["modalidad"], "acceso": est["accession"],
             "solicitante": (inf["solicitante"] if inf else "") or solicitante_de(est), "informe": hay_informe,
             "series": series, "grabado": datetime.now().strftime("%d/%m/%Y %H:%M"), "version": _version()}
    with open(os.path.join(carpeta, "ABRIR_VISOR.HTML"), "w", encoding="utf-8") as f:
        f.write(render_template("cd_visor.html"))
    with open(os.path.join(carpeta, "LEAME.TXT"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(render_template("cd_leame.txt", d=datos))
    with open(os.path.join(carpeta, "AUTORUN.INF"), "w", encoding="ascii", newline="\r\n") as f:
        f.write(f"[autorun]\nlabel={_ascii(c.get('nombre', ''), 32) or 'IMAGENES'}\n")

    fecha = (est.get("fecha_raw") or "")[:8]
    nombre = (_ascii(f"CD_{est['paciente_nombre']}_{fecha}", 60).replace(" ", "_") or "CD_paciente")
    volumen = _ascii(f"{fecha} {est['accession'] or ''}", 32).upper().replace(" ", "_") or "IMAGENES"
    _estado(trabajo, nombre=nombre)
    threading.Thread(target=_armar, args=(trabajo, carpeta, estudio_id, trabajos, datos, nombre, volumen),
                     daemon=True).start()

    if request.form.get("entrega") == "1" or (request.is_json and (request.get_json(silent=True) or {}).get("entrega")):
        registrar_entrega(estudio_id, "cd", "CD/DVD con visor")
    registrar("grabar_cd", f"{est['paciente_nombre']} {est['paciente_id']} · {est['fecha']} · {est['descripcion']}")
    return jsonify(ok=True, estado=f"/cd/{token}/estado", descargar=f"/cd/{token}/descargar")


def _armar(trabajo, carpeta, estudio_id, trabajos, datos, nombre, volumen):
    """Segundo plano: imágenes DICOM con DICOMDIR, imágenes del visor e imagen del disco."""
    try:
        _estado(trabajo, paso="Copiando las imágenes originales (DICOM)…", avance=5)
        r = pacs._req("GET", f"/studies/{estudio_id}/media", stream=True, timeout=1800)
        if r is None:
            raise RuntimeError("El estudio ya no existe en el PACS")
        tmpzip = os.path.join(trabajo, "media.zip")
        with open(tmpzip, "wb") as f:
            for trozo in r.iter_content(1 << 20):
                f.write(trozo)
        with zipfile.ZipFile(tmpzip) as z:
            for n in z.namelist():
                destino = os.path.normpath(os.path.join(carpeta, n))
                if not destino.startswith(carpeta + os.sep):
                    continue   # ruta rara dentro del zip: se ignora
                if n.endswith("/"):
                    os.makedirs(destino, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                with z.open(n) as src, open(destino, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        os.remove(tmpzip)

        total, hechas = len(trabajos), [0]

        def bajar(t):
            iid, ruta = t
            try:
                resp = pacs.preview(iid)
                if resp is not None:
                    with open(ruta, "wb") as f:
                        f.write(resp.content)
            except Exception:  # noqa: BLE001  (una imagen que no se puede mostrar no detiene el disco)
                pass
            hechas[0] += 1
            if hechas[0] % 25 == 0:
                _estado(trabajo, paso=f"Preparando el visor ({hechas[0]} de {total} imágenes)…",
                        avance=30 + int(55 * hechas[0] / max(total, 1)))
        _estado(trabajo, paso="Preparando el visor…", avance=30)
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(bajar, trabajos))
        visor = os.path.join(carpeta, "VISOR")
        for s in datos["series"]:
            s["imagenes"] = [a for a in s["imagenes"] if os.path.exists(os.path.join(visor, a))]
        datos["series"] = [s for s in datos["series"] if s["imagenes"]]
        with open(os.path.join(visor, "DATOS.JS"), "w", encoding="utf-8") as f:
            f.write("window.CD = " + json.dumps(datos, ensure_ascii=False) + ";")

        _estado(trabajo, paso="Armando la imagen del disco…", avance=88)
        if shutil.which("xorriso"):
            salida = os.path.join(trabajo, nombre + ".iso")
            r = subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-V", volumen, "-J", "-joliet-long", "-R",
                                "-o", salida, carpeta], capture_output=True, timeout=1800)
            if r.returncode != 0 or not os.path.exists(salida):
                raise RuntimeError("No se pudo armar la imagen del disco: " +
                                   r.stderr.decode(errors="ignore")[-300:])
        else:
            salida = os.path.join(trabajo, nombre + ".zip")
            with zipfile.ZipFile(salida, "w", zipfile.ZIP_STORED) as z:
                for raiz, _, archivos in os.walk(carpeta):
                    for a in archivos:
                        p = os.path.join(raiz, a)
                        z.write(p, os.path.relpath(p, carpeta))
        shutil.rmtree(carpeta, ignore_errors=True)
        _estado(trabajo, listo=True, avance=100, paso="Listo", archivo=os.path.basename(salida),
                mb=round(os.path.getsize(salida) / 1048576))
    except Exception as e:  # noqa: BLE001
        app.logger.exception("No se pudo preparar el CD")
        _estado(trabajo, error=f"No se pudo preparar el disco: {e}", paso="Error")


@app.route("/cd/<token>/estado")
@login_requerido("grabar_cd")
def cd_estado(token):
    e = _estado(_trabajo(token))
    return jsonify(listo=bool(e.get("listo")), error=e.get("error", ""), avance=e.get("avance", 0),
                   paso=e.get("paso", ""), mb=e.get("mb"))


@app.route("/cd/<token>/descargar")
@login_requerido("grabar_cd")
def cd_descargar(token):
    t = _trabajo(token)
    e = _estado(t)
    if not e.get("listo") or not e.get("archivo"):
        abort(404)
    if e.get("usuario") != usuario_actual()["id"]:
        abort(403)
    ruta = os.path.join(t, e["archivo"])
    tipo = "application/x-iso9660-image" if ruta.endswith(".iso") else "application/zip"
    return send_file(ruta, mimetype=tipo, as_attachment=True, download_name=e["archivo"])


def limpiar_temporales():
    """Borra discos preparados hace más de unas horas (ocupan espacio)."""
    base = _base()
    ahora = time.time()
    for n in os.listdir(base):
        p = os.path.join(base, n)
        if n.startswith("cd-") and ahora - os.path.getmtime(p) > HORAS_GUARDADO * 3600:
            shutil.rmtree(p, ignore_errors=True)
