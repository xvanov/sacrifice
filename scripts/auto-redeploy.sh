#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Sacrifice — host auto-redeploy script
# ──────────────────────────────────────────────────────────────────
# Idempotent redeploy mechanism for a deployed sacrifice host at
# /home/k/sacrifice.  Intended to be triggered by a poll timer
# (cron / systemd timer) and invoked as root or a user with docker
# and journalctl access.
#
# ── What it does ──────────────────────────────────────────────────
# 1. Checks the deploy gate (deploy.enabled in config.yaml).
#    Exits cleanly when disabled — this is the disable procedure.
# 2. Fetches origin/main.
# 3. Compares the local checkout HEAD to origin/main.
#    Exits cleanly when already current (idempotent).
# 4. Fast-forwards to origin/main on genuine advance only.
# 5. Restarts the four sacrifice-* user services:
#      sacrifice-backend   (uvicorn, port 8000)
#      sacrifice-frontend  (expo web, port 8082)
#      sacrifice-celery    (background worker)
#      sacrifice-expo-go   (expo tunnel for Expo Go)
# 6. Runs post-restart health check: curl -fsS http://localhost:8000/healthz
#    Retries up to HEALTH_MAX_ATTEMPTS × HEALTH_INTERVAL seconds.
# 7. On health failure: emits alert to stderr + system log; does NOT
#    leave services broken — attempts rollback to previous HEAD.
# 8. Logs every action to stdout (captured by systemd/journald or
#    cron redirect).
#
# ── Trigger ────────────────────────────────────────────────────────
# Poll timer: a cron entry or systemd timer invokes this script every
# N minutes.  Example cron entry (every 2 minutes):
#   */2 * * * * /home/k/sacrifice/scripts/auto-redeploy.sh >> /var/log/sacrifice-auto-redeploy.log 2>&1
#
# ── Disable procedure ──────────────────────────────────────────────
# Set deploy.enabled: false in the factory config:
#   python3 scripts/verify_deploy_lib.py gate-apply --force-disable --reason "maintenance"
# The script exits cleanly on next poll when deploy.enabled is false.
#
# ── Logs and failure signals ───────────────────────────────────────
# - All actions are written to stdout/stderr; capture via the cron
#   redirect above or journalctl when run as a systemd service.
# - Health-check failures are alerted to stderr prefixed with
#   "AUTO_REDEPLOY_ALERT:" for easy grep/alerting integration.
# - Post-mortem: the last 50 lines of each service log are dumped.
#
# ── Locking ────────────────────────────────────────────────────────
# A lock file (auto-redeploy.lock) prevents concurrent runs. If the
# lock is stale (> LOCK_STALE_MINUTES), it is broken.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────

SACRIFICE_DIR="${SACRIFICE_DIR:-/home/k/sacrifice}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/healthz}"
HEALTH_MAX_ATTEMPTS="${HEALTH_MAX_ATTEMPTS:-15}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-2}"
LOCK_FILE="${LOCK_FILE:-/tmp/sacrifice-auto-redeploy.lock}"
LOCK_STALE_MINUTES="${LOCK_STALE_MINUTES:-10}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-main}"
LOG_PREFIX="[auto-redeploy]"

# ── Helpers ────────────────────────────────────────────────────────

