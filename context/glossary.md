# Glossary

- **Goal** — A user-defined accountability commitment with a deadline, a verification method, and a financial downside for failure (`PRD.md`).
- **Pledge** — The amount of money staked against failure; it can be charged and donated if the goal is not verified (`PRD.md`, `backend/app/models/goal.py`).
- **Goal type** — The verification family attached to a goal, currently limited in creation flows to `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo` (`backend/app/schemas/goal.py`, `frontend/screens/GoalCreateScreen.tsx`).
- **Criteria** — The structured configuration stored alongside a goal that tells a verifier what to check, such as minimum video duration, endpoint expectations, or repository conditions (`backend/app/models/goal.py`, `frontend/screens/GoalCreateScreen.tsx`).
- **Proof submission** — The user-provided payload sent before the deadline to demonstrate completion; it is currently stored as JSONB in `proof_submissions.proof_data` (`backend/app/routes/goals.py`, `backend/app/models/proof.py`).
- **Verification status** — The result tracked for a proof submission (`pending`, `verified`, or `failed`) and returned to clients separately from the raw proof payload (`backend/app/models/proof.py`, `backend/app/routes/goals.py`).
- **Pending review** — A goal status used after activation and before final verification, when proof has been submitted or is awaiting evaluation (`PRD.md`, `backend/app/schemas/goal.py`).
- **Charity** — The recipient selected by the user for a failed pledge; the current app searches Stripe Connect organizations and stores the chosen identifier on the goal (`PRD.md`, `frontend/services/api.ts`, `backend/app/models/goal.py`).
- **Recurring goal** — A goal configured to repeat on a daily, weekly, or monthly cadence instead of running once (`PRD.md`, `backend/app/models/goal.py`).
- **Payment failed** — A terminal goal status present in the database enum for a failed charging flow, even though the normal goal update schema currently exposes only the non-payment statuses (`backend/app/models/goal.py`, `backend/app/schemas/goal.py`).
