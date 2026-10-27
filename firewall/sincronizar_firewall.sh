#!/bin/bash
# Abre el puerto DICOM público SOLO a las IPs de los equipos registrados en la app
# (Administración → Equipos DICOM). Pensado para ejecutarse cada 5 minutos con cron.
#
# Importante: Docker publica puertos saltándose ufw, por eso las reglas van en la cadena DOCKER-USER
# de iptables, que Docker respeta.
#
# Uso:   sudo ./sincronizar_firewall.sh            (lee .env de la carpeta del proyecto)
# Cron:  */5 * * * * root /opt/pacs-informes/firewall/sincronizar_firewall.sh >> /var/log/pacs-firewall.log 2>&1
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Lee solo las variables necesarias del .env (sin ejecutarlo: hay valores con espacios y < >)
leer() { grep -E "^$1=" "$DIR/.env" | tail -n1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//'; }
PUBLIC_BASE_URL="$(leer PUBLIC_BASE_URL)"
FIREWALL_TOKEN="$(leer FIREWALL_TOKEN)"
DICOM_PUERTO_PUBLICO="$(leer DICOM_PUERTO_PUBLICO)"
PUERTO="${DICOM_PUERTO_PUBLICO:-46429}"
URL="${PUBLIC_BASE_URL%/}/api/equipos/ips"
CADENA="PACS-DICOM"

if [ -z "${FIREWALL_TOKEN:-}" ]; then
  echo "$(date '+%F %T') Falta FIREWALL_TOKEN en .env"; exit 1
fi

# 1) Obtener IPs autorizadas. Si la app no responde, se mantienen las reglas actuales (no se corta el servicio).
if ! IPS="$(curl -fsS --max-time 20 -H "X-Token: ${FIREWALL_TOKEN}" "$URL")"; then
  echo "$(date '+%F %T') No se pudo consultar la app; se mantienen las reglas actuales"; exit 0
fi
# Solo IPv4 válidas
IPS="$(echo "$IPS" | grep -E '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || true)"

# 2) Reconstruir la cadena con las IPs permitidas y un bloqueo final
iptables -N "$CADENA" 2>/dev/null || true
iptables -F "$CADENA"
for ip in $IPS; do
  iptables -A "$CADENA" -s "$ip" -j RETURN
done
iptables -A "$CADENA" -j DROP

# 3) Enviar a la cadena todo lo que llega al puerto DICOM público (una sola vez)
REGLA=(-p tcp -m conntrack --ctorigdstport "$PUERTO" --ctdir ORIGINAL -j "$CADENA")
if ! iptables -C DOCKER-USER "${REGLA[@]}" 2>/dev/null; then
  iptables -I DOCKER-USER 1 "${REGLA[@]}"
fi

if [ -n "$IPS" ]; then
  echo "$(date '+%F %T') Puerto $PUERTO abierto solo para: $(echo $IPS)"
else
  echo "$(date '+%F %T') Puerto $PUERTO cerrado: no hay equipos habilitados"
fi
