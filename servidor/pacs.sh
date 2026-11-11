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
#   pacs respaldo              respaldo de las bases de datos de todos los PACS y, si está configurado, copia cifrada
#                              de todo (imágenes, bases, configuración) en Backblaze B2 (lo hace cron cada noche)
#   pacs respaldo-configurar   conecta el respaldo externo con Backblaze B2 (pide keyID, applicationKey y bucket)
#   pacs respaldo-clave        muestra las claves de cifrado del respaldo (guárdalas fuera del servidor)
#   pacs correo-configurar     correo saliente de los PACS (ej. Brevo) y a quién llega el resumen diario y los avisos
#   pacs resumen [--ayer]      envía ahora el resumen del día en curso (cron: 08:00 con --ayer, el día anterior completo)
#   pacs respaldo-subir        sube ahora a Backblaze lo que falte (sin volver a copiar las bases)
#   pacs respaldo-estado       último respaldo externo y cuánto ocupa
#   pacs respaldo-probar       baja archivos al azar del respaldo y los compara con el servidor (cron: día 1 de cada mes)
#   pacs agenda <nombre> <dominio>    agrega (o cambia) la agenda del centro, unida a su PACS
#   pacs agenda-importar <nombre> <archivo.db>   carga el respaldo de la agenda que estaba en Render
#   pacs portal <dominio>      página de ingreso común (ej: examenes.inq.cl) con el acceso de pacientes y del personal
#   pacs panel-clave [usuario] clave del panel de servidores (<portal>/panel): espacio, estudios e informes de cada PACS
#   pacs cuota <nombre> <GB>   espacio contratado de un PACS (se muestra en el panel; 0 = sin límite)
#   pacs estado                actualiza los números del panel (lo hace cron cada 5 min)
#   pacs dicom-modo <nombre> aetitle|ip   cómo acepta equipos: solo por AE Title (cualquier IP) o por AE Title + IP
#   pacs dictado-ia activar [small|medium]   transcripción automática de los dictados con IA (Whisper),
#                              en este mismo servidor y para todos sus PACS (el audio no sale del servidor)
#   pacs dictado-ia desactivar | estado
#   pacs comprimir [nombre]    comprime SIN PÉRDIDA (JPEG-LS, verificado pixel a pixel) las imágenes guardadas
#                              (todos los PACS si no se indica uno). Lo nuevo ya llega comprimido; cron repasa cada noche
#   pacs comprimir <nombre> --probar   solo el primer estudio pendiente, para ver cuánto ahorra
#   pacs comprimir --reparar-visor  repara el caché del visor diagnóstico (error «Something went wrong»)
#   pacs comprimir --descomprimir-fuji devuelve a sin comprimir las imágenes de CR Fujifilm (no se comprimen)
#   pacs auto-actualizar si|no|estado   el servidor revisa GitHub cada 5 minutos y, si hay una versión nueva
#                              del PACS o de la agenda, se actualiza solo (viene activado)
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
# Transcripción automática de dictados (un solo contenedor para todos los PACS)
COMPOSE_TRANSC="$CODIGO/docker-compose.transcriptor.yml"
transc() {
  touch "$INST/servidor.env"
  local v; for v in WHISPER_MODELO WHISPER_HILOS WHISPER_BEAM WHISPER_CPUS WHISPER_RAM; do
    local x; x="$(leer "$INST/servidor.env" "$v")"; [ -n "$x" ] && export "$v=$x"
  done
  INSTANCIAS_DIR="$INST" docker compose -p pacs-transcriptor -f "$COMPOSE_TRANSC" "$@"
}
dictado_ia_activo() { [ "$(leer "$INST/servidor.env" DICTADO_IA)" = "true" ]; }
whisper_url() { dictado_ia_activo && echo "http://transcriptor:8000" || true; }
whisper_modelo() { local m; m="$(leer "$INST/servidor.env" WHISPER_MODELO)"; echo "${m:-whisper-1}"; }

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
footer{text-align:center;color:var(--s);font-size:12px;margin-top:22px}
</style></head><body><main><h1>Exámenes en línea</h1><p>Elija dónde se realizó su examen</p>
<section><h2>Pacientes</h2>$botones_p<small>Ingrese con su RUT y la clave de la hoja que recibió en recepción.</small></section>
<section><h2>Personal</h2>$botones_s<a class="b" href="/panel/">Panel de servidores</a></section>
<footer>$(leer "$INST/servidor.env" PORTAL_DOMINIO || true) · Versión $(version_sistema) · © 2026 MSC26</footer></main></body></html>
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

