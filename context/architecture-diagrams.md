# Architecture Diagrams

## System flow

```mermaid
flowchart LR
    User[User]
    MobileWeb[Expo app\nfrontend/App.tsx]
    CLI[Click CLI\nbackend/cli]
    OAuth[Google / GitHub OAuth]
    API[FastAPI app\nbackend/app/main.py]
    Auth[Auth routes + dependencies\nauth.py / services/auth.py / dependencies.py]
    Product[Goals / Payments / Notifications / Uploads]
    DB[(PostgreSQL)]
    Crypto[Fernet token encryption\ncore/crypto.py]
    Stripe[Stripe]
    Migration[Migration scripts\nscripts/migration]

    User --> MobileWeb
    User --> CLI
    MobileWeb -->|OAuth login + bearer API calls| API
    CLI -->|login + authenticated commands| API
    API --> Auth
    Auth --> OAuth
    Auth --> DB
    Auth --> Crypto
    API --> Product
    Product --> DB
    Product --> Stripe
    Migration --> DB
    Migration --> MobileWeb
```

## Primary auth flow

```mermaid
sequenceDiagram
    participant U as User
    participant A as Expo app / LoginScreen
    participant B as FastAPI auth routes
    participant O as OAuth provider
    participant D as PostgreSQL

    U->>A: Tap Google or GitHub sign-in
    A->>B: GET /api/auth/{provider}/login?redirect_uri=sacrifice://...
    B-->>A: 302 to provider + set oauth_state cookie
    A->>O: Open provider auth page
    O-->>B: Callback with code + state
    B->>B: Verify oauth_state and callback state
    B->>D: Find or create user; store pending auth code
    B-->>A: 302 back to app with one-time auth_code
    A->>B: POST /api/auth/exchange { code }
    B->>D: Consume pending code and rotate active session id
    B-->>A: access_token + user payload
    A->>A: Store bearer locally and attach it on API calls
```
