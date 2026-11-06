FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Santiago
# ffmpeg lee los audios (webm del navegador, ogg, m4a, mp3, wav)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# Versiones fijas, probadas juntas en el servidor (una versión nueva de una pieza puede romper a las otras)
RUN pip install --no-cache-dir "faster-whisper==1.2.1" "ctranslate2==4.8.5" "onnxruntime==1.31.1" "numpy<3"

WORKDIR /app
COPY servidor.py .
EXPOSE 8000
CMD ["python", "servidor.py"]
