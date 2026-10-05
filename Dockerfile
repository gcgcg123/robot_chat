FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_HOST=0.0.0.0 \
    PROJECT_PORT=8080 \
    IOT_DATA_DIR=/var/lib/iotgroup5 \
    DATABASE_PATH=/var/lib/iotgroup5/emotional_robot.sqlite3 \
    ASR_PROVIDER=whisper \
    ASR_SENSEVOICE_MODEL_PATH=/models/asr/sensevoice-small \
    ASR_MODEL_PATH=/models/asr/whisper-large-v3-turbo-ct2 \
    IOT_EMBEDDING_MODEL_PATH=/models/embedding/bge-small-zh-v1.5

WORKDIR /app
# Optional extras are opt-in build args: the base image stays ~110 MB of packages, and
# the two heavy sets are only added when the matching feature is actually wanted.
ARG WITH_VOICEPRINT=0
COPY requirements.txt requirements-asr-whisper.txt requirements-voiceprint.txt requirements-rerank.txt ./
# This image defaults to ASR_PROVIDER=whisper (see ENV above), so the Whisper runtime is
# part of the image. It is small (~65 MB) compared with the voiceprint set.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-asr-whisper.txt
RUN if [ "$WITH_VOICEPRINT" = "1" ]; then pip install --no-cache-dir -r requirements-voiceprint.txt; fi
COPY services ./services
COPY simulator ./simulator
COPY configs ./configs
COPY scripts ./scripts
COPY models/README.md models/manifest.json ./models/
COPY services/dashboard ./services/dashboard

RUN mkdir -p /var/lib/iotgroup5 /models
EXPOSE 8080
CMD ["uvicorn", "services.dialogue.app:app", "--host", "0.0.0.0", "--port", "8080"]