WHISPER_URL=$(whisper_url)
WHISPER_MODEL=$(whisper_modelo)
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
    printf '{"generado":"%s","version":"%s","servidor":{"ip":"%s","disco_total":%s,"disco_usado":%s},"pacs":[' \
      "$(date '+%F %T')" "$(js "$(version_sistema)")" "$(js "$(ip_publica)")" "${total:-0}" "${usado:-0}"
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
  # Aviso inmediato (una vez al día) si el disco pasa del 80 %
  if [ "${total:-0}" -gt 0 ] && [ $(( 100 * usado / total )) -ge 80 ] && [ "$(cat /var/lib/pacs-aviso-disco 2>/dev/null)" != "$(date +%F)" ]; then
    date +%F > /var/lib/pacs-aviso-disco
    avisar "Disco del servidor al $(( 100 * usado / total )) %" "El disco del servidor está al $(( 100 * usado / total )) % de su capacidad.
Conviene agregar un disco lento (HDD) en Vultr y mover las imágenes antiguas antes de que se llene."
  fi
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

cmd_dicom_modo() {
  local n="${1:-}" m="${2:-}"; existe "$n"
  local e="$INST/$n/.env"
  case "$m" in
    aetitle) poner "$e" DICOM_SOLO_AETITLE true; poner "$e" DICOM_VERIFICAR_IP false;;
    ip)      poner "$e" DICOM_SOLO_AETITLE false; poner "$e" DICOM_VERIFICAR_IP true;;
    *) error "uso: pacs dicom-modo <nombre> aetitle|ip";;
  esac
  dc "$n" up -d
  sleep 5
  bash "$CODIGO/firewall/sincronizar_firewall.sh" "$e" | sed "s/^/[$n] /" || true
  if [ "$m" = aetitle ]; then
    echo "PACS $n: acepta equipos por AE Title desde cualquier IP. Usa AE Title difíciles de adivinar."
  else
    echo "PACS $n: acepta equipos solo por AE Title + IP pública registrada."
  fi
}

cmd_dictado_ia() {
  local accion="${1:-estado}" modelo="${2:-small}" n url=""
  case "$accion" in
    activar)
      case "$modelo" in small|medium|large-v3) ;; *) error "modelo no válido: usa small (recomendado) o medium";; esac
      local ram; ram="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
      if [ "$modelo" != small ] && [ "$ram" -lt 7000 ]; then
        echo "Aviso: el modelo $modelo necesita ~3 GB de RAM y este servidor tiene ${ram} MB en total."
        echo "        Con varios PACS funcionando puede quedar justo. Si notas lentitud: pacs dictado-ia activar small"
      fi
      mkdir -p "$INST/transcriptor/modelos"
      touch "$INST/servidor.env"
      poner "$INST/servidor.env" DICTADO_IA true
      poner "$INST/servidor.env" WHISPER_MODELO "$modelo"
      echo "Preparando el transcriptor (la primera vez tarda unos minutos)..."
      transc up -d --build --force-recreate
      url="http://transcriptor:8000";;
    desactivar)
      poner "$INST/servidor.env" DICTADO_IA false
      transc down || true;;
    estado)
      if ! dictado_ia_activo; then echo "Transcripción automática: desactivada (actívala con: pacs dictado-ia activar)"; return; fi
      echo "Transcripción automática: activada · modelo $(whisper_modelo)"
      transc exec -T transcriptor python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/salud',timeout=5).read().decode())" \
        || echo "El transcriptor no responde. Revisa: docker logs pacs-transcriptor-transcriptor-1"
      echo "Últimos registros:"; transc logs --tail 8 transcriptor 2>/dev/null | sed 's/^/  /'
      return;;
    *) error "uso: pacs dictado-ia activar [small|medium] | desactivar | estado";;
  esac
  for n in $(instancias); do
    poner "$INST/$n/.env" WHISPER_URL "$url"
    poner "$INST/$n/.env" WHISPER_MODEL "$(whisper_modelo)"
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    dc "$n" up -d informes
  done
  if [ "$accion" = activar ]; then
    echo "Listo. Los dictados nuevos se transcriben solos con el modelo $modelo."
    echo "La primera vez se descarga el modelo (~500 MB small, ~1,5 GB medium). Revisa cuándo está listo con: pacs dictado-ia estado"
  else
    echo "Transcripción automática desactivada. Los dictados siguen funcionando como siempre."
  fi
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
  # Transcripción automática de dictados: se reconstruye si está activada
  if dictado_ia_activo; then transc up -d --build || echo "Aviso: no se pudo actualizar el transcriptor"; fi
  # Una sola vez (v1.1.25): las imágenes de CR Fujifilm que se comprimieron vuelven a sin comprimir
  # (el visor diagnóstico no las lee bien comprimidas). Corre en segundo plano y queda en el registro.
  if [ ! -f "$INST/.fuji-descomprimido" ]; then
    touch "$INST/.fuji-descomprimido"
    nohup setsid bash -c "sleep 60; bash '$CODIGO/servidor/pacs.sh' comprimir --descomprimir-fuji" \
      >> /var/log/pacs-comprimir.log 2>&1 < /dev/null &
  fi
  # Una sola vez (v1.1.28): arregla las imágenes del CR Fujifilm que el visor diagnóstico no puede abrir
  # y su caché (error «The series metadata json does not contain an array» al abrir el estudio)
  if [ ! -f "$INST/.visor-reparado-3" ]; then
    touch "$INST/.visor-reparado-3"
    nohup setsid bash -c "for i in \$(seq 40); do sleep 30; docker exec pacs-$(instancias | head -1)-informes-1 grep -q dicomweb_ok comprimir.py 2>/dev/null && break; done; bash '$CODIGO/servidor/pacs.sh' comprimir --reparar-visor" \
      >> /var/log/pacs-comprimir.log 2>&1 < /dev/null &
  fi
  # Actualización automática desde GitHub: viene activada (se apaga con: pacs auto-actualizar no)
  touch "$INST/servidor.env"
  grep -qE "^AUTO_ACTUALIZAR=" "$INST/servidor.env" || poner "$INST/servidor.env" AUTO_ACTUALIZAR true
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

