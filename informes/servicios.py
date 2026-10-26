"""Acciones de negocio compartidas por varias pantallas: validar, enviar por correo, registrar entregas."""
from config import Config
from core import (centro_info, guardar_meta, link_vigente, meta_de, nombre_archivo, nuevo_link, pdf_de,
                  registrar, url_link, usuario_actual, webhook_validado)
from correo import CorreoError, configurado, enviar_informe
from db import ahora, ajuste, get_db
from orthanc import fecha_dicom


def registrar_entrega(estudio_id, tipo, detalle=""):
    u = usuario_actual()
    db = get_db()
    db.execute("INSERT INTO entregas (estudio_id, tipo, detalle, usuario_id, creado) VALUES (?,?,?,?,?)",
               (estudio_id, tipo, detalle, u["id"] if u else None, ahora()))
    db.commit()
    registrar("entrega_" + tipo, f"{estudio_id} {detalle}")


def enviar_correo_informe(inf, para):
    """Envía el informe validado por correo. Devuelve (ok, mensaje) y deja registro de la entrega."""
    if inf["estado"] != "firmado":
        return False, "El informe debe estar validado."
    link = link_vigente(inf["id"])
    if not link:
        nuevo_link(inf["id"])
        link = link_vigente(inf["id"])
    c = centro_info()
    datos = {"paciente": inf["paciente_nombre"], "examen": inf["descripcion"] or inf["modalidad"],
             "fecha": fecha_dicom(inf["fecha_estudio"]), "link": url_link(link), "clave": link["clave"],
             "centro": c["nombre"], "telefono_centro": c["telefono"]}
    try:
        direccion = enviar_informe(para, ajuste("correo_asunto"), ajuste("correo_cuerpo"), datos,
                                   pdf_de(inf), nombre_archivo(inf))
    except CorreoError as e:
        registrar_entrega(inf["estudio_id"], "email", f"ERROR a {para}: {e}")
        return False, str(e)
    registrar_entrega(inf["estudio_id"], "email", f"Enviado a {direccion}")
    return True, f"Informe enviado a {direccion}."


def validar_informe(inf):
    """Marca el informe como validado por el usuario actual y dispara las acciones posteriores.
    El llamador ya verificó permisos y que el texto sea válido."""
    u = usuario_actual()
    db = get_db()
    db.execute("UPDATE informes SET estado='firmado', firmado_por=?, firmado_en=? WHERE id=?",
               (u["id"], ahora(), inf["id"]))
    # Los dictados del estudio quedan cerrados
    db.execute("UPDATE dictados SET estado='transcrito', transcrito_en=COALESCE(transcrito_en, ?) "
               "WHERE estudio_id=? AND estado IN ('pendiente','tomado')", (ahora(), inf["estudio_id"]))
    db.commit()
    inf = db.execute("SELECT * FROM informes WHERE id=?", (inf["id"],)).fetchone()
    registrar("firmar", f"informe {inf['id']} v{inf['version']} {inf['paciente_id']}")
    if not link_vigente(inf["id"]):
        nuevo_link(inf["id"])
    webhook_validado(inf)

    aviso = ""
    email = meta_de(inf["estudio_id"]).get("paciente_email")
    if email and ajuste("envio_auto_email") == "1" and configurado():
        ok, msg = enviar_correo_informe(inf, email)
        aviso = msg
    return inf, aviso


def asignar(estudio_id, radiologo_id):
    u = usuario_actual()
    guardar_meta(estudio_id, radiologo_id=radiologo_id or None, asignado_por=u["id"],
                 asignado_en=ahora() if radiologo_id else None)
    registrar("asignar", f"{estudio_id} -> {radiologo_id or 'nadie'}")


def contexto_correo_ok():
    return configurado()


__all__ = ["registrar_entrega", "enviar_correo_informe", "validar_informe", "asignar", "contexto_correo_ok",
           "Config"]
