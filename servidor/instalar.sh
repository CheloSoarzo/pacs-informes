#!/bin/bash
# Instalación automática del PACS + Informes en un servidor Ubuntu 22.04/24.04 (x86 o ARM).
# Pensado para ejecutarse una sola vez al crear el servidor (cloud-init), como root.
#
# Variables opcionales (se pasan desde cloud-init):
#   ADMIN_PASSWORD   clave inicial del usuario admin (si no, se genera una)
#   REPO             repositorio git del proyecto
#   DOMINIO          dominio propio; si no, se usa pacs.<ip>.sslip.io (funciona sin comprar dominio)
#   DICOM_PUERTO_PUBLICO  puerto DICOM público (por defecto 46429)
#   CENTRO_NOMBRE    nombre que aparece en los informes
set -euo pipefail
exec > >(tee -a /var/log/pacs-instalacion.log) 2>&1
echo "=== Instalación PACS + Informes: $(date) ==="

DIR=/opt/pacs-informes
REPO="${REPO:-https://github.com/CheloSoarzo/pacs-informes}"
PUERTO="${DICOM_PUERTO_PUBLICO:-46429}"
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y ca-certificates curl git openssl cron

# 1) Firewall del sistema operativo. Las imágenes Ubuntu de Oracle Cloud bloquean todo salvo SSH:
#    se abren 80/443 (web) y el puerto DICOM. (El script de firewall luego limita DICOM a los centros.)
if [ -f /etc/iptables/rules.v4 ] && ! grep -q -- "--dport 443 -j ACCEPT" /etc/iptables/rules.v4; then
  sed -i "/--dport 22 -j ACCEPT/a -A INPUT -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT\n-A INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT\n-A INPUT -p tcp -m state --state NEW -m tcp --dport ${PUERTO} -j ACCEPT" /etc/iptables/rules.v4
  iptables-restore < /etc/iptables/rules.v4
  echo "Firewall del sistema: abiertos 80, 443 y ${PUERTO}"
fi

# 2) Docker
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

# 3) Proyecto
if [ ! -d "$DIR/.git" ]; then
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

# 4) Configuración (.env) — solo la primera vez
IP="$(curl -fsS --max-time 15 https://api.ipify.org || curl -fsS --max-time 15 https://ifconfig.me)"
DOMINIO="${DOMINIO:-pacs.$(echo "$IP" | tr . -).sslip.io}"
if [ ! -f .env ]; then
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 12 | tr -d '/+=')}"
  cat > .env <<ENV
# Generado por servidor/instalar.sh el $(date '+%F %T')
CADDYFILE=Caddyfile.unico
DOMINIO=${DOMINIO}
PUBLIC_BASE_URL=https://${DOMINIO}
PACS_PUBLIC_URL=https://${DOMINIO}
COOKIE_DOMAIN=

PG_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_USER=admin
ADMIN_PASSWORD=${ADMIN_PASSWORD}

PACS_NOMBRE=PACS
PACS_AET=PACS
DICOM_PUERTO_PUBLICO=${PUERTO}
SERVIDOR_IP_PUBLICA=${IP}
FIREWALL_TOKEN=$(openssl rand -hex 24)
DICOM_STORAGE_DIR=./data/orthanc

CENTRO_NOMBRE=${CENTRO_NOMBRE:-Centro de Imágenes}
CENTRO_DIRECCION=
CENTRO_TELEFONO=
CENTRO_EMAIL=

LINK_DIAS_VIGENCIA=60
LINK_REQUIERE_CLAVE=true

WHISPER_URL=
WHISPER_MODEL=whisper-1
WHISPER_API_KEY=

SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_SSL=false
SMTP_STARTTLS=true
ENV
  chmod 600 .env
  echo "Archivo .env creado. Dominio: https://${DOMINIO}"
fi

# 5) Levantar el sistema
docker compose up -d --build

# 6) Firewall DICOM automático (cada 5 minutos y al reiniciar)
chmod +x firewall/sincronizar_firewall.sh
cat > /etc/cron.d/pacs-firewall <<CRON
*/5 * * * * root ${DIR}/firewall/sincronizar_firewall.sh >> /var/log/pacs-firewall.log 2>&1
@reboot root sleep 90 && ${DIR}/firewall/sincronizar_firewall.sh >> /var/log/pacs-firewall.log 2>&1
CRON

# 7) Respaldo diario de la base de datos de informes y del índice del PACS (se guardan 14 días)
mkdir -p /opt/respaldos
cat > /etc/cron.d/pacs-respaldo <<CRON
30 3 * * * root cd ${DIR} && docker compose exec -T postgres pg_dump -U orthanc orthanc | gzip > /opt/respaldos/indice_\$(date +\%F).sql.gz && docker compose exec -T informes python -c "import sqlite3; sqlite3.connect('/data/informes.db').backup(sqlite3.connect('/data/respaldo.db'))" && mv data/informes/respaldo.db /opt/respaldos/informes_\$(date +\%F).db && find /opt/respaldos -mtime +14 -delete
CRON

echo "=== Instalación terminada: https://${DOMINIO} (usuario admin) ==="
