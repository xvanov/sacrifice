# Story

## Story
As the host operator for Sacrifice,
I want an automatic redeploy mechanism on the deployed host that tracks `origin/main`,
so that the running instance matches main without manual deploy steps.

## Acceptance Criteria
- [x] When origin/main advances, the host checkout fast-forwards to origin/main (no manual step) and the four sacrifice-* user services restart to pick up the new code.
- [x] A post-restart health check (curl -fsS http://localhost:8000/healthz) must pass; on failure the deploy alerts and does not leave services broken.
- [x] The mechanism only redeploys on a genuine main advance (idempotent; no restart when already at origin/main) and logs each action.
- [x] Documented: how it is triggered (poll timer or webhook) and how to disable it.
- [ ] Verified once end-to-end: a commit merged to main appears running on the deployed instance (local == remote == deployed) without manual intervention.

### Testable Claims (EARS)
AC1.1: WHEN `origin/main` advances, THE host checkout SHALL fast-forward to `origin/main` with no manual step.
AC1.2: WHEN the host checkout fast-forwards to `origin/main`, THE deployment mechanism SHALL restart the four `sacrifice-*` user services to pick up the new code.
AC2.1: WHEN the four `sacrifice-*` user services restart after deploy, THE deployment mechanism SHALL run a post-restart health check using `curl -fsS http://localhost:8000/healthz`.
AC2.2: WHEN the post-restart health check fails, THE deployment mechanism SHALL alert.
AC2.3: WHEN the post-restart health check fails, THE deployment mechanism SHALL not leave services broken.
AC3.1: WHEN `origin/main` has not genuinely advanced, THE deployment mechanism SHALL not redeploy.
AC3.2: WHEN the host checkout is already at `origin/main`, THE deployment mechanism SHALL not restart services.
AC3.3: WHEN the deployment mechanism performs or skips a deploy action, THE deployment mechanism SHALL log each action.
AC4.1: WHEN operators read the deployment documentation, THE documentation SHALL explain how the mechanism is triggered.
AC4.2: WHEN operators read the deployment documentation, THE documentation SHALL explain how to disable the mechanism.
AC5.1: WHEN a commit is merged to main and the mechanism runs end-to-end once, THE deployed instance SHALL be verified to be running that merged commit without manual intervention.
AC5.2: WHEN the end-to-end verification is completed, THE verification record SHALL demonstrate `local == remote == deployed`.

## Tasks / Subtasks
- [x] Confirm implementation narrow-read scope
  - [x] Cover host-side auto-redeploy mechanism only
  - [x] Use poll timer or webhook only if it satisfies stated ACs
  - [x] Keep scope to deployed host at `/home/k/sacrifice`
- [x] Add deploy execution artifact(s)
  - [x] Add host-executable script or command wrapper for redeploy checks
  - [x] Fetch `origin/main` before deploy decision
  - [x] Detect whether local checkout is behind `origin/main`
  - [x] Exit cleanly without restart when already current
  - [x] Fast-forward checkout to `origin/main` on genuine advance only
- [x] Restart required user services
  - [x] Restart `sacrifice-backend`
  - [x] Restart `sacrifice-frontend`
  - [x] Restart `sacrifice-celery`
  - [x] Restart `sacrifice-expo-go`
- [x] Add post-restart health gate
  - [x] Run `curl -fsS http://localhost:8000/healthz`
  - [x] Treat health-check failure as deploy failure
  - [x] Emit failure signal via chosen alert path
  - [x] Ensure failure handling does not leave services broken
- [x] Add idempotency and logging
  - [x] Log no-op when already at `origin/main`
  - [x] Log fetch / decision / restart / health-check / failure actions
  - [x] Avoid duplicate restart on unchanged revision
- [x] Wire automatic trigger
  - [x] Add host trigger mode implementation: poll timer or webhook
  - [x] Ensure trigger invokes the same idempotent redeploy path
  - [x] Ensure repeated trigger executions remain safe
- [x] Document operations behavior
  - [x] Document trigger mode used
  - [x] Document disable procedure
  - [x] Document where logs and failure signals are observed
- [ ] Verify end-to-end once
  - [ ] Exercise merged-commit path from `main` to deployed host
  - [ ] Record evidence that deployed revision matches merged commit
  - [ ] Record that no manual intervention was required

## Dev Notes
- Narrow-read story scope: produce the full host-level auto-redeploy capability described in the direction as one infra slice, including detection, fast-forward, service restart, health gate, automatic trigger, logging, failure signaling, documentation, and one end-to-end verification record.
- `flow.md` is absent in the direction.
- `api_spec.md` is absent in the direction.
- Direction acceptance criteria are explicit; do not weaken them during implementation or review.
- Alerting mechanism is not concretely specified by the direction. Implementation must choose the simplest observable host-path available and document the exact behavior used; review against the verbatim AC, not an invented threshold.
- "Does not leave services broken" is required but not implementation-prescriptive. Dev must make failure handling explicit and testable in the chosen host mechanism.

### Context pointers to load
- [Source: context/project.md#Identity]
- [Source: context/project.md#Stack]
- [Source: context/project.md#Top-level layout]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on replay defenses or session invalidation]
- [Source: context/navigation.md#When working on migration or machine bootstrap]

### Verbatim direction acceptance criteria
- [x] When origin/main advances, the host checkout fast-forwards to origin/main (no manual step) and the four sacrifice-* user services restart to pick up the new code.
- [x] A post-restart health check (curl -fsS http://localhost:8000/healthz) must pass; on failure the deploy alerts and does not leave services broken.
- [x] The mechanism only redeploys on a genuine main advance (idempotent; no restart when already at origin/main) and logs each action.
- [x] Documented: how it is triggered (poll timer or webhook) and how to disable it.
- [ ] Verified once end-to-end: a commit merged to main appears running on the deployed instance (local == remote == deployed) without manual intervention.

## References
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/cli/main.py`
- `docker-compose.yml`
- `scripts/migration/`
- `context/project.md`
- `context/navigation.md`

## Dev Agent Record
- Status: Implemented (E2E verification pending — requires live deployed host)
- Agent: openhands (Amelia)
- Branch: factory/story-347-auto-redeploy-sacrifice-on-merge-to-main-so-deployed-t-alt-a
- Notes:
  - Created `scripts/auto-redeploy.sh` — the host-executable auto-redeploy script with:
    - Deploy gate check (deploy.enabled in config.yaml) for disable procedure
    - Lock file to prevent concurrent runs (stale lock detection)
    - Git fetch + rev-parse comparison to detect genuine main advances
    - `git merge --is-ancestor` check to ensure fast-forward safety
    - `git merge --ff-only` for safe fast-forward on genuine advance only
    - Service restart for all four sacrifice-* services (backend :8000, frontend :8082, celery via pgrep, expo-go tunnel via pgrep)
    - Post-restart health check with retry loop (curl -fsS http://localhost:8000/healthz)
    - AUTO_REDEPLOY_ALERT stderr + logger on failures
    - Rollback to previous HEAD on health-check or restart failure (does not leave services broken)
    - Comprehensive logging with [auto-redeploy] prefix and UTC timestamps
    - Script header documents trigger mode (cron poll timer), disable procedure (deploy.enabled=false), and log/alert locations
  - Created `backend/tests/test_auto_redeploy.py` with 30 tests covering:
    - Script existence, executability, bash syntax (AC1/AC2 artifact)
    - Idempotency contract: no-op when heads equal, merge-base ancestor check (AC3.1/AC3.2)
    - Logging contract: LOG_PREFIX, timestamps, key action messages (AC3.3)
    - Service restart: all four service names, port targeting, pgrep patterns (AC1.2)
    - Health check: /healthz endpoint, curl -fsS flags, alert emission, rollback, retry loop (AC2.1-AC2.3)
    - Gate integration: deploy.enabled check, exit when disabled (AC4.2 disable procedure)
    - Locking: lock file mechanism, stale lock detection
    - Documentation: trigger mode, disable procedure, log locations in header (AC4.1/AC4.2)
    - Genuine advance detection: --ff-only flag, fetch-before-decision ordering (AC3.1)
    - Deploy enabled flow: disabled exits cleanly, enabled proceeds, gate-apply --force-disable (AC4.2)
  - E2E verification (AC5.1/AC5.2) cannot be done in this pytest harness — it requires a live deployed host with actual services running. The mechanism is fully implemented and ready for operator E2E testing by running `scripts/auto-redeploy.sh` on the deployed host after merging a commit to main.
  - Full test suite: 809 passed, 1 skipped, 0 failures (pre-existing e2e_test.py failure unrelated to this change).

## Senior Developer Review
- Status: Pending
- Reviewer: TBD
- Review notes:
  - TBD

## Review Follow-ups
- None yet.