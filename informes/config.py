import os


def _bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "si", "sí", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-cambia-esto")
    DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

    ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://localhost:8042").rstrip("/")
    ORTHANC_USER = os.environ.get("ORTHANC_USER", "")
    ORTHANC_PASSWORD = os.environ.get("ORTHANC_PASSWORD", "")

    # En Render, RENDER_EXTERNAL_URL trae la dirección pública del servicio
    PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
                       or "http://localhost:8000").rstrip("/")
    # Si el PACS se sirve en el mismo dominio (Render), PACS_PUBLIC_URL = PUBLIC_BASE_URL
    PACS_PUBLIC_URL = (os.environ.get("PACS_PUBLIC_URL") or PUBLIC_BASE_URL).rstrip("/")

    ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    CENTRO_NOMBRE = os.environ.get("CENTRO_NOMBRE", "Centro de Imágenes")
    CENTRO_DIRECCION = os.environ.get("CENTRO_DIRECCION", "")
    CENTRO_TELEFONO = os.environ.get("CENTRO_TELEFONO", "")
    CENTRO_EMAIL = os.environ.get("CENTRO_EMAIL", "")

    LINK_DIAS_VIGENCIA = int(os.environ.get("LINK_DIAS_VIGENCIA", "60") or 60)
    LINK_REQUIERE_CLAVE = _bool(os.environ.get("LINK_REQUIERE_CLAVE"), False)

    WHISPER_URL = os.environ.get("WHISPER_URL", "").rstrip("/")
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
    WHISPER_API_KEY = os.environ.get("WHISPER_API_KEY", "")

    # Correo saliente (para enviar informes a pacientes)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "") or os.environ.get("CENTRO_EMAIL", "")
    SMTP_SSL = _bool(os.environ.get("SMTP_SSL"), False)        # true para puerto 465
    SMTP_STARTTLS = _bool(os.environ.get("SMTP_STARTTLS"), True)

    SECURE_COOKIES = PUBLIC_BASE_URL.startswith("https://")
    # Dominio común para compartir la sesión entre la app y el PACS (ej: .tucentro.cl)
    COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "") or None
