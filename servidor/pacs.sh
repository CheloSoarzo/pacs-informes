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
#   pacs agenda <nombre> <dominio>    agrega (o cambia) la agenda del centro, unida a su PACS
#   pacs agenda-importar <nombre> <archivo.db>   carga el respaldo de la agenda que estaba en Render
#   pacs portal <dominio>      página de ingreso común (ej: examenes.inq.cl) con el acceso de pacientes y del personal
#   pacs panel-clave [usuario] clave del panel de servidores (<portal>/panel): espacio, estudios e informes de cada PACS
#   pacs cuota <nombre> <GB>   espacio contratado de un PACS (se muestra en el panel; 0 = sin límite)
#   pacs estado                actualiza los números del panel (lo hace cron cada 5 min)
#   pacs base                  (interno) crea la red compartida y la entrada web HTTPS
set -euo pipefail

CODIGO="${PACS_CODIGO:-/opt/pacs-informes}"
AGENDA_CODIGO="${AGENDA_CODIGO:-/opt/agenda-imagenes}"
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
dc() {  # docker compose de un PACS (con su agenda, si tiene)
  local n="$1"; shift
  local perfiles=""
  [ -n "$(leer "$INST/$n/.env" AGENDA_DOMINIO)" ] && perfiles="agenda"
  INSTANCIA_DIR="$INST/$n" AGENDA_CODIGO="$AGENDA_CODIGO" COMPOSE_PROFILES="$perfiles" \
    docker compose -p "pacs-$n" -f "$COMPOSE_INST" --env-file "$INST/$n/.env" "$@"
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
      local ag; ag="$(leer "$INST/$n/.env" AGENDA_DOMINIO)"
      if [ -n "$ag" ]; then
        cat <<EOF

# Agenda: $n
$ag {
	encode gzip
	request_body {
		max_size 20MB
	}
	reverse_proxy $n-agenda:3000
}
EOF
      fi
    done
    local portal; portal="$(leer "$INST/servidor.env" PORTAL_DOMINIO || true)"
    if [ -n "$portal" ]; then
      generar_portal
      echo
      echo "# Portal común de ingreso"
      echo "$portal {"
      echo "	encode gzip"
      for n in $(instancias); do
        [ -n "$(leer "$INST/$n/.env" AGENDA_DOMINIO)" ] || continue
        echo "	redir /$n https://$(leer "$INST/$n/.env" DOMINIO)/paciente 302"
        echo "	redir /$n/ https://$(leer "$INST/$n/.env" DOMINIO)/paciente 302"
      done
      echo "	redir /panel /panel/ 308"
      local ph pu; ph="$(leer "$INST/servidor.env" PANEL_HASH || true)"; pu="$(leer "$INST/servidor.env" PANEL_USUARIO || true)"
      if [ -n "$ph" ]; then
        echo "	basic_auth /panel/* {"
        echo "		${pu:-admin} $ph"
        echo "	}"
      else
        echo "	respond /panel/* \"Panel sin clave. En el servidor ejecuta: pacs panel-clave\" 403"
      fi
      echo "	root * /etc/caddy/portal"
      echo "	file_server"
      echo "}"
    fi
  } > "$f"
  mv "$f" "$INST/caddy/Caddyfile"
}

# Logos que cada PACS subió en Administración → Correo y ajustes → Logo del centro
copiar_logos() {
  local n d="$INST/caddy/portal/logos"
  mkdir -p "$d"
  for n in $(instancias); do
    local src="$INST/$n/data/informes/logo.png"
    if [ -f "$src" ]; then cmp -s "$src" "$d/$n.png" || cp "$src" "$d/$n.png"; else rm -f "$d/$n.png"; fi
  done
}
logo_de() {  # dirección del logo de un PACS en el portal (vacía si no tiene)
  local f="$INST/caddy/portal/logos/$1.png"
  [ -f "$f" ] && echo "/logos/$1.png?v=$(stat -c %Y "$f")" || true
}

