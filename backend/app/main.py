from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Sacrifice API", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