version_sistema() { tr -d '[:space:]' < "$CODIGO/informes/VERSION" 2>/dev/null || echo "1.0.0"; }
version() { git -C "$CODIGO" log -1 --format='%h del %cd' --date=format:'%d/%m/%Y %H:%M' 2>/dev/null || echo "?"; }
escribir_version() {
  mkdir -p "$INST/$1/data/informes"
  version > "$INST/$1/data/informes/version.txt"
}

instalar_cron() {
  # El servidor trabaja en hora de Chile: las tareas de la noche corren a la hora que dice aquí
  local zona; zona="$(leer "$INST/servidor.env" ZONA_HORARIA)"; zona="${zona:-America/Santiago}"
  if command -v timedatectl >/dev/null && [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "$zona" ]; then
    timedatectl set-timezone "$zona" 2>/dev/null && { systemctl restart cron 2>/dev/null || true; }
  fi
  cat > "$CRON_FILE" <<CRON
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Hora de Chile (America/Santiago). Madrugada: 02:00 compresión, 03:00 respaldo, 08:00 resumen del día anterior
# (fuera del horario en que trabajan el radiólogo y la transcripcionista)
*/5 * * * * root pacs firewall >> /var/log/pacs-firewall.log 2>&1
@reboot root sleep 90 && pacs firewall >> /var/log/pacs-firewall.log 2>&1
* * * * * root pacs revisar >> /var/log/pacs-actualizacion.log 2>&1
*/5 * * * * root pacs estado >/dev/null 2>&1
0 2 * * * root pacs comprimir --noche >> /var/log/pacs-comprimir.log 2>&1
0 3 * * * root pacs respaldo >> /var/log/pacs-respaldo.log 2>&1
0 8 * * * root pacs resumen --ayer >> /var/log/pacs-resumen.log 2>&1
0 5 1 * * root pacs respaldo-probar >> /var/log/pacs-respaldo.log 2>&1
CRON
  if [ "$CRON_FILE" = /etc/cron.d/pacs ]; then rm -f /etc/cron.d/pacs-firewall /etc/cron.d/pacs-respaldo; fi
}

