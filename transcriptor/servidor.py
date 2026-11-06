"""Servidor de transcripción (voz a texto) con Whisper, para todos los PACS del servidor.

Corre en su propio contenedor, sin puerto publicado: solo las apps de informes lo alcanzan
por la red interna "pacs-proxy" en http://transcriptor:8000. El audio no sale del servidor.

API compatible con OpenAI (la que ya usa la app de informes):
  POST /v1/audio/transcriptions   multipart: file=<audio>, language=es, prompt=<vocabulario>
                                   → {"text": "...", "duration": 95.2, "segundos": 41.0}
  GET  /salud                     → {"ok": true, "modelo": "small", "listo": true}

Variables: WHISPER_MODELO (small | medium | large-v3, por defecto small), WHISPER_HILOS (hilos de CPU),
           WHISPER_BEAM (5 = más preciso, 1 = más rápido), WHISPER_TOKEN (clave opcional), MODELOS_DIR.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
import traceback
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELO = os.environ.get("WHISPER_MODELO", "small")
HILOS = int(os.environ.get("WHISPER_HILOS", "2") or 2)
BEAM = int(os.environ.get("WHISPER_BEAM", "5") or 5)
TOKEN = os.environ.get("WHISPER_TOKEN", "")
MODELOS_DIR = os.environ.get("MODELOS_DIR", "/modelos")
MAX_BYTES = 200 * 1024 * 1024  # 200 MB por audio

# Vocabulario por defecto: ayuda a que Whisper escriba bien los términos radiológicos
PROMPT_BASE = ("Informe radiológico. Tomografía computarizada, radiografía, ecografía. Técnica, hallazgos, "
               "impresión, conclusión. Parénquima, hipodensidad, hiperdensidad, ecogenicidad, adenopatías, "
               "derrame pleural, lóbulo superior derecho, milímetros, centímetros, sin contraste, con contraste.")

_modelo = None
_error_carga = ""
_listo = threading.Event()
_turno = threading.Lock()  # un audio a la vez: así el PACS no se queda sin CPU


def cargar():
    global _modelo, _error_carga
    try:
        from faster_whisper import WhisperModel
        t = time.time()
        _modelo = WhisperModel(MODELO, device="cpu", compute_type="int8", cpu_threads=HILOS,
                               download_root=MODELOS_DIR)
        print(f"Modelo {MODELO} listo en {time.time() - t:.0f} s", flush=True)
    except Exception as e:  # noqa: BLE001
        _error_carga = str(e)
        print(f"No se pudo cargar el modelo {MODELO}: {e}", flush=True)
    _listo.set()


def leer_audio(ruta):
    """Convierte cualquier audio (webm, ogg, m4a, mp3, wav) a 16 kHz mono con ffmpeg.

    No se usa el lector interno de faster-whisper (PyAV): sus versiones nuevas cambian de API
    y dejan de leer los audios. ffmpeg es estable y lee también los webm sin duración del navegador.
    """
    import numpy as np
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", ruta,
                        "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True, timeout=600)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("no se pudo leer el audio: " + (r.stderr.decode("utf-8", "replace").strip()[-300:] or "archivo vacío"))
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def transcribir(ruta, idioma="es", prompt=""):
    _listo.wait()
    if _modelo is None:
        raise RuntimeError(f"El modelo no está disponible: {_error_carga}")
    audio = leer_audio(ruta)
    with _turno:
        t = time.time()
        segs, info = _modelo.transcribe(audio, language=idioma or "es", beam_size=BEAM, vad_filter=True,
                                        initial_prompt=(prompt or PROMPT_BASE)[:800])
        texto = " ".join(s.text.strip() for s in segs).strip()
        return {"text": texto, "duration": round(len(audio) / 16000, 1), "segundos": round(time.time() - t, 1)}


def leer_multipart(tipo, cuerpo):
    """Devuelve (campos de texto, (nombre, bytes) del archivo) de un multipart/form-data."""
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + tipo.encode() + b"\r\n\r\n" + cuerpo)
    campos, archivo = {}, None
    for parte in msg.iter_parts():
        nombre = parte.get_param("name", header="content-disposition")
        if not nombre:
            continue
        datos = parte.get_payload(decode=True) or b""
        if parte.get_filename() is not None or nombre == "file":
            archivo = (parte.get_filename() or "audio", datos)
        else:
            campos[nombre] = datos.decode("utf-8", "replace")
    return campos, archivo


class Manejador(BaseHTTPRequestHandler):
    server_version = "transcriptor/1"

    def _json(self, codigo, datos):
        b = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.split("?")[0] in ("/salud", "/health"):
            return self._json(200, {"ok": _modelo is not None or not _listo.is_set(), "modelo": MODELO,
                                    "listo": _modelo is not None, "error": _error_carga})
        self._json(404, {"error": "No existe"})

    def do_POST(self):
        if self.path.split("?")[0] != "/v1/audio/transcriptions":
            return self._json(404, {"error": "No existe"})
        if TOKEN and self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
            return self._json(401, {"error": "Clave incorrecta"})
        largo = int(self.headers.get("Content-Length") or 0)
        if not largo or largo > MAX_BYTES:
            return self._json(413 if largo else 400, {"error": "Audio vacío o demasiado grande"})
        campos, archivo = leer_multipart(self.headers.get("Content-Type", ""), self.rfile.read(largo))
        if not archivo or not archivo[1]:
            return self._json(400, {"error": "Falta el audio (campo file)"})
        sufijo = os.path.splitext(archivo[0])[1][:8] or ".audio"
        with tempfile.NamedTemporaryFile(suffix=sufijo) as tmp:
            tmp.write(archivo[1])
            tmp.flush()
            try:
                r = transcribir(tmp.name, campos.get("language", "es"), campos.get("prompt", ""))
            except Exception as e:  # noqa: BLE001
                print("Error al transcribir:\n" + traceback.format_exc(), flush=True)
                return self._json(500, {"error": f"No se pudo transcribir: {e}"})
        print(f"Transcrito: {r['duration']:.0f} s de audio en {r['segundos']:.0f} s", flush=True)
        self._json(200, r)

    def log_message(self, *a):  # sin registro por petición (los audios son datos clínicos)
        pass


if __name__ == "__main__":
    threading.Thread(target=cargar, daemon=True).start()
    print(f"Transcriptor en :{os.environ.get('PUERTO', '8000')} (modelo {MODELO}, {HILOS} hilos)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PUERTO", "8000"))), Manejador).serve_forever()
