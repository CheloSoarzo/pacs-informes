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
```

## Próximos pasos posibles

Agendamiento y admisión con worklist DICOM (los equipos toman los datos del paciente), firma electrónica avanzada,
envío automático por WhatsApp Business, informes comparativos lado a lado, visores de escritorio (Weasis/RadiAnt).