# Cada minuto (cron): si el administrador de un PACS propio pidió actualizar desde la web, se actualiza.
cmd_revisar() {
  local n pedido=""
  # Cada minuto: arregla para el visor diagnóstico las series que terminaron de llegar (ej. CR Fujifilm)
  for n in $(instancias); do
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    flock -n "/var/lock/pacs-reparar-$n.lock" timeout 300 docker exec "pacs-$n-informes-1" python comprimir.py --reparar-nuevos 2>&1 \
      | grep -v "^$" >> /var/log/pacs-comprimir.log || true
  done
  for n in $(instancias); do
    local f="$INST/$n/data/informes/actualizar.solicitud"
    [ -f "$f" ] || continue
    if [ "$(leer "$INST/$n/.env" ADMINISTRA_SERVIDOR)" = "true" ]; then pedido="pedida desde el PACS $n"; fi
    rm -f "$f"     # las de PACS sin permiso se descartan
  done
  # Actualización automática: cada 5 minutos se revisa si hay algo nuevo en GitHub (PACS o agenda)
  if [ -z "$pedido" ] && [ "$(leer "$INST/servidor.env" AUTO_ACTUALIZAR)" = "true" ] && [ $((10#$(date +%M) % 5)) -eq 0 ]; then
    hay_version_nueva && pedido="automática: hay una versión nueva en GitHub"
  fi
  [ -n "$pedido" ] || return 0
  exec 9>/var/lock/pacs-actualizar.lock
  flock -n 9 || return 0
  echo "$(date '+%F %T') actualización $pedido"
  local res
  if res="$(bash "$CODIGO/servidor/pacs.sh" actualizar 2>&1)"; then
    res="$(date '+%d/%m/%Y %H:%M') OK, versión $(version)"
  else
    echo "$res"
    res="$(date '+%d/%m/%Y %H:%M') FALLÓ: $(echo "$res" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
  fi
  echo "$res"
  for n in $(instancias); do
    if [ "$(leer "$INST/$n/.env" ADMINISTRA_SERVIDOR)" = "true" ]; then
      echo "$res" > "$INST/$n/data/informes/actualizacion.txt"
    fi
  done
  return 0
}

hay_version_nueva() {  # 0 si en GitHub hay commits nuevos del PACS o de la agenda
  local dir
  for dir in "$CODIGO" "$AGENDA_CODIGO"; do
    [ -d "$dir/.git" ] || continue
    timeout 60 git -C "$dir" fetch -q origin 2>/dev/null || continue
    [ "$(git -C "$dir" rev-parse HEAD)" != "$(git -C "$dir" rev-parse '@{u}' 2>/dev/null || git -C "$dir" rev-parse HEAD)" ] && return 0
  done
  return 1
}

cmd_auto_actualizar() {
  touch "$INST/servidor.env"
  case "${1:-estado}" in
    si|activar) poner "$INST/servidor.env" AUTO_ACTUALIZAR true
       echo "Actualización automática ACTIVADA: cada 5 minutos se revisa GitHub y, si hay versión nueva, se instala sola.";;
    no|desactivar) poner "$INST/servidor.env" AUTO_ACTUALIZAR false
       echo "Actualización automática desactivada. Para actualizar: pacs actualizar (o el botón en la web del PACS).";;
    estado)
      if [ "$(leer "$INST/servidor.env" AUTO_ACTUALIZAR)" = "true" ]; then echo "Actualización automática: activada"; else echo "Actualización automática: desactivada"; fi
      echo "Versión instalada: $(version)"
      echo "Últimas actualizaciones:"; tail -n 6 /var/log/pacs-actualizacion.log 2>/dev/null | sed 's/^/  /' || true;;
    *) error "uso: pacs auto-actualizar si | no | estado";;
  esac
}

cmd_firewall() {
  local n
  for n in $(instancias); do
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    bash "$CODIGO/firewall/sincronizar_firewall.sh" "$INST/$n/.env" | sed "s/^/[$n] /" || true
  done
}

cmd_comprimir() {
  local objetivo="" extra="" n hubo=""
  for a in "$@"; do
    case "$a" in
      --probar) extra="--estudios 1";;
      --noche) extra="--desde-horas 2";;    # tarea nocturna: solo exámenes que ya terminaron de llegar
      --descomprimir-fuji) extra="--descomprimir-excluidos";;   # devuelve a sin comprimir los CR Fujifilm
      --reparar-visor) extra="--reparar-visor";;   # borra el caché dañado del visor diagnóstico (error al abrir)
      todos) ;;
      *) objetivo="$a";;
    esac
  done
  exec 9>/var/lock/pacs-comprimir.lock
  flock -n 9 || { echo "Ya hay una compresión en curso."; return 0; }
  for n in $(instancias); do
    [ -n "$objetivo" ] && [ "$n" != "$objetivo" ] && continue
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    hubo=1
    echo "== $n · $(date '+%d/%m/%Y %H:%M')"
    # shellcheck disable=SC2086
    docker exec "pacs-$n-informes-1" python comprimir.py $extra || echo "[$n] la compresión no terminó bien"
  done
  [ -n "$hubo" ] || error "no existe el PACS «$objetivo» (mira: pacs lista)"
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
  find "$RESPALDOS" -type f -name "*_20*" -mtime +14 -delete
  echo "Respaldos en $RESPALDOS"
  respaldo_remoto
}

