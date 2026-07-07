from fastapi import APIRouter

router = APIRouter(tags=["health"])


def _health_response() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/health")
async def health_check():
    return _health_response()


@router.get("/healthz")
async def healthz():
    return _health_response()
