"""Every.org Charity API client.

Two capabilities are wired here:

- **Search** (public key): find any registered nonprofit, so the charity
  picker isn't limited to Stripe Connect accounts the platform created.
  Every.org recipients need no onboarding.
- **Donate links**: Every.org has no server-side donation-creation API (that
  is partner-beta only), so money can't be pushed to a nonprofit
  programmatically. Instead, when a failed goal's pledge is charged, we mint
  a prefilled donate link (amount + ``partner_donation_id`` tracking) that
  completes the donation through Every.org checkout.

Every.org recipients are stored on goals as ``charity_id = "everyorg:<slug>"``
to distinguish them from Stripe Connect account ids (``acct_…``).
"""

import logging
import urllib.parse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

EVERYORG_PREFIX = "everyorg:"
_SEARCH_URL = "https://partners.every.org/v0.2/search/{query}"
_DETAILS_URL = "https://partners.every.org/v0.2/nonprofit/{identifier}"


def is_everyorg_id(charity_id: str | None) -> bool:
    return bool(charity_id) and charity_id.startswith(EVERYORG_PREFIX)


def everyorg_slug(charity_id: str) -> str:
    return charity_id[len(EVERYORG_PREFIX) :]


async def search_nonprofits(query: str, take: int = 10) -> list[dict]:
    """Search Every.org nonprofits. Returns [] when unconfigured or on error."""
    if not settings.every_org_api_key or not query.strip():
        return []
    url = _SEARCH_URL.format(query=urllib.parse.quote(query.strip()))
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url, params={"apiKey": settings.every_org_api_key, "take": take}
            )
        resp.raise_for_status()
        nonprofits = resp.json().get("nonprofits", [])
    except Exception as e:  # noqa: BLE001 — search must degrade, not 500
        logger.warning("Every.org search failed for %r: %s", query, e)
        return []

    results = []
    for np in nonprofits:
        slug = np.get("slug")
        if not slug or not np.get("donationsEnabled", True):
            continue
        results.append(
            {
                "id": f"{EVERYORG_PREFIX}{slug}",
                "name": np.get("name") or slug,
                "description": np.get("description"),
                "location": np.get("location"),
                "source": "everyorg",
            }
        )
    return results


async def get_nonprofit_name(charity_id: str) -> str | None:
    """Resolve an everyorg:<slug> id to the nonprofit's display name."""
    if not is_everyorg_id(charity_id) or not settings.every_org_api_key:
        return None
    slug = everyorg_slug(charity_id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _DETAILS_URL.format(identifier=urllib.parse.quote(slug)),
                params={"apiKey": settings.every_org_api_key},
            )
        resp.raise_for_status()
        data = resp.json()
        nonprofit = data.get("data", {}).get("nonprofit") or data.get("nonprofit") or {}
        return nonprofit.get("name") or slug
    except Exception as e:  # noqa: BLE001
        logger.warning("Every.org lookup failed for %r: %s", charity_id, e)
        return slug


def build_donate_url(
    charity_id: str,
    amount_cents: int,
    partner_donation_id: str,
    success_url: str | None = None,
) -> str:
    """Prefilled Every.org checkout link for a pledged donation.

    ``partner_donation_id`` ties the completed donation back to our payment
    row (surfaced in Every.org partner webhooks/reports).
    """
    slug = everyorg_slug(charity_id)
    params = {
        "amount": f"{amount_cents / 100:.2f}",
        "frequency": "ONCE",
        "partner_donation_id": partner_donation_id,
    }
    if success_url:
        params["success_url"] = success_url
    return (
        f"https://www.every.org/{urllib.parse.quote(slug)}?{urllib.parse.urlencode(params)}#donate"
    )
