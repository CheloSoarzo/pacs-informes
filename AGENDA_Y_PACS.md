# Agenda + PACS en Vultr: guía de puesta en marcha

Con esto, la agenda y el PACS de cada centro quedan en el mismo servidor y trabajan juntos:

1. **Al agendar**, la cita recibe un **número de acceso** (ej. `RAN00001234`).
2. **Al pagar**, si el paciente no tenía clave se crea una y se imprime la **hoja de retiro** con la dirección,
   su RUT y su clave.
3. **En el equipo**, el tecnólogo abre la **lista de trabajo (worklist)** y elige al paciente: nombre, RUT,
   examen y número de acceso ya vienen escritos.
4. **El estudio llega al PACS** con ese número y queda unido a la cita. El radiólogo ve la orden de la agenda
   (examen pedido, médico y notas de recepción) dentro del estudio.
5. **En la agenda**, cada cita muestra su avance: *imágenes* → *informado* → *entregado*.
6. **El paciente** entra a `examenes.inq.cl/rancagua` con su RUT y su clave. Ve "informe en proceso" hasta que
   el radiólogo firma, y después puede ver el informe, descargar el PDF y ver las imágenes.

Cada centro sigue siendo independiente: Rancagua tiene su agenda, su PACS y su base de datos, e INSI las suyas.

---

## Parte 1 · Subir los archivos a GitHub

Hay dos zips, uno por repositorio. En cada uno: **Add file → Upload files**, arrastra **el contenido** de la
carpeta (no la carpeta misma) y pulsa **Commit changes**. GitHub reemplaza los archivos que ya existen.

**`agenda-imagenes`** (archivos nuevos o cambiados):
`integracion.js` (nuevo), `server.js`, `db.js`, `README.md`, y en `public/`: `index.html` y `estilos.css`.

**`pacs-informes`** (archivos nuevos o cambiados):
`AGENDA_Y_PACS.md` (nuevo), `README.md`, `docker-compose.instancia.yml`, `servidor/pacs.sh`,
`dev/mock_orthanc.py`, `dev/prueba_agenda.py` (nuevo), `dev/prueba_equipos.py`, y en `informes/`:
`integracion.py` (nuevo), `app.py`, `core.py`, `orthanc.py`, `requirements.txt`, `static/app.css`,
`templates/estudio.html` y `templates/paciente.html` (nuevo).

> Las agendas que siguen en Render se actualizan solas y funcionan igual que antes: sin `PACS_URL`, la
> conexión con el PACS queda apagada. Solo verás el número de acceso en cada cita.

### Repositorio de la agenda privado

El servidor baja la agenda desde GitHub. Si el repositorio `agenda-imagenes` es privado, crea un token de solo
lectura:

1. En GitHub: **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** *Only select repositories* → `agenda-imagenes`. **Permissions → Contents:** *Read-only*.
3. Copia el token y, en el servidor, agrega esta línea a `/opt/pacs-instancias/servidor.env`:

   ```
   AGENDA_REPO=https://TU_TOKEN@github.com/CheloSoarzo/agenda-imagenes.git
   ```

---

## Parte 2 · Actualizar el servidor Vultr

Entra al servidor por consola (Vultr → tu servidor → **View Console**, o `ssh root@IP`).

- **Si el servidor ya está instalado:**

  ```
  pacs actualizar
  ```

- **Si aún no lo creas:** sigue «Instalación automática» del `README.md`. Usa el mismo Cloud-Init de siempre,
  que ya instala esta versión.

---

## Parte 3 · Probar con pacientes de prueba (marcha blanca)

Antes de mover la agenda real, prueba todo con datos inventados en una dirección aparte.

1. En **Cloudflare → DNS → Add record**: tipo **A**, nombre `pruebaagenda`, valor = **IP del servidor Vultr**,
   nube **gris** (DNS only).
2. En el servidor:

   ```
   pacs agenda rancagua pruebaagenda.inq.cl
   ```

   Anota el usuario `admin` y la clave que muestra.
3. Entra a `https://pruebaagenda.inq.cl`, agenda un paciente de prueba **para hoy** con un RUT válido y cóbralo.
   Debe aparecer la **clave** y el botón **Imprimir hoja de retiro**.
