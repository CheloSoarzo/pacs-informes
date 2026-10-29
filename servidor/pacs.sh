#!/bin/bash
# Administración de varios PACS en un mismo servidor (cada uno con su dominio, datos y puerto DICOM).
# Se instala como /usr/local/bin/pacs. Ejecutar como root.
#
#   pacs nueva <nombre> [--centro "Nombre visible"] [--dominio pacs.cliente.cl] [--puerto 46430] [--clave X]
#              [--administra]   (PACS propio: su administrador puede actualizar el servidor desde la web.
#                                No lo uses en PACS arrendados a terceros)
#   pacs lista                 PACS del servidor con su dirección, puerto DICOM, estado y espacio usado
#   pacs iniciar <nombre>      / pacs detener <nombre>
#   pacs logs <nombre>         registros de la app de ese PACS
#   pacs dominio <nombre> <dominio>   cambia la dirección web (ej: cuando compres un dominio propio)
#   pacs actualizar            baja la última versión de GitHub y reinicia todos los PACS
#                              (también desde la web: Administración → Ajustes → Sistema, en los PACS propios)
#   pacs firewall              aplica la lista blanca DICOM de cada PACS (lo hace cron cada 5 min)
#   pacs respaldo              respaldo de las bases de datos de todos los PACS (lo hace cron cada noche)
#   pacs base                  (interno) crea la red compartida y la entrada web HTTPS
set -euo pipefail

CODIGO="${PACS_CODIGO:-/opt/pacs-informes}"
INST="${PACS_INSTANCIAS:-/opt/pacs-instancias}"
RESPALDOS="${PACS_RESPALDOS:-/opt/respaldos}"
CRON_FILE="${PACS_CRON:-/etc/cron.d/pacs}"
COMPOSE_INST="$CODIGO/docker-compose.instancia.yml"
COMPOSE_PROXY="$CODIGO/docker-compose.proxy.yml"
PUERTO_INICIAL=46429

error() { echo "Error: $*" >&2; exit 1; }
leer() { { grep -E "^$2=" "$1" 2>/dev/null || true; } | tail -n1 | cut -d= -f2-; }
[ "$(id -u)" = 0 ] || error "ejecuta como root (sudo pacs ...)"

