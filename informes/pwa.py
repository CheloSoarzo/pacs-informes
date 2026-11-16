"""Portal del paciente como app instalable (PWA): «Agregar a pantalla de inicio» en Android y iPhone.

- /app-pacientes.webmanifest  nombre, colores e íconos de la app (con el nombre del centro)
- /sw-pacientes.js            service worker: guarda solo archivos fijos (estilos, íconos) y una página
                              «sin conexión». NUNCA guarda datos del paciente (exámenes, informes, imágenes).
- /paciente/sin-conexion      página que se muestra si no hay internet
- /app-icono/<tipo>.png       ícono de la app de ESTE centro (cada centro tiene su app con su ícono):
                              el logo del centro centrado sobre blanco; si en Ajustes se subió un «Ícono de la app
                              de pacientes», se usa ese; si no hay ninguno, la R de RAXIO
- /admin/app-icono            subir o quitar ese ícono
"""
import os
from io import BytesIO

from flask import Response, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from core import Config, app, centro_info, login_requerido, logo_path, registrar

ARCHIVOS_FIJOS = ["static/app.css", "static/portal.css", "static/raxio/favicon.svg", "static/raxio/app-192.png"]


@app.route("/app-pacientes.webmanifest")
def manifest_pacientes():
    nombre = Config.CENTRO_NOMBRE or "Mis exámenes"
    r = jsonify({
        "name": f"Mis exámenes · {nombre}",
        "short_name": "Mis exámenes",
        "description": f"Informes e imágenes de sus exámenes en {nombre}",
        "lang": "es-CL",
        "id": "/paciente",
        "start_url": "/paciente?app=1",
        "scope": "/",
        "display": "standalone",
        "background_color": "#F2F6F8",
        "theme_color": "#0F2A3D",
        "icons": [
            {"src": icono_url("192"), "sizes": "192x192", "type": "image/png"},
            {"src": icono_url("512"), "sizes": "512x512", "type": "image/png"},
            {"src": icono_url("mask"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    })
    r.headers["Content-Type"] = "application/manifest+json"
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r


@app.route("/sw-pacientes.js")
def sw_pacientes():
    js = render_template("sw_pacientes.js", version=Config.VERSION,
                         fijos=["/" + f for f in ARCHIVOS_FIJOS], sin_conexion=url_for("portal_sin_conexion"))
    return Response(js, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.route("/paciente/sin-conexion")
def portal_sin_conexion():
    return render_template("paciente_sin_conexion.html", c=centro_info())


# ---------------------------------------------------------------- ícono de la app de cada centro
# tamaño y parte del ícono que ocupa la imagen: (ícono subido, logo del centro). Android recorta en círculo («mask»).
TIPOS_ICONO = {"180": (180, 1.0, 0.86), "192": (192, 1.0, 0.86), "512": (512, 1.0, 0.86), "mask": (512, 0.80, 0.66)}
RAXIO_ICONO = {"180": "raxio/apple-touch-icon.png", "192": "raxio/app-192.png", "512": "raxio/app-512.png",
               "mask": "raxio/app-maskable-512.png"}


def icono_subido():
    p = os.path.join(Config.DATA_DIR, "app-icono.png")
    return p if os.path.exists(p) else None


def icono_path():
    """Imagen de origen del ícono: el subido en Ajustes o, si no hay, el logo del centro."""
    return icono_subido() or logo_path()


def icono_url(tipo):
    """Dirección del ícono (cambia sola al subir otro, para que los teléfonos tomen el nuevo)."""
    p = icono_path()
    if not p:
        return url_for("static", filename=RAXIO_ICONO[tipo])
    return url_for("app_icono", tipo=tipo, v=int(os.path.getmtime(p)))


@app.context_processor
def _icono_app():
    return {"app_icono_url": icono_url, "app_icono_subido": bool(icono_subido())}


@app.route("/app-icono/<tipo>.png")
def app_icono(tipo):
    if tipo not in TIPOS_ICONO:
        abort(404)
    p = icono_path()
    if not p:
        return send_file(os.path.join(app.static_folder, RAXIO_ICONO[tipo]), mimetype="image/png", max_age=86400)
    subido = p == icono_subido()
    lado, parte_subido, parte_logo = TIPOS_ICONO[tipo]
    carpeta = os.path.join(Config.DATA_DIR, "app-iconos")
    destino = os.path.join(carpeta, f"{tipo}-{'i' if subido else 'l'}{int(os.path.getmtime(p))}.png")
    if not os.path.exists(destino):
        from PIL import Image
        os.makedirs(carpeta, exist_ok=True)
        for viejo in os.listdir(carpeta):              # tamaños de un ícono anterior
            if viejo.startswith(tipo + "-"):
                try:
                    os.remove(os.path.join(carpeta, viejo))
                except OSError:
                    pass
        im = Image.open(p).convert("RGBA")
        if subido:
            medida = int(lado * parte_subido)
            im = im.resize((medida, medida), Image.LANCZOS)
        else:
            # logo: se le quita el margen (transparente o blanco) y se ajusta al ancho/alto disponible
            mascara = Image.eval(im.convert("L"), lambda v: 255 if v < 235 else 0)
            mascara = Image.composite(mascara, Image.new("L", im.size, 0), im.getchannel("A"))
            caja = mascara.getbbox()
            if caja:
                im = im.crop(caja)
            medida = int(lado * parte_logo)
            im.thumbnail((medida, medida), Image.LANCZOS)
        fondo = Image.new("RGBA", (lado, lado), (255, 255, 255, 255))
        fondo.alpha_composite(im, ((lado - im.width) // 2, (lado - im.height) // 2))
        tmp = destino + ".tmp"
        fondo.convert("RGB").save(tmp, "PNG", optimize=True)
        os.replace(tmp, destino)
    return send_file(destino, mimetype="image/png", max_age=31536000)


@app.route("/admin/app-icono", methods=["POST"])
@login_requerido("ajustes")
def admin_app_icono():
    """Sube o quita el ícono de la app de pacientes (imagen cuadrada; si no lo es, se completa con blanco)."""
    destino = os.path.join(Config.DATA_DIR, "app-icono.png")
    volver = redirect(url_for("admin_ajustes") + "#app-icono")
    if request.form.get("accion") == "quitar":
        try:
            os.remove(destino)
        except OSError:
            pass
        registrar("app_icono_quitar")
        flash("Ícono quitado: la app vuelve a usar el logo del centro.", "ok")
        return volver
    f = request.files.get("icono")
    if not f or not f.filename:
        flash("Elige una imagen (PNG o JPG).", "error")
        return volver
    datos = f.read(6 * 1024 * 1024 + 1)
    if len(datos) > 6 * 1024 * 1024:
        flash("La imagen supera los 6 MB.", "error")
        return volver
    try:
        from PIL import Image
        im = Image.open(BytesIO(datos))
        im.load()
        if im.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("formato")
        im = im.convert("RGBA")
        lado = max(im.size)
        if im.width != im.height:                       # no cuadrada: se centra sobre blanco
            fondo = Image.new("RGBA", (lado, lado), (255, 255, 255, 255))
            fondo.alpha_composite(im, ((lado - im.width) // 2, (lado - im.height) // 2))
            im = fondo
        if lado > 1024:
            im = im.resize((1024, 1024), Image.LANCZOS)
        tmp = destino + ".tmp"
        im.save(tmp, "PNG", optimize=True)
        os.replace(tmp, destino)
    except Exception:  # noqa: BLE001
        flash("No se pudo leer la imagen. Usa un archivo PNG o JPG.", "error")
        return volver
    registrar("app_icono_subir")
    flash("Ícono guardado. Los teléfonos que ya instalaron la app lo toman en unos días; "
          "para verlo al tiro, borra la app y vuelve a instalarla.", "ok")
    return volver
