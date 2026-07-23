#!/usr/bin/env bash
# verify-oauth.sh — Deployed-web OAuth login verification runner
#
# Usage:
#   # ASGI-level verification (no deployed server needed):
#   ./scripts/verify-oauth.sh asgi
#
#   # Browser-level verification (needs deployed frontend + backend):
#   E2E_BASE_URL=https://app.example.com E2E_API_URL=https://api.example.com \
#     ./scripts/verify-oauth.sh browser
#
#   # Both layers:
#   E2E_BASE_URL=https://app.example.com E2E_API_URL=https://api.example.com \
#     ./scripts/verify-oauth.sh all
#
# Environment variables for browser-level:
#   E2E_BASE_URL   — deployed web app origin (e.g. https://app.example.com)
#   E2E_API_URL    — deployed backend API origin (e.g. https://api.example.com)
#   E2E_HARNESS_READY=true — required to run browser-level tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass_count=0
fail_count=0

report_pass() {
  echo -e "  ${GREEN}PASS${NC} $1"
  pass_count=$((pass_count + 1))
}

report_fail() {
  echo -e "  ${RED}FAIL${NC} $1"
  fail_count=$((fail_count + 1))
}

run_asgi_tests() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Layer 1: ASGI-level OAuth flow verification"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "These tests exercise the FastAPI routing path directly —"
  echo "login, callback, exchange, and session rotation.  They do"
  echo "NOT require a deployed server or browser."
  echo ""

  cd "$BACKEND_DIR"

  if python -m pytest tests/test_oauth_flow_verification.py -v \
    --tb=short \
    --no-header \
    2>&1; then
    report_pass "ASGI OAuth flow verification"
  else
    report_fail "ASGI OAuth flow verification"
  fi
}

run_browser_tests() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Layer 2: Browser-level OAuth verification (Playwright)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  if [ "${E2E_HARNESS_READY:-}" != "true" ]; then
    echo -e "${YELLOW}SKIP${NC} Browser tests — E2E_HARNESS_READY != true"
    echo ""
    echo "Browser-level tests require:"
    echo "  1. A deployed web app at \$E2E_BASE_URL"
    echo "  2. A deployed backend at \$E2E_API_URL"
    echo "  3. Real OAuth provider credentials (Google, GitHub)"
    echo "  4. Run: E2E_HARNESS_READY=true E2E_BASE_URL=<app> E2E_API_URL=<api> \\"
    echo "       npx playwright test e2e/oauth_verification.spec.ts --project=chromium"
    echo ""
    return 0
  fi

  if [ -z "${E2E_BASE_URL:-}" ] || [ -z "${E2E_API_URL:-}" ]; then
    echo -e "${YELLOW}SKIP${NC} Browser tests — E2E_BASE_URL and E2E_API_URL must both be set"
    return 0
  fi

  cd "$FRONTEND_DIR"

  if npx playwright test e2e/oauth_verification.spec.ts \
    --project=chromium \
    2>&1; then
    report_pass "Browser-level OAuth verification"
  else
    report_fail "Browser-level OAuth verification"
  fi
}

print_summary() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Verification summary"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  total=$((pass_count + fail_count))
  echo "  $pass_count passed, $fail_count failed, $total total"

  if [ "$fail_count" -gt 0 ]; then
    echo ""
    echo -e "${RED}Some checks failed.${NC}"
    echo "Review the output above for failure diagnostics."
    exit 1
  else
    echo ""
    echo -e "${GREEN}All checks passed.${NC}"
  fi
}

case "${1:-asgi}" in
  asgi)
    run_asgi_tests
    print_summary
    ;;
  browser)
    run_browser_tests
    print_summary
    ;;
  all)
    run_asgi_tests
    run_browser_tests
    print_summary
    ;;
  *)
    echo "Usage: $0 {asgi|browser|all}"
    echo ""
    echo "  asgi     Run ASGI-level OAuth flow verification (no deployed server needed)"
    echo "  browser  Run browser-level Playwright verification (needs deployed app)"
    echo "  all      Run both layers"
    exit 1
    ;;
esac