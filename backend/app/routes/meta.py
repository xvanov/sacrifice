from datetime import datetime, timezone

from fastapi import APIRouter

VERSION = "0.1.0"

router = APIRouter(tags=["meta"])


@router.get("/api/meta")
async def meta():
    return {
        "service": "sacrifice",
        "version": VERSION,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }
