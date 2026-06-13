# Sacrifice — Integration Smoke Handoff (2026-06-13)

First end-to-end exercise of the app against a live backend. Summary: **the
core flow works in the local browser** (login → chat goal creation → goal
created → proof upload). OAuth (Google/GitHub) needs external console/config
work you must do. Below: what works, how to reproduce, what I fixed, and
what still needs you.

Branch with fixes: `fix/integration-smoke-auth-chat` (4 commits, not pushed).

---

## Running state (leave it as-is)

| Service   | Where                          | Check |
|-----------|--------------------------------|-------|
| Backend   | `:8000` (binds 0.0.0.0)        | `curl http://localhost:8000/api/health` → `{"status":"ok"}` |
| Postgres  | docker `sacrifice-db` `:5433`  | — |
| Redis     | `:6379`                        | — |
| Expo/Metro| `:8090` (pid 265459)           | `curl -o /dev/null -w '%{http_code}' http://10.110.1.68:8090` → 200 |

- Backend now runs from `/home/k/sacrifice/backend` with its healthy venv
  (it had been serving a **deleted** factory worktree — see fixes).
- Expo web app baked `EXPO_PUBLIC_API_URL=http://10.110.1.68:8000`.
- `:8081` is **NOT ours** — it's an unrelated `budget-app-v2-frontend` Docker
  container. Leave it alone.

---

## 1) Local browser smoke — WORKS ✅

Open the web app at **http://localhost:8090** (origin `localhost:8090` is
CORS-allowed; the app calls the LAN backend `10.110.1.68:8000`).

### Login — use Email (recommended) or the dev-token bypass

**Email register / login (works):**
1. On the login screen, use the email form. Pick any email + password
   (≥ the app's min). Register, or log in if already registered.
2. You land on the Home screen.

Reproduce via API:
```bash
curl -X POST http://localhost:8000/api/auth/email/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"Passw0rd!23"}'   # → 200 + token
```

**Dev-token bypass (works, debug only):** if you just want past auth:
```bash
curl "http://localhost:8000/api/auth/dev/token?email=you@example.com"
```
In the browser console on the app:
```js
localStorage.setItem('sacrifice_auth_token', '<access_token>'); location.reload();
```
(Now resilient: it no longer 500s if that email already registered via email.)

### Core flow — chat goal creation (works end-to-end)

Tap **+ New** → chat opens. Then:
1. Type: *"I want to upload a YouTube walkthrough of my project by Friday"* →
   assistant shows a **match card** (youtube_video, confidence ~95%, missing
   criteria) with a **Use this** button.
2. Tap **Use this** → it asks for **pledge** ("How much do you want to
   pledge?") → answer `20`.
3. Asks **charity** → answer e.g. `Doctors Without Borders`.
4. Asks **min video length** → answer `60`.
5. **Ready to create** card → tap **Create goal** → success message
   *"Your goal is created and active…"*. The goal is now in `GET /api/goals`.
   (Note: the app stays on the chat screen with a success message; it does
   not auto-navigate to the goal detail.)

No-match path: type something unsupported (e.g. *"track that I drank 8 glasses
of water"*) → you get a **"build a new goal type"** card (Yes, build it / Let
me rephrase). Building triggers the D010 generation pipeline (long LLM run).

### Proof upload (works)
`POST /api/uploads/video` accepts a video and returns 201 (see media-dir fix).

### Repeatable automated checks (all green)
```bash
cd /home/k/sacrifice/frontend
E2E_BASE_URL=http://localhost:8090 E2E_API_URL=http://localhost:8000 \
  npx playwright test e2e/chat-smoke.spec.ts e2e/video_upload.spec.ts
# 3 passed: matched chat→create, no-match affordance, video upload API
```
Screenshots of each screen: `frontend/handoff-screenshots/*.png` (regenerate
with `npx playwright test e2e/_handoff_screens.spec.ts`).

---

## 2) What I fixed (commits on `fix/integration-smoke-auth-chat`)

1. **Email sign-up "Network error" was actually a backend 500.** The running
   backend was serving a **deleted factory worktree** whose venv had a broken
   `idna` (`ModuleNotFoundError: idna.uts46data`), so every `EmailStr`
   validation 500'd. Restarted the backend from `/home/k/sacrifice` (healthy
   venv). Email register/login now 200.
2. **Chat goal-matching returned 502.** All Azure AI Foundry callers POSTed to
   the bare endpoint (`…/models`), which 404s. Added
   `Settings.azure_foundry_chat_url()` (appends `/chat/completions?api-version=…`)
   and used it in `chat_match`, `llm` (proof/transcript judge), and
   `direction_synth`. Chat matching + proof judging now work.
3. **CORS** — added `:8090` and an `allow_origin_regex` for
   localhost/127.0.0.1/LAN(10.110.1.68)/Tailscale(100.82.97.40) on any port,
   so the browser web build isn't blocked. (Native Expo Go isn't subject to
   CORS.)
4. **dev-token resilience** — no longer 500s when the email already exists
   under another provider; it mints a token for that account. The shared
   anti-takeover guard for real OAuth is left intact.
5. **Media dir** — uploads 500'd because the default root `/var/sacrifice/media`
   needs root. `make up-backend` now sets `SACRIFICE_MEDIA_DIR` to a repo-local
   `.media` dir (runtime only, not `.env`, so pytest's path-convention test
   still passes).
