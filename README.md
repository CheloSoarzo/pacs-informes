# PACS + Informes

Sistema para centros de imágenes (TC, rayos X, ecografía):

- **Almacenamiento DICOM** con [Orthanc](https://www.orthanc-server.com/): los equipos envían los estudios al PACS.
- **Visor web**: OHIF y Stone (diagnóstico, mediciones, MPR) + visor rápido integrado.
- **Informes**: plantillas por modalidad, dictado por voz o tipeo, guardado automático, firma del radiólogo, PDF con QR.
- **Entrega al paciente con link**: el paciente ve el informe, descarga el PDF y recorre las imágenes desde el celular. Opcional: clave de 6 dígitos. Botón para enviar por WhatsApp.
- **Rectificaciones con historial** y **auditoría** de accesos.
- **Flujo con transcripcionista**: el radiólogo graba un dictado de audio, la transcripcionista lo escucha (con atajos de teclado y velocidad ajustable) y lo envía a validación, y el radiólogo valida, uno a uno o en lote.
- **Radiólogo asignado y tiempo sin informar**, con estudios urgentes y alertas de color.
- **Frases de hallazgos y conclusiones** con atajos (`.normal` + espacio).
- **Correo al paciente** con el PDF adjunto (manual o automático al validar) y **registro de entregas**: retiro, CD, placas, informe impreso.
- **Notas con adjuntos**, historial del paciente, filtros guardados, actualización automática, exportar a Excel y estadísticas.
- **Perfiles de permisos editables** (30 permisos), con un solo usuario por persona para la app y el visor. Vienen 10 perfiles listos, iguales a los de pacs4me. Cualquier usuario se puede limitar a ciertos centros.

```
Equipos (CT/RX/US) ──internet, puerto DICOM con lista blanca──▶ Orthanc ◀── OHIF / Stone (pacs.tucentro.cl)
                                     ▲
                                     │ API REST
                              App de informes (informes.tucentro.cl) ──▶ link paciente /r/xxxx
```

> **¿Solo quieres verlo funcionando?** Sigue [DEMO_RENDER.md](DEMO_RENDER.md) para levantar una demo gratis en Render con estudios de ejemplo.

## Requisitos

- Un servidor Linux (en la nube o en el centro) con Docker y Docker Compose.
- Dos subdominios apuntando a la IP del servidor (ej. `informes.tucentro.cl` y `pacs.tucentro.cl`).
- Disco suficiente para las imágenes (como referencia, una TC puede pesar 100–500 MB).

## Instalación

```bash
cp .env.example .env
nano .env                 # cambia TODAS las contraseñas, dominios y datos del centro
docker compose up -d --build
```

- Entra a `https://informes.tucentro.cl` con `ADMIN_USER` / `ADMIN_PASSWORD`.
- El PACS y los visores (`https://pacs.tucentro.cl`) usan el mismo inicio de sesión.

Luego, en **Usuarios**, crea a cada persona con su rol. A los radiólogos agrégales título, registro y una imagen PNG de su firma.
Para poner el logo en el PDF, deja un archivo `data/informes/logo.png`.

## Perfiles y permisos

Cada usuario tiene un **perfil** y cada perfil es una lista de permisos que el administrador edita en
*Administración → Perfiles y permisos*. Vienen estos perfiles iniciales:

| Perfil | Qué hace |
|---|---|
| Administradores | Todo (siempre tiene todos los permisos) |
| Radiólogos | Dictan, informan, validan (también en lote), rectifican los suyos, entregan |
| Transcripcionistas | Escuchan dictados, redactan y envían a validación |
| Transcripcionistas - Avanzado | Lo anterior + asignar radiólogos, plantillas, frases, entrega |
| Tecnólogos | Visor, descargar, subir al PACS, notas |
| Tecnólogos de Ecografías | Como tecnólogos + dejar mediciones en borrador |
| Técnico | Notas y datos de contacto |
| Secretarias de Recepción | Links, correo, registro de entregas, exportar |
| Entrega de Exámenes | Como secretarias + anular entregas |
| Médicos de Consultas | Solo los exámenes que ellos solicitaron, ya validados, con visor |

- **Centros**: en cada usuario puedes indicar qué centros ve (según el nombre de institución que envía el equipo). Vacío = todos.
- **Médicos de consulta**: se indica su nombre como aparece en el campo «médico solicitante» de los exámenes (ej. `CAROLINA ROJAS`).
- Solo quien tiene «Ver informes no validados» ve borradores; el resto ve el informe recién cuando está validado.
- El visor (OHIF/Stone) usa el mismo login y respeta estos permisos. `COOKIE_DOMAIN` debe ser el dominio común (ej. `.tucentro.cl`).


## Instalación en la nube

1. **Servidor**: una máquina virtual Linux (Ubuntu 24.04) en el proveedor que prefieras. Para empezar, 4 vCPU, 8 GB de RAM y un disco de datos separado para las imágenes, que puedas ampliar. Elige una región cercana (Santiago o São Paulo) para que los estudios suban rápido.
2. **DNS**: crea `informes.tucentro.cl` y `pacs.tucentro.cl` apuntando a la IP pública del servidor.
3. **Firewall** del proveedor: abre 80 y 443, y el puerto DICOM (ver «Recibir estudios por internet»). **Nunca abras 8042.**
4. **Equipos de los centros**: envían por internet al puerto DICOM con lista blanca (ver sección siguiente).
5. Instala Docker, copia este proyecto, crea `.env` y ejecuta `docker compose up -d --build`.
6. Configura los **respaldos automáticos** diarios (ver más abajo) hacia otro proveedor o almacenamiento.

## Instalación automática (Vultr, Oracle Cloud u otro servidor Ubuntu)

Un mismo servidor aloja **varios PACS independientes**, como los "servidores PACS" de pacs4me: cada uno con su
dirección web, sus usuarios, su base de datos, sus imágenes, su puerto DICOM y el nombre de su centro en los informes.
Ninguno ve los datos de otro, lo que también permite arrendar un PACS a un tercero.

1. Sube este proyecto a tu repositorio de GitHub (público, sin archivos `.env`).
2. Crea un servidor Ubuntu 24.04 (para 2-3 PACS chicos basta con 2 vCPU y 4 GB de RAM; para más, 8 GB o más).
   - **Vultr**: en «Additional Features → Cloud-Init User-Data» pega `servidor/cloud-init-vultr.sh`.
   - **Oracle Cloud**: en «Opciones avanzadas → Gestión» pega `servidor/cloud-init-oracle.sh`, y en la VCN abre
     TCP 80, 443 y 46429-46479.
   Antes de pegar, cambia la clave inicial y la lista `PACS_INICIALES` (ej. `rancagua:Centro Médico Rancagua;insi:INSI`).
3. Espera 10-15 minutos y entra a cada PACS en `https://<nombre>.<ip-con-guiones>.sslip.io`
   (ej. `https://rancagua.64-176-4-237.sslip.io`) con el usuario `admin`. Cambia la clave al entrar.
   No necesitas comprar un dominio para empezar.

El instalador (`servidor/instalar.sh`) instala Docker, crea una entrada web HTTPS compartida (Caddy), crea los PACS
iniciales con claves seguras, y programa el firewall DICOM cada 5 minutos y un respaldo diario de todos los PACS.
El registro queda en `/var/log/pacs-instalacion.log`.

### Administrar los PACS del servidor (comando `pacs`)

Por SSH o desde la consola web del proveedor, como root:

```
pacs lista                                   # PACS, estado, puerto DICOM, espacio usado y dirección
pacs nueva clinica-sur --centro "Clínica Sur" # crea un PACS nuevo (puerto DICOM y dirección automáticos)
pacs dominio rancagua pacs.cmrancagua.cl     # usar un dominio propio (debe apuntar a la IP del servidor)
pacs detener clinica-sur                     # suspende un PACS (los datos se conservan; la web muestra "suspendido")
pacs iniciar clinica-sur
pacs logs rancagua
pacs actualizar                              # baja la última versión de GitHub y reinicia todos
pacs respaldo                                # respaldo manual (el automático corre cada noche)
```

Cada PACS vive en `/opt/pacs-instancias/<nombre>/` (su `.env` y su carpeta `data`). Los respaldos quedan en
`/opt/respaldos/<nombre>/`. Cada PACS usa unos 400-500 MB de RAM: un servidor de 4 GB aguanta 3-4 PACS chicos.

## Recibir estudios por internet (equipos de cada centro)

Los equipos envían directo a un puerto público del servidor, como en pacs4me, con tres protecciones:

1. **Puerto poco común** (`DICOM_PUERTO_PUBLICO`, por defecto 46429).
2. **Lista blanca en el PACS**: Orthanc solo acepta envíos de los equipos registrados en
   *Administración → Equipos DICOM*, y solo desde la IP pública de su centro (AE Title + IP).
3. **Firewall**: el puerto se abre únicamente a esas IPs; para cualquier otra dirección de internet está cerrado.

### Lo que necesitas

- Una **IP pública fija** en cada centro (pídela a tu proveedor de internet). Para ver la IP actual, abre https://ifconfig.me desde un PC del centro.
- El **AE Title** de cada equipo (está en la configuración DICOM del equipo, o te lo da el técnico). Cada equipo de un mismo centro debe tener uno distinto.

### Pasos

1. En `.env` define `DICOM_PUERTO_PUBLICO`, `SERVIDOR_IP_PUBLICA` y `FIREWALL_TOKEN` (una clave larga; la pantalla de equipos te sugiere una). Reinicia con `docker compose up -d`.
2. En el **firewall de tu proveedor de nube** (si lo tiene) permite el puerto `DICOM_PUERTO_PUBLICO` por TCP.
3. Activa el firewall automático del servidor (se actualiza cada 5 minutos con los equipos de la app):
   ```bash
   sudo ./firewall/sincronizar_firewall.sh        # prueba manual: debe decir "abierto solo para: ..."
   echo "*/5 * * * * root $(pwd)/firewall/sincronizar_firewall.sh >> /var/log/pacs-firewall.log 2>&1" | sudo tee /etc/cron.d/pacs-firewall
   echo "@reboot root sleep 60 && $(pwd)/firewall/sincronizar_firewall.sh >> /var/log/pacs-firewall.log 2>&1" | sudo tee -a /etc/cron.d/pacs-firewall
   ```
   (Docker se salta `ufw`, por eso el script usa la cadena `DOCKER-USER` de iptables.)
4. En la app, entra a *Administración → Equipos DICOM* y agrega cada equipo: nombre, centro, AE Title e IP del centro.
5. En cada equipo, crea un destino DICOM de almacenamiento (C-STORE) con:

| Campo en el equipo | Valor |
|---|---|
| IP / Host destino | IP pública del servidor (`SERVIDOR_IP_PUBLICA`) |
| Puerto | `DICOM_PUERTO_PUBLICO` (ej. 46429) |
| AE Title destino (llamado) | `PACS_AET` (ej. `PACS`) |
| AE Title propio del equipo | El mismo que registraste en la app |

6. Envía un estudio de prueba. Si no llega, revisa en orden: que la IP del centro no haya cambiado, que el AE Title coincida exactamente, y el registro `/var/log/pacs-firewall.log`.

> Importante: DICOM normal viaja **sin cifrar** por internet (igual que en pacs4me). Si algún equipo soporta DICOM TLS,
> conviene activarlo. Para máxima seguridad, la alternativa es un PC «puente» en cada centro que reenvía por HTTPS.

## Uso diario

1. El estudio llega solo a **Estudios** (*Pendiente*). Quien tiene el permiso lo **asigna** a un radiólogo y puede marcarlo **urgente** (uno a uno o seleccionando varios).
2. El radiólogo abre el estudio y **graba un dictado** (botón «● Grabar dictado»), o escribe o dicta directamente el texto.
3. La transcripcionista ve el dictado en **Dictados**, lo toma y lo transcribe. Usa F4 para reproducir o pausar, F3 y F6 para retroceder o adelantar 3 segundos, la velocidad ajustable, plantillas y frases. Luego lo **envía a validación**.
4. El radiólogo ve sus pendientes en **Por validar**, revisa y **valida**. También puede validar varios a la vez desde la lista.
5. Al validar se bloquea el informe, se genera el PDF (con firma e iniciales de transcripción) y el link del paciente. Si está activado y el paciente tiene correo, se envía automáticamente.
6. Secretaría entrega: link, WhatsApp, correo con PDF y registro de retiro, CD o placas.
7. Para corregir un informe validado: **Rectificar** (queda en el historial). Para retirarlo por error grave: **Invalidar** (desactiva el link).

Los tiempos en rojo indican estudios que llevan más horas sin informar que lo configurado en *Administración → Correo y ajustes*.

## Correo a pacientes

Configura en `.env` el servidor de correo saliente (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM`). Con Gmail o Google Workspace usa `smtp.gmail.com`, puerto 587 y una *contraseña de aplicación*.
El asunto y el texto del correo se editan en *Administración → Correo y ajustes*, y ahí se activa el envío automático al validar.


## Dictado

Hay dos formas, y se pueden usar juntas:

- **Dictado de audio para transcripción** (como en pacs4me): se graba en el navegador y queda guardado en el estudio. Funciona en Chrome, Edge, Firefox y Safari.
- **Voz a texto** (F2): el texto aparece mientras hablas. Por defecto usa el reconocimiento de Chrome/Edge (el audio lo procesa Google/Microsoft). Comandos: "punto", "coma", "dos puntos", "punto aparte", "nueva línea".
- **Servidor Whisper propio (opcional, recomendado)**: si configuras `WHISPER_URL`, la voz a texto se procesa en tu servidor y además cada dictado de audio trae un **borrador automático** que la transcripcionista solo corrige (botón «Usar texto automático»).


## Respaldos (importante)

Respalda a diario, idealmente a otro lugar (otro disco, otra nube):

```bash
docker compose exec -T postgres pg_dump -U orthanc orthanc | gzip > respaldo_indice_$(date +%F).sql.gz
sqlite3 data/informes/informes.db ".backup respaldo_informes_$(date +%F).db"
rsync -a data/orthanc/ /ruta/respaldo/orthanc/     # imágenes
```

Prueba restaurar un respaldo antes de pasar a producción.

## Seguridad y normativa

- Todo el tráfico va por HTTPS (Caddy obtiene los certificados solo).
- El puerto web de Orthanc no se publica: solo se llega a él a través de Caddy, que valida cada petición contra la app.
- Los links de pacientes tienen un token aleatorio, vencen (`LINK_DIAS_VIGENCIA`) y se pueden desactivar. Con `LINK_REQUIERE_CLAVE=true` además piden clave.
- Se registra en auditoría quién abrió cada estudio, quién firmó, y cada vez que un paciente abre su link.
- Revisa con tu asesor legal el cumplimiento de la Ley 20.584 (derechos del paciente y ficha clínica) y la ley de protección de datos personales, en particular dónde se alojan los datos y por cuánto tiempo se conservan.
- La "validación electrónica" del PDF es una firma electrónica simple. Si necesitas firma electrónica avanzada, se puede integrar después.

## Migrar desde el PACS actual

Pide a tu proveedor actual una exportación DICOM de tus estudios, o configura el PACS antiguo para enviar
(C-MOVE / C-STORE) su archivo a este Orthanc. Mientras tanto, ambos pueden funcionar en paralelo: configura los
equipos para enviar a los dos destinos.

## Probar sin servidor ni equipos

Incluye un Orthanc simulado con 3 estudios de ejemplo:

```bash
pip install flask requests reportlab pillow
python dev/mock_orthanc.py &
cd informes && ADMIN_PASSWORD=prueba12345 DATA_DIR=./data python app.py
# abrir http://localhost:8000  (usuario admin / prueba12345)
```

## Estructura

```
docker-compose.yml   Orthanc + PostgreSQL + app + Caddy
Caddyfile            HTTPS
.env.example         Configuración
informes/            App de informes (Flask + SQLite)
  app.py             Punto de entrada
  core.py            Configuración, sesión y utilidades
  permisos.py        Catálogo de permisos y perfiles iniciales
  servicios.py       Validar, enviar correo, entregas, asignación
  rutas_*.py         Pantallas (trabajo, estudio, público, PACS, administración)
  orthanc.py         Cliente del PACS
  pdf.py / correo.py PDF del informe y envío por correo
  db.py              Base de datos, migraciones, plantillas y frases iniciales
  templates/ static/ Interfaz
dev/mock_orthanc.py  PACS simulado para pruebas
dev/prueba_completa.py  Prueba automática de todo el flujo (97 verificaciones)
dev/smtp_falso.py    Servidor de correo falso para las pruebas
dev/prueba_equipos.py   Prueba de la lista blanca de equipos
firewall/            Script que abre el puerto DICOM solo a los centros registrados
```

## Próximos pasos posibles

Agendamiento y admisión con worklist DICOM (los equipos toman los datos del paciente), firma electrónica avanzada,
envío automático por WhatsApp Business, informes comparativos lado a lado, visores de escritorio (Weasis/RadiAnt).
