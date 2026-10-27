"""Permisos y perfiles de seguridad.

Cada usuario tiene un PERFIL. Un perfil es una lista de permisos (editable por el administrador)
más una marca opcional "solo ve los estudios que solicitó" (médicos de consulta / derivadores).
Además, a cualquier usuario se le puede limitar a ciertos centros (campo `centros`).

El perfil con clave "admin" tiene siempre todos los permisos.
"""
from orthanc import _normal

# (grupo, clave, descripción) — el orden es el que se muestra en la pantalla de perfiles
CATALOGO = [
    ("Estudios", "visor", "Ver imágenes en los visores (OHIF / Stone)"),
    ("Estudios", "descargar", "Descargar estudios DICOM en ZIP"),
    ("Estudios", "exportar", "Exportar el listado de estudios a Excel"),
    ("Estudios", "notas", "Ver y agregar notas y adjuntos en los estudios"),
    ("Estudios", "notas_eliminar", "Eliminar notas de otros usuarios"),
    ("Estudios", "datos_contacto", "Editar correo y teléfono del paciente"),
    ("Estudios", "asignar", "Asignar radiólogo y marcar estudios urgentes"),
    ("Estudios", "pacs_subir", "Subir estudios y usar el explorador del PACS"),
    ("Estudios", "pacs_borrar", "Borrar o modificar estudios en el PACS"),
    ("Dictados", "dictar", "Grabar dictados de audio"),
    ("Dictados", "dictados_escuchar", "Escuchar dictados de audio"),
    ("Dictados", "transcribir", "Transcribir dictados y enviarlos a validación"),
    ("Dictados", "dictados_anular", "Anular dictados"),
    ("Informes", "ver_borrador", "Ver informes que aún no están validados"),
    ("Informes", "redactar", "Escribir y editar informes (borrador)"),
    ("Informes", "firmar", "Validar y firmar informes (médico radiólogo)"),
    ("Informes", "firmar_lote", "Validar varios informes a la vez"),
    ("Informes", "rectificar", "Rectificar informes firmados por uno mismo"),
    ("Informes", "rectificar_todos", "Rectificar informes firmados por otros"),
    ("Informes", "invalidar", "Invalidar informes (los retira y desactiva el link del paciente)"),
    ("Informes", "plantillas", "Administrar plantillas de informe"),
    ("Informes", "frases", "Administrar frases de hallazgos y conclusiones"),
    ("Entrega", "links", "Generar y desactivar links para pacientes"),
    ("Entrega", "email", "Enviar informes por correo"),
    ("Entrega", "entregas", "Registrar entregas (retiro, CD, placas)"),
    ("Entrega", "entregas_anular", "Anular registros de entrega"),
    ("Administración", "estadisticas", "Ver estadísticas"),
    ("Administración", "usuarios", "Administrar usuarios y perfiles"),
    ("Administración", "auditoria", "Ver auditoría de accesos"),
    ("Administración", "ajustes", "Ajustes de correo y envíos automáticos"),
    ("Administración", "equipos", "Administrar los equipos que envían estudios por internet"),
]
TODOS = [c for _, c, _ in CATALOGO]
DESCRIPCION = {c: d for _, c, d in CATALOGO}

# Perfiles iniciales (copiados de la organización que usabas en pacs4me)
_RADIOLOGO = ["visor", "descargar", "exportar", "notas", "datos_contacto", "dictar", "dictados_escuchar",
              "dictados_anular", "ver_borrador", "redactar", "firmar", "firmar_lote", "rectificar", "links",
              "email", "entregas", "plantillas", "frases", "estadisticas"]
_TRANSCRIPCION = ["notas", "dictados_escuchar", "transcribir", "ver_borrador", "redactar"]
_TECNOLOGO = ["visor", "descargar", "notas", "datos_contacto", "pacs_subir"]
_RECEPCION = ["exportar", "notas", "datos_contacto", "links", "email", "entregas"]

PERFILES_INICIALES = [
    # clave, nombre, descripción, permisos, solo_solicitados
    ("admin", "Administradores", "Acceso total al sistema.", TODOS, 0),
    ("radiologos", "Radiólogos", "Dictan, informan, validan y firman.", _RADIOLOGO, 0),
    ("transcripcionistas", "Transcripcionistas", "Escuchan dictados y redactan informes para validación.",
     _TRANSCRIPCION, 0),
    ("transcripcionistas_avanzado", "Transcripcionistas - Avanzado",
     "Transcripción más asignación de radiólogos, plantillas, frases y entrega.",
     _TRANSCRIPCION + ["visor", "asignar", "plantillas", "frases", "links", "email", "entregas", "exportar"], 0),
    ("tecnologos", "Tecnólogos", "Realizan y suben exámenes. Revisan imágenes.", _TECNOLOGO, 0),
    ("tecnologos_eco", "Tecnólogos de Ecografías", "Como tecnólogos, y pueden dejar mediciones en borrador.",
     _TECNOLOGO + ["ver_borrador", "redactar"], 0),
    ("tecnico", "Técnico", "Apoyo: notas y datos de contacto.", ["notas", "datos_contacto"], 0),
    ("secretarias", "Secretarias de Recepción", "Entregan informes, links y registran retiros.", _RECEPCION, 0),
    ("entrega", "Entrega de Exámenes", "Entrega de exámenes y anulación de registros de entrega.",
     _RECEPCION + ["entregas_anular", "descargar"], 0),
    ("medicos_consulta", "Médicos de Consultas",
     "Médicos derivadores: ven solo los exámenes que ellos solicitaron, ya validados.",
     ["visor", "descargar"], 1),
]

# Migración desde la versión con 5 roles fijos
ROL_A_PERFIL = {"admin": "admin", "radiologo": "radiologos", "tecnologo": "tecnologos",
                "secretaria": "secretarias", "derivador": "medicos_consulta"}


def es_admin(u):
    return bool(u) and u["perfil_clave"] == "admin"


def permisos_de(u):
    if not u:
        return set()
    if es_admin(u):
        return set(TODOS)
    return {p for p in (u["permisos"] or "").split(",") if p}


def puede(u, accion):
    if not u:
        return False
    if es_admin(u):
        return True
    return accion in permisos_de(u)


def centros_de(u):
    return {_normal(c.strip()) for c in (u["centros"] or "").split(",") if c.strip()}


def solo_solicitados(u):
    return bool(u) and not es_admin(u) and bool(u["solo_solicitados"])


def restringido(u):
    """True si el usuario no puede ver todo el archivo."""
    return solo_solicitados(u) or bool(centros_de(u))


def ve_estudio(u, est):
    """est: dict con 'institucion' y 'solicitante' (nombres legibles, desde el DICOM)."""
    if not u or not est:
        return False
    centros = centros_de(u)
    if centros and _normal(est.get("institucion", "")) not in centros:
        return False
    if solo_solicitados(u):
        filtro = [p for p in _normal(u["filtro_solicitante"] or "").replace("^", " ").split() if p]
        if not filtro:
            return False
        solicitante = _normal(est.get("solicitante", ""))
        return all(p in solicitante for p in filtro)
    return True
