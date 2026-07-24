#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# Sacrifice — UX Audit Target Boot & Verification Script
# ──────────────────────────────────────────────────────────
# Boots the audit docker-compose stack (if not already up),
# waits for backend + frontend health, runs minimal smoke
# checks, and prints the target locators needed by downstream
# audit stories.
#
# This is the canonical entrypoint for:
#   - UX audit execution against camera proof flow branches
#   - Executable smoke checks for target availability
#   - Later test stories that attach permission-denied coverage
#
# Ports used (none conflict with the orchestrator):
#   Backend  → 8001 (docker internal 8000)
#   Frontend → 8083 (docker internal 8082)
#   DB       → 5434 (docker internal 5432)
#   Redis    → 6380 (docker internal 6379)
#
# Usage:
#   ./scripts/audit-target.sh           # boot + verify
#   ./scripts/audit-target.sh --down    # tear down
#   ./scripts/audit-target.sh --status  # health check only
# ──────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.audit.yml"
BACKEND_URL="${AUDIT_BACKEND_URL:-http://localhost:8001}"
FRONTEND_URL="${AUDIT_FRONTEND_URL:-http://localhost:8083}"
CAMERA_DENIED_AUDIT_URL="$FRONTEND_URL/?uxAuditScenario=camera-permission-denied"
BACKEND_HEALTH="$BACKEND_URL/healthz"
BE_TIMEOUT="${AUDIT_BE_TIMEOUT:-60}"
FE_TIMEOUT="${AUDIT_FE_TIMEOUT:-90}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── helpers ─────────────────────────────────────────────────────────────

_step_ok()  { echo -e "  ${GREEN}✓${NC} $*"; }
_step_fail() { echo -e "  ${RED}✗${NC} $*"; }
_info()     { echo -e "  ${YELLOW}→${NC} $*"; }

_die() {
  echo -e "${RED}FATAL:${NC} $*" >&2
  exit 1
}

# ── down ────────────────────────────────────────────────────────────────

cmd_down() {
  echo "── tearing down audit stack ──"
  docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
  echo "Audit stack is down."
}

# ── status ──────────────────────────────────────────────────────────────

cmd_status() {
  echo "── audit stack status ──"
  if curl -fsS "$BACKEND_HEALTH" >/dev/null 2>&1; then
    _step_ok "backend healthy at $BACKEND_URL"
  else
    _step_fail "backend NOT healthy at $BACKEND_URL"
  fi
  fe_code=$(curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_URL" 2>/dev/null || echo "000")
  if [ "$fe_code" = "200" ]; then
    _step_ok "frontend serving at $FRONTEND_URL (HTTP 200)"
  else
    _step_fail "frontend NOT serving at $FRONTEND_URL (HTTP $fe_code)"
  fi

  scenario_code=$(curl -s -o /dev/null -w "%{http_code}" "$CAMERA_DENIED_AUDIT_URL" 2>/dev/null || echo "000")
  if [ "$scenario_code" = "200" ]; then
    _step_ok "camera-permission-denied scenario URL reachable"
  else
    _step_fail "camera-permission-denied scenario URL NOT reachable (HTTP $scenario_code)"
  fi
}

# ── verify-no-raw-token ──────────────────────────────────────────────────
# Confirm the OAuth redirect path never leaks a raw access_token to the
# browser — it must always use a one-time auth_code (see context/project.md
# "Active constraints").

verify_no_raw_token() {
  echo ""
  echo "── verifying no raw token redirect ──"

  # Test: the backend's /healthz does NOT redirect, but we can verify the
  # auth callback endpoints redirect with ?auth_code= (not access_token=)
  # by following the redirect chain of a known OAuth endpoint.
  #
  # We test the Google callback with a bogus state parameter — the backend
  # should redirect with auth_code in the URL, never access_token.
  for endpoint in "/api/auth/google/callback?code=test&state=test" "/auth/github/callback?code=test&state=test"; do
    redirect_url=$(curl -s -o /dev/null -w "%{redirect_url}" "$BACKEND_URL$endpoint" 2>/dev/null || true)
    if [ -z "$redirect_url" ]; then
      _info "no redirect from $endpoint (may require valid OAuth params — acceptable)"
      continue
    fi
    if echo "$redirect_url" | grep -q "access_token="; then
      _step_fail "$endpoint redirect contains access_token= — RAW TOKEN LEAK"
      echo "    redirect URL: $redirect_url"
      exit 1
    fi
    if echo "$redirect_url" | grep -q "auth_code="; then
      _step_ok "$endpoint redirects with auth_code (not access_token)"
    else
      _info "$endpoint redirects without auth_code or access_token (safe)"
    fi
  done
}

# ── verify-camera-entry ──────────────────────────────────────────────────
# Confirm the frontend serves the main app shell. The camera component
# itself is exercised in-browser by the Playwright audit smoke spec, but
# this verifies the entry path is reachable from the audit environment.

verify_camera_entry() {
  echo ""
  echo "── verifying camera proof entry path ──"

  # The frontend is a SPA — the root serves the app shell. For this story we
  # also verify the scenario URL used by scheduled camera-permission-denied
  # audits resolves to the same shell.
  frontend_html=$(curl -s "$CAMERA_DENIED_AUDIT_URL" 2>/dev/null || true)
  if [ -z "$frontend_html" ]; then
    _step_fail "camera-permission-denied scenario URL returned empty response"
    exit 1
  fi

  # The Expo web app shell contains a root div. This confirms the SPA loaded.
  if echo "$frontend_html" | grep -q '<div id="root"'; then
    _step_ok "camera-permission-denied scenario app shell loads"
  else
    _step_fail "scenario app shell does not contain expected root element"
  fi

  # Verify the backend API is reachable through the full chain
  if curl -fsS "$BACKEND_URL/api/health" >/dev/null 2>&1; then
    _step_ok "backend /api/health reachable from audit environment"
  else
    _step_fail "backend /api/health NOT reachable"
    exit 1
  fi
}

# ── main (boot + verify) ─────────────────────────────────────────────────

cmd_up() {
  echo "── audit target: booting stack ──"
  echo "    compose file: $COMPOSE_FILE"
  echo "    backend:      $BACKEND_URL"
  echo "    frontend:     $FRONTEND_URL"
  echo ""

  # Build and start
  docker compose -f "$COMPOSE_FILE" build --quiet 2>&1 || _die "docker compose build failed"
  docker compose -f "$COMPOSE_FILE" up -d 2>&1 || _die "docker compose up -d failed"

  # Wait for backend
  echo -n "── waiting for backend ($BACKEND_HEALTH) "
  be_ready=0
  for i in $(seq 1 "$BE_TIMEOUT"); do
    if curl -fsS "$BACKEND_HEALTH" >/dev/null 2>&1; then
      be_ready=1
      break
    fi
    echo -n "."
    sleep 1
  done
  echo ""
  if [ "$be_ready" != "1" ]; then
    _step_fail "backend did not become healthy within ${BE_TIMEOUT}s"
    echo "  backend logs:"
    docker compose -f "$COMPOSE_FILE" logs backend --tail 30 2>/dev/null || true
    exit 1
  fi
  _step_ok "backend healthy ($BACKEND_HEALTH)"

  # Wait for frontend (Expo web takes longer for first bundle)
  echo -n "── waiting for frontend ($FRONTEND_URL) "
  fe_ready=0
  for i in $(seq 1 "$FE_TIMEOUT"); do
    fe_code=$(curl -s -o /dev/null -w "%{http_code}" "$FRONTEND_URL" 2>/dev/null || echo "000")
    if [ "$fe_code" = "200" ]; then
      fe_ready=1
      break
    fi
    echo -n "."
    sleep 1
  done
  echo ""
  if [ "$fe_ready" != "1" ]; then
    _step_fail "frontend did not become ready within ${FE_TIMEOUT}s"
    echo "  frontend logs:"
    docker compose -f "$COMPOSE_FILE" logs frontend --tail 30 2>/dev/null || true
    exit 1
  fi
  _step_ok "frontend ready ($FRONTEND_URL, HTTP 200)"

  # Run verifications
  verify_no_raw_token
  verify_camera_entry

  echo ""
  echo "──────────────────────────────────────────"
  echo "  Audit target is UP"
  echo "──────────────────────────────────────────"
  echo "  Backend  → $BACKEND_URL"
  echo "  Frontend → $FRONTEND_URL"
  echo "  Scenario → $CAMERA_DENIED_AUDIT_URL"
  echo ""
  echo "  Run the camera permission denied audit scenario:"
  echo "    ./scripts/scheduled-camera-permission-audit.sh"
  echo ""
  echo "  Or run the Playwright spec directly:"
  echo "    cd frontend && E2E_BASE_URL=$CAMERA_DENIED_AUDIT_URL E2E_API_URL=$BACKEND_URL npx playwright test e2e/audit_camera_permission_denied.spec.ts --project=chromium"
  echo ""
  echo "  Tear down:"
  echo "    ./scripts/audit-target.sh --down"
  echo "──────────────────────────────────────────"
}

# ── dispatch ─────────────────────────────────────────────────────────────

case "${1:-}" in
  --down)   cmd_down ;;
  --status) cmd_status ;;
  "")       cmd_up ;;
  *)        echo "Usage: $0 [--down|--status]" >&2; exit 1 ;;
esac