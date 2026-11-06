"""Transcripción automática de los dictados de audio (borrador para la transcripcionista).

Cada dictado nuevo queda en auto_estado='pendiente'. Un hilo por proceso toma los pendientes
de a uno (la toma es atómica en la base, así dos procesos nunca toman el mismo), los manda al
servidor Whisper (WHISPER_URL) y guarda el texto en dictados.texto_auto.

  pendiente → procesando → listo
                         ↘ pendiente (reintento) → error (tras 3 intentos)

Si la app se reinicia a mitad de una transcripción, el dictado se retoma solo (a los 20 min).
Si WHISPER_URL está vacío, no hace nada y el flujo de dictado sigue igual que siempre.
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import requests

from config import Config
from core import app

MAX_INTENTOS = 3
ESPERA_REINTENTO = int(os.environ.get("WHISPER_REINTENTO_SEG", "30") or 30)  # segundos entre intentos
PROMPT = ("Informe radiológico. Tomografía computarizada, radiografía, ecografía. Técnica, hallazgos, impresión, "
          "conclusión. Parénquima, hipodensidad, hiperdensidad, ecogenicidad, adenopatías, derrame pleural, "
          "lóbulo superior derecho, milímetros, centímetros, sin contraste, con contraste.")
_aviso = threading.Event()
_iniciado = False
_candado = threading.Lock()


def _con():
    con = sqlite3.connect(os.path.join(Config.DATA_DIR, "informes.db"), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tomar(con):
    """Toma el próximo dictado por transcribir (o uno que quedó colgado) y lo marca 'procesando'."""
    colgado = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    return con.execute(
        """UPDATE dictados SET auto_estado='procesando', auto_en=?, auto_intentos=auto_intentos+1
           WHERE id = (SELECT id FROM dictados
                       WHERE estado != 'anulado' AND archivo != ''
                         AND (auto_estado='pendiente' OR (auto_estado='procesando' AND auto_en < ?))
                       ORDER BY id LIMIT 1)
           RETURNING id, archivo, mime, auto_intentos""", (_ahora(), colgado)).fetchone()


def transcribir_archivo(ruta, mime):
    headers = {"Authorization": f"Bearer {Config.WHISPER_API_KEY}"} if Config.WHISPER_API_KEY else {}
    with open(ruta, "rb") as fh:
        r = requests.post(f"{Config.WHISPER_URL}/v1/audio/transcriptions", headers=headers,
                          files={"file": (os.path.basename(ruta), fh, mime or "audio/webm")},
                          data={"model": Config.WHISPER_MODEL, "language": "es", "prompt": PROMPT}, timeout=1800)
    if not r.ok:
        try:
            detalle = r.json().get("error") or r.text
        except ValueError:
            detalle = r.text
        raise RuntimeError(f"Transcriptor respondió {r.status_code}: {detalle[:250]}")
    return (r.json().get("text") or "").strip()


def procesar_cola():
    """Transcribe todos los pendientes. Devuelve cuántos terminó (listos o con error)."""
    if not Config.WHISPER_URL:
        return 0
    hechos = 0
    with _candado:
        while True:
            con = _con()
            try:
                d = _tomar(con)
                con.commit()
            finally:
                con.close()
            if not d:
                return hechos
            ruta = os.path.join(Config.DATA_DIR, "dictados", d["archivo"])
            try:
                texto = transcribir_archivo(ruta, d["mime"])
                estado, error = "listo", ""
            except Exception as e:  # noqa: BLE001 - se reintenta y, si sigue fallando, queda en error
                texto = ""
                estado = "error" if d["auto_intentos"] >= MAX_INTENTOS or not os.path.exists(ruta) else "pendiente"
                error = str(e)[:300]
                app.logger.warning("Transcripción automática del dictado %s falló (intento %s): %s",
                                   d["id"], d["auto_intentos"], error)
            con = _con()
            try:
                con.execute("UPDATE dictados SET auto_estado=?, texto_auto=?, auto_error=?, auto_en=? WHERE id=?",
                            (estado, texto, error, _ahora(), d["id"]))
                con.commit()
            finally:
                con.close()
            if estado == "pendiente":
                time.sleep(ESPERA_REINTENTO)  # espera antes de reintentar (p. ej. el transcriptor se está iniciando)
            else:
                hechos += 1


def _bucle():
    time.sleep(int(os.environ.get("WHISPER_INICIO_SEG", "10") or 10))
    while True:
        try:
            procesar_cola()
        except Exception as e:  # noqa: BLE001 - el hilo nunca debe morir
            app.logger.warning("Cola de transcripción: %s", e)
        _aviso.wait(60)  # revisa cada minuto, o al tiro cuando llega un dictado
        _aviso.clear()


def avisar():
    """Llamar después de guardar un dictado: lo transcribe apenas pueda."""
    iniciar()
    _aviso.set()


def iniciar():
    global _iniciado
    if _iniciado or not Config.WHISPER_URL:
        return
    _iniciado = True
    threading.Thread(target=_bucle, daemon=True, name="transcripcion-auto").start()
