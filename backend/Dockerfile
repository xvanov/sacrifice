# ──────────────────────────────────────────────────────────
# Sacrifice — production backend image
# ──────────────────────────────────────────────────────────
# Serves two roles from one image:
#   * API    — uvicorn on port 8000 (the default CMD below)
#   * worker — celery worker + beat (command overridden in compose)
# Build:  docker compose -f docker-compose.prod.yml build
# ──────────────────────────────────────────────────────────

FROM python:3.11-slim

# Security: run as non-root.
# `-m -d /home/sacrifice` is load-bearing: a system user with no home has
# nowhere to put a cache, and anything that touches $HOME at runtime dies with
# "Permission denied (os error 13)" (this is exactly how the prod backend
# crash-looped: `uv run` could not create /home/sacrifice/.cache/uv).
RUN groupadd -r sacrifice && useradd -r -g sacrifice -m -d /home/sacrifice sacrifice

# System deps:
#   libpq-dev — asyncpg / psycopg build+runtime
#   curl      — container healthchecks
#   git       — the dev_sandbox goal type shells out to `git clone --depth=1`
#               (app/workers/dev_sandbox.py:clone_repo) before running the
#               user's tests, so the worker image needs a git binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, lockfile-driven installs.
# PINNED BY DIGEST, deliberately — `:latest` made the image non-reproducible and
# let an upstream uv release change the dependency resolver (or break `uv sync`)
# under a build nobody had touched. The tag is kept alongside the digest purely
# so a human can see which version this is; the digest is what Docker resolves.
# To upgrade: `docker pull ghcr.io/astral-sh/uv:<new-version>`, read off
# `docker image inspect --format '{{.RepoDigests}}'`, update both here, rebuild.
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv

WORKDIR /app

# Layer deps first (cache-friendly)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# Copy application code
COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY cli/ cli/

# Runtime directories:
#   /var/sacrifice/media    — uploaded proof files (written by the API, read by
#                             the worker during verification; a shared volume)
#   /var/sacrifice/beat     — celery beat's schedule database. Beat writes
#                             `celerybeat-schedule` to its CWD by default, and
#                             /app is root-owned, so `-B` needs an explicit
#                             writable --schedule path.
#   /var/factory/directions — bind-mount target for factory directions
RUN mkdir -p /var/sacrifice/media /var/sacrifice/beat /var/factory/directions && \
    chown -R sacrifice:sacrifice /var/sacrifice /var/factory/directions

# Runtime environment.
#   PATH/VIRTUAL_ENV — make the already-synced venv the default interpreter so
#                      no runtime `uv` invocation (and therefore no uv cache
#                      write) is needed to start a process.
#   HOME/UV_CACHE_DIR — writable, user-owned locations, so an interactive
#                      `uv run ...` inside the container still works.
#   PYTHONPATH        — `uv sync --no-editable` builds the project from a layer
#                      that has no source in it, so the `app` package never
#                      lands in site-packages; it is importable only from
#                      WORKDIR. Pinning it here keeps console-script entry
#                      points (alembic, celery, uvicorn) working too, since
#                      those put their own bin/ dir on sys.path, not the CWD.
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    HOME=/home/sacrifice \
    UV_CACHE_DIR=/home/sacrifice/.cache/uv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER sacrifice

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# `python -m`, not `uv run`: the image already contains a fully synced venv, so
# there is nothing for uv to resolve at boot — and `uv run` insists on a
# writable cache, which is what crash-looped this container as a non-root user.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
