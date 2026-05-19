import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.auth import (
    create_access_token,
    decode_access_token,
    exchange_github_code,
    exchange_google_code,
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


@router.get("/google/login")
async def google_login(request: Request):
    state = secrets.token_urlsafe(32)
    redirect_uri = str(request.url_for("google_callback"))
    params = {
        "response_type": "code",
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(key="oauth_state", value=state, httponly=True, max_age=300)
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error={error}", status_code=302
        )
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    cookie_state = request.cookies.get("oauth_state")
    if cookie_state and state != cookie_state:
        raise HTTPException(status_code=400, detail="State mismatch")
    redirect_uri = str(request.url_for("google_callback"))
    try:
        token_data = await exchange_google_code(code, redirect_uri)
    except ValueError:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error=invalid_code", status_code=302
        )
    id_token = token_data.get("id_token")
    if not id_token:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error=missing_id_token", status_code=302
        )
    google_data = await verify_google_token(id_token)
    user = await get_or_create_user(
        db=db,
        provider="google",
        provider_id=google_data["sub"],
        email=google_data["email"],
        display_name=google_data["name"],
        avatar_url=google_data.get("picture"),
    )
    access_token = create_access_token(str(user.id))
    resp = RedirectResponse(
        url=f"{settings.frontend_url}?access_token={access_token}", status_code=302
    )
    resp.delete_cookie("oauth_state")
    return resp


@router.get("/github/login")
async def github_login(request: Request):
    state = secrets.token_urlsafe(32)
    redirect_uri = str(request.url_for("github_callback"))
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",
        "state": state,
    }
    url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(key="oauth_state", value=state, httponly=True, max_age=300)
    return resp


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error={error}", status_code=302
        )
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    cookie_state = request.cookies.get("oauth_state")
    if cookie_state and state != cookie_state:
        raise HTTPException(status_code=400, detail="State mismatch")
    try:
        github_data = await exchange_github_code(code)
    except ValueError:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error=invalid_code", status_code=302
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
    resp = RedirectResponse(
        url=f"{settings.frontend_url}?access_token={access_token}", status_code=302
    )
    resp.delete_cookie("oauth_state")
    return resp


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