4. **En el equipo** (empieza con uno solo, por ejemplo RX):
   - El equipo ya debe estar registrado en el PACS (**Administración → Equipos**), igual que para enviar estudios.
   - En la configuración DICOM del equipo, busca **Worklist / MWL / RIS** y agrega un servidor con los mismos datos
     del PACS: **IP del servidor**, **puerto DICOM** del centro y **AE Title** del PACS (`pacs lista` los muestra).
   - Busca la lista del día: debe aparecer el paciente de prueba. Hazle un examen de prueba y envíalo.
5. Revisa que:
   - el estudio llegue al PACS con el **N° de examen** igual al de la cita y con el recuadro **Orden de agenda**;
   - en la agenda la cita diga **imágenes** al minuto siguiente;
   - al firmar el informe, la agenda diga **informado**;
   - el paciente de prueba, entrando a `https://pacsrancagua.inq.cl/paciente` con RUT y clave, vea primero
     "informe en proceso" y después **Ver informe e imágenes**.

**Si el equipo no muestra la lista:**
- Prueba buscando sin filtro de modalidad ni de AE Title.
- Para RX la lista sale como modalidad `CR`. Si el equipo pide `DX`, en `/opt/pacs-instancias/rancagua/.env`
  cambia `MWL_MODALIDAD_RX=DX` y ejecuta `pacs iniciar rancagua`.
- Si quieres que aparezcan todos los agendados, no solo los que ya pagaron, usa `MWL_INCLUIR=todos`.
- Revisa los registros con `pacs logs rancagua`.

---

## Parte 4 · Pasar la agenda real de Render a Vultr

Hazlo a una hora tranquila, por ejemplo al cierre del día, después de cerrar la caja.

1. **En la agenda de Render** (Rancagua), pulsa **Respaldo**. Se descarga `agenda-respaldo-AAAA-MM-DD-HH-MM.db`.
2. **Súbelo al servidor** desde tu computador, en PowerShell (Windows) o Terminal (Mac):

   ```
   scp agenda-respaldo-AAAA-MM-DD-HH-MM.db root@IP_DEL_SERVIDOR:/root/
   ```

3. **En el servidor**, cambia la agenda a su dirección definitiva y carga el respaldo:

   ```
   pacs agenda rancagua agendarancagua.inq.cl
   pacs agenda-importar rancagua /root/agenda-respaldo-AAAA-MM-DD-HH-MM.db
   ```

   Esto borra las citas de prueba de la Parte 3. Borra también en el PACS los estudios del paciente de prueba.
4. **En Cloudflare**, edita el registro `agendarancagua`: cambia el tipo **CNAME** (a Render) por **A**, con la
   **IP del servidor Vultr** y la nube **gris**. Borra el registro `pruebaagenda`.
5. Espera 2 a 5 minutos y entra a `https://agendarancagua.inq.cl` con los **mismos usuarios de siempre**.
   Revisa que estén las citas de mañana y la caja de hoy.
6. **Deja Render encendido unas semanas como respaldo, pero sin usarlo.** Si lo usas, las citas quedarían
   repartidas en dos lugares. Cuando estés conforme, suspende el servicio en Render.

Repite los pasos para **INSI** con `insi` y `agendainsi.inq.cl`.

---

## Parte 5 · Portal común `examenes.inq.cl`

1. En **Cloudflare**: registro **A**, nombre `examenes`, valor = **IP del servidor**, nube **gris**.
2. En el servidor:

   ```
   pacs portal examenes.inq.cl
   ```

`https://examenes.inq.cl` muestra un botón por centro para pacientes y los accesos del personal. Desde ese momento,
la hoja de retiro imprime la dirección corta de cada centro (ej. `examenes.inq.cl/rancagua`), que lleva directo al
ingreso del paciente.

---

## Panel de servidores (`examenes.inq.cl/panel`)

Una página para elegir a qué PACS entrar, con una tarjeta por centro: espacio usado, estudios, dictados, informes
validados, pendientes por validar y por transcribir, y botones **Entrar al PACS**, **Agenda** y **Datos DICOM**
(IP, puerto y AE Title, para configurar equipos).

