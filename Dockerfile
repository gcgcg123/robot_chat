FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_HOST=0.0.0.0 \
    PROJECT_PORT=8080 \
    IOT_DATA_DIR=/var/lib/iotgroup5 \
    DATABASE_PATH=/var/lib/iotgroup5/emotional_robot.sqlite3 \
    ASR_PROVIDER=sensevoice \
    ASR_SENSEVOICE_MODEL_PATH=/models/asr/sensevoice-small \
    ASR_MODEL_PATH=/models/asr/whisper-large-v3-turbo-ct2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services ./services
COPY simulator ./simulator
COPY configs ./configs
COPY scripts ./scripts
COPY models/README.md models/manifest.json ./models/
COPY services/dashboard ./services/dashboard

RUN mkdir -p /var/lib/iotgroup5 /models
EXPOSE 8080
CMD ["uvicorn", "services.dialogue.app:app", "--host", "0.0.0.0", "--port", "8080"]