log()  { echo "$LOG_PREFIX $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
alert() {
  echo "AUTO_REDEPLOY_ALERT: $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2
  logger -t sacrifice-auto-redeploy -p daemon.err "ALERT: $*" 2>/dev/null || true
}
die() {
  alert "$@"
  log "FATAL: $*"
  exit 1
}

# ── Locking ────────────────────────────────────────────────────────

acquire_lock() {
  if [ -f "$LOCK_FILE" ]; then
    local now
    now=$(date +%s)
    local lock_mtime
    lock_mtime=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
    local staleness=$(( (now - lock_mtime) / 60 ))
    if [ "$staleness" -gt "$LOCK_STALE_MINUTES" ]; then
      log "breaking stale lock file (${staleness}m old, threshold ${LOCK_STALE_MINUTES}m)"
      rm -f "$LOCK_FILE"
    else
      log "lock file exists (${staleness}m old) — another redeploy is in progress; exiting"
      exit 0
    fi
  fi
  echo $$ > "$LOCK_FILE"
}

release_lock() {
  rm -f "$LOCK_FILE"
}

# ── Deploy gate check ──────────────────────────────────────────────

check_deploy_gate() {
  local config_script="$SACRIFICE_DIR/scripts/verify_deploy_lib.py"
  if [ ! -f "$config_script" ]; then
    # If the lib isn't available, default to enabled (fail open for deploy)
    log "verify_deploy_lib.py not found at $config_script; assuming deploy enabled"
    return 0
  fi
  local enabled
  enabled=$(python3 -c "
import sys; sys.path.insert(0, '$SACRIFICE_DIR/scripts')
from verify_deploy_lib import get_deploy_enabled
print('true' if get_deploy_enabled() else 'false')
" 2>/dev/null || echo "true")
  if [ "$enabled" = "false" ]; then
    log "deploy.enabled is false — auto-redeploy disabled; exiting"
    exit 0
  fi
  log "deploy gate: enabled"
}

# ── Git operations ─────────────────────────────────────────────────

fetch_and_detect() {
  cd "$SACRIFICE_DIR"

  local local_head remote_head

  log "fetching $GIT_REMOTE/$GIT_BRANCH..."
  git fetch "$GIT_REMOTE" "$GIT_BRANCH" --quiet

  local_head=$(git rev-parse HEAD)
  remote_head=$(git rev-parse "$GIT_REMOTE/$GIT_BRANCH")

  log "local HEAD:  $local_head"
  log "remote HEAD: $remote_head"

  if [ "$local_head" = "$remote_head" ]; then
    log "already at $GIT_REMOTE/$GIT_BRANCH — nothing to do"
    return 1  # no deploy needed
  fi

  # Verify this is a fast-forward (genuine advance, no diverged history)
  if ! git merge-base --is-ancestor "$local_head" "$remote_head"; then
    alert "local HEAD ($local_head) is not an ancestor of $GIT_REMOTE/$GIT_BRANCH ($remote_head) — refusing to deploy (not a fast-forward)"
    return 2  # diverged/non-ff is a failure condition
  fi

  log "genuine advance detected: $local_head → $remote_head"
  return 0
}

fast_forward() {
  cd "$SACRIFICE_DIR"

  local previous_head
  previous_head=$(git rev-parse HEAD)

  log "fast-forwarding to $GIT_REMOTE/$GIT_BRANCH..."
  if git merge --ff-only "$GIT_REMOTE/$GIT_BRANCH" 2>&1; then
    log "fast-forward succeeded"
    # Save previous HEAD for rollback
    echo "$previous_head" > /tmp/sacrifice-auto-redeploy-previous-head
    return 0
  else
    alert "fast-forward failed"
    return 1
  fi
}

rollback_checkout() {
  cd "$SACRIFICE_DIR"
  local previous_head
  previous_head=$(cat /tmp/sacrifice-auto-redeploy-previous-head 2>/dev/null || echo "")
  if [ -z "$previous_head" ]; then
    alert "no previous HEAD recorded; cannot rollback"
    return 1
  fi
  log "rolling back to previous HEAD: $previous_head"
  if git checkout "$previous_head" 2>&1; then
    log "rollback succeeded — checkout at $previous_head"
  else
    alert "rollback checkout failed — services may be at wrong revision"
    return 1
  fi
}

# ── Service restart ────────────────────────────────────────────────

restart_services() {
  log "restarting four sacrifice-* user services..."

  local failed=""

  restart_backend() {
    log "restarting sacrifice-backend (port 8000)..."
    local pids
    pids=$(lsof -ti :8000 2>/dev/null || true)
    if [ -n "$pids" ]; then
      kill $pids 2>/dev/null || true
      sleep 2
      # Hard-kill survivors
      local survivors
      survivors=$(lsof -ti :8000 2>/dev/null || true)
      if [ -n "$survivors" ]; then
        kill -9 $survivors 2>/dev/null || true
      fi
    fi
    # Restart via Makefile
    if make -C "$SACRIFICE_DIR" up-backend >> /tmp/sacrifice-auto-redeploy-backend.log 2>&1; then
      log "sacrifice-backend restarted"
    else
      alert "sacrifice-backend restart failed"
      failed+="backend "
    fi
  }

  restart_frontend() {
    log "restarting sacrifice-frontend (port 8082)..."
    local pids
    pids=$(lsof -ti :8082 2>/dev/null || true)
    if [ -n "$pids" ]; then
      kill $pids 2>/dev/null || true
      sleep 2
      local survivors
      survivors=$(lsof -ti :8082 2>/dev/null || true)
      if [ -n "$survivors" ]; then
        kill -9 $survivors 2>/dev/null || true
      fi
    fi
    if make -C "$SACRIFICE_DIR" up-frontend >> /tmp/sacrifice-auto-redeploy-frontend.log 2>&1; then
      log "sacrifice-frontend restarted"
    else
      alert "sacrifice-frontend restart failed"
      failed+="frontend "
    fi
  }

  restart_celery() {
    log "restarting sacrifice-celery..."
    local pids
    pids=$(pgrep -af "celery.*worker" 2>/dev/null | grep -v pgrep | awk '{print $1}' || true)
    if [ -n "$pids" ]; then
      kill $pids 2>/dev/null || true
      sleep 1
      local survivors
      survivors=$(pgrep -af "celery.*worker" 2>/dev/null | grep -v pgrep | awk '{print $1}' || true)
      if [ -n "$survivors" ]; then
        kill -9 $survivors 2>/dev/null || true
      fi
      log "sacrifice-celery stopped"
    fi
    # Start fresh — make celery from the Makefile
    if make -C "$SACRIFICE_DIR" celery >> /tmp/sacrifice-auto-redeploy-celery.log 2>&1; then
      log "sacrifice-celery restarted"
    else
      alert "sacrifice-celery restart failed"
      failed+="celery "
    fi
  }

  restart_expo_go() {
    log "restarting sacrifice-expo-go (tunnel)..."
    local pids
    pids=$(pgrep -f "expo start --tunnel" 2>/dev/null | grep -v pgrep | awk '{print $1}' || true)
    if [ -n "$pids" ]; then
      kill $pids 2>/dev/null || true
      sleep 1
      local survivors
      survivors=$(pgrep -f "expo start --tunnel" 2>/dev/null | grep -v pgrep | awk '{print $1}' || true)
      if [ -n "$survivors" ]; then
        kill -9 $survivors 2>/dev/null || true
      fi
      log "sacrifice-expo-go stopped"
    fi
    if make -C "$SACRIFICE_DIR" mobile-serve >> /tmp/sacrifice-auto-redeploy-expo-go.log 2>&1; then
      log "sacrifice-expo-go restarted"
    else
      alert "sacrifice-expo-go restart failed"
      failed+="expo-go "
    fi
  }

  restart_backend
  restart_frontend
  restart_celery
  restart_expo_go

  if [ -n "$failed" ]; then
    alert "service restart failures: $failed"
    return 1
  fi

  log "all four services restarted"
  return 0
}

# ── Health check ───────────────────────────────────────────────────

run_health_check() {
  log "running post-restart health check: curl -fsS $HEALTH_URL"
  local attempt
  for attempt in $(seq 1 "$HEALTH_MAX_ATTEMPTS"); do
    log "health check attempt $attempt/$HEALTH_MAX_ATTEMPTS..."
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      log "health check PASSED (attempt $attempt)"
      return 0
    fi
    log "health check not ready (attempt $attempt)"
    sleep "$HEALTH_INTERVAL"
  done

  alert "health check FAILED after $HEALTH_MAX_ATTEMPTS attempts (${HEALTH_INTERVAL}s interval)"
  return 1
}

dump_service_logs() {
  log "── dumping last 50 lines of service logs for post-mortem ──"
  for logfile in /tmp/sacrifice-auto-redeploy-backend.log /tmp/sacrifice-auto-redeploy-frontend.log /tmp/sacrifice-auto-redeploy-celery.log /tmp/sacrifice-auto-redeploy-expo-go.log; do
    if [ -f "$logfile" ]; then
      log "── $logfile ──"
      tail -50 "$logfile" 2>/dev/null || true
    fi
  done
}

# ── Main ───────────────────────────────────────────────────────────

main() {
  log "auto-redeploy run starting"

  acquire_lock
  trap release_lock EXIT

  check_deploy_gate

  detect_rc=0
  fetch_and_detect || detect_rc=$?
  if [ "$detect_rc" -eq 1 ]; then
    # Already current — idempotent exit
    log "no deploy needed — idempotent exit"
    exit 0
  elif [ "$detect_rc" -eq 2 ]; then
    # Not fast-forward (diverged history) — failure condition
    die "diverged history — refusing to deploy (not a fast-forward)"
  elif [ "$detect_rc" -ne 0 ]; then
    die "fetch_and_detect failed with unknown exit code $detect_rc"
  fi

  if ! fast_forward; then
    die "fast-forward failed — manual intervention required"
  fi

  if ! restart_services; then
    alert "service restart had failures — rolling back checkout"
    rollback_checkout
    restart_services || alert "rollback restart also had failures"
    die "deploy failed during service restart — rolled back to previous HEAD"
  fi

  if ! run_health_check; then
    alert "health check failed after restart — rolling back"
    dump_service_logs
    rollback_checkout
    restart_services || alert "rollback restart also had failures"
    die "deploy failed on health check — rolled back to previous HEAD"
  fi

  log "auto-redeploy completed successfully"
  log "deployed revision: $(cd "$SACRIFICE_DIR" && git rev-parse HEAD)"
}

main "$@"