6. **e2e spec** — the factory's `chat-smoke.spec.ts` was written against a
   speculative contract and had never run live. Aligned its selectors,
   prompts, reply order, and post-create assertion to the real component +
   backend state machine. Both `@smoke` tests pass.

Tests: backend `416 passed` (excluding the 6 pre-existing failures below and
the live-stack e2e), frontend jest `183 passed`.

---

## 3) Still needs YOU

### Google OAuth — `redirect_uri_mismatch` (external, console-side)
The app currently sends this redirect URI (from `.env GOOGLE_REDIRECT_URI`):
```
https://aaf6-2605-a601-8110-1600-bac1-a36f-b976-c22b.ngrok-free.app/api/auth/google/callback
```
That ngrok tunnel is **dead** and almost certainly isn't whitelisted on the
OAuth client. To make local browser Google login work:
1. In **Google Cloud Console → APIs & Services → Credentials → your OAuth 2.0
   Client → Authorized redirect URIs**, add **exactly**:
   ```
   http://localhost:8000/api/auth/google/callback
   ```
2. Set in `/home/k/sacrifice/.env`:
   `GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback`
   and `make restart` (or restart the backend).
3. Open the web app via **http://localhost:8000-reachable host = localhost**
   (i.e. run/visit it so the API base is `localhost:8000`), since the redirect
   host must match the host that set the `oauth_state` cookie (see GitHub).

### GitHub OAuth — `State mismatch` (external config, NOT an in-code bug)
Root cause confirmed: `/api/auth/github/login` sets the CSRF `oauth_state`
**cookie on the API host**, then redirects to GitHub with
`redirect_uri = GITHUB_REDIRECT_URI` = the **dead ngrok host**. The browser
only sends the cookie back to the host that set it, so the callback never
sees `oauth_state` → 400 `State mismatch`. (The code is correct: cookie +
the `/auth/github/callback`→`/api/...` 307 shim + state preservation all
verified working.)

To fix:
1. In the **GitHub OAuth App → Authorization callback URL**, set:
   ```
   http://localhost:8000/auth/github/callback
   ```
   (The backend's shim forwards `/auth/github/callback` → `/api/auth/...`.)
2. Set in `.env`:
   `GITHUB_REDIRECT_URI=http://localhost:8000/auth/github/callback`
   and restart.
3. Crucial: visit the web app so its API base is the **same host** that the
   callback returns to (use `localhost:8000` for both). If the app's API base
   is `10.110.1.68:8000` but the callback is `localhost:8000`, the cookie
   won't match and you'll get `State mismatch` again. Easiest: run Expo web
   with `EXPO_PUBLIC_API_URL=http://localhost:8000` for browser OAuth testing.
4. Also consider setting `FRONTEND_URL` in `.env` to wherever the web app
   actually runs (it's `http://localhost:8082`; the running Expo is on
   `:8090`) — after a successful OAuth the backend redirects the token to
   `FRONTEND_URL`, so a mismatch sends the token to a dead port.

> For smoke testing, you don't need OAuth — **email + dev-token both work.**

### Pre-existing test failures (NOT caused by my changes; separate work)
6 backend tests fail in proof-submission / registry paths I never touched:
- `test_youtube_verification`: `submit_proof` returns **202 instead of 422/400**
  for invalid / non-YouTube URLs (validation happens async, not synchronously).
- `test_api_endpoint_verification`: same 202-vs-422/400 shape.
- `test_notifications::test_proof_submitted_auto_creates_notification`.
- `test_goal_type_smoke`: a `_smoke` test goal-type isn't registered.

These are real integration gaps in the proof/verification path worth a
follow-up, but independent of login/chat.

---

## 4) Mobile (Expo Go) readiness — preconditions verified ✅

`e2e_harness_ready` is false (no automated device test), so these are
preconditions, not a device run:
- Metro serves on the LAN IP: `http://10.110.1.68:8090` → 200.
- **Native bundles build with no import errors** (the earlier
  expo-camera/expo-video breakage is fixed — both installed):
  iOS bundle → 200 (~7.7 MB), Android bundle → 200 (~7.7 MB).
- App API base baked to `http://10.110.1.68:8000`; backend reachable there
  (health → 200) and binds 0.0.0.0.
- CORS won't block the device — native fetch sends no Origin / isn't subject
  to CORS.

**On your phone:** open Expo Go → scan / enter **`exp://10.110.1.68:8090`**
(phone must be on the same LAN as this machine). Log in via **email** or seed
the **dev-token** (the device hits the LAN backend automatically). OAuth on
device uses a different (mobile redirect) path and still depends on the
console config above.

---

## 5) Quick reference

```bash
# manage stack
cd /home/k/sacrifice && make status | make restart | make down

# backend logs
tail -f /home/k/sacrifice/logs/backend.log

# instant login token (debug)
curl "http://localhost:8000/api/auth/dev/token?email=you@example.com"
```

- Expo Go URL: **`exp://10.110.1.68:8090`**
- Browser web app: **http://localhost:8090**
- Fixes branch: `fix/integration-smoke-auth-chat` (review/merge when ready;
  not pushed)
