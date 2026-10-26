"""Envío del informe al paciente por correo (SMTP)."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from config import Config


class CorreoError(Exception):
    pass


def configurado():
    return bool(Config.SMTP_HOST and Config.SMTP_FROM)


def _rellenar(plantilla, datos):
    texto = plantilla
    for k, v in datos.items():
        texto = texto.replace("{" + k + "}", str(v or ""))
    return texto


def enviar_informe(para, asunto_tpl, cuerpo_tpl, datos, pdf_bytes, nombre_pdf):
    """datos: paciente, examen, fecha, link, clave, centro, telefono_centro."""
    if not configurado():
        raise CorreoError("El correo saliente no está configurado (SMTP_HOST / SMTP_FROM).")
    _, direccion = parseaddr(para or "")
    if "@" not in direccion or "." not in direccion.split("@")[-1]:
        raise CorreoError("La dirección de correo no es válida.")

    datos = dict(datos)
    datos["linea_clave"] = f"Clave de acceso: {datos['clave']}\n" if datos.get("clave") else ""
    msg = EmailMessage()
    msg["Subject"] = _rellenar(asunto_tpl, datos).replace("\n", " ")
    nombre_from, dir_from = parseaddr(Config.SMTP_FROM)
    msg["From"] = formataddr((nombre_from or datos.get("centro", ""), dir_from))
    msg["To"] = direccion
    msg.set_content(_rellenar(cuerpo_tpl, datos))
    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=nombre_pdf)

    try:
        if Config.SMTP_SSL:
            servidor = smtplib.SMTP_SSL(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30,
                                        context=ssl.create_default_context())
        else:
            servidor = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=30)
            if Config.SMTP_STARTTLS:
                servidor.starttls(context=ssl.create_default_context())
        with servidor:
            if Config.SMTP_USER:
                servidor.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            servidor.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise CorreoError(f"No se pudo enviar el correo: {e}") from e
    return direccion
