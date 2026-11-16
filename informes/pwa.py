"""Portal del paciente como app instalable (PWA): «Agregar a pantalla de inicio» en Android y iPhone.

- /app-pacientes.webmanifest  nombre, colores e íconos de la app (con el nombre del centro)
- /sw-pacientes.js            service worker: guarda solo archivos fijos (estilos, íconos) y una página
                              «sin conexión». NUNCA guarda datos del paciente (exámenes, informes, imágenes).
- /paciente/sin-conexion      página que se muestra si no hay internet
- /app-icono/<tipo>.png       ícono de la app con el logo de ESTE centro (cada centro tiene su app con su logo);
                              si el centro no tiene logo cargado, se usa la R de RAXIO
"""
import os
from io import BytesIO

from flask import Response, abort, jsonify, render_template, send_file, url_for

from core import Config, app, centro_info, logo_path

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


# ---------------------------------------------------------------- ícono con el logo del centro
TIPOS_ICONO = {"180": (180, 0.80), "192": (192, 0.80), "512": (512, 0.80), "mask": (512, 0.58)}  # tamaño, parte que ocupa el logo
RAXIO_ICONO = {"180": "raxio/apple-touch-icon.png", "192": "raxio/app-192.png", "512": "raxio/app-512.png",
               "mask": "raxio/app-maskable-512.png"}


def icono_url(tipo):
    """Dirección del ícono (cambia sola cuando el centro sube otro logo, para que los teléfonos tomen el nuevo)."""
    p = logo_path()
    if not p:
        return url_for("static", filename=RAXIO_ICONO[tipo])
    return url_for("app_icono", tipo=tipo, v=int(os.path.getmtime(p)))


@app.context_processor
def _icono_app():
    return {"app_icono_url": icono_url}


@app.route("/app-icono/<tipo>.png")
def app_icono(tipo):
    if tipo not in TIPOS_ICONO:
        abort(404)
    p = logo_path()
    if not p:
        return send_file(os.path.join(app.static_folder, RAXIO_ICONO[tipo]), mimetype="image/png", max_age=86400)
    lado, parte = TIPOS_ICONO[tipo]
    carpeta = os.path.join(Config.DATA_DIR, "app-iconos")
    destino = os.path.join(carpeta, f"{tipo}-{int(os.path.getmtime(p))}.png")
    if not os.path.exists(destino):
        from PIL import Image
        os.makedirs(carpeta, exist_ok=True)
        for viejo in os.listdir(carpeta):              # íconos de un logo anterior
            if viejo.startswith(tipo + "-"):
                try:
                    os.remove(os.path.join(carpeta, viejo))
                except OSError:
                    pass
        logo = Image.open(p).convert("RGBA")
        caja = logo.getbbox()                          # quita el borde transparente del logo
        if caja:
            logo = logo.crop(caja)
        maximo = int(lado * parte)
        logo.thumbnail((maximo, maximo), Image.LANCZOS)
        fondo = Image.new("RGBA", (lado, lado), (255, 255, 255, 255))   # fondo blanco: los logos vienen para fondo claro
        fondo.alpha_composite(logo, ((lado - logo.width) // 2, (lado - logo.height) // 2))
        buf = BytesIO()
        fondo.convert("RGB").save(buf, "PNG", optimize=True)
        tmp = destino + ".tmp"
        with open(tmp, "wb") as f:
            f.write(buf.getvalue())
        os.replace(tmp, destino)
    return send_file(destino, mimetype="image/png", max_age=31536000)
