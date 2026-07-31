import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz")
@router.get("/api/health")
async def health_check():
    return {"status": "ok"}


@router.get("/healthz/db")
async def healthz_db(db: AsyncSession = Depends(get_db)):
    """Readiness check: verify DB reachability with a trivial read.

    Performs a SELECT 1 round-trip (no writes, no auth required).
    Returns 200 ``{"db": "ok"}`` on success, 503 ``{"db": "unreachable"}``
    when the DB is not reachable.
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"db": "ok"}
    except Exception:
        logger.exception("Database health-check failed")
        return JSONResponse(status_code=503, content={"db": "unreachable"})