# ---------- Correo de los sistemas: resumen diario y avisos ----------
# Los PACS envían por SMTP (ej. Brevo): smtp-relay.brevo.com, puerto 587, usuario y clave SMTP de Brevo.
# RESUMEN_PARA (servidor.env): a quién llegan el resumen de las 00:00 y los avisos.

pacs_con_correo() {  # el primer PACS activo con el correo saliente configurado
  local n
  for n in $(instancias); do
    [ "$(leer "$INST/$n/.env" ACTIVO)" = "false" ] && continue
    [ -n "$(leer "$INST/$n/.env" SMTP_HOST)" ] && [ -n "$(leer "$INST/$n/.env" SMTP_FROM)" ] && { echo "$n"; return 0; }
  done
  return 1
}

# avisar "<asunto>" "<texto>"  — aviso inmediato por correo (si está configurado); nunca hace fallar al que lo llama
avisar() {
  local para n
  para="$(leer "$INST/servidor.env" RESUMEN_PARA)"
  [ -n "$para" ] || return 0
  n="$(pacs_con_correo)" || return 0
  printf '%s\n' "$2" | timeout 60 docker exec -i "pacs-$n-informes-1" python resumen_cli.py aviso --para "$para" \
    --asunto "$1 · $(hostname)" >/dev/null 2>&1 || true
}

cmd_correo_configurar() {
  echo "Correo saliente de los PACS (resumen diario, avisos y envío de informes a pacientes)"
  echo "Con Brevo: Configuración → SMTP y API → pestaña SMTP (servidor, puerto, login y una clave SMTP)."
  echo
  local host puerto usuario clave remitente para n
  read -rp "Servidor SMTP [smtp-relay.brevo.com]: " host; host="${host:-smtp-relay.brevo.com}"
  read -rp "Puerto [587]: " puerto; puerto="${puerto:-587}"
  read -rp "Usuario / login SMTP: " usuario
  read -rsp "Clave SMTP (no se ve al escribir): " clave; echo
  read -rp "Dirección remitente (verificada en Brevo, ej. informes@inq.cl): " remitente
  read -rp "¿A qué correo(s) envío el resumen diario y los avisos? (separados por coma): " para
  [ -n "$usuario" ] && [ -n "$clave" ] && [[ "$remitente" == *@*.* ]] || error "faltan datos"
  [[ "$clave" == *"|"* ]] && error "la clave no puede tener el carácter |"
  for n in $(instancias); do
    local e="$INST/$n/.env" centro
    centro="$(leer "$e" CENTRO_NOMBRE | tr -d '"<>')"
    poner "$e" SMTP_HOST "$host"; poner "$e" SMTP_PORT "$puerto"; poner "$e" SMTP_USER "$usuario"
    poner "$e" SMTP_PASSWORD "$clave"; poner "$e" SMTP_FROM "${centro:-Centro} <$remitente>"
    poner "$e" SMTP_SSL "$([ "$puerto" = 465 ] && echo true || echo false)"; poner "$e" SMTP_STARTTLS true
    [ "$(leer "$e" ACTIVO)" = "false" ] || dc "$n" up -d >/dev/null 2>&1
  done
  [ -n "$para" ] && poner "$INST/servidor.env" RESUMEN_PARA "$para"
  echo "Esperando que los PACS se reinicien..."; sleep 15
  n="$(pacs_con_correo)" || error "no quedó ningún PACS con correo"
  if [ -n "$para" ]; then
    docker exec "pacs-$n-informes-1" python resumen_cli.py probar --para "$para" \
      || error "no se pudo enviar: revisa usuario, clave y que el remitente esté verificado en Brevo"
  fi
  echo "Listo. El resumen diario llega a las 00:00 (para probarlo ahora: pacs resumen)."
}

