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
# execution.  Accepts two patterns:
#   1. Same-line guard (ternary / short-circuit on the violation line itself).
#   2. Enclosing-scope guard found by scanning backwards from the violation
#      line while tracking brace depth and function boundaries.  The scan
#      stops when it exits the enclosing function (depth < 0) or crosses a
#      named-function boundary.  This replaces the old fixed-window grep with
#      true scope-aware parsing — only guards in the same active scope can
#      satisfy the check.
_is_guarded_context() {
  local file=$1
  local lineno=$2

  # Same-line check: violation line itself contains a guard
  if sed -n "${lineno}p" "$file" 2>/dev/null | grep -qE "(Platform\.OS\s*(===|!==)\s*'web'|typeof window\s*(!==|===)\s*'undefined'|typeof document\s*(!==|===)\s*'undefined'|typeof localStorage\s*(===|!==)\s*'undefined')"; then
    return 0
  fi

  # Scan backwards from the violation line, tracking brace depth and
  # function boundaries.  Because we scan backwards:
  #   - '}' increases depth (we are entering a block going backwards)
  #   - '{' decreases depth (we are leaving a block going backwards)
  #   - depth can go negative — that just means we exited the violation's
  #     innermost block and are now scanning the enclosing scope
  #   - a named function at depth 0 is a scope boundary → stop, no guard
  #   - a Platform.OS guard found at any depth protects the violation if
  #     the forward scope check confirms the guard's block encloses it
  local depth=0
  local i=$((lineno - 1))

  while [ "$i" -ge 1 ]; do
    local line
    line=$(sed -n "${i}p" "$file" 2>/dev/null)

    # Detect named-function boundaries before adjusting depth.
    # A named function at depth 0 starts a new enclosing scope that
    # any guard above it cannot protect.  Arrow functions (=>) do NOT
    # create scope boundaries — a guard in the enclosing scope still
    # protects code inside an arrow callback.
    #
    # Strip string literals before matching so quoted 'function' (e.g.
    # typeof x === 'function') does not create a false boundary.
    local stripped
    stripped=$(echo "$line" | sed -e "s|'[^']*'|''|g" -e 's|"[^"]*"|""|g' -e 's|`[^`]*`|``|g')
    if [ "$depth" -eq 0 ] && echo "$stripped" | grep -qE '\bfunction\b'; then
      return 1  # crossed function boundary, no guard found
    fi

    # Adjust depth (backwards: '}' opens, '{' closes)
    local opens=$(echo "$line" | tr -cd '}' | wc -c)
    local closes=$(echo "$line" | tr -cd '{' | wc -c)
    depth=$((depth + opens - closes))

    # Check for a Platform.OS / typeof guard on this line.
    # Two patterns:
    #   a) Block-opener guard: `if (Platform.OS === 'web') {` or similar
    #      with `{` on the same line.  Protects everything inside its block
    #      AND (when the block contains a return/throw) the rest of the
    #      enclosing function.
    #   b) Early-return guard: `if (Platform.OS !== 'web') return;` or
    #      similar with `return` on the same line, no opening brace.
    #      Protects everything below in the same function.
    if echo "$line" | grep -qE "(Platform\.OS\s*(===|!==)\s*'web'|typeof window\s*(!==|===)\s*'undefined'|typeof document\s*(!==|===)\s*'undefined'|typeof localStorage\s*(===|!==)\s*'undefined')"; then
      # Guard line found.  Determine if it is a block-opener or early-return
      # guard by checking for '{' (block opener) or 'return' on the same line.
      local has_brace=$(echo "$line" | tr -cd '{' | wc -c)
      local has_return=$(echo "$line" | grep -c '\breturn\b' || true)

      if [ "$has_brace" -gt 0 ]; then
        # Block-opener guard.  The violation is guarded if, scanning
        # forward from the guard, the guard's block (or a containing
        # block with a return) still encloses the violation.
        if _scope_contains_forward "$file" "$i" "$lineno"; then
          return 0
        fi
        # If the block doesn't enclose the violation, this guard doesn't
        # protect it — keep scanning backwards for another guard.
      elif [ "$has_return" -gt 0 ]; then
        # Early-return guard.  Protects everything below in the same
        # function.  Since we found it while scanning backwards and
        # we haven't crossed a function boundary, it protects us.
        return 0
      fi
      # If the guard line has neither '{' nor 'return', it might be a
      # same-line ternary guard (e.g. `Platform.OS === 'web' ? ... : ...`).
      # But those would be caught by the same-line check above since the
      # violation would be on the same line.  If we get here, skip it.
    fi

    i=$((i - 1))
  done

  return 1
}

