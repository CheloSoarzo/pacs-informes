#!/bin/bash
# Instalación automática del PACS + Informes en un servidor Ubuntu 22.04/24.04 (x86 o ARM).
# Pensado para ejecutarse una sola vez al crear el servidor (cloud-init), como root.
#
# El servidor puede alojar varios PACS independientes (cada uno con su dirección, usuarios, datos y
# puerto DICOM). Después de instalar se administran con el comando `pacs` (ver servidor/pacs.sh).
#
# Variables opcionales (se pasan desde cloud-init):
#   PACS_INICIALES   PACS a crear, separados por ";" con formato nombre:Nombre del centro
#                    ej: 'rancagua:Centro Médico Rancagua;insi:INSI'  (por defecto: principal)
#   ADMIN_PASSWORD   clave inicial del usuario admin de cada PACS (si no, se genera una por PACS)
#   DOMINIO_BASE     dominio propio para las direcciones (<nombre>.<DOMINIO_BASE>);
#                    si no, se usa <nombre>.<ip>.sslip.io (funciona sin comprar dominio)
#   REPO             repositorio git del proyecto
set -euo pipefail
exec > >(tee -a /var/log/pacs-instalacion.log) 2>&1
echo "=== Instalación PACS + Informes: $(date) ==="

DIR=/opt/pacs-informes
REPO="${REPO:-https://github.com/CheloSoarzo/pacs-informes}"
PUERTO=46429   # primer puerto DICOM (cada PACS usa el siguiente libre)
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y ca-certificates curl git openssl cron

# 1) Firewall del sistema operativo. Las imágenes Ubuntu de Oracle Cloud bloquean todo salvo SSH:
#    se abren 80/443 (web) y un rango de puertos DICOM (46429-46479, hasta 50 PACS).
#    El firewall de cada PACS luego limita su puerto a las IP de sus equipos.
if [ -f /etc/iptables/rules.v4 ] && ! grep -q -- "--dport 443 -j ACCEPT" /etc/iptables/rules.v4; then
  sed -i "/--dport 22 -j ACCEPT/a -A INPUT -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT\n-A INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT\n-A INPUT -p tcp -m state --state NEW -m tcp --dport ${PUERTO}:46479 -j ACCEPT" /etc/iptables/rules.v4
  iptables-restore < /etc/iptables/rules.v4
  echo "Firewall del sistema: abiertos 80, 443 y ${PUERTO}-46479"
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
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp; ufw allow 443/tcp; ufw allow ${PUERTO}:46479/tcp
fi

# 4) Comando de administración y entrada web HTTPS compartida
# (se llama con bash: así no depende de permisos de ejecución, que se pierden al subir archivos por la web de GitHub)
printf '#!/bin/bash\nexec bash %s/servidor/pacs.sh "$@"\n' "$DIR" > /usr/local/bin/pacs
chmod 755 /usr/local/bin/pacs
export DOMINIO_BASE="${DOMINIO_BASE:-}"
pacs base

# 5) PACS iniciales
IFS=';' read -ra LISTA <<< "${PACS_INICIALES:-principal:${CENTRO_NOMBRE:-Centro de Imágenes}}"
for item in "${LISTA[@]}"; do
  nombre="$(echo "${item%%:*}" | tr -d ' ')"; centro="${item#*:}"
  [ -n "$nombre" ] || continue
  if [ -d "/opt/pacs-instancias/$nombre" ]; then echo "PACS $nombre ya existe"; continue; fi
  if [ -n "${ADMIN_PASSWORD:-}" ]; then
    pacs nueva "$nombre" --centro "$centro" --clave "$ADMIN_PASSWORD"
  else
    pacs nueva "$nombre" --centro "$centro"
  fi
done

# 6) Tareas automáticas: firewall DICOM (cada 5 min y al reiniciar) y respaldos diarios (se guardan 14 días)
cat > /etc/cron.d/pacs <<CRON
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root pacs firewall >> /var/log/pacs-firewall.log 2>&1
@reboot root sleep 90 && pacs firewall >> /var/log/pacs-firewall.log 2>&1
30 3 * * * root pacs respaldo >> /var/log/pacs-respaldo.log 2>&1
CRON
rm -f /etc/cron.d/pacs-firewall /etc/cron.d/pacs-respaldo

echo "=== Instalación terminada ==="
pacs lista
