#!/usr/bin/env bash
# Auto-heal the Expo Go app when the Cloudflare quick-tunnel URL rotates.
#
# Cloudflare account-less "quick" tunnels get a NEW hostname whenever
# sacrifice-tunnel.service restarts (crash, reboot, manual restart). The
# Expo app bakes the backend URL (EXPO_PUBLIC_API_URL) into its JS bundle at
# Metro-start time, so after a rotation the app on the phone points at a dead
# backend. This watcher detects the change and re-bakes:
#   1. write the new URL to logs/tunnel-url.txt + logs/expo.env
#   2. restart sacrifice-expo-go.service (Metro re-bundles with the new URL)
# The exp:// project URL + QR are machine-stable and DON'T change — the user
# just reopens the app in Expo Go; no re-scan needed.
#
# Idempotent: exits 0 with no action when the URL is unchanged or the tunnel
# isn't up yet. Safe to run on a 60s timer.
set -euo pipefail

LOG_DIR="/home/k/sacrifice/logs"
CUR="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/cloudflared.log" 2>/dev/null | tail -1 || true)"
[ -z "$CUR" ] && exit 0                       # tunnel not registered yet

PREV="$(cat "$LOG_DIR/tunnel-url.txt" 2>/dev/null || true)"
[ "$CUR" = "$PREV" ] && exit 0                # no rotation — nothing to do

# Rotation detected — re-bake.
echo "$CUR" > "$LOG_DIR/tunnel-url.txt"
echo "EXPO_PUBLIC_API_URL=$CUR" > "$LOG_DIR/expo.env"
logger -t sacrifice-tunnel-sync "cloudflare URL rotated -> $CUR; re-baking Expo Go"
systemctl --user restart sacrifice-expo-go.service || true
