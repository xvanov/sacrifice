#!/usr/bin/env bash
# Runtime smoke harness — drives the core user journey against a LIVE backend.
#
# This is sacrifice's `smoke_command` for the factory's D002 runtime verifier
# (Karpathy Layer-2 "external signal"): the oracle the chain was missing when
# the backlog shipped all-green while the app could not log in. It runs the
# register→login→create→activate→submit-proof journey and exits 0 only if the
# product actually runs.
#
# Two modes, chosen automatically:
#   1. REUSE — a backend is already healthy at SMOKE_BASE_URL. Run the journey
#      straight against it; touch nothing else. (Operator dev stack, or CI's.)
#   2. BOOT  — no backend is up. Bring one up the way this repo actually runs it
#      (host uvicorn via the Makefile — there is no backend Docker image), run
#      the journey, then stop ONLY the backend we started (the persistent db
#      container is left alone).
#
# NOTE: the compose `backend` service cannot build (no backend/Dockerfile), so
# the backend is host-run. BOOT mode therefore needs backend/.venv present
# (run the repo's dependency bootstrap first); it fails loudly if missing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BASE_URL="${SMOKE_BASE_URL:-http://localhost:8000}"

run_journey() {
  echo "── running smoke journey ──"
  SMOKE_BASE_URL="$1" python3 "$REPO_ROOT/scripts/smoke_journey.py"
}

# Mode 1: reuse an already-healthy backend (never disturbs a running stack).
if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then
  echo "── backend already healthy at $BASE_URL — reusing ──"
  run_journey "$BASE_URL"
  exit $?
fi

# Mode 2: boot the backend the repo's own way (host uvicorn + db container).
if [ ! -x "$REPO_ROOT/backend/.venv/bin/uvicorn" ]; then
  echo "✗ no running backend and backend/.venv is missing — bootstrap deps first" >&2
  echo "  (the compose backend service has no Dockerfile; the backend is host-run)" >&2
  exit 1
fi

started_backend=0
cleanup() {
  if [ "$started_backend" = "1" ]; then
    echo "── stopping the backend this harness started ──"
    make down-backend >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "── booting backend (make up-db up-backend wait-backend) ──"
make up-db
make up-backend
started_backend=1
make wait-backend

run_journey "$BASE_URL"
