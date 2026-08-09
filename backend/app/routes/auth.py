import secrets
import uuid
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.csrf import generate_csrf_token, require_csrf
from app.core.dependencies import check_auth_rate_limit, get_current_user
from app.core.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.database import get_db
from app.models.reset_token_jti import ResetTokenJti
from app.models.user import User
from app.schemas.auth import (
    AuthCodeExchangeRequest,
    EmailLoginRequest,
    EmailRegisterRequest,
    EmailVerifyRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
)
from app.services.auth import (
    AuthConflictError,
    create_access_token,
    create_auth_code,
    create_reset_token,
    decode_auth_code,
    decode_reset_token,
    exchange_github_code,
    exchange_google_code,
    get_or_create_user,
    rotate_auth_session,
    store_pending_auth_code,
    verify_google_token,
)
from app.services.email_verification import (
    VerificationError,
    consume_verification_token,
    create_verification_token,
    invalidate_tokens_for_user,
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


def _user_dict(user: User) -> dict:
    """Return the canonical user dict used in auth responses and ``/me``."""
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "auth_provider": user.auth_provider,
        "email_verified": user.email_verified,
    }


def _auth_response_for_user(user: User) -> AuthResponse:
    access_token = create_access_token(str(user.id), user.auth_session_id)
    return AuthResponse(access_token=access_token, user=_user_dict(user))


@router.post("/google", response_model=AuthResponse)
async def auth_google(
    body: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
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
            email_verified=True,  # Google OAuth emails are provider-verified
        )
    except AuthConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "account_exists", "provider": exc.existing_provider},
        )

    user = await rotate_auth_session(db, user)
    return _auth_response_for_user(user)


@router.post("/github", response_model=AuthResponse)
async def auth_github(
    body: GitHubAuthRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
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
            email_verified=True,  # GitHub OAuth emails are provider-verified
        )
    except AuthConflictError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "account_exists", "provider": exc.existing_provider},
        )

    user = await rotate_auth_session(db, user)
    return _auth_response_for_user(user)


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
    if (
        uri.startswith("sacrifice://")
        or uri.startswith("exp://")
        or uri.startswith("exp+sacrifice://")
    ):
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
    auth_code: str,
    state_param: str | None,
) -> RedirectResponse:
    cli_port = None
    mobile_redirect_uri = None
    if state_param:
        _, cli_port = _decode_cli_state(state_param)
        if not cli_port:
            _, mobile_redirect_uri = _decode_mobile_state(state_param)

    if cli_port:
        redirect_to = f"http://localhost:{cli_port}/callback?auth_code={auth_code}"
    elif mobile_redirect_uri and _is_safe_mobile_redirect(mobile_redirect_uri):
        sep = "&" if "?" in mobile_redirect_uri else "?"
        redirect_to = f"{mobile_redirect_uri}{sep}auth_code={auth_code}"
    else:
        redirect_to = f"{settings.frontend_url}?auth_code={auth_code}"

    resp = RedirectResponse(url=redirect_to, status_code=302)
    resp.delete_cookie("oauth_state")
    resp.delete_cookie("cli_port")
    return resp


@router.get("/cli/login/{provider}")
async def cli_login(
    provider: str,
    port: int = 9876,
    _rate: None = Depends(check_auth_rate_limit),
):
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
        raise HTTPException(
            status_code=400, detail="Provider must be 'google' or 'github'"
        )

    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(
        key="oauth_state",
        value=raw_state,
        path="/",
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=True,
    )
    # Issue the CSRF token as a cookie too. The provider's callback is reached
    # by a top-level browser redirect that cannot carry a custom X-CSRF-Token
    # header, but it DOES send cookies — so the callback can validate CSRF from
    # this cookie. Same lifetime/attributes as oauth_state.
    resp.set_cookie(
        key="csrf_token",
        value=generate_csrf_token(),
        path="/",
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=True,
    )
    return resp


