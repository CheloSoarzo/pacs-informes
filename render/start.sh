#!/bin/bash
# Arranque de la demo en Render: PACS + app de informes + Caddy en un solo contenedor.
set -e
mkdir -p /data /var/lib/orthanc/db

echo "== Iniciando PACS (Orthanc)"
/docker-entrypoint.sh /tmp/orthanc.json &

echo "== Iniciando app de informes"
cd /app/informes
gunicorn -w 1 --threads 8 -b 127.0.0.1:8000 --timeout 300 app:app &

if [ "${DEMO_DATOS:-true}" = "true" ]; then
  echo "== Cargando estudios y usuarios de demostración"
  python3 /app/render/demo_datos.py &
fi

echo "== Iniciando Caddy en el puerto ${PORT}"
exec caddy run --config /app/render/Caddyfile --adapter caddyfile
