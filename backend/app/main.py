from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routes.auth import router as auth_router
from app.routes.goals import router as goals_router
from app.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Sacrifice API", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(goals_router)
