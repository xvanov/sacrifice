import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core.logging import install_redacting_logging
from app.core.request_id import RequestIDMiddleware
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.dashboard import router as dashboard_router
from app.routes.demo import router as demo_router
from app.routes.goal_count import router as goal_count_router
from app.routes.goals import goal_types_router
from app.routes.goals import router as goals_router
from app.routes.health import router as health_router
from app.routes.meta import router as meta_router
from app.routes.notifications import router as notifications_router
from app.routes.operator import router as operator_router
from app.routes.payment import router as payment_router
from app.routes.uploads import router as uploads_router
from app.routes.webhooks import router as webhooks_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_redacting_logging()
    logger.info("Log redaction installed.")

    # Run goal-type discovery at startup so misconfigured / tampered modules
    # cause a deterministic failure before the server accepts traffic.
    from app.goal_types.registry import discover_all

    discover_all()

    # Which Stripe mode this process came up in, stated once, at WARNING when it
    # is the one that moves real money. "Am I charging real cards?" should be
    # answerable from the log rather than by inspecting a key prefix in a running
    # process — and the answer must be loud, because the whole product is charging
    # someone's card when they miss a goal.
    from app.config import settings

    if settings.stripe_live_mode:
        logger.warning(
            "Stripe LIVE mode: real cards, real charges. A failed goal will move "
            "real money. Webhook reconciliation is %s.",
            "enabled"
            if settings.stripe_webhook_secret
            else "DISABLED (no live signing secret configured)",
        )
    else:
        logger.info("Stripe test mode: real cards will be refused by Stripe.")
    yield


app = FastAPI(title="Sacrifice API", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8082",
        "http://localhost:8081",
        "http://localhost:8090",
        "http://localhost:19006",
        "http://100.82.97.40:8081",
        "http://100.82.97.40:8082",
        "http://100.82.97.40:8090",
        "http://100.82.97.40:19006",
        "http://100.64.38.18",
        "http://100.64.38.18:8082",
        "http://100.64.38.18:8090",
        "https://aaf6-2605-a601-8110-1600-bac1-a36f-b976-c22b.ngrok-free.app",
    ],
    # Allow Expo web (any port) from localhost/127.0.0.1 and the LAN/Tailscale
    # IPs used for device testing. Native Expo Go (the phone) does not send an
    # Origin header / is not subject to CORS, so this only matters for the
    # desktop browser web build — but we keep it permissive across dev ports.
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|10\.110\.1\.68|100\.82\.97\.40|100\.64\.38\.18)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(meta_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(dashboard_router)
app.include_router(goal_types_router)
app.include_router(goal_count_router)
app.include_router(goals_router)
app.include_router(notifications_router)
app.include_router(operator_router)
app.include_router(payment_router)
app.include_router(demo_router)
app.include_router(uploads_router)
app.include_router(webhooks_router)


# GitHub OAuth App has /auth/github/callback registered; redirect to /api/auth/ prefix
@app.get("/auth/github/callback")
async def github_callback_legacy(
    code: str, state: str | None = None, error: str | None = None
):
    url = f"/api/auth/github/callback?code={code}"
    if state:
        url += f"&state={state}"
    if error:
        url += f"&error={error}"
    return RedirectResponse(url=url, status_code=307)
