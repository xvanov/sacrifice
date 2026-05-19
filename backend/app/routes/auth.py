from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.auth import (
    create_access_token,
    decode_access_token,
    exchange_github_code,
    get_or_create_user,
    verify_google_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleAuthRequest(BaseModel):
    token: str


class GitHubAuthRequest(BaseModel):
    code: str


class AuthResponse(BaseModel):
    access_token: str
    user: dict


class TokenResponse(BaseModel):
    access_token: str


@router.post("/google", response_model=AuthResponse)
async def auth_google(
    body: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        google_data = await verify_google_token(body.token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        )

    user = await get_or_create_user(
        db=db,
        provider="google",
        provider_id=google_data["sub"],
        email=google_data["email"],
        display_name=google_data["name"],
        avatar_url=google_data.get("picture"),
    )

    access_token = create_access_token(str(user.id))
    return AuthResponse(
        access_token=access_token,
        user={
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "auth_provider": user.auth_provider,
        },
    )


@router.post("/github", response_model=AuthResponse)
async def auth_github(
    body: GitHubAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        github_data = await exchange_github_code(body.code)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub code",
        )

    user = await get_or_create_user(
        db=db,
        provider="github",
        provider_id=github_data["id"],
        email=github_data["email"],
        display_name=github_data["name"],
        avatar_url=github_data.get("avatar_url"),
    )

    access_token = create_access_token(str(user.id))
    return AuthResponse(
        access_token=access_token,
        user={
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "auth_provider": user.auth_provider,
        },
    )


@router.get("/me")
async def auth_me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "display_name": current_user.display_name,
        "avatar_url": current_user.avatar_url,
        "auth_provider": current_user.auth_provider,
    }


@router.post("/refresh", response_model=TokenResponse)
async def auth_refresh(
    current_user: User = Depends(get_current_user),
):
    access_token = create_access_token(str(current_user.id))
    return TokenResponse(access_token=access_token)
