#!/usr/bin/env bash
# Parity audit — inventory web-only API usage in shared (non-.web) TS/TSX code.
#
# Scans frontend/ for:
#   1. document.          (DOM-only)
#   2. window. outside of Platform.OS guard context
#   3. localStorage       (web-only storage)
#   4. DOM event types    (React.MouseEvent, HTMLInputElement, etc.)
#
# Excludes:
#   - node_modules
#   - __tests__
#   - e2e/     (playwright tests — expected to use browser APIs)
#   - *.web.ts, *.web.tsx files (web-only files are expected to use web APIs)
#
# Exit 0 when no violations found (pass). Exit 1 with a report when violations
# are detected (fail).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$ROOT/frontend"

cd "$FRONTEND"

VIOLATIONS=()

# Collect all .ts and .tsx files that are NOT .web.* and NOT in __tests__, e2e, or node_modules
mapfile -t FILES < <(
  find . -type f \( -name '*.ts' -o -name '*.tsx' \) \
    ! -path '*/node_modules/*' \
    ! -path '*/__tests__/*' \
    ! -path '*/e2e/*' \
    ! -name '*.web.ts' \
    ! -name '*.web.tsx' \
    ! -name '*.d.ts' \
    | sort
)

# Helper: check if a line at $lineno in $file is inside a platform guard
# Looks backward from the violation line to find the nearest enclosing guard
# pattern: Platform.OS check, typeof window/document check, or an early return.
_is_guarded_context() {
  local file=$1
  local lineno=$2
  local start=$((lineno - 50))
  [ "$start" -lt 1 ] && start=1

  # Extract lines from start to the violation line, then scan for guards
  # that appear before the violation and are NOT followed by a closing brace
  # of the same scope (simplified: just check if a guard exists within 50 lines)
  sed -n "${start},${lineno}p" "$file" 2>/dev/null | grep -qE "(Platform\.OS\s*(===|!==)\s*'web'|typeof window\s*(!==|===)\s*'undefined'|typeof document\s*(!==|===)\s*'undefined'|typeof localStorage\s*(===|!==)\s*'undefined')"
}

# --- document. usage ---
while IFS= read -r line; do
  file=$(echo "$line" | cut -d: -f1)
  lineno=$(echo "$line" | cut -d: -f2)
  if ! _is_guarded_context "$file" "$lineno"; then
    VIOLATIONS+=("document.: $file:$lineno")
  fi
done < <(grep -nH 'document\.' "${FILES[@]}" 2>/dev/null || true)

# --- localStorage usage ---
while IFS= read -r line; do
  file=$(echo "$line" | cut -d: -f1)
  lineno=$(echo "$line" | cut -d: -f2)
  if ! _is_guarded_context "$file" "$lineno"; then
    VIOLATIONS+=("localStorage: $file:$lineno")
  fi
done < <(grep -nH 'localStorage' "${FILES[@]}" 2>/dev/null || true)

# --- window. usage ---
while IFS= read -r line; do
  file=$(echo "$line" | cut -d: -f1)
  lineno=$(echo "$line" | cut -d: -f2)
  if ! _is_guarded_context "$file" "$lineno"; then
    VIOLATIONS+=("window. (unguarded): $file:$lineno")
  fi
done < <(grep -nH 'window\.' "${FILES[@]}" 2>/dev/null || true)

# --- DOM event types (React.MouseEvent, HTMLInputElement, etc.) ---
while IFS= read -r line; do
  file=$(echo "$line" | cut -d: -f1)
  lineno=$(echo "$line" | cut -d: -f2)
  if ! _is_guarded_context "$file" "$lineno"; then
    VIOLATIONS+=("DOM type: $file:$lineno")
  fi
done < <(grep -nH -E '(React\.(MouseEvent|KeyboardEvent|FocusEvent|DragEvent|ClipboardEvent|ChangeEvent|FormEvent|UIEvent|TouchEvent|WheelEvent)|HTML(Input|Button|Select|Anchor|Div|Span|TextArea|Image|Element)\b|Element\.prototype|HTMLElement\b)' "${FILES[@]}" 2>/dev/null || true)

# --- Report ---
if [ ${#VIOLATIONS[@]} -eq 0 ]; then
  echo "PASS: No unguarded web-only API usage found in shared code paths."
  exit 0
fi

echo "FAIL: Found ${#VIOLATIONS[@]} web-only API violation(s) in shared code paths:"
for v in "${VIOLATIONS[@]}"; do
  echo "  $v"
done
exit 1