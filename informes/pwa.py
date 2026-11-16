"""Portal del paciente como app instalable (PWA): «Agregar a pantalla de inicio» en Android y iPhone.

- /app-pacientes.webmanifest  nombre, colores e íconos de la app (con el nombre del centro)
- /sw-pacientes.js            service worker: guarda solo archivos fijos (estilos, íconos) y una página
                              «sin conexión». NUNCA guarda datos del paciente (exámenes, informes, imágenes).
- /paciente/sin-conexion      página que se muestra si no hay internet
"""
from flask import Response, jsonify, render_template, url_for

from core import Config, app, centro_info

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
            {"src": url_for("static", filename="raxio/app-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="raxio/app-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": url_for("static", filename="raxio/app-maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
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