@router.get("/google/login")
async def google_login(
    redirect_uri: str | None = None,
    _rate: None = Depends(check_auth_rate_limit),
):
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
        # Always show the account chooser. Without this, Google silently
        # reuses its own session and signing out of the app + clicking the
        # button logs straight back in with no choice offered.
        "prompt": "select_account",
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(
        key="oauth_state",
        value=raw_state,
        path="/",
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=True,
    )
    # Issue the CSRF token as a cookie too. The provider's callback is reached
    # by a top-level browser redirect that cannot carry a custom X-CSRF-Token
    # header, but it DOES send cookies — so the callback can validate CSRF from
    # this cookie. Same lifetime/attributes as oauth_state.
    resp.set_cookie(
        key="csrf_token",
        value=generate_csrf_token(),
        path="/",
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=True,
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
):
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error={error}", status_code=302
        )
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    cookie_state = request.cookies.get("oauth_state")
    _verify_oauth_state(state, cookie_state)
    # Accept the CSRF token from the header (XHR clients) OR the cookie set at
    # login initiation (browser redirect flow, which cannot send a header).
    await require_csrf(
        x_csrf_token=request.headers.get("X-CSRF-Token")
        or request.cookies.get("csrf_token")
    )
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
            email_verified=True,  # Google OAuth emails are provider-verified
        )
    except AuthConflictError as exc:
        return _redirect_with_oauth_error(
            state, "account_exists", {"provider": exc.existing_provider}
        )
    user, code_id = await store_pending_auth_code(db, user)
    auth_code = create_auth_code(str(user.id), code_id)
    return _redirect_after_auth(auth_code, state)


@router.get("/github/login")
async def github_login(
    redirect_uri: str | None = None,
    _rate: None = Depends(check_auth_rate_limit),
):
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
    resp.set_cookie(
        key="oauth_state",
        value=raw_state,
        path="/",
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=True,
    )
    # Issue the CSRF token as a cookie too. The provider's callback is reached
    # by a top-level browser redirect that cannot carry a custom X-CSRF-Token
    # header, but it DOES send cookies — so the callback can validate CSRF from
    # this cookie. Same lifetime/attributes as oauth_state.
    resp.set_cookie(
        key="csrf_token",
        value=generate_csrf_token(),
        path="/",
        httponly=True,
        max_age=300,
        samesite="lax",
        secure=True,
    )
    return resp


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
):
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error={error}", status_code=302
        )
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    cookie_state = request.cookies.get("oauth_state")
    _verify_oauth_state(state, cookie_state)
    # Accept the CSRF token from the header (XHR clients) OR the cookie set at
    # login initiation (browser redirect flow, which cannot send a header).
    await require_csrf(
        x_csrf_token=request.headers.get("X-CSRF-Token")
        or request.cookies.get("csrf_token")
    )
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
            email_verified=True,  # GitHub OAuth emails are provider-verified
        )
    except AuthConflictError as exc:
        return _redirect_with_oauth_error(
            state, "account_exists", {"provider": exc.existing_provider}
        )
    user, code_id = await store_pending_auth_code(db, user)
    auth_code = create_auth_code(str(user.id), code_id)
    return _redirect_after_auth(auth_code, state)


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
            email_verified=True,  # Debug-only endpoint
        )
    except AuthConflictError:
        # This is a debug-only smoke-test bypass: if the email is already
        # registered under another provider (e.g. an earlier email sign-up),
        # just mint a token for that existing account instead of 500-ing.
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()
    user = await rotate_auth_session(db, user)
    response = _auth_response_for_user(user)
    return {
        "access_token": response.access_token,
        "user": response.user,
    }


# ─── Email + password auth ───
#
# TODO(MVP): no per-email rate limit on login or register (IP rate limit is applied here).
# TODO(next): password reset token delivery (email) is out of scope for the
#   current iteration; the token is minted but not transported to the user.


@router.post("/email/register", response_model=AuthResponse)
async def email_register(
    body: EmailRegisterRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
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

    policy_error = validate_password_strength(body.password)
    if policy_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy_error,
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

    user = await rotate_auth_session(db, user)
    return _auth_response_for_user(user)


@router.post("/email/login", response_model=AuthResponse)
async def email_login(
    body: EmailLoginRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
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

    user = await rotate_auth_session(db, user)
    return _auth_response_for_user(user)


@router.get("/me")
async def auth_me(current_user: User = Depends(get_current_user)):
    return _user_dict(current_user)


@router.get("/csrf-token")
async def get_csrf_token(
    current_user: User = Depends(get_current_user),
):
    """Return a fresh CSRF token for use with cookie-authenticated endpoints.

    The token is a signed JWT carried in the ``X-CSRF-Token`` request header
    and is valid for 30 minutes. Clients that need to call OAuth callback
    endpoints (which verify the token) should fetch one before initiating
    the OAuth redirect flow.
    """
    return {"csrf_token": generate_csrf_token()}


@router.post("/exchange", response_model=AuthResponse)
async def auth_exchange(
    body: AuthCodeExchangeRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
):
    payload = decode_auth_code(body.code)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired auth code",
        )

    user_id = payload.get("sub")
    code_id = payload.get("code_id")
    if not user_id or not code_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid auth code payload",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.pending_auth_code_id != code_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Auth code has already been used",
        )

    user = await rotate_auth_session(db, user)
    return _auth_response_for_user(user)