# Página del portal común: un botón por centro para pacientes y otro para el personal
generar_portal() {
  mkdir -p "$INST/caddy/portal/panel"
  cp "$CODIGO/servidor/panel.html" "$INST/caddy/portal/panel/index.html" 2>/dev/null || true
  copiar_logos
  local f="$INST/caddy/portal/index.html" n botones_p="" botones_s=""
  for n in $(instancias); do
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    local c d a l img=""; c="$(leer "$INST/$n/.env" CENTRO_NOMBRE | sed 's/[<>&"]//g')"; d="$(leer "$INST/$n/.env" DOMINIO)"; a="$(leer "$INST/$n/.env" AGENDA_DOMINIO)"
    l="$(logo_de "$n")"; [ -n "$l" ] && img="<img src=\"$l\" alt=\"\">"
    [ -n "$a" ] && botones_p="$botones_p<a class=\"b p centro\" href=\"https://$d/paciente\">$img<span>$c</span></a>"
    botones_s="$botones_s<div class=\"fila\"><b>$img$c</b><a class=\"b\" href=\"https://$d/\">PACS e informes</a>"
    [ -n "$a" ] && botones_s="$botones_s<a class=\"b\" href=\"https://$a/\">Agenda</a>"
    botones_s="$botones_s</div>"
  done
  cat > "$f" <<EOF
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Exámenes en línea</title><style>
:root{--bg:#f4f6f8;--p:#fff;--t:#18212b;--s:#5b6573;--b:#dce2e8;--a:#1f5fa8}
@media (prefers-color-scheme:dark){:root{--bg:#0f151b;--p:#16202a;--t:#e6edf3;--s:#93a3b2;--b:#2a3846;--a:#6fb1e0}}
body{margin:0;background:var(--bg);color:var(--t);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:520px;margin:0 auto;padding:40px 16px}h1{font-size:24px;margin:0 0 6px;text-align:center}
p{color:var(--s);text-align:center;margin:0 0 24px}section{background:var(--p);border:1px solid var(--b);border-radius:14px;padding:20px;margin-bottom:16px}
h2{font-size:16px;margin:0 0 12px}.b{display:block;text-align:center;padding:14px;border-radius:10px;border:1px solid var(--b);color:var(--t);text-decoration:none;font-weight:600;margin-top:8px}
.b.p{background:var(--a);border-color:var(--a);color:#fff}.fila{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
.fila b{flex:1 1 100%;display:flex;align-items:center;gap:10px}.fila .b{flex:1;margin:0}small{display:block;color:var(--s);margin-top:12px;font-size:13px}
img{background:#fff;border-radius:8px;object-fit:contain}.fila b img{height:34px;max-width:90px;padding:3px 5px;border:1px solid var(--b)}
.b.centro{display:flex;align-items:center;justify-content:center;gap:14px;background:var(--p);color:var(--t);border:2px solid var(--a);padding:12px}
.b.centro img{height:56px;max-width:150px;padding:4px 6px}.b.centro span{font-size:17px}
</style></head><body><main><h1>Exámenes en línea</h1><p>Elija dónde se realizó su examen</p>
<section><h2>Pacientes</h2>$botones_p<small>Ingrese con su RUT y la clave de la hoja que recibió en recepción.</small></section>
<section><h2>Personal</h2>$botones_s<a class="b" href="/panel/">Panel de servidores</a></section></main></body></html>
EOF
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

  mkdir -p "$INST/$n/data/worklists"
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

# Agenda unida a este PACS (la activa: pacs agenda $n <dominio>)
AGENDA_DOMINIO=
AGENDA_ADMIN_CLAVE=
INTEGRACION_TOKEN=$(openssl rand -hex 24)
PORTAL_URL=https://$dominio/paciente
ACCESION_PREFIJO=$(echo "$n" | tr 'a-z' 'A-Z' | tr -cd 'A-Z' | cut -c1-3)
# Worklist: modalidad DICOM de cada equipo y qué pacientes aparecen (llegados = pagados o marcados "llegó")
MWL_MODALIDAD_TC=CT
MWL_MODALIDAD_RX=CR
MWL_MODALIDAD_ECO=US
MWL_INCLUIR=llegados

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

traer_agenda() {  # baja (o actualiza) el código de la agenda
  local repo; repo="$(leer "$INST/servidor.env" AGENDA_REPO || true)"
  repo="${repo:-https://github.com/CheloSoarzo/agenda-imagenes.git}"
  if [ -d "$AGENDA_CODIGO/.git" ]; then
    git -C "$AGENDA_CODIGO" remote set-url origin "$repo"
    git -C "$AGENDA_CODIGO" -c core.fileMode=false pull --ff-only
  else
    git clone "$repo" "$AGENDA_CODIGO" || error "no pude bajar la agenda de GitHub. Si el repositorio es privado, pon en $INST/servidor.env la línea AGENDA_REPO=https://<token>@github.com/CheloSoarzo/agenda-imagenes.git"
  fi
}

cmd_agenda() {
  local n="${1:-}" d="${2:-}"; existe "$n"; [ -n "$d" ] || error "uso: pacs agenda <nombre> <dominio>   (ej: pacs agenda rancagua agendarancagua.inq.cl)"
  local e="$INST/$n/.env" clave=""
  traer_agenda
  mkdir -p "$INST/$n/data/agenda" "$INST/$n/data/worklists"
  [ -n "$(leer "$e" INTEGRACION_TOKEN)" ] || poner "$e" INTEGRACION_TOKEN "$(openssl rand -hex 24)"
  [ -n "$(leer "$e" PORTAL_URL)" ] || poner "$e" PORTAL_URL "https://$(leer "$e" DOMINIO)/paciente"
  [ -n "$(leer "$e" ACCESION_PREFIJO)" ] || poner "$e" ACCESION_PREFIJO "$(echo "$n" | tr 'a-z' 'A-Z' | tr -cd 'A-Z' | cut -c1-3)"
  if [ -z "$(leer "$e" AGENDA_ADMIN_CLAVE)" ] && [ ! -f "$INST/$n/data/agenda/agenda.db" ]; then
    clave="$(openssl rand -base64 12 | tr -d '/+=')"; poner "$e" AGENDA_ADMIN_CLAVE "$clave"
  fi
  poner "$e" AGENDA_DOMINIO "$d"
  dc "$n" up -d --build
  recargar_caddy
  echo
  echo "Agenda de $n en https://$d (el certificado HTTPS tarda 1-2 minutos)"
  [ -n "$clave" ] && echo "  Usuario: admin   Clave inicial: $clave   (cámbiala al entrar)"
  [ -f "$INST/$n/data/agenda/agenda.db" ] && [ -z "$clave" ] && echo "  Usa los usuarios de siempre de la agenda."
  echo "  Pacientes: https://$(leer "$e" DOMINIO)/paciente"
}

cmd_agenda_importar() {
  local n="${1:-}" f="${2:-}"; existe "$n"
  [ -f "$f" ] || error "uso: pacs agenda-importar <nombre> <archivo.db>   (el respaldo que bajaste de la agenda)"
  [ "$(head -c 15 "$f")" = "SQLite format 3" ] || error "$f no es un respaldo de la agenda"
  [ -n "$(leer "$INST/$n/.env" AGENDA_DOMINIO)" ] || error "primero activa la agenda: pacs agenda $n <dominio>"
  local d="$INST/$n/data/agenda"
  dc "$n" stop agenda
  mkdir -p "$RESPALDOS/$n"
  [ -f "$d/agenda.db" ] && cp "$d/agenda.db" "$RESPALDOS/$n/agenda_antes_de_importar_$(date +%F_%H%M).db"
  rm -f "$d/agenda.db" "$d/agenda.db-wal" "$d/agenda.db-shm"
  cp "$f" "$d/agenda.db"
  # Las órdenes de prueba que haya recibido el PACS se borran: la agenda importada vuelve a enviar las suyas
  docker exec "pacs-$n-informes-1" python -c \
    "import sqlite3; c=sqlite3.connect('/data/informes.db'); c.execute('DELETE FROM ordenes'); c.commit()"
  rm -f "$INST/$n/data/worklists/"*.wl
  dc "$n" up -d agenda
  echo "Agenda de $n cargada desde $f. Entra con los mismos usuarios que usabas en Render."
}

cmd_portal() {
  local d="${1:-}"; [ -n "$d" ] || error "uso: pacs portal <dominio>   (ej: pacs portal examenes.inq.cl)"
  [ -f "$INST/servidor.env" ] || cmd_base
  poner "$INST/servidor.env" PORTAL_DOMINIO "$d"
  local n
  for n in $(instancias); do   # la hoja de retiro imprime la dirección corta: examenes.inq.cl/rancagua
    [ -n "$(leer "$INST/$n/.env" AGENDA_DOMINIO)" ] && poner "$INST/$n/.env" PORTAL_URL "https://$d/$n" && dc "$n" up -d agenda
  done
  recargar_caddy
  echo "Portal en https://$d (el certificado HTTPS tarda 1-2 minutos)"
}

# Números de cada PACS para el panel (cron cada 5 minutos)
js() { printf '%s' "$1" | tr -d '"\\' | tr -d '\000-\037'; }
cmd_estado() {
  local dir="$INST/caddy/portal/panel" n primero=1 total usado
  mkdir -p "$dir"
  copiar_logos
  read -r total usado <<<"$(df -B1 --output=size,used "$INST" | tail -n1)"
  {
    printf '{"generado":"%s","servidor":{"ip":"%s","disco_total":%s,"disco_usado":%s},"pacs":[' \
      "$(date '+%F %T')" "$(js "$(ip_publica)")" "${total:-0}" "${usado:-0}"
    for n in $(instancias); do
      local e="$INST/$n/.env" u datos creado cuota activo=true
      u="$(du -sb "$INST/$n/data" 2>/dev/null | cut -f1)"
      datos="$(timeout 60 docker exec "pacs-$n-informes-1" python estado_cli.py 2>/dev/null | tail -n1 || true)"
      case "$datos" in "{"*"}") ;; *) datos='{"error_pacs":"sin respuesta"}';; esac
      creado="$(sed -n "s/.*creado por 'pacs nueva' el \(.*\)$/\1/p" "$e" | head -n1)"
      cuota="$(leer "$e" CUOTA_GB)"; [[ "$cuota" =~ ^[0-9]+$ ]] && [ "$cuota" -gt 0 ] || cuota=null
      [ "$(leer "$e" ACTIVO)" = "false" ] && activo=false
      [ $primero = 1 ] || printf ','; primero=0
      printf '{"nombre":"%s","centro":"%s","logo":"%s","dominio":"%s","agenda":"%s","activo":%s,"creado":"%s","usado":%s,"cuota_gb":%s,"aet":"%s","puerto":"%s","datos":%s}' \
        "$(js "$n")" "$(js "$(leer "$e" CENTRO_NOMBRE)")" "$(js "$(logo_de "$n")")" "$(js "$(leer "$e" DOMINIO)")" "$(js "$(leer "$e" AGENDA_DOMINIO)")" \
        "$activo" "$(js "$creado")" "${u:-0}" "$cuota" "$(js "$(leer "$e" PACS_AET)")" "$(js "$(leer "$e" DICOM_PUERTO_PUBLICO)")" "$datos"
    done
    printf ']}\n'
  } > "$dir/.estado.json"
  mv "$dir/.estado.json" "$dir/estado.json"
}

cmd_panel_clave() {
  local u="${1:-admin}" c1 c2 h
  [[ "$u" =~ ^[a-zA-Z0-9._-]{3,30}$ ]] || error "usuario inválido"
  [ -n "$(leer "$INST/servidor.env" PORTAL_DOMINIO || true)" ] || error "primero crea el portal: pacs portal examenes.inq.cl"
  read -rsp "Clave para el panel (usuario $u): " c1; echo
  read -rsp "Repite la clave: " c2; echo
  [ "$c1" = "$c2" ] || error "las claves no coinciden"
  [ ${#c1} -ge 8 ] || error "la clave debe tener al menos 8 caracteres"
  h="$(proxy exec -T caddy caddy hash-password --plaintext "$c1" | tr -d '\r' | tail -n1)"
  [[ "$h" == \$2* ]] || error "no se pudo cifrar la clave"
  poner "$INST/servidor.env" PANEL_USUARIO "$u"
  poner "$INST/servidor.env" PANEL_HASH "$h"
  recargar_caddy
  cmd_estado
  echo "Panel listo en https://$(leer "$INST/servidor.env" PORTAL_DOMINIO)/panel/  (usuario $u)"
}

cmd_cuota() {
  local n="${1:-}" g="${2:-}"; existe "$n"
  [[ "$g" =~ ^[0-9]+$ ]] || error "uso: pacs cuota <nombre> <GB>   (0 = sin límite, muestra el disco del servidor)"
  poner "$INST/$n/.env" CUOTA_GB "$g"
  cmd_estado
  echo "PACS $n: espacio contratado ${g} GB"
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
  # Agenda: se actualiza si algún PACS la tiene
  local con_agenda=""
  for n in $(instancias); do [ -n "$(leer "$INST/$n/.env" AGENDA_DOMINIO)" ] && con_agenda="$n"; done
  if [ -n "$con_agenda" ]; then traer_agenda || echo "Aviso: no se pudo actualizar la agenda"; dc "$con_agenda" build agenda || true; fi
  for n in $(instancias); do
    # PACS creados antes de existir esta opción: son los propios
    grep -qE "^ADMINISTRA_SERVIDOR=" "$INST/$n/.env" || poner "$INST/$n/.env" ADMINISTRA_SERVIDOR true
    # Variables de la integración con la agenda (PACS creados antes de esta versión)
    [ -n "$(leer "$INST/$n/.env" INTEGRACION_TOKEN)" ] || poner "$INST/$n/.env" INTEGRACION_TOKEN "$(openssl rand -hex 24)"
    grep -qE "^PORTAL_URL=" "$INST/$n/.env" || poner "$INST/$n/.env" PORTAL_URL "https://$(leer "$INST/$n/.env" DOMINIO)/paciente"
    mkdir -p "$INST/$n/data/worklists"
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
*/5 * * * * root pacs estado >/dev/null 2>&1
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
    if [ -n "$(leer "$INST/$n/.env" AGENDA_DOMINIO)" ]; then
      docker exec "pacs-$n-agenda-1" node --no-warnings -e \
        "new (require('node:sqlite').DatabaseSync)('/data/agenda.db').exec(\"VACUUM INTO '/data/respaldo.db'\")" \
        && mv "$INST/$n/data/agenda/respaldo.db" "$RESPALDOS/$n/agenda_$hoy.db" || echo "[$n] fallo respaldo agenda"
    fi
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
  agenda) cmd_agenda "$@";;
  agenda-importar) cmd_agenda_importar "$@";;
  portal) cmd_portal "$@"; cmd_estado;;
  panel-clave) cmd_panel_clave "$@";;
  cuota) cmd_cuota "$@";;
  estado) cmd_estado;;
  *) sed -n '2,21p' "$0"; exit 1;;
esac
