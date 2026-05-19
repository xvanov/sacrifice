# Sacrifice — Product Requirements Document

## 1. Executive Summary

Sacrifice is an accountability platform that ties goal completion to financial pledges. Users define a goal with a deadline, pledge a monetary amount that will be donated to a charity of their choice if they fail, and provide verifiable proof of completion. The system automatically verifies the proof and triggers the donation on failure. This creates a powerful incentive structure — the pain of losing money to a cause (potentially one the user disagrees with) drives follow-through.

The MVP focuses on three verifiable artifact types: **YouTube video uploads** (LLM judges the transcript against the goal description), **HTTP API endpoint checks** (request/response validation), and **Dev Sandbox** (Docker-sandboxed code review where an LLM judges whether the delivered code satisfies the goal description).

## 2. Problem Statement

Existing accountability apps (e.g., alarms requiring math problems or push-ups) are narrowly scoped to waking up. There is no general-purpose platform that:

- Supports arbitrary task definitions with flexible proof mechanisms.
- Automatically enforces consequences via financial pledges.
- Allows users to select their own charity recipient.
- Provides programmable (developer-friendly) verification for technical goals.

People struggle with follow-through on important personal and professional goals. The threat of losing money — especially to a cause they may not support — is a proven motivational driver.

## 3. Solution Overview

Sacrifice is a web + mobile application where users:

1. **Create a goal** with a description, deadline, pledge amount, and verification type.
2. **Select a charity** (any organization reachable via Stripe Connect).
3. **Provide a payment method** upfront (stored via Stripe; charged only on failure).
4. **Submit proof** of completion before the deadline.
5. **System verifies** the proof automatically.
6. **On success**: nothing happens — the pledge is released, no charge.
7. **On failure** (deadline passed without valid proof): the pledge amount is charged and sent to the chosen charity.

## 4. User Personas

### Primary: The Hustler (Individual Contributor)
Developers, founders, freelancers, and creatives who set personal or professional goals (shipping a feature, recording a video, finishing a project) and need external accountability.

### Secondary: The Builder (Developer-Oriented User)
Engineers who want programmatic verification — "I will have this API endpoint working" or "my code will do X and pass review."

### Tertiary: The Charity (Passive Beneficiary)
Receives donations via Stripe Connect when users fail. No active participation in the app.

## 5. User Flows

### 5.1 Onboarding & Authentication
1. User lands on app → signs in with Google or GitHub OAuth.
2. User is prompted to connect a payment method (Stripe).
3. (Optional) User connects a YouTube account (OAuth) for streamlined verification — not required for MVP; manual link submission is the default.

### 5.2 Goal Creation Flow
1. User taps "Create Goal."
2. Enters: title, description, deadline (date/time with timezone), pledge amount ($).
3. Selects verification type: **YouTube Video**, **API Endpoint**, or **Dev Sandbox**.
4. For YouTube Video: specifies minimum duration and a natural language description of what the video should cover. The LLM will compare this against the transcript.
5. For API Endpoint: provides URL + expected response criteria (method, headers, status code, body schema).
6. For Dev Sandbox: provides a Git repo URL + branch + test command + a natural language description of what the code should do. The system will run the tests and an LLM will review the code to judge whether it genuinely satisfies the goal.
6. Selects charity recipient (search/find via Stripe Connect).
7. Reviews and confirms. Goal is created with status `pending`.

### 5.3 Proof Submission Flow (YouTube)
1. User uploads a video to YouTube and copies the link.
2. User submits the YouTube URL in the Sacrifice app before the deadline.
3. System fetches video metadata (YouTube Data API): duration, title, description.
4. System fetches or generates the transcript (YouTube Transcript API or similar).
5. System checks:
   - Video exists and is publicly accessible.
   - Duration >= minimum specified.
   - LLM receives the goal description and the transcript, and judges whether the video genuinely covers what was promised.
6. Result: `verified` only if checks pass and LLM judges the content as authentic. Otherwise `failed`.

### 5.4 Proof Submission Flow (API Endpoint)
1. User deploys their API endpoint and submits the URL + expected response spec (method, headers, body schema, expected status code).
2. System calls the endpoint and validates the response against the spec.
3. Result: `verified` or `failed`.

### 5.5 Proof Submission Flow (Dev Sandbox — Test Suite + LLM Review)
1. User provides a Git repo URL + branch/commit + test command (e.g., `pytest tests/`) + a natural language description of what the code is supposed to do.
2. System clones the repo into a disposable Docker sandbox.
3. System installs dependencies and runs the specified test command.
4. System captures exit code, stdout/stderr, and test results.
5. LLM receives the goal description, the codebase (or a structured summary), and the test results. It judges whether the code genuinely implements what was promised (not just hardcoded to pass tests).
6. Result: `verified` only if tests pass **and** LLM judges the code as authentic. Otherwise `failed`.

### 5.6 Deadline & Payment Flow
1. When deadline passes, system checks verification status.
 2. If `verified`: goal moves to `completed`. User is notified in-app. No charge.
 3. If `failed` or no proof submitted: system charges the stored payment method the pledge amount via Stripe, and disburses to the chosen charity via Stripe Connect. Goal moves to `failed`. User is notified in-app.

### 5.7 Dashboard & History
- Users see all goals (active, completed, failed) with status.
- Per-goal detail page: description, deadline, pledge, verification result, donation receipt (if failed).
- Aggregate stats: success rate, total pledged, total donated, total saved (pledges not lost).

## 6. Functional Requirements

### FR-1: User Authentication
- OAuth with Google and GitHub.
- JWT-based session management.
- Profile management (name, email, avatar).

