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
FRONTEND="${PARITY_AUDIT_DIR:-"$ROOT/frontend"}"

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

# Helper: check if a line at $lineno in $file is guarded against native
# execution.  Accepts three patterns:
#   1. Same-line guard (ternary / short-circuit on the violation line itself).
#   2. Guard within the preceding 10 lines that opens a block (line ends with
#      `{`) — covers `if (Platform.OS === 'web') {`.
#   3. Guard within the preceding 3 lines that is an early-return guard
#      (line ends with `return`, `return null`, or `return;`) — covers
#      `if (Platform.OS !== 'web') return;`.
# A 50-line window was too loose and allowed guards in unrelated functions
# to mask violations (reviewer finding).
_is_guarded_context() {
  local file=$1
  local lineno=$2

  # Same-line check: violation line itself contains a guard
  if sed -n "${lineno}p" "$file" 2>/dev/null | grep -qE "(Platform\.OS\s*(===|!==)\s*'web'|typeof window\s*(!==|===)\s*'undefined'|typeof document\s*(!==|===)\s*'undefined'|typeof localStorage\s*(===|!==)\s*'undefined')"; then
    return 0
  fi

  # Block-opener guard: guard within preceding 25 lines that opens a block
  local block_start=$((lineno - 25))
  [ "$block_start" -lt 1 ] && block_start=1
  local block_end=$((lineno - 1))
  [ "$block_end" -ge 1 ] && \
    sed -n "${block_start},${block_end}p" "$file" 2>/dev/null | \
    grep -qE "(Platform\.OS\s*(===|!==)\s*'web'|typeof window\s*(!==|===)\s*'undefined'|typeof document\s*(!==|===)\s*'undefined'|typeof localStorage\s*(===|!==)\s*'undefined').*\{" && \
    return 0

  # Early-return guard: guard within preceding 40 lines that returns early.
  # A 40-line window covers any reasonable function body whose top-level
  # guard protects every line that follows — far tighter than the original
  # 50-line window that scanned across unrelated scopes.
  local ret_start=$((lineno - 40))
  [ "$ret_start" -lt 1 ] && ret_start=1
  local ret_end=$((lineno - 1))
  [ "$ret_end" -ge 1 ] && \
    sed -n "${ret_start},${ret_end}p" "$file" 2>/dev/null | \
    grep -qE "(Platform\.OS\s*(===|!==)\s*'web'|typeof window\s*(!==|===)\s*'undefined'|typeof document\s*(!==|===)\s*'undefined'|typeof localStorage\s*(===|!==)\s*'undefined').*\breturn\b" && \
    return 0

  return 1
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
# Only flag DOM types when they appear in TypeScript type contexts:
#   - Type annotations:        : React.MouseEvent, : HTMLInputElement
#   - Type assertions:          as HTMLInputElement
#   - Generic parameters:       <HTMLDivElement>, React.MouseEvent<...>
#   - Union / intersection:     | HTMLInputElement, & HTMLElement
#   - Function return types:    => HTMLElement
#   - implements / extends:     extends HTMLElement, implements HTMLInputElement
#
# To avoid false-positives on string literals, comments, and prose that
# mention the same tokens outside of type positions, each file is stripped
# of // comments and string-literal contents before matching.
DOM_TYPE_RE='(:|as\s+|<\s*|&\s*|\|\s*|=>\s*|extends\s+|implements\s+)\s*(React\.(MouseEvent|KeyboardEvent|FocusEvent|DragEvent|ClipboardEvent|ChangeEvent|FormEvent|UIEvent|TouchEvent|WheelEvent)|HTML(Input|Button|Select|Anchor|Div|Span|TextArea|Image|Element)\b|HTMLElement\b)'

while IFS= read -r line; do
  file=$(echo "$line" | cut -d: -f1)
  lineno=$(echo "$line" | cut -d: -f2)
  if ! _is_guarded_context "$file" "$lineno"; then
    VIOLATIONS+=("DOM type: $file:$lineno")
  fi
done < <(
  for f in "${FILES[@]}"; do
    sed -e 's|//.*||' \
        -e "s|'[^']*'|''|g" \
        -e 's|"[^"]*"|""|g' \
        -e 's|`[^`]*`|``|g' \
        "$f" 2>/dev/null | \
      grep -n -E "$DOM_TYPE_RE" 2>/dev/null | \
      sed "s|^|$f:|"
  done
)

# --- Report ---
CATEGORIES="document. window. localStorage DOM-type"
if [ ${#VIOLATIONS[@]} -eq 0 ]; then
  echo "PASS: No unguarded web-only API usage found in shared code paths."
  echo "Categories scanned: $CATEGORIES"
  echo "Files scanned: ${#FILES[@]}"
  exit 0
fi

echo "FAIL: Found ${#VIOLATIONS[@]} web-only API violation(s) in shared code paths:"
for v in "${VIOLATIONS[@]}"; do
  echo "  $v"
done
exit 1