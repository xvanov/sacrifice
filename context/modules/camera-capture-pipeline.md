# camera-capture-pipeline

## What exists today
There is no shared camera-capture pipeline yet.

The current proof UI is still split by existing proof types:
- `ProofSubmissionScreen` asks for a pasted YouTube URL and polls verification status.
- Navigation includes `proof-submission`, `api-endpoint-proof-submission`, and `dev-sandbox-proof-submission`, but no capture, recorder, or media-review screen (`frontend/screens/ProofSubmissionScreen.tsx`, `frontend/hooks/useNavigation.tsx`, `frontend/App.tsx`).

The current transport is still JSON-only:
- `frontend/services/api.ts` always sends `Content-Type: application/json`.
- proof helpers all `JSON.stringify()` their bodies before POSTing.
- the backend `ProofSubmissionCreate` schema only contains URL, API request, repo, branch, command, env var, and token-style fields (`frontend/services/api.ts`, `backend/app/schemas/proof.py`).

The native app surface is also not prepared for capture work:
- `frontend/app.json` only enables datetime picker, secure store, and web browser plugins.
- `frontend/package.json` does not show a camera or media-capture dependency among the app dependencies.
- `frontend/AGENTS.md` explicitly says frontend work should use the Expo `v54.0.0` docs.

## Why this matters to generator work
A physical-world goal type such as a pushup counter cannot work end-to-end through plugin generation alone. The missing pieces are below the plugin layer:
- a mobile capture entrypoint
- native permissions/plugin setup
- an upload or media-reference contract between client and backend
- proof schema fields that can carry captured media identity
- verifier logic that knows how to consume that media reference (`frontend/app.json`, `frontend/services/api.ts`, `backend/app/schemas/proof.py`, `backend/app/routes/goals.py`)

## Current backend boundary
`backend/app/main.py` mounts no upload router, and `POST /api/goals/{id}/submit-proof` still accepts a Pydantic JSON body instead of `UploadFile` or multipart form data (`backend/app/main.py`, `backend/app/routes/goals.py`). `ProofSubmission` can store arbitrary JSONB, which is useful once a media-reference contract exists, but that contract is not present yet (`backend/app/models/proof.py`).

## Practical takeaway
Treat camera capture as shared infrastructure for the generator direction, not as a one-off addition inside a single generated goal module. The generator can eventually emit a `pushup_counter` plugin, but the current repo still needs first-class capture and upload primitives before that plugin can actually receive phone-camera proof.