### FR-2: Goal Management
- CRUD for goals.
- Goal states: `draft` → `active` → `pending_review` → `verified | failed`.
- Deadline enforcement (system-side, cannot be bypassed).
- Timezone-aware scheduling (store in UTC, display in user's timezone).
- Recurring goals: daily, weekly, monthly (with reset logic).

### FR-3: YouTube Verification
- Accept YouTube URL as proof.
- YouTube Data API v3 integration to fetch video metadata.
- YouTube Transcript API to fetch captions/transcript.
- LLM integration: receives goal description + transcript, judges whether the video covers what was promised.
- Minimum duration check.
- Caching and rate-limit awareness for YouTube API.

### FR-4: API Endpoint Verification
- Configurable HTTP method, headers, body, and expected response.
- Support for JSON schema matching for response validation.
- Configurable timeout.
- Support for auth tokens (user can include headers for private endpoints).

### FR-5: Dev Sandbox Verification (Test Suite + LLM Review)
- Docker sandbox with resource limits (CPU, memory, disk, network).
- Support for common languages: Python, Node.js, Go, Rust, and others (detect from repo or user specifies).
- Configurable timeout per test run.
- Secure sandboxing: no persistent storage, network restricted to outbound only, no access to internal services.
- Cleanup: containers destroyed after execution.
- Support for custom environment variables (user-provided secrets for CI-like behavior).
- LLM integration (Azure Foundry) for code review.
- LLM receives: goal description (natural language), codebase snapshot, test output, and is prompted to judge whether the code genuinely satisfies the goal (not hardcoded or gamed).
- LLM judgment + test results combined for final verdict: `verified` or `failed`.

### FR-6: Payment & Donations
- Stripe integration for payment method storage (Stripe PaymentMethods).
- Charge on failure only — no pre-authorization hold required (but card validation on save).
- Stripe Connect for disbursement to user-selected charities.
- Charity search/discovery via Stripe Connect (or a curated list with Stripe payouts).
- Receipt generation (stored in app, accessible via dashboard).
- Refund policy: TBD (generally no refunds — the motivational mechanism requires commitment).

### FR-7: In-App Notifications
- In-app notification feed available from a bell icon in the app header.
- Events that generate notifications: goal created, reminder (24h, 1h before deadline), proof received, goal completed, goal failed (with donation amount and charity name).
- Notifications are stored in the database and polled by the frontend.
- Unread count badge on the bell icon.

### FR-8: Dashboard & Analytics
- Goal list with filters (active, completed, failed).
- Success/failure rate charts.
- Total money "saved" (goals completed on time) vs donated.
- Charity impact summary (total donated per charity).

## 7. Non-Functional Requirements

### NFR-1: Security
- All data in transit encrypted (TLS 1.3).
- Payment data never touches our servers — handled entirely by Stripe.js + Stripe API.
- Docker sandbox must be escape-proof (no host mounts, no privileged mode, network isolation).
- Secrets (API keys, DB credentials) managed via environment variables / secret manager.
- Rate limiting on all API endpoints.

### NFR-2: Availability & Reliability
- Target 99.5% uptime for the verification system.
- Deadline checks must be accurate to within 1 minute.
- Payment failures must be retried (up to 3 retries) before marking as failed.
- Web app must work offline-capable for goal viewing (service worker/PWA).

### NFR-3: Performance
- YouTube verification: < 30s from submission to result.
- API endpoint check: < 10s.
- Test suite run: depends on user's test suite; system timeout of 10 minutes.
- Page load times: < 2s for dashboard.

### NFR-4: Scalability
- Vertical scaling initially (single server + Postgres).
- Horizontal scaling planned: background workers for verification tasks (Celery / Redis queue).
- Docker sandbox pool with queue for concurrent test runs (limit: e.g., 5 concurrent).

## 8. High-Level Technical Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Clients                             │
│  ┌──────────────────────────────────────────────────┐   │
│  │     Expo (React Native Web) — iOS + Android + Web│   │
│  │     Single TypeScript codebase targeting all     │   │
│  │     three platforms via Expo SDK + RN Web        │   │
│  └──────────────────────┬───────────────────────────┘   │
│                         │ HTTPS/REST                    │
└─────────────────────────┼───────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────┐
│                  API Gateway / Load Balancer             │
│                    (Traefik / Nginx)                     │
├─────────────────────────┼───────────────────────────────┤
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │             FastAPI Backend                       │   │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────────────┐   │   │
│  │  │ Auth    │ │ Goal     │ │ Verification     │   │   │
│  │  │ Module  │ │ CRUD     │ │ Orchestrator     │   │   │
│  │  └─────────┘ └──────────┘ └──────────────────┘   │   │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────────────┐   │   │
│  │  │Payment  │ │Webhook   │ │ Notification     │   │   │
│  │  │Module   │ │Handler   │ │ Service          │   │   │
│  │  └─────────┘ └──────────┘ └──────────────────┘   │   │
│  └──────────────────────────────────────────────────┘   │
│                          │                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │            Background Workers (Celery)            │   │
│  │  ┌──────────────────┐ ┌──────────────────────┐   │   │
│  │  │ YouTube Verifier │ │  API Check Worker    │   │   │
│  │  │ (Transcript +    │ │  (HTTP request +     │   │   │
│  │  │  LLM Content     │ │   Response Validate) │   │   │
│  │  │   Review)        │ │                      │   │   │
│  │  └──────────────────┘ └──────────────────────┘   │   │
│  │  ┌──────────────────┐ ┌──────────────────────┐   │   │
│  │  │ Dev Sandbox      │ │  LLM Review Worker   │   │   │
│  │  │ (Docker Runner + │ │  (YouTube transcript │   │   │
│  │  │  Test Executor)  │ │   + code authenticity│   │   │
│  │  │                  │ │   via Azure Foundry)   │   │   │
│  │  └──────────────────┘ └──────────────────────┘   │   │
│  │  ┌──────────────────┐ ┌──────────────────────┐   │   │
│  │  │ Deadline Checker │ │ Payment Processor   │   │   │
│  │  │ (Cron/Scheduler) │ │ (Stripe Integration) │   │   │
│  │  └──────────────────┘ └──────────────────────┘   │   │
│  └──────────────────────────────────────────────────┘   │
│                          │                               │
│  ┌──────────┐ ┌──────────┴──────────┐ ┌────────────┐   │
│  │PostgreSQL│ │     Redis           │ │   Docker   │   │
│  │          │ │ (Queue + Cache +    │ │  Sandbox   │   │
│  │          │ │  Session Store)     │ │   Pool     │   │
│  └──────────┘ └─────────────────────┘ └────────────┘   │
│                                                         │
│  External Integrations:                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐    │
│  │Stripe    │ │ YouTube  │ │ GitHub / Git          │    │
│  │(Payments │ │ Data API │ │ (Repo Clone for       │    │
│  │+Connect) │ │ v3       │ │  Dev Sandbox)         │    │
│  └──────────┘ └──────────┘ └──────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  LLM API (Azure Foundry)                         │    │
│  │  (Code Review & Authenticity Judgment)            │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### 8.1 Proposed Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python — FastAPI (async, auto-docs, Pydantic validation) |
| ORM | SQLAlchemy + Alembic for migrations |
| Database | PostgreSQL |
| Queue / Scheduler | Celery + Redis |
| Docker SDK | docker-py for sandbox management |
| LLM API | Azure Foundry (DeepSeek-V4-Flash / Kimi-K2.6) |
| Frontend | Expo (React Native Web) — single TypeScript codebase targeting iOS, Android, and Web |
| Mobile Push | Firebase Cloud Messaging (Android) + APN via Expo |
| Styling | Tailwind CSS (via NativeWind for Expo) |
| Payments | Stripe (PaymentIntents + Connect) |
| Auth | Auth0 or custom OAuth (Google, GitHub) |
| API Style | RESTful with JSON |
| Hosting | AWS / GCP / Railway / Fly.io (TBD) |
| CI/CD | GitHub Actions |

## 9. Data Models (Initial Schema)

### Users
- id (UUID)
- email (string, unique)
- display_name (string)
- avatar_url (string, nullable)
- auth_provider (enum: google, github)
- auth_provider_id (string)
- stripe_customer_id (string, nullable)
- created_at, updated_at

### Goals
- id (UUID)
- user_id (FK → users)
- title (string)
- description (text)
- goal_type (enum: youtube_video, api_endpoint, dev_sandbox)
- pledge_amount (integer, cents)
- currency (string, default: "usd")
- deadline (timestamptz)
- timezone (string, IANA tz)
- recurrence (enum: none, daily, weekly, monthly)
- status (enum: draft, active, pending_review, verified, failed, cancelled)
- charity_id (string, Stripe Connect account ID)
- created_at, updated_at

### GoalCriteria (polymorphic per goal_type)
- id (UUID)
- goal_id (FK → goals)
- criteria_type (enum: youtube, api_endpoint, dev_sandbox)
- criteria_data (JSONB — stores type-specific config)

For YouTube:
```json
{
  "min_duration_seconds": 300,
  "video_description": "A walkthrough demo of the Sacrifice app showing how to create a goal, submit proof, and what happens on failure."
}
```

For API Endpoint:
```json
{
  "method": "GET",
  "url": "https://example.com/api/health",
  "headers": {"Authorization": "Bearer ..."},
  "expected_status": 200,
  "expected_body_schema": {"type": "object", "properties": {"status": {"type": "string"}}}
}
```

For Dev Sandbox:
```json
{
  "repo_url": "https://github.com/user/repo.git",
  "branch": "main",
  "test_command": "pytest tests/ -v",
  "language": "python",
  "env_vars": {"DATABASE_URL": "..."},
  "goal_description": "Build a FastAPI endpoint that accepts POST requests with a user ID and returns their profile data from a database.",
  "llm_review_prompt": "Optional custom instructions for the LLM review"
}
```

### ProofSubmissions
- id (UUID)
- goal_id (FK → goals)
- submitted_at (timestamptz)
- proof_data (JSONB)
- verification_status (enum: pending, verified, failed)
- verification_details (JSONB, nullable — detailed results)
- created_at

### Payments
- id (UUID)
- goal_id (FK → goals)
- user_id (FK → users)
- amount (integer, cents)
- currency (string)
- stripe_payment_intent_id (string)
- stripe_transfer_id (string, nullable — Connect payout)
- status (enum: pending, succeeded, failed, refunded)
- created_at

### Notifications
- id (UUID)
- user_id (FK → users)
- goal_id (FK → goals, nullable — allows navigation to relevant goal)
- type (enum: goal_created, reminder, proof_received, goal_completed, goal_failed, donation_receipt)
- title (string)
- body (text)
- read (boolean, default: false)
- created_at

## 10. High-Level API Endpoints

### Auth
- `POST /api/auth/google` — Google OAuth login
- `POST /api/auth/github` — GitHub OAuth login
- `POST /api/auth/refresh` — Refresh token
- `GET /api/auth/me` — Current user profile

### Goals
- `GET /api/goals` — List user's goals (filterable by status)
- `POST /api/goals` — Create goal
- `GET /api/goals/{id}` — Goal detail
- `PUT /api/goals/{id}` — Update goal (only if status = draft/active)
- `DELETE /api/goals/{id}` — Delete goal (only if status = draft)
- `POST /api/goals/{id}/submit-proof` — Submit proof (YouTube URL, API endpoint details, or Git repo details)

### Verification
- `GET /api/goals/{id}/verification-status` — Poll verification result

### Payments
- `POST /api/payment/setup-intent` — Create SetupIntent for saving payment method
- `GET /api/payment/methods` — List saved payment methods
- `DELETE /api/payment/methods/{id}` — Remove payment method
- `GET /api/payments` — Payment history

### Dashboard
- `GET /api/dashboard/stats` — Aggregated user stats
- `GET /api/dashboard/history` — Goal history with pagination

### Charities (Stripe Connect)
- `GET /api/charities/search?q=...` — Search charities via Stripe Connect

### Webhooks
- `POST /api/webhooks/stripe` — Stripe webhook events (payment success/failure, Connect transfers)

## 11. MVP Feature Roadmap

The MVP should deliver these capabilities in no particular order:

**Foundation**
- [ ] Project scaffolding (FastAPI backend, React web, React Native mobile shells)
- [ ] PostgreSQL schema + Alembic migrations
- [ ] OAuth authentication (Google + GitHub)
- [ ] Goal CRUD (create, read, update, delete)
- [ ] Stripe payment method setup (save card, no charge until failure)
- [ ] Stripe Connect charity search / selection
- [ ] Deadline enforcement + status transitions

**YouTube Verification**
- [ ] YouTube Data API integration (fetch video metadata)
- [ ] YouTube Transcript fetching
- [ ] LLM integration for transcript review against goal description
- [ ] Proof submission flow (YouTube URL → verification result)

**API Endpoint Verification**
- [ ] Configurable HTTP request builder (method, headers, body)
- [ ] Response validation (status code, JSON schema matching)
- [ ] Auth token support for private endpoints

**Dev Sandbox Verification**
- [ ] Docker sandbox infrastructure (resource limits, network isolation, cleanup)
- [ ] Repo clone + dependency installation + test execution
- [ ] LLM integration for code authenticity review
- [ ] Combined verdict (test results + LLM judgment)

**Payment & Donations**
- [ ] Automatic charge on failure via Stripe
- [ ] Disbursement to charity via Stripe Connect
- [ ] Receipt generation (stored in app)
- [ ] Retry logic for failed charges

**Dashboard & Polish**
- [ ] Dashboard with goal list (filterable by status)
- [ ] Analytics (success rate, total pledged, total donated)
- [ ] In-app notification feed (bell icon, unread count, polling)
- [ ] Recurring goal support (daily, weekly, monthly)

## 12. Risks & Open Questions

### Risks
| Risk | Mitigation |
|------|-----------|
| Stripe fraud/dispute risk (users claiming they didn't authorize the charge) | Clear terms of service, strong user authentication, in-app confirmation on goal creation |
| YouTube API rate limits / quota exhaustion | Caching, quota monitoring, fallback to manual review |
| Docker sandbox security (container escape) | Run in isolated VMs or use gVisor/Firecracker for extra isolation |
| Users gaming the system (e.g., uploading irrelevant videos with keywords sprinkled in) | More sophisticated content analysis (NLP beyond keyword matching) in future iterations |
| Regulatory: this may be considered gambling in some jurisdictions | Legal review needed before launch; restrict to jurisdictions where it's legal |

### Open Questions
1. Should we hold the pledge amount as a pre-authorization (temporary hold) to ensure the card is valid and has sufficient funds? This would increase commitment but adds complexity.
2. For recurring goals (e.g., daily), how do we handle the verification window? A new "instance" is created each period?
3. Should users be able to pick a "cause they hate" as the donation recipient to increase motivation? (e.g., donate to a political opponent)
4. What happens when Stripe charges fail (insufficient funds, expired card)? Auto-retry with escalating notifications, then mark as delinquent?
5. Is there a minimum / maximum pledge amount?
6. Should charities be proactively onboarded, or is it fully self-serve via Stripe Connect?

## 13. Success Metrics (MVP)

- **User goal completion rate** > 60% (average across all users).
- **Payment success rate** > 95% (charges that go through on first attempt).
- **Verification accuracy** < 5% false positives (verified when shouldn't be) and < 10% false negatives (failed when should have passed).
- **User retention** > 40% of users create a second goal within 30 days.
- **NPS** > 30 (considered good for an MVP in the productivity/accountability space).

---

## 14. Ralph Loop Task List

Each task includes a user story and acceptance criteria. Follow TDD: write tests for the acceptance criteria first, then implement until they pass.

```json
[
  {
    "category": "setup",
    "description": "Initialize FastAPI backend project structure",
    "story": "As a developer, I want a FastAPI backend scaffold so that I can build API endpoints for the Sacrifice app.",
    "steps": [
      "Create backend/ directory with pyproject.toml listing all dependencies (FastAPI, SQLAlchemy, Alembic, Celery, Redis, httpx, stripe, youtube-transcript-api, docker-py, pytest, httpx)",
      "Set up app structure: app/main.py, app/config.py, app/database.py, app/models/__init__.py, app/routes/__init__.py, app/services/__init__.py, app/workers/__init__.py",
      "Create app/core/celery_app.py with Celery app configured for Redis broker",
      "Write a health check endpoint GET /api/health that returns {status: 'ok'}",
      "Write a test that starts uvicorn in a subprocess and verifies GET /api/health returns 200"
    ],
    "acceptance": [
      "uvicorn starts without errors and /docs serves Swagger UI",
      "GET /api/health returns 200 with {status: 'ok'}",
      "Celery app initializes and connects to Redis",
      "pytest discovers and runs at least one test successfully"
    ],
    "passes": true
  },
  {
    "category": "setup",
    "description": "Set up database models and Alembic migrations",
    "story": "As a developer, I want SQLAlchemy models for all core entities so that the app can persist users, goals, proofs, payments, and notifications.",
    "steps": [
      "Define all SQLAlchemy models: User, Goal, GoalCriteria, ProofSubmission, Payment, Notification with proper relationships and indexes",
      "Configure Alembic with autogenerate support",
      "Write tests that create each model in a test database and verify all fields, relationships, and constraints",
      "Run alembic autogenerate to produce the initial migration"
    ],
    "acceptance": [
      "All six models are defined with correct fields, types, and nullable constraints per PRD data model section",
      "Foreign key relationships between models work (e.g., Goal.user_id -> User.id)",
      "Alembic generates a migration file when running alembic revision --autogenerate",
      "Writing and reading each model to/from the database works via pytest with a test database"
    ],
    "passes": true
  },
  {
    "category": "setup",
    "description": "Initialize Expo frontend project with TypeScript and NativeWind",
    "story": "As a user, I want a mobile-first Expo app so that I can access Sacrifice on my iPhone, Android, and web browser.",
    "steps": [
      "Create frontend/ with npx create-expo-app using TypeScript template",
      "Install and configure NativeWind (Tailwind CSS for Expo)",
      "Set up project structure: app/, components/, screens/, services/api.ts, hooks/, types/",
      "Create an API client service that wraps fetch and points to http://localhost:8000",
      "Create a basic App component that renders text 'Sacrifice' and verify it loads in Expo web"
    ],
    "acceptance": [
      "Expo web starts at localhost:8081 and renders 'Sacrifice' text",
      "API client service makes a request to GET /api/health and logs the response",
      "TypeScript compiles without errors (npx tsc --noEmit passes)",
      "NativeWind classes apply styles correctly"
    ],
    "passes": true
  },
  {
    "category": "auth",
    "description": "Implement OAuth authentication (Google + GitHub) on backend",
    "story": "As a user, I want to sign in with my Google or GitHub account so that I can access the app without creating a new account.",
    "steps": [
      "Write tests for all auth endpoints before implementation",
      "Create POST /api/auth/google that accepts {token: string}, validates the Google token, creates or retrieves the user, and returns a signed JWT",
      "Create POST /api/auth/github that accepts {code: string}, exchanges it for a GitHub access token, fetches the user's GitHub profile, creates or retrieves the user, and returns a signed JWT",
      "Implement JWT middleware that validates tokens on protected routes and attaches user to request state",
      "Create GET /api/auth/me that returns the authenticated user's profile",
      "Create POST /api/auth/refresh that issues a new JWT from a valid one"
    ],
    "acceptance": [
      "POST /api/auth/google with a valid Google token returns 200 with {access_token, user}",
      "POST /api/auth/google with an invalid token returns 401",
      "POST /api/auth/github with a valid GitHub code returns 200 with {access_token, user}",
      "POST /api/auth/github with an invalid code returns 401",
      "GET /api/auth/me with a valid JWT returns the user profile",
      "GET /api/auth/me without a JWT returns 401",
      "POST /api/auth/refresh with a valid JWT returns a new JWT",
      "Repeated login with the same Google/GitHub account returns the same user (idempotent)"
    ],
    "passes": true
  },
  {
    "category": "auth",
    "description": "Build OAuth login UI in Expo",
    "story": "As a user, I want to see a login screen with Google and GitHub buttons so that I can authenticate into the app.",
    "steps": [
      "Create a LoginScreen with 'Sign in with Google' and 'Sign in with GitHub' buttons styled with NativeWind",
      "Implement an auth service that stores the JWT in SecureStore (expo-secure-store) and attaches it to all API requests via an interceptor",
      "Create an AuthContext (React Context) that exposes user, login, logout, and isLoading state throughout the app",
      "Create root navigation that shows LoginScreen when unauthenticated and the main app when authenticated"
    ],
    "acceptance": [
      "LoginScreen renders both OAuth buttons when user is not authenticated",
      "Tapping 'Sign in with Google' triggers the Google OAuth flow and the button shows a loading state",
      "Tapping 'Sign in with GitHub' triggers the GitHub OAuth flow and the button shows a loading state",
      "After successful login, the user is redirected to the main app screen",
      "After logout, the user is redirected back to the login screen",
      "The JWT is persisted in SecureStore and survives app restart",
      "Unauthenticated API calls (no JWT) show an error state prompting re-login"
    ],
    "passes": true
  },
  {
    "category": "goals",
    "description": "Implement Goal CRUD API endpoints",
    "story": "As a user, I want to create, view, update, and delete my goals so that I can manage my accountability tasks.",
    "steps": [
      "Write tests for all goal CRUD endpoints before implementation",
      "Create POST /api/goals that accepts title, description, deadline, pledge_amount, goal_type, criteria, charity_id and returns the created goal",
      "Create GET /api/goals that returns the authenticated user's goals, filterable by status query param",
      "Create GET /api/goals/{id} that returns a single goal (only accessible by the owning user)",
      "Create PUT /api/goals/{id} that updates mutable fields (only when status is draft or active)",
      "Create DELETE /api/goals/{id} that soft-deletes (only when status is draft)",
      "Implement goal state machine: draft -> active -> pending_review -> verified | failed (reject invalid transitions)"
    ],
    "acceptance": [
      "POST /api/goals with valid fields returns 201 with the created goal object including an id",
      "POST /api/goals without required fields returns 422 with validation errors",
      "GET /api/goals returns only the authenticated user's goals",
      "GET /api/goals?status=active returns only active goals",
      "GET /api/goals/{id} returns the goal for the owning user and 404 for other users",
      "PUT /api/goals/{id} updates fields and returns the updated goal",
      "PUT /api/goals/{id} rejects edits after the goal is in verified or failed status",
      "DELETE /api/goals/{id} removes a draft goal and 404s for non-draft goals",
      "A goal cannot transition from active directly to verified without passing through pending_review"
    ],
    "passes": false
  },
  {
    "category": "goals",
    "description": "Build goal creation UI in Expo",
    "story": "As a user, I want a form to create a new goal so that I can define what I'm committing to and the consequences of failure.",
    "steps": [
      "Create a goal creation screen with form fields: title, description, deadline (date/time picker), pledge amount (currency input)",
      "Add a verification type selector with conditional sub-forms for each type",
      "Add a charity search field that queries GET /api/charities/search as the user types",
      "Create a confirmation/review step showing all goal details before final submission",
      "Wire the form to POST /api/goals and handle success/error states"
    ],
    "acceptance": [
      "All form fields render with proper labels and input types",
      "Selecting 'YouTube Video' shows duration and video description fields",
      "Selecting 'API Endpoint' shows URL, method, headers, and expected response fields",
      "Selecting 'Dev Sandbox' shows repo URL, branch, test command, and goal description fields",
      "Charity search shows autocomplete results as the user types",
      "Form validates required fields before allowing submission",
      "Successful submission navigates to the goal detail screen",
      "Failed submission shows error message with field-level validation hints"
    ],
    "passes": false
  },
  {
    "category": "goals",
    "description": "Build goal list and detail UI in Expo",
    "story": "As a user, I want to see all my goals in a list and view details of each so that I can track my progress.",
    "steps": [
      "Create a goal list screen with FlatList, pull-to-refresh, and status filter tabs (All, Active, Completed, Failed)",
      "Create a goal detail screen showing title, description, deadline, pledge amount, charity, verification status, and proof submission status",
      "Add loading skeletons and empty state when no goals exist",
      "Wire up to GET /api/goals and GET /api/goals/{id} endpoints"
    ],
    "acceptance": [
      "Goal list loads and displays all goals for the authenticated user",
      "Filter tabs correctly filter goals by status",
      "Pull-to-refresh reloads the list from the API",
      "Tapping a goal navigates to the goal detail screen",
      "Goal detail shows all fields per the PRD data model",
      "Empty state shows a message when the user has no goals",
      "Loading skeleton shows while data is being fetched"
    ],
    "passes": false
  },
  {
    "category": "youtube",
    "description": "Implement YouTube verification backend service",
    "story": "As a user with a YouTube video goal, I want the system to automatically check my video transcript against my goal description using an LLM so that I can prove I completed the task.",
    "steps": [
      "Write tests for YouTube verification before implementation",
      "Create a YouTube service that fetches video metadata (duration, title) via YouTube Data API v3",
      "Create a YouTube transcript service that fetches captions via youtube-transcript-api",
      "Create a Celery task that: fetches metadata, fetches transcript, calls an LLM to judge whether the transcript content matches the goal description",
      "Create POST /api/goals/{id}/submit-proof that accepts {youtube_url: string} and enqueues the verification Celery task",
      "Create GET /api/goals/{id}/verification-status that returns the current verification result"
    ],
    "acceptance": [
      "POST /api/goals/{id}/submit-proof with a valid YouTube URL returns 202 and enqueues a verification task",
      "POST /api/goals/{id}/submit-proof with an invalid URL returns 422",
      "GET /api/goals/{id}/verification-status returns pending after submission, then verified or failed once the Celery task completes",
      "A video shorter than the minimum duration is marked as failed",
      "A video whose transcript does not match the goal description is marked as failed",
      "A video meeting all criteria is marked as verified",
      "The goal status transitions to verified or failed after verification completes"
    ],
    "passes": false
  },
  {
    "category": "youtube",
    "description": "Build YouTube proof submission UI in Expo",
    "story": "As a user, I want to submit my YouTube video URL and see the verification result so that I can prove I completed my YouTube goal.",
    "steps": [
      "Create a proof submission screen with a text input for YouTube URL and a submit button",
      "Add client-side URL validation (must be a valid youtube.com or youtu.be link)",
      "Show a polling indicator that checks GET /api/goals/{id}/verification-status every 3 seconds",
      "Display the final verification result with details: duration passed/failed and LLM judgment passed/failed"
    ],
    "acceptance": [
      "The screen shows the goal description and deadline at the top",
      "Pasting a YouTube URL validates it client-side before submission",
      "After submission, a loading state shows with status updates as the Celery task processes",
      "On verified, the screen shows a success state with green checkmark and details",
      "On failed, the screen shows which criteria failed (duration or content)",
      "The user cannot resubmit once the deadline has passed"
    ],
    "passes": false
  },
  {
    "category": "api_endpoint",
    "description": "Implement API endpoint verification backend worker",
    "story": "As a developer user, I want the system to call my API endpoint and validate the response so that I can prove my endpoint is working as specified.",
    "steps": [
      "Write tests for API endpoint verification before implementation",
      "Create a Celery task that makes an HTTP request with configurable method, headers, and body to the user-specified URL",
      "Implement response validation: expected status code matching and expected JSON body schema matching",
      "Support auth tokens so users can include Authorization headers for private endpoints",
      "Handle timeouts, connection errors, and non-JSON responses gracefully with clear failure reasons"
    ],
    "acceptance": [
      "A Celery task making a GET request to a valid URL returns the status code and body",
      "A task with expected_status: 200 marks as verified when the endpoint returns 200, and failed when it returns 500",
      "A task with a JSON body schema validates the endpoint's response body against the schema",
      "A task with custom headers sends those headers in the request",
      "A task hitting a timeout or unreachable host returns a clear failure reason",
      "The verification result is stored in the ProofSubmission record with full request/response details"
    ],
    "passes": false
  },
  {
    "category": "api_endpoint",
    "description": "Build API endpoint proof submission UI in Expo",
    "story": "As a developer user, I want to configure my API endpoint check and see the verification result so that I can prove my endpoint works.",
    "steps": [
      "Create a proof submission screen with fields for URL, HTTP method selector, headers (key-value pairs), request body, expected status code, and expected response body schema",
      "Show the verification result with the actual request made and response received",
      "Allow saving endpoint configurations as reusable templates"
    ],
    "acceptance": [
      "All configuration fields render and accept input",
      "Headers support adding and removing key-value rows",
      "Expected body schema accepts a JSON object for validation",
      "After submission, the screen shows the request URL, method, headers, and body that were sent",
      "The result shows the actual response status, headers, and body alongside what was expected",
      "Templates can be saved with a name and loaded again"
    ],
    "passes": false
  },
  {
    "category": "dev_sandbox",
    "description": "Implement Docker sandbox management service",
    "story": "As a developer user, I want the system to clone my repo, install dependencies, and run my test suite in a secure sandbox so that I can prove my code works.",
    "steps": [
      "Write tests for Docker sandbox before implementation",
      "Create a Docker sandbox service that manages container lifecycle: pull image, create container with resource limits, execute commands, capture output, destroy container",
      "Implement secure defaults: no privileged mode, network restricted to outbound only (no internal access), tempfs mount for /tmp, memory limit 1GB, CPU limit 1 core, 5-minute execution timeout",
      "Implement repo cloning: git clone --depth=1 the user-specified repo URL at the specified branch",
      "Implement language-agnostic dependency detection: check for requirements.txt (pip install), package.json (npm install), go.mod (go mod download), Cargo.toml (cargo build), or similar",
      "Run the user-specified test command and capture stdout, stderr, and exit code",
      "Ensure containers are always destroyed after execution, even on failure or timeout"
    ],
    "acceptance": [
      "A sandbox container is created, runs a command, and returns stdout/stderr and exit code",
      "The container is destroyed after execution (docker ps -a does not show it)",
      "A repo URL is cloned successfully and the files exist inside the container",
      "Dependencies are installed based on the detected language",
      "A test command with exit code 0 succeeds, exit code non-zero fails",
      "A command exceeding the timeout is killed and returns a timeout failure",
      "No privileged containers or insecure mounts are used"
    ],
    "passes": false
  },
  {
    "category": "dev_sandbox",
    "description": "Implement LLM code review integration for Dev Sandbox",
    "story": "As a developer user, I want an LLM to review my code for authenticity so that I cannot cheat by hardcoding test answers.",
    "steps": [
      "Write tests for LLM code review before implementation",
      "Create an LLM service that sends a structured prompt to Azure Foundry containing: the goal description, a summary of the codebase (file tree + key function signatures), and the test output",
      "Design a prompt template that instructs the LLM to judge whether the code genuinely implements what was promised vs. being hardcoded to pass tests",
      "Create a Celery task that chains: Docker sandbox run -> LLM review -> combine results into a final verdict",
      "Store the structured verdict with LLM reasoning in the ProofSubmission record"
    ],
    "acceptance": [
      "The LLM receives the goal description, code summary, and test results in a single prompt",
      "The LLM returns a structured verdict: {authentic: bool, reasoning: string}",
      "A codebase that hardcodes test answers receives authentic: false",
      "A legitimate implementation receives authentic: true",
      "The final verification is verified only if tests pass AND authentic is true",
      "The verdict reasoning is stored and displayed to the user"
    ],
    "passes": false
  },
  {
    "category": "dev_sandbox",
    "description": "Build Dev Sandbox proof submission UI in Expo",
    "story": "As a developer user, I want to submit my repo for sandbox verification and see the combined test + LLM result so that I can prove my code is legitimate.",
    "steps": [
      "Create a proof submission screen with fields for Git repo URL, branch, language selector (auto-detect or manual), test command, environment variables (key-value pairs), and a text area for the goal description",
      "Show real-time progress stages: cloning -> installing dependencies -> running tests -> LLM reviewing",
      "Display the combined verdict with test exit code/output and LLM reasoning side by side"
    ],
    "acceptance": [
      "All fields render with appropriate input types",
      "Progress shows each stage with a spinner and status text",
      "On verified, both 'Tests Passed' and 'Code Authentic' show green checkmarks",
      "On failed, the failing stage is highlighted red with details",
      "Test output is scrollable and searchable",
      "LLM reasoning is displayed in a readable format",
      "The user can retry submission if it fails"
    ],
    "passes": false
  },
  {
    "category": "payment",
    "description": "Implement Stripe payment method setup and charity search",
    "story": "As a user, I want to save a payment method and choose a charity so that my pledge can be charged and donated if I fail.",
    "steps": [
      "Write tests for payment endpoints before implementation",
      "Create POST /api/payment/setup-intent that creates a Stripe SetupIntent to securely save a payment method (card details never touch our server)",
      "Create GET /api/payment/methods that lists the user's saved payment methods (last 4 digits, brand, expiry)",
      "Create DELETE /api/payment/methods/{id} that removes a saved payment method",
      "Create GET /api/charities/search?q=... that queries Stripe Connect for charity organizations matching the search term",
      "Store the selected charity's Stripe Connect account ID on the goal"
    ],
    "acceptance": [
      "POST /api/payment/setup-intent returns a client_secret for Stripe.js to complete card setup",
      "GET /api/payment/methods returns the user's saved payment methods with masked card details",
      "DELETE /api/payment/methods/{id} removes the method and it no longer appears in the list",
      "GET /api/charities/search?q=red cross returns a list of matching charities with name and Stripe Connect ID",
      "GET /api/charities/search without a query returns an empty list"
    ],
    "passes": false
  },
  {
    "category": "payment",
    "description": "Implement automatic charge on failure and disbursement",
    "story": "As a user, I want my pledge to be automatically charged and donated to my chosen charity if I fail my goal so that the accountability mechanism works automatically.",
    "steps": [
      "Write tests for the charge/disbursement flow before implementation",
      "Create a Celery beat task (runs every 60 seconds) that finds goals past their deadline with status = active",
      "For each expired goal: transition to failed, create a Stripe PaymentIntent for the pledge amount on the user's saved payment method",
      "On successful charge: create a Stripe Transfer to the charity's Connect account",
      "Implement retry logic: on PaymentIntent failure, retry up to 3 times with exponential backoff, then mark as payment_failed",
      "Generate a donation receipt (stored in app, accessible via dashboard)"

    "acceptance": [
      "A goal past its deadline with no verification is automatically transitioned to failed",
      "A Stripe PaymentIntent is created for the exact pledge amount on the user's saved payment method",
      "A successful charge triggers a Stripe Transfer to the charity's Connect account minus platform fee",
      "A failed charge is retried 3 times with exponential backoff",
      "After 3 failed retries, the goal status is updated to payment_failed",
      "A donation receipt is created and accessible from the goal detail page",
      "A goal that was verified before the deadline is never charged"
    ],
    "passes": false
  },
  {
    "category": "deadline",
    "description": "Implement deadline enforcement and recurring goal support",
    "story": "As a user, I want my goals to automatically enforce deadlines and support recurring schedules so that I can set daily/weekly/monthly accountability chains.",
    "steps": [
      "Write tests for deadline enforcement and recurrence before implementation",
      "Create a Celery beat task (runs every 60 seconds) that checks for goals with deadline < now and status = active",
      "For expired goals with no proof submitted: transition to failed (triggering the payment flow)",
      "For expired goals with proof pending review: allow a 5-minute grace period, then evaluate",
      "For recurring goals: after an active period ends, create the next period's goal instance immediately",
      "Send in-app notification on goal failure and on new recurring instance creation"
    ],
    "acceptance": [
      "An active goal past its deadline with no proof is marked failed within 60 seconds",
      "An active goal past its deadline with a pending proof in the grace period waits up to 5 minutes before failing",
      "A recurring daily goal creates a new instance immediately after the previous period ends",
      "Recurring weekly goals reset on the same day of the week",
      "Recurring monthly goals reset on the same day of the month",
      "In-app notifications are created for both failure and new recurring instance creation",
      "A verified goal is never affected by deadline enforcement"
    ],
    "passes": false
  },
  {
    "category": "dashboard",
    "description": "Build dashboard API and UI",
    "story": "As a user, I want a dashboard showing my goal statistics and history so that I can track my accountability over time.",
    "steps": [
      "Write tests for dashboard endpoints before implementation",
      "Create GET /api/dashboard/stats that returns: total goals, completed count, failed count, success rate, total pledge amount, total donated amount, total saved amount (pledges not lost)",
      "Create GET /api/dashboard/history that returns paginated goal history with status, result, and timestamps",
      "Build the dashboard screen in Expo with stat cards at the top and a scrollable history list below",
      "Add a simple bar chart showing success vs failure over time (last 30 days)"
    ],
    "acceptance": [
      "GET /api/dashboard/stats returns correct aggregate numbers for the authenticated user",
      "GET /api/dashboard/history returns goals sorted by creation date with pagination metadata",
      "The dashboard screen shows stat cards: total goals, success rate, total donated, total saved",
      "The history list shows each goal with title, status, and date",
      "The chart renders and accurately represents the user's data",
      "All values update in real-time when navigating back from goal creation"
    ],
    "passes": false
  },
  {
    "category": "notifications",
    "description": "Implement in-app notification feed",
    "story": "As a user, I want to see a notification feed within the app so that I stay informed about my goal status changes, deadlines, and results.",
    "steps": [
      "Write tests for the notification service before implementation",
      "Create a Notification model with fields: user_id, type (goal_created, reminder, proof_received, goal_completed, goal_failed), title, body, read (boolean), created_at",
      "Create GET /api/notifications that returns the user's notifications sorted by most recent, with pagination",
      "Create GET /api/notifications/unread-count that returns the count of unread notifications",
      "Create PUT /api/notifications/{id}/read to mark a notification as read",
      "Create PUT /api/notifications/read-all to mark all notifications as read",
      "Create a service function that creates notifications for each event type (used by other parts of the system)",
      "Build the notification UI in Expo: bell icon in header with unread badge, notification list screen, tap to navigate to relevant goal"
    ],
    "acceptance": [
      "GET /api/notifications returns the user's notifications paginated by most recent first",
      "GET /api/notifications/unread-count returns the correct count of unread notifications",
      "PUT /api/notifications/{id}/read marks a single notification as read",
      "PUT /api/notifications/read-all marks all notifications as read",
      "Creating a goal automatically creates a 'goal created' notification",
      "Submitting proof creates a 'proof received' notification",
      "A verified goal creates a 'goal completed' notification",
      "A failed goal creates a 'goal failed' notification with the donation amount",
      "The bell icon in the app header shows the unread count badge",
      "Tapping a notification navigates to the relevant goal detail screen"
    ],
    "passes": false
  }
]
```

---

*This PRD is a living document and should be updated as requirements evolve during development.*
