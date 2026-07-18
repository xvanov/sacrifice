# ──────────────────────────────────────────────────────────
# Sacrifice — production backend image
# ──────────────────────────────────────────────────────────
# FastAPI / uvicorn on port 8000, Python 3.11.
# Build:  docker compose -f docker-compose.prod.yml build
# ──────────────────────────────────────────────────────────

FROM python:3.11-slim

# Security: run as non-root
RUN groupadd -r sacrifice && useradd -r -g sacrifice sacrifice

# Install system deps for asyncpg (libpq) and health probes (curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, lockfile-driven installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Layer deps first (cache-friendly)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# Copy application code
COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY cli/ cli/

# Runtime directory for media uploads and factory directions
RUN mkdir -p /var/sacrifice/media /var/factory/directions && \
    chown -R sacrifice:sacrifice /var/sacrifice /var/factory/directions

USER sacrifice

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]