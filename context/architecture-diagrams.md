# Architecture Diagrams

## Current implemented system flow

```mermaid
flowchart LR
    user[User]
    frontend[Expo frontend\nfrontend/App.tsx]
    nav[Local screen state\nfrontend/hooks/useNavigation.tsx]
    form[Typed goal + proof screens\nGoalCreateScreen / ProofSubmissionScreen]
    apiClient[JSON API client\nfrontend/services/api.ts]
    cli[Click CLI\nbackend/cli/main.py]
    api[FastAPI routers\nbackend/app/main.py]
    goals[Goals + goal-types routes\nbackend/app/routes/goals.py]
    registry[Goal-type registry\nbackend/app/goal_types/registry.py]
    db[(PostgreSQL)]
    redis[(Redis)]
    workers[Celery workers\nbackend/app/core/celery_app.py]
    plugins[Goal-type plugins\nbackend/app/goal_types/*]
    external[Verification targets\nYouTube / HTTP endpoint / GitHub / Docker sandbox]
    oauth[Google / GitHub OAuth]
    stripe[Stripe]

    user --> frontend
    frontend --> nav
    nav --> form
    form --> apiClient
    user --> cli
    apiClient --> api
    cli --> api
    api --> goals
    goals --> registry
    goals --> db
    api --> oauth
    api --> stripe
    api --> redis
    redis --> workers
    workers --> plugins
    workers --> db
    plugins --> external
```

## Current D010 boundary: where a chat-factory flow would have to attach

```mermaid
flowchart TD
    docs[D010 context + requirements\nPRD.md / PROMPT.md / context/*.md]
    frontendShell[Frontend shell\nApp.tsx + useNavigation.tsx]
    typedCreate[Typed create flow\nGoalCreateScreen.tsx]
    apiClient[frontend/services/api.ts]
    backendEntry[FastAPI entry\nbackend/app/main.py]
    goalRoutes[Goal routes\nbackend/app/routes/goals.py]
    registry[Plugin registry\nbackend/app/goal_types/registry.py]
    proofSurface[Proof flow\nProofSubmissionScreen.tsx + schemas/proof.py]
    missingChat[No chat route or chat client today]
    missingCamera[No camera/upload pipeline today]

    docs --> frontendShell
    docs --> backendEntry
    frontendShell --> typedCreate
    typedCreate --> apiClient
    apiClient --> goalRoutes
    backendEntry --> goalRoutes
    goalRoutes --> registry
    goalRoutes --> proofSurface
    frontendShell --> missingChat
    backendEntry --> missingChat
    proofSurface --> missingCamera
```

## Primary user flow today: create a goal, submit proof, await verification

```mermaid
sequenceDiagram
    actor User
    participant Frontend as Expo frontend
    participant API as FastAPI API
    participant DB as PostgreSQL
    participant Registry as Goal-type registry
    participant Worker as Celery worker
    participant External as Verification target

    User->>Frontend: Navigate to goal-create
    User->>Frontend: Fill typed goal form
    Frontend->>API: POST /api/goals with goal_type + criteria JSON
    API->>DB: Persist goal and criteria
    API->>DB: Create goal_created notification
    API-->>Frontend: Goal response

    User->>Frontend: Open proof submission screen
    User->>Frontend: Submit proof payload as JSON
    Frontend->>API: POST /api/goals/{goal_id}/submit-proof
    API->>Registry: get_type(goal.goal_type)
    API->>DB: Create ProofSubmission(status=pending)
    API->>Worker: dispatch_verification(...)
    Worker->>External: Verify video, endpoint, repo, or sandbox target
    Worker->>DB: Persist verification result

    Frontend->>API: GET /api/goals/{goal_id}/verification-status
    API->>DB: Load latest submission result
    API-->>Frontend: verification_status + verification_details
```
