"""Niveles de acceso.

Rol          | Ve estudios        | Informa/firma | Links paciente | Visor PACS          | Subir/gestionar PACS
-------------|--------------------|---------------|----------------|---------------------|---------------------
admin        | todos              | sí            | sí             | sí                  | sí (incluye borrar)
radiologo    | todos o sus centros| sí            | sí             | sí                  | no
tecnologo    | todos o sus centros| no            | no             | sí                  | sí (sin borrar)*
secretaria   | todos o sus centros| no            | sí             | no                  | no
derivador    | solo sus pacientes | no            | no             | solo sus estudios   | no

* El tecnólogo usa el explorador del PACS solo si no tiene restricción de centros.
Restricción de centros: campo "centros" del usuario (vacío = todos los centros).
Derivador: ve los estudios cuyo "médico solicitante" en el DICOM coincide con su filtro.
"""
from orthanc import _normal

ROLES = {
    "admin": "Administrador",
    "radiologo": "Radiólogo",
    "tecnologo": "Tecnólogo",
    "secretaria": "Secretaría / recepción",
    "derivador": "Médico derivador",
}

DESCRIPCION_ROLES = {
    "admin": "Todo: usuarios, plantillas, auditoría, informes y administración del PACS.",
    "radiologo": "Ve estudios, informa, firma y rectifica sus informes. Usa el visor.",
    "tecnologo": "Ve y sube estudios, revisa imágenes en el visor. No informa.",
    "secretaria": "Ve informes firmados, descarga PDF y entrega links a pacientes.",
    "derivador": "Ve solo los exámenes que él solicitó, con informe firmado e imágenes.",
}

PERMISOS = {
    "informar": {"admin", "radiologo"},
    "ver_borrador": {"admin", "radiologo"},
    "links": {"admin", "radiologo", "secretaria"},
    "visor": {"admin", "radiologo", "tecnologo", "derivador"},
    "explorador_pacs": {"admin", "tecnologo"},
    "borrar_pacs": {"admin"},
    "plantillas": {"admin", "radiologo"},
    "usuarios": {"admin"},
    "descargar": {"admin", "radiologo", "tecnologo", "secretaria", "derivador"},
}


def puede(u, accion):
    return bool(u) and u["rol"] in PERMISOS.get(accion, set())


def centros_de(u):
    return {_normal(c.strip()) for c in (u["centros"] or "").split(",") if c.strip()}


def restringido(u):
    """True si el usuario no puede ver todo el archivo."""
    return u["rol"] == "derivador" or bool(centros_de(u))


def ve_estudio(u, est):
    """est: dict con 'institucion' y 'solicitante' (nombres legibles, desde el DICOM)."""
    if not u or not est:
        return False
    centros = centros_de(u)
    if centros and _normal(est.get("institucion", "")) not in centros:
        return False
    if u["rol"] == "derivador":
        filtro = [p for p in _normal(u["filtro_solicitante"] or "").replace("^", " ").split() if p]
        if not filtro:
            return False
        solicitante = _normal(est.get("solicitante", ""))
        return all(p in solicitante for p in filtro)
    return True
