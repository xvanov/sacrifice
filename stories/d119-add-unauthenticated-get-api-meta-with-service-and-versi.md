# Story

## Title
D119 add unauthenticated GET /api/meta with service and version

## Slug
`d119-add-unauthenticated-get-api-meta-with-service-and-versi`

## Scope
`backend`

## Acceptance Criteria
- [x] `GET /api/meta` returns 200 with a JSON object whose `service` field is
  exactly the string `"sacrifice"`, so a caller can confirm which application
  answered.
- [x] The same response includes a `version` field that is a non-empty string,
  so two deployments of different builds are distinguishable from the outside.
- [x] The endpoint answers without any `Authorization` header — an unauthenticated
  `GET /api/meta` returns 200 and the body described above, so the smoke gate and
  an operator can call it before any login exists.

### Testable Claims (EARS)
AC1.1: WHEN a client sends `GET /api/meta`, THE API SHALL return status 200.
AC1.2: WHEN a client sends `GET /api/meta`, THE API SHALL return a JSON object containing a `service` field.
AC1.3: WHEN a client sends `GET /api/meta`, THE API SHALL return the `service` field with value exactly `"sacrifice"`.
AC2.1: WHEN a client sends `GET /api/meta`, THE API SHALL return a JSON object containing a `version` field.
AC2.2: WHEN a client sends `GET /api/meta`, THE API SHALL return the `version` field as a string.
AC2.3: WHEN a client sends `GET /api/meta`, THE API SHALL return the `version` field as a non-empty string.
AC3.1: WHEN a client sends unauthenticated `GET /api/meta` without an `Authorization` header, THE API SHALL return status 200.
AC3.2: WHEN a client sends unauthenticated `GET /api/meta` without an `Authorization` header, THE API SHALL return the body described in AC1 and AC2.

## Tasks / Subtasks
- [x] Add a dedicated `/api/meta` route module patterned after `backend/app/routes/health.py`.
- [x] Define a module-level non-empty version constant; do not shell out to git.
- [x] Return JSON containing `service` and `version`.
- [x] Ensure the route has no auth dependency and no database dependency.
- [x] Wire the route into `backend/app/main.py` under the existing `/api` prefix.
- [x] Preserve `/api/health` contract unchanged.
- [x] Add backend tests for unauthenticated `GET /api/meta` returning 200.
- [x] Add backend tests asserting `service == "sacrifice"`.
- [x] Add backend tests asserting `version` is a non-empty string.
- [x] Add backend tests asserting an `Authorization` header does not change the response contract.
- [x] Add backend tests proving the endpoint does not require any pre-existing user/account state.

## Dev Notes
### flow.md (verbatim embed)
```md
# Flow — identifying a running instance

## Flow A — an operator asks a deployed instance what it is

1. The operator (or the smoke gate, or a load balancer probe) issues
   `GET /api/meta` against a running instance, with no credentials.
2. The instance responds `200` with a JSON body containing `service` and
   `version`.
3. The operator reads `service` to confirm the right application answered — a
   misrouted DNS record or a wrong tunnel points at a different service, and that
   has happened here before.
4. The operator reads `version` to confirm which build is serving. If it does not
   match the commit they expected to have deployed, the deploy did not take
   effect and they escalate.

## Flow B — the same request before any user exists

1. A freshly booted instance has an empty database and no accounts.
2. `GET /api/meta` still returns `200` with the same body — it reads no user
   state and touches no tables, so it answers correctly on a cold instance.
3. This is what makes it usable as a boot check: it distinguishes "the process is
   up and is the build I expect" from "the process is up" (which `/api/health`
   already covers).
```