1. Primero crea el portal (Parte 5).
2. Ponle clave al panel. Pide la clave dos veces y no se ve al escribirla:

   ```
   pacs panel-clave
   ```

3. Entra a `https://examenes.inq.cl/panel` con usuario `admin` y esa clave. Desde la página del portal también hay
   un botón **Panel de servidores**.

Los números se actualizan solos cada 5 minutos (o al momento con `pacs estado`). El **Total** es el disco del
servidor. Para un PACS con espacio contratado (por ejemplo, uno arrendado), fija su límite y el panel mostrará el
porcentaje sobre ese límite:

```
pacs cuota rismed 50
```

## Aceptar equipos solo por AE Title (sin IP fija)

Si la IP pública de un centro cambia (por ejemplo, porque su red sale a internet a través de un servicio de
seguridad o de dos proveedores), el PACS puede aceptar a los equipos **solo por su AE Title**, desde cualquier IP:

```
pacs dicom-modo insi aetitle
```

En **Administración → Equipos DICOM** la IP pasa a ser opcional. Como el puerto queda abierto a internet, el AE Title
funciona como una clave: usa uno de 8 o más letras y números, distinto para cada equipo (la pantalla sugiere uno).
Los AE Title fáciles de adivinar aparecen marcados como **AE débil**. Para volver a exigir la IP:
`pacs dicom-modo insi ip`.

## Informe ya firmado por el médico (Subir informe firmado)

Cuando el informe viene terminado y firmado fuera del sistema, en el estudio se usa **📄 Subir informe firmado**:
se elige el archivo (PDF, o foto/escaneo JPG o PNG) y el médico que lo firmó. El archivo se guarda tal cual, el
estudio queda **validado** a nombre de ese médico y ese archivo es el que recibe el paciente. Word no se acepta:
hay que guardarlo como PDF. Para cambiarlo: **Rectificar** y subir el nuevo. Permiso: «Subir el informe final ya
firmado» (Transcripcionistas, Radiólogos y Administradores).

## Asignación de exámenes a radiólogos

Los **tecnólogos** asignan cada examen a un radiólogo: en el estudio (recuadro «Radiólogo asignado», con prioridad
normal o urgente) o varios a la vez desde la lista (casillas → Asignar). Los usuarios del perfil **Radiólogos**
solo ven los exámenes que tienen asignados (lista, búsqueda, estudio, visor y dictado) y ya no pueden tomarlos por
su cuenta. Se configura en **Administración → Perfiles y permisos** con la casilla «Solo ve los estudios que tiene
asignados»; el permiso para asignar es «Asignar radiólogo».

## Informe dictado: validado por el médico al terminar la transcripción

Con el ajuste **Administración → Correo y ajustes → «Validación de informes dictados»** (activo por defecto), cuando la
transcripcionista termina un dictado su botón dice **✓ Terminar y validar (Dr. X)**: el informe queda validado y
firmado a nombre del médico que dictó, sin pasar por «Por validar». En el PDF sale su imagen de firma (se carga en
**Administración → Usuarios**), nombre, título y registro. Queda en la auditoría y el médico puede rectificarlo.
Si no hay dictado, o el ajuste está desactivado, el informe sigue el camino normal de validación.

## Corregir un error en un informe validado

Los **tecnólogos** y las **transcripcionistas** ven en el estudio **Corregir error en el informe**: editan el texto,
indican qué se corrigió y guardan. El informe vuelve a quedar validado por el **mismo médico** como nueva versión;
la versión anterior queda en el historial, la corrección en la auditoría, y el paciente la ve con el mismo link y QR.
Permiso: «Corregir errores en informes validados». Los informes subidos como archivo firmado se cambian con
**Rectificar** y subiendo el archivo corregido.

## Código QR en el informe

Todo informe validado trae un código QR que abre el informe y las **imágenes** en el celular, con la **clave de
acceso** impresa al lado. En los informes subidos ya firmados, el QR va en una página final agregada al archivo.

## Botón «Ver en el PACS» en la agenda

