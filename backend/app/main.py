from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.auth import router as auth_router
from app.routes.dashboard import router as dashboard_router
from app.routes.goals import router as goals_router
from app.routes.health import router as health_router
from app.routes.notifications import router as notifications_router
from app.routes.payment import router as payment_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Sacrifice API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8082", "http://localhost:8081", "http://localhost:19006"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(goals_router)
app.include_router(notifications_router)
app.include_router(payment_router)