instancias() { for d in "$INST"/*/; do [ -f "$d.env" ] && basename "$d"; done 2>/dev/null || true; }
existe() { [ -f "$INST/$1/.env" ] || error "no existe el PACS '$1' (mira: pacs lista)"; }
dc() {  # docker compose de un PACS
  local n="$1"; shift
  INSTANCIA_DIR="$INST/$n" docker compose -p "pacs-$n" -f "$COMPOSE_INST" --env-file "$INST/$n/.env" "$@"
}
proxy() { INSTANCIAS_DIR="$INST" docker compose -p pacs-proxy -f "$COMPOSE_PROXY" "$@"; }

ip_publica() {
  leer "$INST/servidor.env" IP_PUBLICA || true
}
dominio_base() {
  local b; b="$(leer "$INST/servidor.env" DOMINIO_BASE || true)"
  [ -n "$b" ] && echo "$b" || echo "$(ip_publica | tr . -).sslip.io"
}

# ---------- Caddyfile: un bloque por PACS ----------
generar_caddy() {
  local n
  mkdir -p "$INST/caddy"
  local f="$INST/caddy/Caddyfile.nuevo"
  {
    echo "# Generado por 'pacs' el $(date '+%F %T'). No editar a mano: se reescribe al crear o cambiar un PACS."
    for n in $(instancias); do
      local dom; dom="$(leer "$INST/$n/.env" DOMINIO)"
      [ -n "$dom" ] || continue
      if [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ]; then
        cat <<EOF

$dom {
	respond "Este PACS está suspendido. Contacte al administrador." 503
}
EOF
        continue
      fi
      cat <<EOF

# PACS: $n
$dom {
	encode gzip
	@pacs path /ohif/* /dicom-web/* /stone-webviewer/* /ui/* /app/* /system /plugins /plugins/* /tools/* /instances /instances/* /studies /studies/* /series /series/* /patients /patients/* /jobs/* /changes /statistics /volview/*
	handle @pacs {
		request_body {
			max_size 4GB
		}
		forward_auth $n-informes:8000 {
			uri /api/auth/pacs
			copy_headers X-Usuario
		}
		reverse_proxy $n-orthanc:8042
	}
	handle {
		reverse_proxy $n-informes:8000
	}
}
EOF
    done
  } > "$f"
  mv "$f" "$INST/caddy/Caddyfile"
}

recargar_caddy() {
  generar_caddy
  if proxy ps --status running -q caddy 2>/dev/null | grep -q .; then
    proxy exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  else
    proxy up -d
  fi
}

cmd_base() {
  mkdir -p "$INST" "$RESPALDOS"
  if [ ! -f "$INST/servidor.env" ]; then
    local ip; ip="$(curl -fsS --max-time 15 https://api.ipify.org || curl -fsS --max-time 15 https://ifconfig.me)"
    printf "IP_PUBLICA=%s\nDOMINIO_BASE=%s\n" "$ip" "${DOMINIO_BASE:-$(echo "$ip" | tr . -).sslip.io}" > "$INST/servidor.env"
  fi
  docker network inspect pacs-proxy >/dev/null 2>&1 || docker network create pacs-proxy >/dev/null
  generar_caddy
  proxy up -d
  echo "Entrada web lista. Dominio base: $(dominio_base)"
}

siguiente_puerto() {
  local n p=$PUERTO_INICIAL usados
  usados="$(for n in $(instancias); do leer "$INST/$n/.env" DICOM_PUERTO_PUBLICO; done)"
  while echo "$usados" | grep -qx "$p" || ss -ltn "sport = :$p" 2>/dev/null | grep -q LISTEN; do p=$((p+1)); done
  echo "$p"
}

cmd_nueva() {
  local n="${1:-}"; shift || true
  [[ "$n" =~ ^[a-z][a-z0-9-]{1,19}$ ]] || error "nombre inválido: usa minúsculas, números o guiones (2 a 20 letras), ej: rancagua"
  [ -e "$INST/$n" ] && error "ya existe el PACS '$n'"
  local centro="$n" dominio="" puerto="" clave="" administra=false
  while [ $# -gt 0 ]; do
    case "$1" in
      --centro) centro="$2"; shift 2;;
      --dominio) dominio="$2"; shift 2;;
      --puerto) puerto="$2"; shift 2;;
      --clave) clave="$2"; shift 2;;
      --administra) administra=true; shift;;
      *) error "opción desconocida: $1";;
    esac
  done
  [ -f "$INST/servidor.env" ] || cmd_base
  dominio="${dominio:-$n.$(dominio_base)}"
  puerto="${puerto:-$(siguiente_puerto)}"
  clave="${clave:-$(openssl rand -base64 12 | tr -d '/+=')}"
  local aet; aet="$(echo "$n" | tr 'a-z-' 'A-Z_' | cut -c1-16)"

  mkdir -p "$INST/$n/data"
  cat > "$INST/$n/.env" <<ENV
# PACS "$n" — creado por 'pacs nueva' el $(date '+%F %T')
INSTANCIA=$n
ACTIVO=true
ADMINISTRA_SERVIDOR=$administra
DOMINIO=$dominio
PUBLIC_BASE_URL=https://$dominio
PACS_PUBLIC_URL=https://$dominio
COOKIE_DOMAIN=

PG_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_USER=admin
ADMIN_PASSWORD=$clave

PACS_NOMBRE=$aet
PACS_AET=$aet
DICOM_PUERTO_PUBLICO=$puerto
SERVIDOR_IP_PUBLICA=$(ip_publica)
FIREWALL_TOKEN=$(openssl rand -hex 24)
WEB_WORKERS=2

CENTRO_NOMBRE=$centro
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
  chmod 600 "$INST/$n/.env"
  escribir_version "$n"
  ufw_abrir "$puerto"
  dc "$n" up -d --build
  recargar_caddy
  cat <<FIN

PACS "$n" creado
  Dirección:      https://$dominio   (el certificado HTTPS tarda 1-2 minutos)
  Usuario:        admin
  Clave inicial:  $clave   (cámbiala al entrar, en Mi cuenta)
  DICOM:          IP $(ip_publica)  puerto $puerto  AE Title $aet
FIN
}

ufw_abrir() {
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$1/tcp" >/dev/null || true
  fi
}

cmd_lista() {
  local n
  printf "%-14s %-8s %-7s %-9s %-46s %s\n" PACS ESTADO DICOM ESPACIO DIRECCION CENTRO
  for n in $(instancias); do
    local e="$INST/$n/.env" estado="detenido"
    dc "$n" ps --status running -q informes 2>/dev/null | grep -q . && estado="activo"
    [ "$(leer "$e" ACTIVO)" = "false" ] && estado="suspendido"
    printf "%-14s %-8s %-7s %-9s %-46s %s\n" "$n" "$estado" "$(leer "$e" DICOM_PUERTO_PUBLICO)" \
      "$(du -sh "$INST/$n/data" 2>/dev/null | cut -f1)" "https://$(leer "$e" DOMINIO)" "$(leer "$e" CENTRO_NOMBRE)"
  done
  echo; df -h "$INST" | awk 'NR==2 {print "Disco del servidor: usado " $3 " de " $2 " (libre " $4 ")"}'
}

poner() {  # poner <archivo> <VAR> <valor>
  if grep -qE "^$2=" "$1"; then sed -i "s|^$2=.*|$2=$3|" "$1"; else echo "$2=$3" >> "$1"; fi
}

cmd_dominio() {
  local n="${1:-}" d="${2:-}"; existe "$n"; [ -n "$d" ] || error "uso: pacs dominio <nombre> <dominio>"
  local e="$INST/$n/.env"
  poner "$e" DOMINIO "$d"; poner "$e" PUBLIC_BASE_URL "https://$d"; poner "$e" PACS_PUBLIC_URL "https://$d"
  dc "$n" up -d
  recargar_caddy
  echo "PACS $n ahora en https://$d (el dominio debe apuntar a $(ip_publica))"
}

cmd_iniciar() { existe "${1:-}"; poner "$INST/$1/.env" ACTIVO true; dc "$1" up -d; recargar_caddy; echo "PACS $1 iniciado"; }
cmd_detener() { existe "${1:-}"; poner "$INST/$1/.env" ACTIVO false; dc "$1" stop; recargar_caddy; echo "PACS $1 detenido (sus datos se conservan)"; }
cmd_logs() { existe "${1:-}"; dc "$1" logs --tail 200 -f informes; }

cmd_actualizar() {
  git -C "$CODIGO" -c core.fileMode=false pull --ff-only
  # Lo que sigue lo hace la versión recién descargada de este mismo script
  exec bash "$CODIGO/servidor/pacs.sh" _post-actualizar
}

cmd_post_actualizar() {
  local n primera; primera="$(instancias | head -n1)"
  [ -n "$primera" ] && dc "$primera" build informes   # la imagen de la app es la misma para todos los PACS
  for n in $(instancias); do
    # PACS creados antes de existir esta opción: son los propios
    grep -qE "^ADMINISTRA_SERVIDOR=" "$INST/$n/.env" || poner "$INST/$n/.env" ADMINISTRA_SERVIDOR true
    escribir_version "$n"
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    dc "$n" pull -q orthanc postgres || true
    dc "$n" up -d
  done
  proxy pull -q || true
  recargar_caddy
  proxy up -d
  instalar_cron
  echo "Todos los PACS actualizados a la versión $(version)"
}

version() { git -C "$CODIGO" log -1 --format='%h del %cd' --date=format:'%d-%m-%Y %H:%M' 2>/dev/null || echo "?"; }
escribir_version() {
  mkdir -p "$INST/$1/data/informes"
  version > "$INST/$1/data/informes/version.txt"
}

instalar_cron() {
  cat > "$CRON_FILE" <<CRON
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root pacs firewall >> /var/log/pacs-firewall.log 2>&1
@reboot root sleep 90 && pacs firewall >> /var/log/pacs-firewall.log 2>&1
30 3 * * * root pacs respaldo >> /var/log/pacs-respaldo.log 2>&1
* * * * * root pacs revisar >> /var/log/pacs-actualizacion.log 2>&1
CRON
  if [ "$CRON_FILE" = /etc/cron.d/pacs ]; then rm -f /etc/cron.d/pacs-firewall /etc/cron.d/pacs-respaldo; fi
}

# Cada minuto (cron): si el administrador de un PACS propio pidió actualizar desde la web, se actualiza.
cmd_revisar() {
  local n pedido=""
  for n in $(instancias); do
    local f="$INST/$n/data/informes/actualizar.solicitud"
    [ -f "$f" ] || continue
    if [ "$(leer "$INST/$n/.env" ADMINISTRA_SERVIDOR)" = "true" ]; then pedido="$n"; fi
    rm -f "$f"     # las de PACS sin permiso se descartan
  done
  [ -n "$pedido" ] || return 0
  exec 9>/var/lock/pacs-actualizar.lock
  flock -n 9 || return 0
  echo "$(date '+%F %T') actualización pedida desde el PACS $pedido"
  local res
  if res="$(bash "$CODIGO/servidor/pacs.sh" actualizar 2>&1)"; then
    res="$(date '+%d-%m-%Y %H:%M') OK, versión $(version)"
  else
    echo "$res"
    res="$(date '+%d-%m-%Y %H:%M') FALLÓ: $(echo "$res" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
  fi
  echo "$res"
  for n in $(instancias); do
    if [ "$(leer "$INST/$n/.env" ADMINISTRA_SERVIDOR)" = "true" ]; then
      echo "$res" > "$INST/$n/data/informes/actualizacion.txt"
    fi
  done
  return 0
}

cmd_firewall() {
  local n
  for n in $(instancias); do
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    bash "$CODIGO/firewall/sincronizar_firewall.sh" "$INST/$n/.env" | sed "s/^/[$n] /" || true
  done
}

cmd_respaldo() {
  local n
  mkdir -p "$RESPALDOS"
  local hoy; hoy="$(date +%F)"
  for n in $(instancias); do
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    mkdir -p "$RESPALDOS/$n"
    docker exec "pacs-$n-postgres-1" pg_dump -U orthanc orthanc | gzip > "$RESPALDOS/$n/indice_$hoy.sql.gz" || echo "[$n] fallo respaldo índice"
    docker exec "pacs-$n-informes-1" python -c \
      "import sqlite3; sqlite3.connect('/data/informes.db').backup(sqlite3.connect('/data/respaldo.db'))" \
      && mv "$INST/$n/data/informes/respaldo.db" "$RESPALDOS/$n/informes_$hoy.db" || echo "[$n] fallo respaldo informes"
  done
  find "$RESPALDOS" -type f -mtime +14 -delete
  echo "Respaldos en $RESPALDOS"
}

c="${1:-}"; shift || true
case "$c" in
  base) cmd_base;;
  nueva) cmd_nueva "$@";;
  lista|"") cmd_lista;;
  iniciar) cmd_iniciar "$@";;
  detener) cmd_detener "$@";;
  logs) cmd_logs "$@";;
  dominio) cmd_dominio "$@";;
  actualizar) cmd_actualizar;;
  _post-actualizar) cmd_post_actualizar;;
  revisar) cmd_revisar;;
  instalar-cron) instalar_cron;;
  firewall) cmd_firewall;;
  respaldo) cmd_respaldo;;
  *) sed -n '2,14p' "$0"; exit 1;;
esac
