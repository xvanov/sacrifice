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
        GoalTypes[Goal-types endpoint\nGET /api/goal-types]
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
    API --> GoalTypes
    Goals --> Registry
    GoalTypes --> Registry
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

## Chat → factory goal-type generation flow (D009/D010)

When a user's prompt matches no existing goal type, Sacrifice can commission a
new one from the factory and activate the user's goal once it merges.

```mermaid
sequenceDiagram
    participant User
    participant App
    participant Chat as Chat API (chat.py)
    participant Match as chat_match service
    participant Synth as direction_synth service
    participant Vol as directions volume (bind-mount)
    participant Factory as factory chain (external)
    participant DB

    User->>App: Describe goal in chat
    App->>Chat: POST /api/chat/sessions/{id}/messages
    Chat->>Match: match_message(prompt, catalog)
    alt match >= threshold
        Match-->>Chat: match_proposed(goal_type, missing_criteria)
        Chat-->>App: assistant card "Use this goal type"
        Note over App,Chat: conversational criteria fill → ready_to_create
        App->>Chat: POST .../create-goal {goal_payload}
        Chat->>DB: create goal (active)
    else no match
        Match-->>Chat: no_match
        Chat-->>App: card "Build a new goal type?"
        User->>App: "Yes, build it"
        App->>Chat: POST .../request-new-goal-type
        Chat->>Synth: synthesize_direction(chat_history)
        Synth->>Vol: write direction.md (+flow/api_spec)
        Chat->>DB: create goal (awaiting_goal_type, awaiting_direction_id)
        Chat-->>App: 202 {direction_id, goal_id, status: queued}
    end

    Factory->>Vol: pick up direction, run chain
    Note over Factory: queued → in_progress → pr_open → pr_merged

    loop App polls
        App->>Chat: GET .../generation-status
        Chat->>Vol: read direction state.yaml
        Chat-->>App: coarse status
    end

    Factory->>Vol: direction state → pr_merged
    Chat->>DB: fire goal_type_ready notification (idempotent)
    User->>App: tap notification
    alt accept
        App->>Chat: POST .../accept-generated-type
        Chat->>DB: goal awaiting_goal_type → active; clear awaiting_direction_id
    else iterate
        App->>Chat: POST .../iterate-generated-type {feedback}
        Chat->>Synth: synthesize child direction (parent_direction linkage)
        Note over Chat,Factory: new direction runs; goal stays awaiting_goal_type until accept
    end
```