# Resumen del día (cron a las 00:00: resume el día que terminó)
cmd_resumen() {
  local para n fecha primero=1 caidos="" c
  para="$(leer "$INST/servidor.env" RESUMEN_PARA)"
  [ -n "$para" ] || { echo "Falta el destinatario (pacs correo-configurar)"; return 0; }
  n="$(pacs_con_correo)" || { echo "El correo saliente no está configurado (pacs correo-configurar)"; return 0; }
  # --ayer (cron de las 08:00): el día anterior completo. A mano: el día en curso
  if [ "${1:-}" = "--ayer" ]; then fecha="$(date -d yesterday +%F)"; else fecha="$(date +%F)"; fi
  cmd_estado >/dev/null 2>&1 || true
  for c in $(instancias); do
    [ "$(leer "$INST/$c/.env" ACTIVO)" = "false" ] && continue
    while read -r nombre estado; do
      [ "$estado" = running ] || caidos="$caidos \"$nombre ($estado)\","
    done < <(docker ps -a --filter "name=^pacs-$c-" --format '{{.Names}} {{.State}}')
  done
  local respaldo_conf=false srv; [ -f "$RCLONE_CONF" ] && grep -q "^\[pacs-cifrado\]" "$RCLONE_CONF" && respaldo_conf=true
  srv="$(sed -n 's/.*"servidor":\({[^}]*}\).*/\1/p' "$INST/caddy/portal/panel/estado.json" 2>/dev/null || true)"
  [ -n "$srv" ] || srv='{}'
  {
    printf '{"fecha":"%s","version":"%s","servidor":%s,"respaldo":{"configurado":%s,"estado":"%s"},"contenedores_caidos":[%s],"pacs":[' \
      "$fecha" "$(js "$(version_sistema)")" \
      "$srv" \
      "$respaldo_conf" "$(js "$(cat "$ESTADO_REMOTO" 2>/dev/null)")" "${caidos%,}"
    for c in $(instancias); do
      [ "$(leer "$INST/$c/.env" ACTIVO)" = "false" ] && continue
      local d; d="$(timeout 600 docker exec "pacs-$c-informes-1" python resumen_cli.py datos --fecha "$fecha" 2>/dev/null | tail -n1 || true)"
      case "$d" in "{"*"}") ;; *) d="{\"centro\":\"$c\",\"error_pacs\":\"sin respuesta\"}";; esac
      # Caja del día (si el centro tiene agenda)
      if [ -n "$(leer "$INST/$c/.env" AGENDA_DOMINIO)" ]; then
        local cj; cj="$(timeout 120 docker exec "pacs-$c-agenda-1" node --no-warnings scripts/resumen-caja.js --fecha "$fecha" 2>/dev/null | tail -n1 || true)"
        case "$cj" in "{"*"}") ;; *) cj='{"error":"sin respuesta de la agenda"}';; esac
        d="${d%\}},\"caja\":$cj}"
      fi
      [ $primero = 1 ] || printf ','; primero=0
      printf '%s' "$d"
    done
    printf ']}\n'
  } | docker exec -i "pacs-$n-informes-1" python resumen_cli.py enviar --para "$para"
}

# ---------- Respaldo fuera del servidor (Backblaze B2), cifrado ----------
# Todo sale cifrado con rclone (crypt): Backblaze guarda los archivos sin poder leer nombres, RUT ni imágenes.
# La clave de cifrado vive en /root/.config/rclone/rclone.conf y hay que guardarla también fuera del servidor
# (pacs respaldo-clave): sin ella el respaldo no se puede abrir.
RCLONE_CONF=/root/.config/rclone/rclone.conf
REMOTO=pacs-cifrado:
ESTADO_REMOTO="$RESPALDOS/estado-remoto.txt"

