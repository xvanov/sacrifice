# Architecture diagrams

## System flow

```mermaid
flowchart LR
    User((User))

    subgraph Clients
        Mobile[Expo app\nfrontend/App.tsx]
        CLI[Click CLI\nbackend/cli/main.py]
    end

    subgraph Backend
        API[FastAPI app\nbackend/app/main.py]
        Goals[Goal routes\nbackend/app/routes/goals.py]
        Registry[Goal-type registry\nbackend/app/goal_types/registry.py]
        Workers[Celery worker/beat\nbackend/app/core/celery_app.py]
    end

    subgraph Data
        Postgres[(PostgreSQL)]
        Redis[(Redis)]
    end

    subgraph Configured Integrations
        OAuth[Google + GitHub OAuth\nbackend/app/config.py]
        Stripe[Stripe\nbackend/app/config.py]
        External[YouTube + Azure Foundry\nbackend/app/config.py]
    end

    User --> Mobile
    User --> CLI
    Mobile -->|JSON HTTP| API
    CLI -->|JSON HTTP| API
    API --> Goals
    Goals --> Registry
    API --> Postgres
    Goals --> Postgres
    Workers --> Redis
    Workers --> Postgres
    Registry -. include modules .-> Workers
    API -. configured by env .-> OAuth
    API -. configured by env .-> Stripe
    Registry -. goal-type integrations .-> External
```

## Primary user flow

```mermaid
sequenceDiagram
    actor User
    participant App as Expo app
    participant API as FastAPI goals routes
    participant DB as PostgreSQL
    participant Registry as Goal-type registry
    participant Worker as Optional Celery dispatch

    User->>App: Fill goal form with one built-in goal type
    App->>API: POST /api/goals (JSON)
    API->>DB: Insert goal + criteria
    API->>DB: Insert goal_created notification
    API-->>App: Goal response

    User->>App: Submit proof before deadline
    App->>API: POST /api/goals/{goal_id}/submit-proof (JSON)
    API->>DB: Load goal + criteria
    API->>Registry: get_type(goal.goal_type).verify(proof_data, criteria_data)
    Registry-->>API: Verification result

    alt verifier rejects immediately
        API-->>App: Rejected response with verification details
    else verifier accepts pending submission
        API->>DB: Insert proof_submission JSONB row
        opt goal type exposes dispatch_verification
            API->>Worker: dispatch_verification(...)
        end
        API->>DB: Insert proof_received notification
        API-->>App: 202 Accepted / pending
    end
```
