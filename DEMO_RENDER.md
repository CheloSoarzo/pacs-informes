# Probar la demo en Render (gratis)

La demo corre todo en un solo servicio: PACS (Orthanc), visores OHIF y Stone, y la app de informes.
Al arrancar carga 4 estudios de ejemplo (TC cerebro, TC abdomen, ecografía y RX de tórax, con pacientes
ficticios) y un usuario de prueba por cada rol.

> Es solo para probar: en el plan gratis los datos se borran cada vez que el servicio se reinicia
> o se duerme (tras 15 minutos sin uso). No subas exámenes de pacientes reales.

## 1. Subir el proyecto a GitHub

1. Entra a github.com y crea un repositorio nuevo, por ejemplo `pacs-informes` (puede ser privado).
2. En el repositorio, usa **Add file → Upload files** y arrastra **el contenido** de la carpeta
   `pacs-informes` (no la carpeta misma): deben quedar `render.yaml`, `Dockerfile.render`,
   `informes/`, `render/`, etc. en la raíz del repositorio.
3. Presiona **Commit changes**.

## 2. Crear el servicio en Render

1. En dashboard.render.com: **New → Blueprint**.
2. Conecta tu cuenta de GitHub si te lo pide y elige el repositorio `pacs-informes`.
3. Render lee `render.yaml` y te pide **ADMIN_PASSWORD**: escribe una clave de al menos 10 caracteres.
4. Presiona **Apply / Deploy Blueprint**. La primera construcción tarda unos 5–10 minutos.
5. Cuando diga *Live*, abre la dirección que te da Render (algo como `https://pacs-informes-demo.onrender.com`).
   Espera un minuto más para que se carguen los estudios de ejemplo.

## 3. Usuarios para probar

Todos usan la **misma clave que pusiste en ADMIN_PASSWORD**:

| Usuario | Perfil | Qué verás |
|---|---|---|
| `admin` | Administradores | Todo: Administración (usuarios, perfiles, plantillas, frases, ajustes, auditoría) |
| `radiologo` | Radiólogos | Grabar dictados, informar, validar (también en lote), entregar |
| `radiologo_insi` | Radiólogos, solo INSI | Solo los estudios del centro INSI |
| `transcripcionista` | Transcripcionistas | Cola de dictados, reproductor con atajos, enviar a validación |
| `tecnologo` | Tecnólogos | Ver estudios, notas y subir DICOM desde «PACS / subir» |
| `secretaria` | Secretarias de Recepción | Informes validados, PDF, link, correo y registro de entregas |
| `derivador` | Médicos de Consultas (Dra. Rojas) | Solo los exámenes que ella solicitó, cuando están validados |

Prueba sugerida del flujo completo:
1. `admin`: en Estudios selecciona la TC de cerebro, asígnala a Dr. Andrés López y márcala urgente.
2. `radiologo`: abre la TC, presiona «● Grabar dictado», habla unos segundos, termina y envíala a transcripción.
3. `transcripcionista`: en **Dictados** toma el dictado, escúchalo (F4), inserta una plantilla y frases (`.normal` + espacio) y envíalo a validación.
4. `radiologo`: en **Por validar** revisa y valida. Abre el link del paciente en tu celular.
5. `secretaria`: registra la entrega (retiro, CD). `derivador`: comprueba que solo ve lo suyo.

## 4. Subir tus propios estudios de prueba

Entra como `admin` o `tecnologo` → **PACS / subir estudios** → botón de subir (Upload) y arrastra
archivos DICOM anonimizados. Aparecerán en la lista de Estudios.

## Limitaciones de la demo

- Los equipos (CT, rayos, eco) **no pueden enviar directo** a Render, porque Render solo acepta
  conexiones web. Para eso está la versión de producción (`docker-compose.yml`) en un servidor propio.
- El plan gratis se duerme sin uso: la primera visita después de un rato tarda ~1 minuto en despertar
  y vuelve a empezar con los datos de ejemplo.
- Tiene poca memoria: estudios muy grandes pueden ir lentos.
