#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Sacrifice — production deploy verification and gate script
# ──────────────────────────────────────────────────────────────────
# Runs the ordered verification sequence from the story:
#   1. docker compose build
#   2. docker compose up -d
#   3. deployed health (/healthz)
#   4. deployed smoke journey
#   5. deployed mobile email auth (register + login)
#   6. Conditional gate: flip deploy.enabled=true only on all-pass
#
# Every step is captured and the report is printed for operator review.
# The script exits 0 when all steps pass; exits 1 when any step fails.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.prod.yml"
BASE_URL="${DEPLOY_BASE_URL:-http://localhost:8000}"
VERIFY_LIB="$REPO_ROOT/scripts/verify_deploy_lib.py"
REPORT_FILE="$REPO_ROOT/.deploy-verification-report.txt"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass_count=0
fail_count=0
blocked_count=0
steps_output=""

_step_pass() {
  local name="$1"
  local detail="${2:-}"
  pass_count=$((pass_count + 1))
  echo -e "  ${GREEN}✓${NC} ${name}${detail:+: $detail}"
  steps_output+="  PASS: ${name}${detail:+ — $detail}"$'\n'
}

_step_fail() {
  local name="$1"
  local detail="${2:-}"
  fail_count=$((fail_count + 1))
  echo -e "  ${RED}✗${NC} ${name}${detail:+: $detail}"
  steps_output+="  FAIL: ${name}${detail:+ — $detail}"$'\n'
}

_step_blocked() {
  local name="$1"
  local detail="${2:-}"
  blocked_count=$((blocked_count + 1))
  echo -e "  ${YELLOW}⊘${NC} ${name} (BLOCKED)${detail:+: $detail}"
  steps_output+="  BLOCKED: ${name}${detail:+ — $detail}"$'\n'
}

_fail_report() {
  local title="$1"
  local detail="${2:-}"
  echo ""
  echo "============================================"
  echo "  DEPLOY VERIFICATION REPORT"
  echo "============================================"
  echo ""
  echo "$steps_output"
  echo "--------------------------------------------"
  echo "  Passed:  $pass_count"
  echo "  Failed:  $fail_count"
  echo "  Blocked: $blocked_count"
  echo "--------------------------------------------"
  echo "  RESULT:  FAILED / BLOCKED"
  echo "============================================"
  echo ""
  echo "Failure Report:"
  echo "  Step: $title"
  echo "  Detail: $detail"
  echo ""
}

_success_report() {
  echo ""
  echo "============================================"
  echo "  DEPLOY VERIFICATION REPORT"
  echo "============================================"
  echo ""
  echo "$steps_output"
  echo "--------------------------------------------"
  echo "  Passed:  $pass_count"
  echo "  Failed:  $fail_count"
  echo "  Blocked: $blocked_count"
  echo "--------------------------------------------"
  echo "  RESULT:  ALL PASSED"
  echo "============================================"
  echo ""
}

# ── Prerequisite check ──────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
  echo "FATAL: python3 is required but not found on PATH"
  exit 1
fi

if ! python3 -c "import yaml" 2>/dev/null; then
  echo "FATAL: PyYAML is required (python3 -c 'import yaml' failed)"
  exit 1
fi

# ── Step 1: docker compose build ────────────────────────────────────

echo "── Step 1: docker compose build ──"
if docker compose -f "$COMPOSE_FILE" build; then
  _step_pass "docker compose build"
else
  _step_fail "docker compose build" "build command exited non-zero"
  _fail_report "docker compose -f $COMPOSE_FILE build" "build command failed"
  python3 "$VERIFY_LIB" gate-apply --force-disable --reason "compose build failed"
  exit 1
fi

# ── Step 2: docker compose up -d ────────────────────────────────────

echo ""
echo "── Step 2: docker compose up -d ──"

# Stop any previous deploy stack first (idempotent)
docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true

if docker compose -f "$COMPOSE_FILE" up -d 2>&1; then
  _step_pass "docker compose up -d"
else
  up_output=$(docker compose -f "$COMPOSE_FILE" up -d 2>&1 || true)
  _step_fail "docker compose up -d" "compose boot failed"
  _fail_report "docker compose -f $COMPOSE_FILE up -d" "compose up -d failed: $up_output"
  python3 "$VERIFY_LIB" gate-apply --force-disable --reason "compose up -d failed"
  exit 1
fi

# ── Step 3: deployed health ─────────────────────────────────────────

echo ""
echo "── Step 3: deployed health (/healthz) ──"

HEALTH_MAX_ATTEMPTS="${HEALTH_MAX_ATTEMPTS:-10}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"

health_ok=0
for i in $(seq 1 "$HEALTH_MAX_ATTEMPTS"); do
  echo -n "  attempt $i/$HEALTH_MAX_ATTEMPTS... "
  if curl -fsS "$BASE_URL/healthz" >/dev/null 2>&1; then
    echo "OK"
    health_ok=1
    break
  fi
  echo "not ready"
  sleep "$HEALTH_INTERVAL"