### api_spec.md (verbatim embed)
```md
# API spec — build-metadata endpoint

## GET /api/meta

Unauthenticated. No query parameters, no request body, no side effects.

**200 OK**

```json
{
  "service": "sacrifice",
  "version": "0.1.0"
}
```

| field | type | constraint |
|---|---|---|
| `service` | string | exactly `"sacrifice"` |
| `version` | string | non-empty; identifies the running build |

Notes:

- `service` is a fixed literal, not derived from configuration — a caller uses it
  to confirm *which application* answered, so it must not vary by environment.
- `version` may be a module-level constant. It must be non-empty. Do not invoke
  `git` at request time.
- No `Authorization` header is required or honoured. Sending one must not change
  the response.
- Additional fields are permitted but must contain no user data, secrets,
  environment variables, or database contents.

**Errors**

None expected. This endpoint reads no external state, so it has no failure mode
of its own; it must not touch the database.
```

### Direction acceptance criteria (verbatim embed)
```md
- [ ] `GET /api/meta` returns 200 with a JSON object whose `service` field is
  exactly the string `"sacrifice"`, so a caller can confirm which application
  answered.
- [ ] The same response includes a `version` field that is a non-empty string,
  so two deployments of different builds are distinguishable from the outside.
- [ ] The endpoint answers without any `Authorization` header — an unauthenticated
  `GET /api/meta` returns 200 and the body described above, so the smoke gate and
  an operator can call it before any login exists.
```

### Implementation constraints
- Public endpoint only; no authenticated or per-user information.
- Must not include user data, request counts, environment variables, secrets, or database contents.
- Must not change `/api/health`.
- Must not touch the database.
- Version source may be a module-level constant; non-empty required.
- No request-time git invocation.

### Context pointers
- [Source: context/project.md#Identity]
- [Source: context/project.md#Active constraints]
- [Source: context/navigation.md#When working on auth or token lifecycle]
- [Source: context/modules/backend.md]
- [Source: context/modules/auth.md]
- [Source: context/modules/security.md]
- [Source: context/current-state.md#backend]

## References
- `backend/app/main.py`
- `backend/app/routes/health.py`
- `backend/app/routes/`
- `backend/tests/`
- `backend/tests/test_auth.py`

## Dev Agent Record
- Agent: Amelia (dev)
- Status: Complete (reviewer findings addressed)
- Notes:
  - Route module at `backend/app/routes/meta.py`: single `VERSION = "0.1.0"` module-level
    constant, single `GET /api/meta` endpoint with no dependencies (no auth, no DB), returns
    `{"service": "sacrifice", "version": "0.1.0"}`.
  - Wired into `backend/app/main.py` immediately after health_router, under the existing
    `/api` prefix.
  - 11 tests in `backend/tests/test_meta.py` cover all acceptance criteria.
  - Reviewer-driven fixes applied:
    - Shared `client` fixture (pytest_asyncio) replaces repeated ASGITransport/AsyncClient setup.
    - `test_meta_with_authorization_header_returns_same_contract` now issues both
      unauthenticated and authorized requests and asserts exact body equality.
    - `test_meta_no_user_state_required` now overrides `get_db` with a generator that
      raises `AssertionError` if invoked, then asserts GET /api/meta still returns 200
      with the correct body — proving the endpoint never touches the database.
    - Dependency override is scoped with try/finally `del` so the conftest fixture is
      undisturbed.
  - All meta and health tests pass (16/16).
- File List:
  - `backend/app/routes/meta.py` (new)
  - `backend/app/main.py` (modified — import + include_router)
  - `backend/tests/test_meta.py` (modified — reviewer-driven test hardening)

## Senior Developer Review
- Review status: Addressed
- Checklist:
  - [x] Route mounted at `/api/meta` under `/api` prefix.
  - [x] No auth dependency.
  - [x] No database access (proved by dependency-override test).
  - [x] `service` literal exactly `"sacrifice"`.
  - [x] `version` is non-empty string.
  - [x] `/api/health` unchanged.
  - [x] Tests cover no-header and header-present requests (body equality comparison).
  - [x] Tests cover cold-instance/no-user scenario (DB-guard override).

## Review Follow-ups
- None.