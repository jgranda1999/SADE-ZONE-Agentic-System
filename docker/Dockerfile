# SADE decision ingest + worker (same image; override command in Compose / ECS).
# Build from repository root: docker build -f docker/Dockerfile .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Metadata + package (README referenced by pyproject.toml)
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Default: HTTP ingest. Worker: python -m sade.decision_worker
CMD ["uvicorn", "sade.api:app", "--host", "0.0.0.0", "--port", "8000"]
