import secrets
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.core.passwords import hash_password, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.auth import EmailLoginRequest, EmailRegisterRequest
from app.services.auth import (
    AuthConflictError,
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

    try:
        user = await get_or_create_user(
            db=db,
            provider="google",
            provider_id=google_data["sub"],
            email=google_data["email"],
            display_name=google_data["name"],
            avatar_url=google_data.get("picture"),
        )
    except AuthConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "account_exists", "provider": exc.existing_provider},
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

    try:
        user = await get_or_create_user(
            db=db,
            provider="github",
            provider_id=github_data["id"],
            email=github_data["email"],
            display_name=github_data["name"],
            avatar_url=github_data.get("avatar_url"),
        )
    except AuthConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "account_exists", "provider": exc.existing_provider},
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


def _make_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def _encode_cli_state(state: str, port: int) -> str:
    return f"cli|{port}|{state}"


def _encode_mobile_state(state: str, redirect_uri: str) -> str:
    return f"mobile|{redirect_uri}|{state}"


def _decode_cli_state(encoded: str) -> tuple[str, int | None]:
    parts = encoded.split("|", 2)
    if len(parts) == 3 and parts[0] == "cli":
        return parts[2], int(parts[1])
    return encoded, None


def _decode_mobile_state(encoded: str) -> tuple[str, str | None]:
    parts = encoded.split("|", 2)
    if len(parts) == 3 and parts[0] == "mobile":
        return parts[2], parts[1]
    return encoded, None


def _verify_oauth_state(state: str | None, cookie_state: str | None) -> str | None:
    """Validate the OAuth ``state`` parameter and return the raw nonce.

    Browser-initiated flows MUST present an ``oauth_state`` cookie that
    matches the state nonce — a missing cookie is treated as a CSRF failure
    rather than silently passing.  CLI/mobile flows can't reliably set
    cookies across browser contexts, so they fall back on the unguessable
    nonce inside the encoded state (and, for mobile, on the redirect_uri
    allowlist enforced by :func:`_is_safe_mobile_redirect`).
    """
    if state and state.startswith("cli|"):
        raw_state, _ = _decode_cli_state(state)
    elif state and state.startswith("mobile|"):
        raw_state, _ = _decode_mobile_state(state)
    else:
        raw_state = state

    if not (state and state.startswith(("cli|", "mobile|"))):
        if not cookie_state or not raw_state or raw_state != cookie_state:
            raise HTTPException(status_code=400, detail="State mismatch")

    return raw_state


def _is_safe_mobile_redirect(uri: str) -> bool:
    if not uri:
        return False
    if uri.startswith("sacrifice://") or uri.startswith("exp://") or uri.startswith("exp+sacrifice://"):
        return True
    try:
        target = urlparse(uri)
        allowed = urlparse(settings.frontend_url)
    except Exception:
        return False
    return target.scheme == allowed.scheme and target.netloc == allowed.netloc


def _redirect_with_oauth_error(
    state_param: str | None,
    error: str,
    extra: dict[str, str] | None = None,
) -> RedirectResponse:
    """Redirect the browser / CLI / mobile flow back to the originating
    surface with ``?error=<error>`` (and any extra query params).

    Mirrors :func:`_redirect_after_auth`'s routing logic so that an
    error returns to wherever the user came from rather than always to
    the web frontend.
    """
    cli_port = None
    mobile_redirect_uri = None
    if state_param:
        _, cli_port = _decode_cli_state(state_param)
        if not cli_port:
            _, mobile_redirect_uri = _decode_mobile_state(state_param)

    params = {"error": error}
    if extra:
        params.update(extra)
    qs = urlencode(params)

    if cli_port:
        redirect_to = f"http://localhost:{cli_port}/callback?{qs}"
    elif mobile_redirect_uri and _is_safe_mobile_redirect(mobile_redirect_uri):
        sep = "&" if "?" in mobile_redirect_uri else "?"
        redirect_to = f"{mobile_redirect_uri}{sep}{qs}"
    else:
        redirect_to = f"{settings.frontend_url}?{qs}"

    resp = RedirectResponse(url=redirect_to, status_code=302)
    resp.delete_cookie("oauth_state")
    resp.delete_cookie("cli_port")
    return resp


def _redirect_after_auth(
    access_token: str,
    state_param: str | None,
    request: Request,
) -> RedirectResponse:
    cli_port = None
    mobile_redirect_uri = None
    if state_param:
        _, cli_port = _decode_cli_state(state_param)
        if not cli_port:
            _, mobile_redirect_uri = _decode_mobile_state(state_param)

    if cli_port:
        redirect_to = f"http://localhost:{cli_port}/callback?access_token={access_token}"
    elif mobile_redirect_uri and _is_safe_mobile_redirect(mobile_redirect_uri):
        sep = "&" if "?" in mobile_redirect_uri else "?"
        redirect_to = f"{mobile_redirect_uri}{sep}access_token={access_token}"
    else:
        redirect_to = f"{settings.frontend_url}?access_token={access_token}"

    resp = RedirectResponse(url=redirect_to, status_code=302)
    resp.delete_cookie("oauth_state")
    resp.delete_cookie("cli_port")
    return resp


