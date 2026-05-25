# Glossary

| Term | Meaning in this codebase |
| --- | --- |
| **Sacrifice** | The accountability product where missing a goal can trigger a donation of the user's pledged money (`PRD.md`). |
| **Goal** | A user-defined commitment with a title, deadline, pledge amount, type, and verification criteria (`backend/app/models/goal.py`, `backend/app/schemas/goal.py`). |
| **Pledge** | The amount of money attached to a goal and charged on failure (`PRD.md`, `backend/app/routes/goals.py`). |
| **Charity** | The recipient selected by the user; the backend currently searches Stripe standard accounts to populate choices (`PRD.md`, `backend/app/routes/payment.py`). |
| **Proof submission** | The evidence a user sends for a goal, stored as a `ProofSubmission` and marked pending until verification finishes (`backend/app/routes/goals.py`). |
| **YouTube video goal** | A goal verified from a submitted YouTube URL and related transcript / metadata checks (`PRD.md`, `backend/app/routes/goals.py`). |
| **API endpoint goal** | A goal verified by calling a submitted endpoint and comparing the response against configured expectations (`PRD.md`, `backend/app/routes/goals.py`). |
| **Dev sandbox goal** | A goal verified by running repository code in a sandboxed worker flow using repo URL, branch, and test command inputs (`PRD.md`, `backend/app/routes/goals.py`). |
| **GitHub repo goal** | A separate current code path where proof is a repository URL plus optional encrypted GitHub token (`backend/app/models/goal.py`, `backend/app/routes/goals.py`). |
| **Verification status** | The state returned for the latest proof submission, such as `pending`, plus any stored verification details (`backend/app/routes/goals.py`). |
| **Recurrence** | The repeat cadence attached to a goal: `none`, `daily`, `weekly`, or `monthly` (`PRD.md`, `backend/app/models/goal.py`, `backend/app/schemas/goal.py`). |
| **Saved money** | A dashboard concept from the PRD describing pledge amounts not lost because goals were completed on time (`PRD.md`). |
