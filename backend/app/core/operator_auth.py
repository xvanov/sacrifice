"""Authorization for operator-only endpoints.

The problem this solves
-----------------------
Operator endpoints expose *other people's* data — the blocked-goals reader shows
every stuck pledge with the owner's email. Nothing in this codebase distinguishes
a privileged user from an ordinary one: ``users`` has no role, no flag, no group,
and ``get_current_user`` returns the same object for everybody. So there is no
existing authorization to reuse, and the two obvious shortcuts are worse than
having no endpoint at all:

* an email allowlist in code or config — privilege attached to a mutable-looking
  identifier, and one that is not secret;
* trusting anything the client asserts about itself (a header saying "admin", a
  claim the client can mint) — not authorization, only the shape of it.

What this does instead
----------------------
A pre-shared operator secret, configured server-side as
``settings.operator_api_token`` and sent in the ``X-Operator-Token`` header. This
is the same class of credential as ``stripe_webhook_secret``, already used in
this codebase for a route that must not be reachable by users.

Properties that make it defensible:

* **Off by default.** With no token configured, the routes behind this dependency
  return 404 — not 403 — so an unconfigured deployment exposes nothing and does
  not advertise that anything is there.
* **A weak token is treated as no token.** Shorter than
  ``MIN_TOKEN_LENGTH`` and the route stays 404, with a warning in the log. This
  is what stops "temporarily" setting it to ``ops`` in a live environment.
* **Constant-time comparison**, so a wrong token leaks no information about how
  wrong it was.
* **Rate limited per IP**, because a bearer secret with no lockout is otherwise
  online-guessable.
* **User sessions grant nothing.** A valid login is neither sufficient nor
  relevant here: an ordinary authenticated user hitting an operator route is
  rejected exactly like an anonymous one.

Its honest limitation: the secret is shared, so it identifies "an operator" and
not *which* operator. That is a real gap for auditing and is the reason to
prefer a per-user admin flag once the product has more than one operator — see
the module tests for what that would take.
"""

import hmac
import logging

from fastapi import Header, HTTPException, Request, status

from app.config import settings
from app.core.rate_limiter import RateLimitExceeded, check_rate_limit

logger = logging.getLogger(__name__)

OPERATOR_TOKEN_HEADER = "X-Operator-Token"

# A shared secret with no lockout has to be long enough that rate limiting is a
# backstop rather than the only defence. 32 chars is `secrets.token_urlsafe(24)`.
MIN_TOKEN_LENGTH = 32

_RATE_LIMIT = 10
_RATE_WINDOW = 60.0


def operator_access_configured() -> bool:
    """True when a usable operator token is configured."""
    token = settings.operator_api_token or ""
    return len(token) >= MIN_TOKEN_LENGTH


async def require_operator(
    request: Request,
    x_operator_token: str | None = Header(default=None, alias=OPERATOR_TOKEN_HEADER),
) -> None:
    """Gate a route on the operator secret. Raises 404/403/429; returns None."""
    configured = settings.operator_api_token or ""

    if not configured or len(configured) < MIN_TOKEN_LENGTH:
        if configured:
            logger.warning(
                "operator_api_token is set but shorter than %d characters; "
                "operator routes stay disabled",
                MIN_TOKEN_LENGTH,
            )
        # 404, not 403: an unconfigured deployment should look like one that has
        # no such route.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    # Before the comparison, so guessing costs the attacker the same whether or
    # not the header is present.
    try:
        await check_rate_limit(
            request, max_requests=_RATE_LIMIT, window_seconds=_RATE_WINDOW
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(int(exc.retry_after + 1))},
        )

    presented = x_operator_token or ""
    if not hmac.compare_digest(presented, configured):
        logger.warning(
            "Rejected operator request to %s: %s",
            request.url.path,
            "missing operator token" if not presented else "invalid operator token",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator authorization required",
        )
