# PACS + Informes

Sistema para centros de imágenes (TC, rayos X, ecografía):

- **Almacenamiento DICOM** con [Orthanc](https://www.orthanc-server.com/): los equipos envían los estudios al PACS.
- **Visor web**: OHIF y Stone (diagnóstico, mediciones, MPR) + visor rápido integrado.
- **Informes**: plantillas por modalidad, dictado por voz o tipeo, guardado automático, firma del radiólogo, PDF con QR.
- **Entrega al paciente con link**: el paciente ve el informe, descarga el PDF y recorre las imágenes desde el celular. Opcional: clave de 6 dígitos. Botón para enviar por WhatsApp.
- **Rectificaciones con historial** y **auditoría** de accesos.
- **Cinco niveles de acceso** con un solo usuario por persona para la app y el visor: administrador, radiólogo, tecnólogo, secretaría y médico derivador. Cualquiera se puede limitar a ciertos centros.

```
Equipos (CT/RX/US) ──DICOM:4242──▶ Orthanc ◀── OHIF / Stone (pacs.tucentro.cl)
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

## Niveles de acceso

| Rol | Estudios que ve | Informa y firma | Links a pacientes | Visor (OHIF/Stone) | Subir al PACS |
|---|---|---|---|---|---|
| Administrador | Todos | Sí | Sí | Sí | Sí, y puede borrar |
| Radiólogo | Todos o sus centros | Sí | Sí | Sí | No |
| Tecnólogo | Todos o sus centros | No | No | Sí | Sí, sin borrar |
| Secretaría | Todos o sus centros | No | Sí | No | No |
| Médico derivador | Solo los que él solicitó | No | No | Solo sus estudios | No |

- **Centros**: en cada usuario puedes indicar qué centros ve (según el nombre de institución que envía el equipo). Vacío = todos.
- **Médico derivador**: se indica su nombre tal como aparece en el campo "médico solicitante" de los exámenes (ej. `ROJAS CAROLINA`). Ve solo esos estudios, y el informe solo cuando está firmado.
- Borradores: solo administradores y radiólogos ven informes no firmados.
- Un solo login: Caddy consulta a la app antes de cada acceso al PACS, así que el visor respeta los mismos permisos. Para que funcione, `COOKIE_DOMAIN` debe ser el dominio común (ej. `.tucentro.cl`).
- Todo acceso al visor de un usuario restringido, permitido o denegado, queda en Auditoría.

## Instalación en la nube

1. **Servidor**: una máquina virtual Linux (Ubuntu 24.04) en el proveedor que prefieras. Para empezar, 4 vCPU, 8 GB de RAM y un disco de datos separado para las imágenes, que puedas ampliar. Elige una región cercana (Santiago o São Paulo) para que los estudios suban rápido.
2. **DNS**: crea `informes.tucentro.cl` y `pacs.tucentro.cl` apuntando a la IP pública del servidor.
3. **Firewall** del proveedor: abre solo 80 y 443 a internet, y 51820/UDP para la VPN. **No abras 4242 ni 8042 a internet.**
4. **VPN para los centros** (WireGuard): cada centro tiene un pequeño equipo o router con WireGuard conectado al servidor. Los equipos del centro envían DICOM a la IP del servidor dentro de la VPN, puerto 4242. Así las imágenes viajan cifradas.
5. Instala Docker, copia este proyecto, crea `.env` y ejecuta `docker compose up -d --build`.
6. Configura los **respaldos automáticos** diarios (ver más abajo) hacia otro proveedor o almacenamiento.

## Configurar los equipos

En cada equipo (CT, rayos, ecógrafo) agrega un destino DICOM de almacenamiento (C-STORE):

| Campo | Valor |
|---|---|
| AE Title | `PACS` (o el valor de `PACS_AET`) |
| IP / Host | IP del servidor |
| Puerto | `4242` |

Si el servidor está en la nube, usa la IP del servidor dentro de la VPN (ver "Instalación en la nube").
Para que la restricción por centro funcione, cada equipo debe enviar el nombre de institución (InstitutionName)
de su centro; casi todos los equipos lo tienen en su configuración.

## Uso diario

1. El estudio llega solo a **Estudios** (estado *Pendiente*). Se puede filtrar por fecha, modalidad y estado, o buscar por nombre, RUT (con o sin puntos) o N° de examen.
2. El radiólogo abre el estudio, mira las imágenes (visor rápido u OHIF), inserta una plantilla y **dicta (F2)** o escribe. Se guarda solo.
3. **Firmar y validar** bloquea el informe, genera el PDF y crea el link del paciente.
4. Secretaría copia el link o lo envía por WhatsApp. Puede desactivarlo o generar uno nuevo.
5. Para corregir un informe firmado: **Rectificar informe** (con motivo). La versión anterior queda en el historial y el PDF indica la nueva versión.

## Dictado por voz

- **Por defecto** usa el reconocimiento de voz de Chrome/Edge (gratis, sin instalar nada). Ojo: el audio lo procesa el servicio de voz del navegador (Google/Microsoft).
- **Recomendado para producción**: un servidor Whisper propio (mejor con vocabulario médico y el audio no sale de tu servidor). Cualquier servidor con API compatible con OpenAI sirve (por ejemplo *faster-whisper* / *speaches*). Configura `WHISPER_URL` en `.env`.
- Comandos: "punto", "coma", "dos puntos", "punto y coma", "punto aparte", "nueva línea", "abrir/cerrar paréntesis".

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
  app.py             Rutas y lógica
  orthanc.py         Cliente del PACS
  pdf.py             PDF del informe
  db.py              Base de datos y plantillas iniciales
  templates/ static/ Interfaz
dev/mock_orthanc.py  PACS simulado para pruebas
```

## Próximos pasos posibles

Agendamiento y admisión con worklist DICOM (los equipos toman los datos del paciente), firma electrónica avanzada,
envío automático del link por correo/WhatsApp, frases rápidas y macros de dictado, informes comparativos con estudios previos.
