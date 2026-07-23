#!/usr/bin/env bash
# Reproduce the CI `lint` required check.
#
# Usage:
#   ./scripts/ci-lint-check.sh [--changed-only] [base-ref]
#
#   --changed-only  Diff against base-ref (default: HEAD~1 for push, origin/main for PR)
#                   instead of linting the full tree. This is what the CI job actually does.
#   base-ref        The git ref to diff against (default: HEAD~1).
#
# Exit code mirrors CI: non-zero when lint violations are found in changed files.
#
# CI workflow this reproduces: .github/workflows/ci.yml → job: lint
# Story context: 310-fix-failing-required-check-s-on-main-lint-narrow-read-alt-a
#
# Current state (2026-07-18): on the HEAD~1 diff this script reproduces
#   ruff check:  113 errors across 12 changed Python files
#   ruff format: 12 files would be reformatted
#   frontend lint: passes (warnings only, exit 0)
# The follow-on fix story (infra-scoped) should make this script exit 0.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CHANGED_ONLY=false
BASE_REF="HEAD~1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --changed-only) CHANGED_ONLY=true; shift ;;
    *) BASE_REF="$1"; shift ;;
  esac
done

FAILED=0

echo "=== CI lint reproduction ==="
echo "Repo: $REPO_ROOT"
echo "Mode: $([ "$CHANGED_ONLY" = true ] && echo "changed-only (base=$BASE_REF)" || echo "full-tree")"
echo ""

if [ "$CHANGED_ONLY" = true ]; then
  # --- Changed-files gate (mirrors ci.yml: Determine changed files) ---
  # Resolve BASE_REF: if it contains a '/' treat as origin/$BASE_REF...HEAD,
  # otherwise it's a local ref like HEAD~1 for push events.
  if [[ "$BASE_REF" == */* ]]; then
    git fetch --no-tags --depth=1 origin "${BASE_REF#*/}" 2>/dev/null || true
    RANGE="$BASE_REF...HEAD"
  else
    RANGE="$BASE_REF HEAD"
  fi

  CHANGED_PY=$(git diff --name-only --diff-filter=ACMR $RANGE 2>/dev/null | grep -E '\.py$' || true)
  CHANGED_FE=$(git diff --name-only --diff-filter=ACMR $RANGE 2>/dev/null | grep -E '^frontend/' || true)
else
  # Full-tree mode: lint everything (not what CI does, but useful for
  # understanding total debt vs incremental delta).
  CHANGED_PY=$(find backend -name '*.py' -not -path '*/__pycache__/*')
  CHANGED_FE=""
fi

# --- Python lint: ruff check + ruff format (mirrors ci.yml: Ruff lint + format) ---
if [ -n "$CHANGED_PY" ]; then
  mapfile -t PY_FILES < <(echo "$CHANGED_PY" | sed '/^$/d')
  echo "── Python files (${#PY_FILES[@]}) ──"
  printf '  %s\n' "${PY_FILES[@]}"
  echo ""

  echo "── ruff check ──"
  if uvx ruff check "${PY_FILES[@]}" 2>&1; then
    echo "  PASSED"
  else
    FAILED=1
    echo "  FAILED (ruff check)"
  fi

  echo ""
  echo "── ruff format --check ──"
  if uvx ruff format --check "${PY_FILES[@]}" 2>&1; then
    echo "  PASSED"
  else
    FAILED=1
    echo "  FAILED (ruff format)"
  fi
else
  echo "── No Python files ── skipping ruff."
fi

echo ""

# --- Frontend lint (mirrors ci.yml: Frontend lint) ---
if [ -n "$CHANGED_FE" ]; then
  echo "── Frontend files ──"
  echo "$CHANGED_FE"
  echo ""

  echo "── npm ci + expo lint ──"
  cd frontend
  CI=true npm ci --silent 2>&1 | tail -3
  if CI=true npm run lint 2>&1; then
    echo "  PASSED"
  else
    FAILED=1
    echo "  FAILED (frontend lint)"
  fi
  cd "$REPO_ROOT"
else
  echo "── No frontend files changed ── skipping frontend lint."
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "=== lint: PASSED ==="
else
  echo "=== lint: FAILED ==="
fi
exit $FAILED