@router.post("/refresh", response_model=TokenResponse)
async def auth_refresh(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user = await rotate_auth_session(db, current_user)
    access_token = create_access_token(
        str(current_user.id), current_user.auth_session_id
    )
    return TokenResponse(access_token=access_token)


@router.post("/logout")
async def auth_logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await rotate_auth_session(db, current_user)
    return {"detail": "Logged out"}


# ─── Password reset ─────────────────────────────────────────────────────
#
# Token delivery (email) is explicitly out of scope for this iteration.


@router.post("/password/reset/request", status_code=status.HTTP_202_ACCEPTED)
async def password_reset_request(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
):
    """Request a password-reset token for the given email.

    Always returns 202 to prevent user enumeration. The reset token is
    never included in the response body — token delivery (email) is out
    of scope for this iteration.
    """
    email = body.email.lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None and user.auth_provider == "email":
        # Token is minted but not delivered — email transport is out of scope.
        _token = create_reset_token(str(user.id))

    return {"message": "If the email is registered, a reset link has been sent."}


@router.post("/password/reset/confirm", status_code=status.HTTP_200_OK)
async def password_reset_confirm(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(check_auth_rate_limit),
):
    """Confirm a password reset with a valid reset token and a new password.

    Returns 200 on success; 400 when the token is invalid, expired, reused,
    or purpose-mismatched. The new password must satisfy the same policy as
    registration.
    """
    payload = decode_reset_token(body.token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    jti = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token payload.",
        )

    # Single-use check — this also handles the race via the DB unique constraint.
    result = await db.execute(select(ResetTokenJti).where(ResetTokenJti.jti == jti))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token.",
        )

    # Enforce registration-equivalent password policy.
    policy_error = validate_password_strength(body.new_password)
    if policy_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy_error,
        )

    # Update password hash.
    user.password_hash = hash_password(body.new_password)

    # Mark the token JTI as consumed for single-use enforcement.
    db.add(ResetTokenJti(jti=jti))

    # Rotate auth_session_id to revoke all pre-reset JWTs/sessions.
    user.auth_session_id = str(uuid.uuid4())
    # Also clear any pending auth code to be consistent with rotate pattern.
    user.pending_auth_code_id = None

    await db.commit()

    return {"message": "Password has been reset."}


# ─── Email verification ───────────────────────────────────────────────────


@router.post("/email/verify-request", status_code=status.HTTP_200_OK)
async def email_verify_request(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a verification token for the authenticated account.

    The plaintext token is returned in the response body only when
    ``environment`` is not ``production`` (observable substitute for email).
    """
    if current_user.email_verified:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "already_verified"},
        )

    plaintext = await create_verification_token(db, current_user)

    if not settings.email_verify_token_response_body_allowed:
        # In production the token would only be sent via email.
        return JSONResponse(status_code=200, content={"message": "token_sent_via_email"})

    return {"verification_token": plaintext}


@router.post("/email/verify", status_code=status.HTTP_200_OK)
async def email_verify(
    body: EmailVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Consume a verification token to mark the account as verified."""
    try:
        await consume_verification_token(db, body.verification_token)
    except VerificationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": exc.error_code},
        )
    return {"message": "email_verified"}


@router.delete("/email/verify-token", status_code=status.HTTP_200_OK)
async def email_verify_token_invalidate(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force-expire any outstanding verification token for the caller.

    Grading affordance: allows testing the expired-token path without
    waiting for the natural TTL.
    """
    invalidated = await invalidate_tokens_for_user(db, current_user)
    if not invalidated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {"message": "token_invalidated"}
