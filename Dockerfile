FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BGV_CONFIG_DIR=/app/config \
    BGV_MODELS_DIR=/app/models

WORKDIR /app

# libgomp1 is required by onnxruntime (sherpa-onnx, piper)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the Bulgarian ASR + Piper voice into the image so `docker compose up`
# works without a host bind mount. ~130 MB; cached in the Docker layer.
COPY models/fetch_models.sh ./models/fetch_models.sh
RUN bash ./models/fetch_models.sh

COPY app ./app
COPY config ./config

RUN useradd --create-home app \
    && mkdir -p /app/models/tts-cache \
    && chown -R app:app /app

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS -H "X-API-Key: ${BGV_API_KEY:-}" http://127.0.0.1:8080/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