Al abrir una cita en la agenda aparece abajo el botón **«Ver en el PACS ↗»** (en gris si las imágenes aún no llegan).
La agenda aprende sola la dirección del PACS al consultar el avance de los exámenes. Abre en otra pestaña la ficha de ese estudio en el PACS del centro (imágenes, informe, PDF).
Se entra con el usuario del PACS, así que cada uno ve solo lo que su perfil permite (un radiólogo, solo lo asignado).
Si el estudio aún no llegó, el PACS lo avisa y muestra la lista buscando ese número de acceso.

## Borrar un examen

Para exámenes de prueba, enviados al paciente equivocado o duplicados. En la **lista de estudios**, al final de cada
fila hay un **tarro de basura** que abre la ventana de borrado; también está **«Borrar examen»** al final de la ficha
del estudio (lo ven el administrador y los tecnólogos; permiso «Borrar exámenes completos»).

- Hay que escribir el **motivo** y la palabra **BORRAR** para confirmar.
- Si el examen tiene un **informe validado**, además hay que marcar la casilla «Entiendo que el informe validado
  también se borra». El link y la clave del paciente dejan de mostrarlo.
- Se borran las imágenes del PACS, el informe con su historial y links, los dictados, las notas y las entregas.
  **No se puede deshacer.**
- Queda en la auditoría: paciente, RUT, fecha, examen, número de acceso, si tenía informe validado y por quién, y el motivo.
- Si estaba unido a una cita, la agenda la vuelve a mostrar **sin imágenes**; si después llega el estudio correcto,
  se une solo como siempre. Lo mismo pasa al **separar** un estudio de una cita.

## Equipos sin worklist

El worklist es lo ideal, pero no es obligatorio. Los equipos que no lo usen (o mientras lo configuras) igual quedan
unidos a la agenda:

1. **Automático, por RUT + fecha + tipo de equipo.** Si el tecnólogo escribe en el equipo el **RUT del paciente**
   como ID (con o sin puntos y guion) y ese día el paciente tiene **una sola cita** de ese tipo (TC, RX o Eco),
   el PACS la une sola en menos de un minuto.
2. **Escribiendo el N° de acceso.** Si en el equipo escribe en "Accession Number" el número de la cita (está en la
   ficha de la cita y en la hoja de retiro), se une igual que con el worklist.
3. **A mano, desde el estudio.** Si el RUT quedó mal escrito o el paciente tiene dos exámenes iguales el mismo día,
   el estudio muestra un recuadro amarillo **"Sin cita de agenda"** con las citas posibles (primero las del mismo
   RUT). Se elige la correcta y se pulsa **Unir con la cita elegida**. Si se unió mal, **No es esta cita** la separa.
   Lo pueden hacer los perfiles con permiso para corregir datos del paciente (tecnólogos y recepción, por defecto).

La regla para el tecnólogo es simple: **en el ID del paciente, siempre el RUT.**

## Durante la marcha blanca

- **Los equipos envían a los dos PACS** (pacs4me y el nuevo), así nada se pierde si algo falla.
- **Suma los equipos de a uno** al worklist, cuando el anterior funcione bien.
- **Respaldos:** cada noche se guardan solos en `/opt/respaldos/<centro>/`, junto con los del PACS
  (`agenda_AAAA-MM-DD.db`). Sigue bajando también el **Respaldo** desde la agenda.
- **Si el PACS se reinicia**, la agenda guarda los cambios en cola y los reenvía sola al volver.

## Datos que se usan del paciente

- En el **worklist** van el nombre, el RUT (como `12345678-9`), la fecha de nacimiento, el examen, el médico
  solicitante y el número de acceso.
- La **clave** del paciente se guarda solo cifrada, en la agenda y en el PACS. Nadie puede leerla después de impresa:
  si el paciente la pierde, recepción genera una nueva y la anterior deja de servir.
- **Ingresos al portal:** tras 8 intentos fallidos en 15 minutos se bloquea el acceso por 15 minutos. Cada ingreso
  queda en la **Auditoría** del PACS.

## Prueba automática

`dev/prueba_agenda.py` recorre el flujo completo (agendar, pagar, clave, llegada del estudio, firma, portal,
nueva clave, eliminar cita y bloqueo por intentos), con el PACS simulado y la agenda en el mismo computador.
Las instrucciones para correrla están al inicio del archivo.
