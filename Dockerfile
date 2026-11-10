# Quizz / Nexus Platform + MCQ extractor
#
#   docker compose up --build            # see README "Run with Docker"
#
# Build without the ~1 GB local OCR engine (use OCR_PROVIDER=nvidia then):
#   docker compose build --build-arg INSTALL_LOCAL_OCR=false
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # PaddleOCR: skip the model-hoster connectivity check on every start.
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# Shared libraries PaddlePaddle/OpenCV need at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code changes don't invalidate this (slow) layer.
ARG INSTALL_LOCAL_OCR=true
COPY requirements.txt requirements-optional.txt ./
RUN if [ "$INSTALL_LOCAL_OCR" = "true" ]; then \
        pip install -r requirements.txt; \
    else \
        grep -viE '^paddle' requirements.txt > /tmp/requirements.txt \
        && pip install -r /tmp/requirements.txt; \
    fi \
    && pip install "celery>=5.4" "redis>=5.0"

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/media /app/logs /home/app/.paddlex \
    && chown -R app:app /app /home/app

COPY --chown=app:app . .

# Strip Windows line endings in case git converted them.
RUN sed -i 's/\r$//' docker/entrypoint.sh && chmod +x docker/entrypoint.sh

# Everything from here runs as the unprivileged user. collectstatic must too:
# loading settings creates logs/mcq_app.log, and a root-owned log file would
# stop the app from starting ("Unable to configure handler 'file'").
USER app
RUN SECRET_KEY=build-only-not-secret DEBUG=False python manage.py collectstatic --noinput \
    && rm -f /app/logs/*.log
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# One worker keeps memory low (the OCR models load once per worker); the
# threads serve requests while extraction runs in the background.
CMD ["gunicorn", "Quizz.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-"]
