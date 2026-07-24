#!/usr/bin/env bash
# Runs the scheduled UX audit scenario for camera-permission denied flow
# against the canonical live audit target.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP_SCRIPT="$REPO_ROOT/scripts/audit-target.sh"

AUDIT_FRONTEND_URL="${AUDIT_FRONTEND_URL:-http://localhost:8083}"
AUDIT_BACKEND_URL="${AUDIT_BACKEND_URL:-http://localhost:8001}"
AUDIT_SCENARIO="camera-permission-denied"
PLAYWRIGHT_SPEC="${PLAYWRIGHT_SPEC:-e2e/audit_camera_permission_denied.spec.ts}"
E2E_BASE_URL="${AUDIT_FRONTEND_URL}/?uxAuditScenario=${AUDIT_SCENARIO}"
PLAYWRIGHT_CMD="cd frontend && E2E_BASE_URL=${E2E_BASE_URL} E2E_API_URL=${AUDIT_BACKEND_URL} npx playwright test ${PLAYWRIGHT_SPEC} --project=chromium"

print_plan() {
  echo "./scripts/audit-target.sh"
  echo "$PLAYWRIGHT_CMD"
}

if [ "${1:-}" = "--dry-run" ]; then
  print_plan
  exit 0
fi

if [ "${1:-}" = "--skip-boot" ]; then
  :
else
  "$BOOTSTRAP_SCRIPT"
fi

cd "$REPO_ROOT/frontend"
E2E_BASE_URL="$E2E_BASE_URL" E2E_API_URL="$AUDIT_BACKEND_URL" \
  npx playwright test "$PLAYWRIGHT_SPEC" --project=chromium
