# Mobile runbook — running Sacrifice on a phone via Expo Go

The Sacrifice frontend is an Expo/React Native app. It runs three ways:
web (`expo start --web`), Android emulator (for automated E2E), and on a
physical iPhone/Android via **Expo Go** over a public tunnel. This runbook
covers the phone path and its host services.

## Architecture

```
iPhone (Expo Go)  ──exp://…exp.direct──▶  Metro bundler (host :8081)
       │
       └── app fetches API from EXPO_PUBLIC_API_URL
                     │
                     ▼
        Cloudflare tunnel (https://…trycloudflare.com)
                     │
                     ▼
              backend (host :8000, live DB)
```

Two independent tunnels: **Metro/Expo** ships the JS bundle to the phone;
**Cloudflare** exposes the backend API. The app is told the backend URL via
`EXPO_PUBLIC_API_URL` at `expo start` time (baked into the bundle).

## Host services (systemd --user, survive logout via `loginctl enable-linger`)

| Unit | Purpose | Port |
|------|---------|------|
| `sacrifice-backend.service` | FastAPI, live DB (`sacrifice_live`), OAuth env for ts.net | 8000 |
| `sacrifice-celery.service` | Proof-verification worker + beat | — |
| `sacrifice-frontend.service` | `expo start --web` | 8082 |
| `sacrifice-tunnel.service` | Cloudflare quick tunnel → backend | — |

```bash
systemctl --user status sacrifice-backend sacrifice-tunnel sacrifice-frontend sacrifice-celery
```

## Put the app on your iPhone

1. Install **Expo Go** from the App Store.
2. Start the Expo tunnel (writes URL + QR to `logs/expo-go-connection.txt`):
   ```bash
   make mobile-serve            # boots expo start --tunnel
   make mobile-serve-status     # confirms Metro + tunnel are up
   cat logs/expo-go-connection.txt
   ```
3. Scan the QR with the iPhone camera (or type the `exp://…` URL into Expo Go).
4. The app loads. Open the **Diagnostics** screen (dev builds) and confirm:
   resolved API URL = the current Cloudflare tunnel, backend health = OK,
   platform = iOS.

## The tunnel URLs rotate — the one gotcha

Cloudflare *quick* tunnels (no account) get a new hostname on every
`sacrifice-tunnel.service` restart, and Expo's `exp.direct` URL changes on
every `expo start`. When either rotates:

```bash
# refresh the backend URL the file/monitors read
grep -m1 -o 'https://[a-z0-9-]*\.trycloudflare\.com' logs/cloudflared.log > logs/tunnel-url.txt

# re-bake it into the app bundle and re-issue the QR
EXPO_PUBLIC_API_URL=$(cat logs/tunnel-url.txt) make mobile-serve
```

If the app on the phone shows a connectivity error, the baked API URL went
stale — re-run the two commands above and re-scan. A **named** Cloudflare
tunnel (free Cloudflare account) gives a permanent hostname and removes this
step entirely; recommended if the app is used regularly.

## Native E2E verification (Android emulator + Maestro)

Proves the same journey a phone takes, against the public tunnel:

```bash
# emulator (needs KVM: user in `kvm` group + setfacl -m u:$USER:rw /dev/kvm)
~/Android/sdk/emulator/emulator -avd factory_test -no-window -no-audio &
adb wait-for-device
API_URL=$(cat logs/tunnel-url.txt) make mobile-e2e   # Maestro flow in e2e/mobile/
```

## Troubleshooting

- **White screen / infinite spinner** → backend unreachable; check the
  Diagnostics screen and `systemctl --user status sacrifice-tunnel`.
- **OAuth "redirect_uri not associated"** → the backend must advertise the
  ts.net callback URLs; `make oauth-urls` prints the exact strings registered
  in the Google/GitHub consoles. Login round-trips only through the ts.net
  front door, not raw `localhost`.
- **Camera permission denied** → the proof flow falls back to the library
  picker; re-enable in iOS Settings → Expo Go → Camera.
- **Metro cache weirdness after a dependency change** → `expo start -c`.

## Backend tunnel — PERMANENT (named Cloudflare tunnel, 2026-07-18)

The backend is exposed at a STABLE hostname, **https://sacrifice.rentus.homes**,
via a dedicated named Cloudflare tunnel (`sacrifice`, id 4f412527…),
`~/.cloudflared/sacrifice-config.yml`, systemd unit `sacrifice-cf-named.service`.
It is fully isolated from the `rental-mgmt` tunnel that serves rentus.homes /
app.rentus.homes — separate tunnel, separate config, one dedicated DNS record
for `sacrifice.rentus.homes` only (the `*.rentus.homes` wildcard and the rental
app are untouched).

Because the hostname never rotates, the app's baked `EXPO_PUBLIC_API_URL`
(`logs/expo.env`) never goes stale — no re-bake, no re-scan. The old rotating
`trycloudflare.com` quick-tunnel and its `sync-tunnel-url.sh` auto-heal watcher
are retired (disabled; script kept for reference).
