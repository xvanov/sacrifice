#!/usr/bin/env bash
# Runtime smoke harness — drives the core user journey against a LIVE backend.
#
# This is sacrifice's `smoke_command` for the factory's D002 runtime verifier
# (Karpathy Layer-2 "external signal"): the oracle the chain was missing when
# the backlog shipped all-green while the app could not log in. It runs the
# register→login→create→activate→submit-proof journey and exits 0 only if the
# product actually runs.
#
# Two modes:
#   1. REUSE    — ONLY when SMOKE_BASE_URL is explicitly set: run the journey
#                 against that backend and touch nothing else. (Operator dev
#                 stack, or CI's own service.) Reuse is opt-in because it can
#                 silently test the WRONG code: a merge-gate run inside a git
#                 worktree that reuses whatever is bound on :8000 would smoke
#                 the production checkout, not the worktree's diff, and pass
#                 falsely green. That exact hazard is why isolated boot is now
#                 the default (D002 P0.2).
#   2. ISOLATED — default: boot THIS repo checkout's backend (host uvicorn —
#                 there is no backend Docker image) on a free ephemeral port,
#                 run the journey against it, then kill exactly the process we
#                 started. Safe to run while a dev stack occupies :8000, and
#                 safe inside factory worktrees. The persistent db container
#                 is shared (started via `make up-db` if needed) — the journey
#                 only creates throwaway rows.
#
# The backend is launched via `uv run uvicorn` when backend/.venv is missing
# (fresh worktrees have no .venv; uv materializes one from its cache), or the
# existing .venv when present (faster).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

run_journey() {
  echo "── running smoke journey ──"
  SMOKE_BASE_URL="$1" python3 "$REPO_ROOT/scripts/smoke_journey.py"
}

# Mode 1: REUSE — opt-in only, via an explicitly provided SMOKE_BASE_URL.
if [ -n "${SMOKE_BASE_URL:-}" ]; then
  if curl -fsS "$SMOKE_BASE_URL/api/health" >/dev/null 2>&1; then
    echo "── reusing operator-specified backend at $SMOKE_BASE_URL ──"
    run_journey "$SMOKE_BASE_URL"
    exit $?
  fi
  echo "✗ SMOKE_BASE_URL=$SMOKE_BASE_URL is set but not healthy — refusing to" >&2
  echo "  fall back to a boot (unset SMOKE_BASE_URL for an isolated boot)." >&2
  exit 1
fi

# Mode 2: ISOLATED — boot this checkout's backend on a free ephemeral port.
echo "── ensuring db container is up (make up-db) ──"
make up-db

# Pick a free port in the ephemeral range (bounded retries).
PORT=""
for _ in $(seq 1 20); do
  candidate=$((20000 + RANDOM % 20000))
  if ! lsof -ti ":$candidate" >/dev/null 2>&1; then
    PORT="$candidate"
    break
  fi
done
if [ -z "$PORT" ]; then
  echo "✗ could not find a free ephemeral port after 20 tries" >&2
  exit 1
fi

BASE_URL="http://127.0.0.1:$PORT"
MEDIA_DIR="$REPO_ROOT/.media"
DIRECTIONS_DIR="$REPO_ROOT/.directions"
LOG_DIR="$REPO_ROOT/.logs"
BE_LOG="$LOG_DIR/backend-smoke-$PORT.log"
mkdir -p "$MEDIA_DIR" "$DIRECTIONS_DIR" "$LOG_DIR"

# Launcher: prefer the existing .venv (fast); fall back to `uv run`, which
# materializes an env from the lockfile — this is what makes fresh factory
# worktrees (no .venv) bootable.
if [ -x "$REPO_ROOT/backend/.venv/bin/uvicorn" ]; then
  LAUNCH=("$REPO_ROOT/backend/.venv/bin/uvicorn")
elif command -v uv >/dev/null 2>&1; then
  LAUNCH=(uv run uvicorn)
else
  echo "✗ neither backend/.venv/bin/uvicorn nor uv is available — cannot boot" >&2
  exit 1
fi

backend_pid=""
cleanup() {
  if [ -n "$backend_pid" ] && kill -0 "$backend_pid" 2>/dev/null; then
    echo "── stopping smoke backend (pid $backend_pid, port $PORT) ──"
    kill "$backend_pid" 2>/dev/null || true
    for _ in $(seq 1 5); do
      kill -0 "$backend_pid" 2>/dev/null || return 0
      sleep 1
    done
    kill -9 "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "── booting isolated backend on :$PORT (log: $BE_LOG) ──"
(
  cd "$REPO_ROOT/backend"
  # Settings rejects its hardcoded jwt_secret default (secret-governance
  # AC1.1/AC1.2) — isolated smoke boots have no vault/.env entry for it, so
  # supply a safe test-only value unless the caller already set one.
  SACRIFICE_MEDIA_DIR="$MEDIA_DIR" \
  DIRECTIONS_PATH="$DIRECTIONS_DIR" FACTORY_DIRECTIONS_PATH="$DIRECTIONS_DIR" \
  FRONTEND_URL="http://localhost:5173" \
  GOOGLE_REDIRECT_URI="$BASE_URL/auth/google/callback" \
  GITHUB_REDIRECT_URI="$BASE_URL/auth/github/callback" \
  JWT_SECRET="${JWT_SECRET:-smoke-test-only-secret}" \
  exec "${LAUNCH[@]}" app.main:app --host 127.0.0.1 --port "$PORT" \
    > "$BE_LOG" 2>&1
) &
backend_pid=$!

# First boot in a fresh worktree may need `uv` to materialize the env — allow
# a generous window; a healthy cached boot is ready in a few seconds.
BOOT_TIMEOUT="${SMOKE_BOOT_TIMEOUT:-120}"
echo -n "── waiting for $BASE_URL/api/health "
ready=0
for _ in $(seq 1 "$BOOT_TIMEOUT"); do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    break
  fi
  if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  echo -n "."
  sleep 1
done
echo ""
if [ "$ready" != "1" ]; then
  echo "✗ backend did not become healthy within ${BOOT_TIMEOUT}s" >&2
  echo "── tail of $BE_LOG ──" >&2
  tail -30 "$BE_LOG" >&2 || true
  exit 1
fi

run_journey "$BASE_URL"