# Check whether $violation_line is in the same scope as $guard_line.
# Tracks brace depth from the guard line to the violation line.  If the
# depth returns to zero before the violation, the block (and function)
# containing the guard has closed — the violation is in a different scope.
#
# Special case: when a block guard contains a return/throw/exit statement
# before its closing brace, the guard is effectively an early-return guard
# that protects the remainder of the enclosing function.  In that case we
# reset depth to 1 (function body) and only fail when the function closes.
_scope_contains_forward() {
  local file=$1
  local guard_line=$2
  local violation_line=$3

  local start=$((guard_line + 1))
  local end=$((violation_line - 1))
  [ "$start" -gt "$end" ] && return 0  # adjacent lines, same scope

  # Compute initial depth from the guard line itself.
  local guard_content
  guard_content=$(sed -n "${guard_line}p" "$file" 2>/dev/null)
  local depth=0
  local opens=$(echo "$guard_content" | tr -cd '{' | wc -c)
  local closes=$(echo "$guard_content" | tr -cd '}' | wc -c)
  depth=$((depth + opens - closes))

  # If the guard line opens a block, peek ahead to see whether the block
  # contains a return/throw before it closes.  If it does, the guard is
  # effectively an early-return guard — it protects everything below it
  # in the same function, not just its own block.
  if [ "$opens" -gt 0 ] && [ "$opens" -gt "$closes" ]; then
    local peek_depth=$depth
    local peek_line=$start
    local found_exit=0
    while [ "$peek_line" -le "$end" ]; do
      local pline
      pline=$(sed -n "${peek_line}p" "$file" 2>/dev/null)
      local po=$(echo "$pline" | tr -cd '{' | wc -c)
      local pc=$(echo "$pline" | tr -cd '}' | wc -c)
      peek_depth=$((peek_depth + po - pc))
      if echo "$pline" | grep -qE '\b(return|throw)\b'; then
        found_exit=1
        break
      fi
      if [ "$peek_depth" -le 0 ]; then
        break  # block closed without finding a return
      fi
      peek_line=$((peek_line + 1))
    done
    if [ "$found_exit" -eq 1 ]; then
      # Treat as early-return guard: add one level for the enclosing
      # function body on top of the guard's own block depth.
      depth=$((depth + 1))
    fi
  elif [ "$opens" -eq 0 ] && [ "$closes" -eq 0 ]; then
    # No braces on guard line — early-return guard. Start inside function body.
    depth=1
  fi
  # (If opens > 0 but block has no return, depth stays at block depth and
  # the scope closes when the block closes — correct for non-return blocks.)

  while IFS= read -r line; do
    # Detect nested function definitions: if we encounter a function
    # keyword at the enclosing function-body depth (depth=1),
    # the violation is inside a nested scope the guard cannot protect.
    # We check BEFORE adjusting depth so we catch the function opener.
    # Only \bfunction\b (named function declarations), not => (arrows),
    # creates a scope boundary — arrows defined inside a guarded scope
    # inherit the guard's protection.
    if [ "$depth" -eq 1 ] && echo "$line" | grep -qE '\bfunction\b'; then
      return 1  # nested named-function boundary — guard does not enclose violation
    fi
    local o=$(echo "$line" | tr -cd '{' | wc -c)
    local c=$(echo "$line" | tr -cd '}' | wc -c)
    depth=$((depth + o - c))
    if [ "$depth" -le 0 ]; then
      return 1  # scope closed before reaching violation
    fi
  done < <(sed -n "${start},${end}p" "$file" 2>/dev/null)

  return 0  # scope still open
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