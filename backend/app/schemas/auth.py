from typing import Any

from pydantic import BaseModel, EmailStr, Field


class EmailRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = None


class EmailLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str


class AuthResponse(TokenResponse):
    user: dict[str, Any]


class AuthErrorResponse(BaseModel):
    """Body of a 4xx response from an email-auth endpoint.

    ``error`` is a stable machine-readable code (``account_exists``,
    ``invalid_credentials``). ``provider`` is populated when ``error`` is
    ``account_exists`` so the frontend can point the user at the right
    sign-in button.
    """

    error: str
    provider: str | None = None
