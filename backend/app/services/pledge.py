"""Pledge.to API client — public-charity search AND automatic donations.

Unlike Every.org (donate links only), Pledge.to exposes a server-side
donation-creation endpoint, so a failed goal's pledge can be disbursed to a
public charity with no human involvement: charge the card via Stripe, then
POST the donation here. Donations are billed to the platform's Pledge.to
account (fund it / set up billing in their dashboard).

Pledge recipients are stored on goals as ``charity_id = "pledge:<org-uuid>"``.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

PLEDGE_PREFIX = "pledge:"
_BASE = "https://api.pledge.to/v1"


def is_pledge_id(charity_id: str | None) -> bool:
    return bool(charity_id) and charity_id.startswith(PLEDGE_PREFIX)


def pledge_org_id(charity_id: str) -> str:
    return charity_id[len(PLEDGE_PREFIX) :]


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.pledge_api_key}"}


async def search_organizations(query: str, per_page: int = 10) -> list[dict]:
    """Search Pledge.to organizations. Returns [] when unconfigured/on error."""
    if not settings.pledge_api_key or not query.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_BASE}/organizations",
                params={"q": query.strip(), "per_page": per_page},
                headers=_headers(),
            )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:  # noqa: BLE001 — search must degrade, not 500
        logger.warning("Pledge.to search failed for %r: %s", query, e)
        return []

    out = []
    for org in results:
        org_id = org.get("id")
        if not org_id:
            continue
        location = ", ".join(part for part in (org.get("city"), org.get("region")) if part) or None
        out.append(
            {
                "id": f"{PLEDGE_PREFIX}{org_id}",
                "name": org.get("name") or org_id,
                "description": (org.get("mission") or "")[:140] or None,
                "location": location,
                "source": "pledge",
            }
        )
    return out


async def get_organization_name(charity_id: str) -> str | None:
    if not is_pledge_id(charity_id) or not settings.pledge_api_key:
        return None
    org_id = pledge_org_id(charity_id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_BASE}/organizations/{org_id}", headers=_headers())
        resp.raise_for_status()
        return resp.json().get("name") or org_id
    except Exception as e:  # noqa: BLE001
        logger.warning("Pledge.to lookup failed for %r: %s", charity_id, e)
        return None


async def create_donation(
    charity_id: str,
    amount_cents: int,
    *,
    email: str,
    first_name: str,
    last_name: str,
    external_id: str | None = None,
) -> dict:
    """Create a real donation. Raises on failure — the caller decides how a
    disbursement error is recorded (the card charge must never be blocked or
    rolled back by it).

    ``amount`` is decimal dollars per the Pledge API. If their contract were
    ever cents, this format under-donates rather than over-donating — the
    safe failure direction for real money.
    """
    if not settings.pledge_api_key:
        raise RuntimeError("Pledge.to is not configured")
    payload = {
        "organization_id": pledge_org_id(charity_id),
        "amount": f"{amount_cents / 100:.2f}",
        "email": email,
        "first_name": first_name or "Sacrifice",
        "last_name": last_name or "Pledger",
    }
    if external_id:
        payload["external_id"] = external_id
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_BASE}/donations", json=payload, headers=_headers())
    if resp.status_code >= 400:
        raise RuntimeError(f"Pledge.to donation failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()
