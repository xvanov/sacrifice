from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
@router.get("/api/health")
async def health_check():
    return {"status": "ok"}