done

if [ "$health_ok" -eq 1 ]; then
  _step_pass "deployed health (/healthz)" "responded 200 OK"
else
  _step_fail "deployed health (/healthz)" "did not become healthy within $((HEALTH_MAX_ATTEMPTS * HEALTH_INTERVAL))s"
  _fail_report "curl -fsS $BASE_URL/healthz" "health check did not pass"
  python3 "$VERIFY_LIB" gate-apply --force-disable --reason "deployed health check failed"
  exit 1
fi

# ── Step 4: deployed smoke journey ──────────────────────────────────

echo ""
echo "── Step 4: deployed smoke journey ──"

smoke_output=$(SMOKE_BASE_URL="$BASE_URL" python3 "$REPO_ROOT/scripts/smoke_journey.py" 2>&1) || smoke_rc=$?
smoke_rc=${smoke_rc:-$?}

if [ "$smoke_rc" -eq 0 ] && echo "$smoke_output" | grep -q "SMOKE PASSED"; then
  _step_pass "deployed smoke journey" "register → login → create → activate → submit-proof"
else
  _step_fail "deployed smoke journey" "smoke did not pass (rc=$smoke_rc)"
  echo "  smoke output:"
  echo "$smoke_output" | sed 's/^/    /'
  _fail_report "deployed smoke journey" "smoke journey failed against $BASE_URL"
  python3 "$VERIFY_LIB" gate-apply --force-disable --reason "deployed smoke journey failed"
  exit 1
fi

# ── Step 5: deployed mobile email auth ──────────────────────────────

echo ""
echo "── Step 5: deployed mobile email auth ──"

MOBILE_EMAIL="verify+$(date +%s)-$$@example.com"
MOBILE_PASSWORD="VerifyTest123!"

# 5a. Register
echo "  testing POST /api/auth/email/register..."
register_output=$(python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/scripts')
from verify_deploy_lib import verify_deployed_mobile_register
result = verify_deployed_mobile_register('$BASE_URL', email='$MOBILE_EMAIL', password='$MOBILE_PASSWORD')
print(result.get('_status', 0))
print(result.get('access_token', '')[:20] + '...' if result.get('access_token') else 'NO_TOKEN')
" 2>&1) || register_rc=$?
register_rc=${register_rc:-$?}
register_status=$(echo "$register_output" | head -1)

if [ "$register_rc" -eq 0 ] && [ "$register_status" = "200" ] || [ "$register_status" = "201" ]; then
  _step_pass "deployed mobile POST /api/auth/email/register" "HTTP $register_status"
else
  _step_fail "deployed mobile POST /api/auth/email/register" "HTTP $register_status (rc=$register_rc)"
  _fail_report "POST $BASE_URL/api/auth/email/register" "register returned status=$register_status"
  python3 "$VERIFY_LIB" gate-apply --force-disable --reason "deployed mobile register failed"
  exit 1
fi

# 5b. Login
echo "  testing POST /api/auth/email/login..."
login_output=$(python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/scripts')
from verify_deploy_lib import verify_deployed_mobile_login
result = verify_deployed_mobile_login('$BASE_URL', email='$MOBILE_EMAIL', password='$MOBILE_PASSWORD')
print(result.get('_status', 0))
print(result.get('access_token', '')[:20] + '...' if result.get('access_token') else 'NO_TOKEN')
" 2>&1) || login_rc=$?
login_rc=${login_rc:-$?}
login_status=$(echo "$login_output" | head -1)

if [ "$login_rc" -eq 0 ] && [ "$login_status" = "200" ]; then
  _step_pass "deployed mobile POST /api/auth/email/login" "HTTP $login_status"
else
  _step_fail "deployed mobile POST /api/auth/email/login" "HTTP $login_status (rc=$login_rc)"
  _fail_report "POST $BASE_URL/api/auth/email/login" "login returned status=$login_status"
  python3 "$VERIFY_LIB" gate-apply --force-disable --reason "deployed mobile login failed"
  exit 1
fi

# ── Step 6: flip deploy.enabled=true ────────────────────────────────

echo ""
echo "── Step 6: all verification passed — enabling deploy ──"

python3 "$VERIFY_LIB" gate-apply --enable

# Verify the flip took effect
enabled_now=$(python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/scripts')
from verify_deploy_lib import get_deploy_enabled
print('true' if get_deploy_enabled() else 'false')
")

if [ "$enabled_now" = "true" ]; then
  _step_pass "deploy.enabled flipped to true" "all verification steps passed end-to-end"
  _success_report
  echo "  deploy.enabled is now: true"
  exit 0
else
  _step_fail "deploy.enabled flip verification" "config still reports false"
  _fail_report "deploy.enabled flip" "config write succeeded but read-back was false"
  exit 1
fi