respaldo_remoto() {
  if ! [ -f "$RCLONE_CONF" ] || ! grep -q "^\[pacs-cifrado\]" "$RCLONE_CONF"; then
    echo "Respaldo externo: no configurado (pacs respaldo-configurar)"; return 0
  fi
  command -v rclone >/dev/null || { echo "Respaldo externo: falta rclone"; return 1; }
  local n ini fallas=0 opciones=(--config "$RCLONE_CONF" --fast-list --transfers 4 --checkers 8 --retries 5
    --low-level-retries 20 --stats-one-line --stats 0 -v)
  ini=$(date +%s)
  echo "$(date '+%d/%m/%Y %H:%M') Respaldo externo: inicio"
  # Copias de las bases (las de esta noche y las de los últimos 14 días)
  rclone sync "$RESPALDOS" "${REMOTO}bases" --exclude "estado-remoto.txt" "${opciones[@]}" 2>&1 | tail -3 || fallas=$((fallas+1))
  # Configuración de cada PACS y del servidor (contraseñas de la base, dominios, token de la agenda)
  rclone sync "$INST" "${REMOTO}configuracion" --include "*/.env" --include "/servidor.env" "${opciones[@]}" 2>&1 | tail -3 \
    || fallas=$((fallas+1))
  for n in $(instancias); do
    # Imágenes: los archivos que guarda Orthanc (el índice va en las bases)
    [ -d "$INST/$n/data/orthanc" ] && { rclone sync "$INST/$n/data/orthanc" "${REMOTO}$n/imagenes" "${opciones[@]}" 2>&1 \
      | tail -3 || fallas=$((fallas+1)); }
    # Informes: audios de los dictados, logos, firmas (la base va aparte, copiada sin cortes)
    [ -d "$INST/$n/data/informes" ] && { rclone sync "$INST/$n/data/informes" "${REMOTO}$n/informes" \
      --exclude "informes.db*" --exclude "respaldo.db" "${opciones[@]}" 2>&1 | tail -3 || fallas=$((fallas+1)); }
    # Agenda: la llave de las claves del portal del paciente y archivos (la base va aparte)
    if [ -d "$INST/$n/data/agenda" ]; then
      rclone sync "$INST/$n/data/agenda" "${REMOTO}$n/agenda" --exclude "agenda.db*" --exclude "respaldo.db" \
        "${opciones[@]}" 2>&1 | tail -3 || fallas=$((fallas+1))
    fi
  done
  local seg=$(( $(date +%s) - ini ))
  if [ "$fallas" = 0 ]; then
    echo "ok $(date '+%Y-%m-%d %H:%M:%S') ${seg}s" > "$ESTADO_REMOTO"
    echo "$(date '+%d/%m/%Y %H:%M') Respaldo externo: OK en ${seg} s"
  else
    echo "error $(date '+%Y-%m-%d %H:%M:%S') $fallas partes fallaron" > "$ESTADO_REMOTO"
    echo "$(date '+%d/%m/%Y %H:%M') Respaldo externo: $fallas partes fallaron (se reintenta mañana)"
    avisar "Falló el respaldo externo" "El respaldo de esta noche a Backblaze falló en $fallas partes. Se reintenta mañana.
Detalle en el servidor: /var/log/pacs-respaldo.log  (pacs respaldo-estado)"
    return 1
  fi
}

cmd_respaldo_configurar() {
  if ! command -v rclone >/dev/null; then
    echo "Instalando rclone..."; apt-get update -qq && apt-get install -y -qq rclone >/dev/null || error "no se pudo instalar rclone"
  fi
  echo "Respaldo externo en Backblaze B2"
  echo "Necesitas: el nombre del bucket y una Application Key (keyID + applicationKey) con acceso solo a ese bucket."
  echo
  local id clave bucket
  read -rp "keyID: " id
  read -rsp "applicationKey (no se ve al escribir): " clave; echo
  read -rp "Nombre del bucket: " bucket
  [ -n "$id" ] && [ -n "$clave" ] && [ -n "$bucket" ] || error "faltan datos"
  mkdir -p "$(dirname "$RCLONE_CONF")"; chmod 700 "$(dirname "$RCLONE_CONF")"
  local p1 p2
  if grep -q "^\[pacs-cifrado\]" "$RCLONE_CONF" 2>/dev/null; then
    echo "Ya había un respaldo configurado: se mantiene su clave de cifrado (los archivos ya subidos siguen sirviendo)."
    p1=$(awk '/^\[pacs-cifrado\]/{f=1} f&&/^password = /{print $3; exit}' "$RCLONE_CONF")
    p2=$(awk '/^\[pacs-cifrado\]/{f=1} f&&/^password2 = /{print $3; exit}' "$RCLONE_CONF")
  else
    p1=$(rclone obscure "$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-40)")
    p2=$(rclone obscure "$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-40)")
  fi
  umask 077
  cat > "$RCLONE_CONF" <<CONF
[backblaze]
type = b2
account = $id
key = $clave
hard_delete = false

[pacs-cifrado]
type = crypt
remote = backblaze:$bucket/servidor
filename_encryption = standard
directory_name_encryption = true
password = $p1
password2 = $p2
CONF
  chmod 600 "$RCLONE_CONF"
  echo "Probando..."
  echo "prueba $(date)" > /tmp/pacs-prueba-respaldo.txt
  rclone --config "$RCLONE_CONF" copyto /tmp/pacs-prueba-respaldo.txt "${REMOTO}prueba.txt" \
    && rclone --config "$RCLONE_CONF" cat "${REMOTO}prueba.txt" >/dev/null \
    || error "no se pudo escribir en el bucket: revisa keyID, applicationKey y nombre del bucket"
  rm -f /tmp/pacs-prueba-respaldo.txt
  echo "Conexión OK. El respaldo externo corre cada madrugada a las 03:00 (hora de Chile)."
  echo
  cmd_respaldo_clave
  echo
  echo "Para subir todo ahora (la primera vez puede tardar): pacs respaldo"
}

