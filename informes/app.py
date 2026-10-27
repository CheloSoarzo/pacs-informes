"""App de informes radiológicos sobre Orthanc.

Flujo: el equipo envía el estudio al PACS → aparece en la lista → se asigna a un radiólogo →
el radiólogo dicta (audio) o escribe → la transcripcionista transcribe y envía a validación →
el radiólogo valida y firma → PDF, link, correo y registro de la entrega al paciente.

Módulos:
  core.py           configuración, sesión, utilidades
  servicios.py      validar, enviar correo, registrar entregas, asignar
  rutas_trabajo.py  login, lista de trabajo, acciones masivas, filtros, exportar, cola de dictados, estadísticas
  rutas_estudio.py  pantalla del estudio, informe, dictados, notas, entregas
  rutas_publico.py  links y página del paciente, PDF, imágenes
  rutas_pacs.py     control de acceso al visor/PACS, dictado con Whisper
  rutas_admin.py    usuarios, perfiles, plantillas, frases, ajustes, auditoría
  rutas_equipos.py  equipos autorizados a enviar por internet (lista blanca DICOM)
"""
import os

from core import app  # noqa: F401  (gunicorn usa app:app)
import rutas_trabajo  # noqa: F401,E402
import rutas_estudio  # noqa: F401,E402
import rutas_publico  # noqa: F401,E402
import rutas_pacs  # noqa: F401,E402
import rutas_admin  # noqa: F401,E402
import rutas_equipos  # noqa: F401,E402

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=os.environ.get("DEBUG") == "1")