@router.get("/cli/login/{provider}")
async def cli_login(provider: str, port: int = 9876):
    raw_state = _make_oauth_state()
    state = _encode_cli_state(raw_state, port)
    redirect_uri = (
        settings.google_redirect_uri
        if provider == "google"
        else settings.github_redirect_uri
    )

    if provider == "google":
        params = {
            "response_type": "code",
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
        }
        url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    elif provider == "github":
        params = {
            "client_id": settings.github_client_id,
            "redirect_uri": redirect_uri,
            "scope": "user:email",
            "state": state,
        }
        url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    else:
        raise HTTPException(status_code=400, detail="Provider must be 'google' or 'github'")

    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(key="oauth_state", value=raw_state, path="/", httponly=True, max_age=300, samesite="lax")
    return resp


@router.get("/google/login")
async def google_login(redirect_uri: str | None = None):
    raw_state = _make_oauth_state()
    if redirect_uri:
        state = _encode_mobile_state(raw_state, redirect_uri)
    else:
        state = raw_state
    params = {
        "response_type": "code",
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(key="oauth_state", value=raw_state, path="/", httponly=True, max_age=300, samesite="lax")
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
    _verify_oauth_state(state, cookie_state)
    try:
        token_data = await exchange_google_code(code, settings.google_redirect_uri)
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
    try:
        user = await get_or_create_user(
            db=db,
            provider="google",
            provider_id=google_data["sub"],
            email=google_data["email"],
            display_name=google_data["name"],
            avatar_url=google_data.get("picture"),
        )
    except AuthConflictError as exc:
        return _redirect_with_oauth_error(
            state, "account_exists", {"provider": exc.existing_provider}
        )
    access_token = create_access_token(str(user.id))
    return _redirect_after_auth(access_token, state, request)


@router.get("/github/login")
async def github_login(redirect_uri: str | None = None):
    raw_state = _make_oauth_state()
    if redirect_uri:
        state = _encode_mobile_state(raw_state, redirect_uri)
    else:
        state = raw_state
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": "user:email",
        "state": state,
    }
    url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(key="oauth_state", value=raw_state, path="/", httponly=True, max_age=300, samesite="lax")
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
    _verify_oauth_state(state, cookie_state)
    try:
        github_data = await exchange_github_code(code)
    except ValueError:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error=invalid_code", status_code=302
        )
    try:
        user = await get_or_create_user(
            db=db,
            provider="github",
            provider_id=github_data["id"],
            email=github_data["email"],
            display_name=github_data["name"],
            avatar_url=github_data.get("avatar_url"),
        )
    except AuthConflictError as exc:
        return _redirect_with_oauth_error(
            state, "account_exists", {"provider": exc.existing_provider}
        )
    access_token = create_access_token(str(user.id))
    return _redirect_after_auth(access_token, state, request)


@router.get("/dev/token")
async def dev_token(
    email: str = "dev@example.com",
    db: AsyncSession = Depends(get_db),
):
    if not settings.debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    try:
        user = await get_or_create_user(
            db=db,
            provider="dev",
            provider_id=email,
            email=email,
            display_name="Dev User",
            avatar_url=None,
        )
    except AuthConflictError:
        # This is a debug-only smoke-test bypass: if the email is already
        # registered under another provider (e.g. an earlier email sign-up),
        # just mint a token for that existing account instead of 500-ing.
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()
    access_token = create_access_token(str(user.id))
    return {
        "access_token": access_token,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "auth_provider": user.auth_provider,
        },
    }


# ─── Email + password auth ───
#
# TODO(MVP): no email verification — anyone can register with any
# email they don't actually own. Add a verify-by-token flow before
# real users see this.
# TODO(MVP): no password reset / forgot-password flow.
# TODO(MVP): no per-IP / per-email rate limit on login or register.


@router.post("/email/register", response_model=AuthResponse)
async def email_register(
    body: EmailRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    email = body.email.lower()
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "account_exists",
                "provider": existing.auth_provider,
            },
        )

    user = User(
        email=email,
        display_name=body.display_name or email.split("@", 1)[0],
        avatar_url=None,
        auth_provider="email",
        # auth_provider_id is required (non-null); use email as the
        # provider-scoped id since (provider, provider_id) is unique.
        auth_provider_id=email,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

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


@router.post("/email/login", response_model=AuthResponse)
async def email_login(
    body: EmailLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    email = body.email.lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user and user.auth_provider != "email":
        # Account exists under an OAuth provider — tell the frontend
        # which one so it can route the user to the right button.
        # NB: this leaks "this email is registered" to anyone who
        # guesses; acceptable tradeoff for UX vs. the 401 alternative.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "account_exists", "provider": user.auth_provider},
        )

    if not user or not verify_password(body.password, user.password_hash or ""):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "invalid_credentials"},
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
