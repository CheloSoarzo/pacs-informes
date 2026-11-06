FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Santiago
# faster-whisper trae su propio decodificador de audio (webm, ogg, m4a, mp3, wav)
RUN pip install --no-cache-dir "faster-whisper>=1.1,<2"

WORKDIR /app
COPY servidor.py .
EXPOSE 8000
CMD ["python", "servidor.py"]
