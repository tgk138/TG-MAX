FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ ./
COPY alembic ./alembic
COPY alembic.ini ./
RUN ln -s /app /app/src

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# --- API target ---
FROM base AS api
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

# --- Worker target ---
FROM base AS worker
CMD ["sh", "-c", "celery -A app.celery_app:celery worker --loglevel=info"]
