#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Close the unauthenticated-broker exposure on the host redis-server.
#
# THE PROBLEM (verified 2026-07-25):
#   redis-server listens on 172.17.0.1:6379 — the Docker bridge gateway — with
#   no `requirepass`. Every container on the default bridge can therefore talk
#   to it, and it is the live Celery broker (`.env` REDIS_URL). The dev_sandbox
#   goal type runs repo-authored code during dependency install (`pip install
#   -e .`, setup.py build hooks, npm postinstall) with the network attached, so
#   untrusted code can enqueue arbitrary Celery tasks. Those tasks execute in
#   the worker, which runs as a user in the `docker` group with sudo — i.e. the
#   step from "reach the broker" to "own the host" is short.
#
#   Verified from inside a default-bridge container:
#     REACHABLE  172.17.0.1:6379   (PING -> +PONG, no auth)
#
# WHAT THIS DOES
#   1. Backs up redis.conf.
#   2. Drops the bridge-gateway bind, keeping loopback only.
#   3. Sets a generated `requirepass`.
#   4. Updates REDIS_URL in .env to carry the password.
#   5. Restarts redis, then the backend and celery worker so they pick up the
#      new URL.
#
# ORDER MATTERS: .env is written BEFORE redis restarts, because the running
# uvicorn/celery hold their config in memory and only re-read it on restart.
#
# ROLLBACK: restore the printed redis.conf backup, `git checkout .env` (or
# restore the printed .env backup), then `sudo systemctl restart redis-server`
# and re-run `make up && make celery`.
#
# Run:  sudo bash scripts/secure_redis_broker.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script edits /etc/redis/redis.conf and restarts redis — run it with sudo." >&2
  exit 1
fi

REDIS_CONF=${REDIS_CONF:-/etc/redis/redis.conf}
REPO_DIR=${REPO_DIR:-/home/k/sacrifice}
ENV_FILE="$REPO_DIR/.env"
STAMP=$(date +%Y%m%d-%H%M%S)
# The unprivileged user that owns the app processes (sudo makes $USER root).
APP_USER=${APP_USER:-$(stat -c '%U' "$ENV_FILE")}

[[ -f "$REDIS_CONF" ]] || { echo "not found: $REDIS_CONF" >&2; exit 1; }
[[ -f "$ENV_FILE" ]]   || { echo "not found: $ENV_FILE" >&2; exit 1; }

# Reuse an existing password if this script already ran, so re-running is safe.
PASS=$(grep -oP '^\s*requirepass\s+\K\S+' "$REDIS_CONF" | tail -1 || true)
if [[ -z "$PASS" ]]; then
  PASS=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)
  echo "[redis] generated a new password"
else
  echo "[redis] reusing the existing requirepass"
fi

cp -a "$REDIS_CONF" "$REDIS_CONF.bak-$STAMP"
cp -a "$ENV_FILE"   "$ENV_FILE.bak-$STAMP"
echo "[backup] $REDIS_CONF.bak-$STAMP"
echo "[backup] $ENV_FILE.bak-$STAMP"

# ── 1. bind: loopback only ────────────────────────────────────────────────
# Comment out every existing bind line, then append an explicit loopback bind.
sed -i -E 's/^([[:space:]]*bind[[:space:]].*)$/# [secured] \1/' "$REDIS_CONF"
sed -i -E 's/^([[:space:]]*requirepass[[:space:]].*)$/# [secured] \1/' "$REDIS_CONF"
cat >> "$REDIS_CONF" <<EOF

# ── Added by scripts/secure_redis_broker.sh on $STAMP ──
# Loopback only: this is the live Celery broker, and binding the Docker bridge
# gateway (172.17.0.1) let any container on the default bridge enqueue tasks
# that execute in a worker with docker-group access. Do NOT re-add a bridge or
# 0.0.0.0 bind. If a container genuinely needs this broker, put it on a
# user-defined network with the broker, or run redis inside compose.
bind 127.0.0.1 -::1
protected-mode yes
requirepass $PASS
EOF
echo "[redis] bind -> loopback only; requirepass set"

# ── 2. .env: carry the password in REDIS_URL ──────────────────────────────
# URL-encode the few characters that would break URL parsing (the generator
# strips /+= already, so this is belt-and-braces).
ENC_PASS=$(printf '%s' "$PASS" | sed -e 's/%/%25/g' -e 's/@/%40/g' -e 's/:/%3A/g')
if grep -qE '^REDIS_URL=' "$ENV_FILE"; then
  sed -i -E "s#^REDIS_URL=.*#REDIS_URL=redis://:${ENC_PASS}@localhost:6379/0#" "$ENV_FILE"
else
  printf '\nREDIS_URL=redis://:%s@localhost:6379/0\n' "$ENC_PASS" >> "$ENV_FILE"
fi
chown "$APP_USER":"$APP_USER" "$ENV_FILE"
echo "[env] REDIS_URL updated (password redacted)"

# ── 3. restart redis, then the app ────────────────────────────────────────
systemctl restart redis-server
sleep 2
if redis-cli -a "$PASS" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
  echo "[verify] authenticated PING ok"
else
  echo "[verify] FAILED: authenticated ping did not return PONG" >&2
  echo "         restore with: cp -a $REDIS_CONF.bak-$STAMP $REDIS_CONF && systemctl restart redis-server" >&2
  exit 1
fi
if redis-cli ping 2>&1 | grep -qi 'NOAUTH\|AUTH'; then
  echo "[verify] unauthenticated access is refused"
else
  echo "[verify] WARNING: redis answered without auth — inspect $REDIS_CONF" >&2
fi

echo "[app] restarting backend + celery as $APP_USER so they pick up the new REDIS_URL"
sudo -u "$APP_USER" make -C "$REPO_DIR" stop-celery   >/dev/null 2>&1 || true
sudo -u "$APP_USER" make -C "$REPO_DIR" down-backend  >/dev/null 2>&1 || true
sudo -u "$APP_USER" make -C "$REPO_DIR" up-backend    >/dev/null 2>&1 || true
sudo -u "$APP_USER" make -C "$REPO_DIR" celery        >/dev/null 2>&1 || true
sleep 6

code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/health || true)
echo "[verify] backend /api/health -> $code"
[[ "$code" == "200" ]] || echo "         check $REPO_DIR/logs/backend.log" >&2

echo
echo "Done. The bridge-gateway listener is gone; confirm with:"
echo "  ss -ltn | grep 6379          # expect only 127.0.0.1 / ::1"
echo "  docker run --rm redis:7-alpine redis-cli -h 172.17.0.1 ping   # expect a failure"