cmd_respaldo_clave() {
  [ -f "$RCLONE_CONF" ] || error "el respaldo externo no está configurado (pacs respaldo-configurar)"
  echo "==================== GUARDA ESTO FUERA DEL SERVIDOR ===================="
  echo "Sin estas dos claves el respaldo NO se puede abrir (ni Backblaze ni nadie puede recuperarlas)."
  echo "Guárdalas en tu gestor de contraseñas o impresas en un lugar seguro."
  echo
  echo "  Clave de cifrado 1: $(rclone reveal "$(awk '/^\[pacs-cifrado\]/{f=1} f&&/^password = /{print $3; exit}' "$RCLONE_CONF")")"
  echo "  Clave de cifrado 2: $(rclone reveal "$(awk '/^\[pacs-cifrado\]/{f=1} f&&/^password2 = /{print $3; exit}' "$RCLONE_CONF")")"
  echo "  Bucket: $(awk '/^\[pacs-cifrado\]/{f=1} f&&/^remote = /{print $3; exit}' "$RCLONE_CONF")"
  echo "========================================================================="
}

cmd_respaldo_estado() {
  [ -f "$RCLONE_CONF" ] || { echo "Respaldo externo: no configurado (pacs respaldo-configurar)"; return 0; }
  echo "Último respaldo externo: $(cat "$ESTADO_REMOTO" 2>/dev/null || echo 'todavía no corre')"
  echo "Tamaño en Backblaze (cifrado):"
  rclone --config "$RCLONE_CONF" size "backblaze:$(awk '/^\[pacs-cifrado\]/{f=1} f&&/^remote = /{print $3; exit}' "$RCLONE_CONF" | cut -d: -f2)"
}

# Prueba de que el respaldo sirve: baja algunos archivos al azar y los compara con los del servidor
cmd_respaldo_probar() {
  [ -f "$RCLONE_CONF" ] || error "el respaldo externo no está configurado"
  local n f rel ok=0 mal=0 tmp; tmp=$(mktemp -d)
  for n in $(instancias); do
    while IFS= read -r f; do
      rel="${f#"$INST/$n/data/orthanc/"}"
      if rclone --config "$RCLONE_CONF" copyto "${REMOTO}$n/imagenes/$rel" "$tmp/x" 2>/dev/null && cmp -s "$f" "$tmp/x"; then
        ok=$((ok+1))
      else
        mal=$((mal+1)); echo "  [$n] no coincide o no está: $rel"
      fi
      rm -f "$tmp/x"
    done < <(find "$INST/$n/data/orthanc" -type f -mmin +1500 2>/dev/null | shuf -n 5)
  done
  rm -rf "$tmp"
  echo "$(date '+%d/%m/%Y %H:%M') Prueba de restauración: $ok archivos idénticos, $mal con problemas"
  [ "$mal" = 0 ] || avisar "Prueba de restauración con problemas" "La prueba mensual bajó archivos del respaldo y $mal no coinciden con el servidor ($ok sí).
Revisar: pacs respaldo-probar"
  [ "$mal" = 0 ]
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
  respaldo-configurar) cmd_respaldo_configurar;;
  respaldo-clave) cmd_respaldo_clave;;
  respaldo-estado) cmd_respaldo_estado;;
  respaldo-probar) cmd_respaldo_probar;;
  respaldo-subir) respaldo_remoto;;
  correo-configurar) cmd_correo_configurar;;
  resumen) cmd_resumen "$@";;
  agenda) cmd_agenda "$@";;
  agenda-importar) cmd_agenda_importar "$@";;
  portal) cmd_portal "$@"; cmd_estado;;
  panel-clave) cmd_panel_clave "$@";;
  cuota) cmd_cuota "$@";;
  estado) cmd_estado;;
  dicom-modo) cmd_dicom_modo "$@";;
  dictado-ia) cmd_dictado_ia "$@";;
  comprimir) cmd_comprimir "$@";;
  auto-actualizar) cmd_auto_actualizar "$@";;
  *) sed -n '2,30p' "$0"; exit 1;;
esac
