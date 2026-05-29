# Glossary

- **Goal** — A user commitment with a title, deadline, pledge amount, goal type, status, optional charity, and verifier-specific criteria.
- **Goal type** — The verification category for a goal. The live code currently allows `youtube_video`, `api_endpoint`, `dev_sandbox`, and `github_repo`.
- **Criteria** — The structured verifier-specific payload stored with a goal in `goal_criteria.criteria_data`, such as minimum YouTube duration or an API expectation.
- **Pledge amount** — The amount of money, stored in cents, that the user risks losing if the goal fails.
- **Charity ID** — The Stripe Connect account identifier stored on a goal so a failed pledge can be donated to the selected recipient.
- **Proof submission** — The evidence payload a user sends before the deadline. In the current app this is stored as JSON in `proof_submissions.proof_data`.
- **Verification status** — The status on a proof record: `pending`, `verified`, or `failed`.
- **Goal status** — The lifecycle state on the goal itself: `draft`, `active`, `pending_review`, `verified`, `failed`, `cancelled`, or `payment_failed`.
- **Pending review** — The goal state that represents a submitted proof waiting for a final verification outcome.
- **Recurrence** — The reset cadence for a goal: `none`, `daily`, `weekly`, or `monthly`.
- **Dev sandbox** — A goal type whose proof concerns a code repository and test command intended for sandboxed verification.
- **YouTube proof** — The current built-in proof path where the user submits a YouTube URL and the system checks video-related criteria.
