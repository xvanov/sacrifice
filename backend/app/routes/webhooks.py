"""Stripe webhook endpoint.

Belt-and-suspenders for the off-session charge flow: even though
``process_charge_for_goal`` confirms the PaymentIntent synchronously, Stripe is
the source of truth for whether money actually moved (async captures, disputes,
delayed failures). This endpoint verifies the event signature and reconciles
the local Payment row + goal status. Without it, a charge that completed
asynchronously would never be reflected (Direction 019).

Signature verification is mandatory: an unsigned/mis-signed request is rejected
with 400 so a forged "you succeeded" event can't flip a goal.
"""

from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# PaymentIntent status → (local payment status, resulting goal status).
# A charged pledge means the goal FAILED and money moved; a failed charge
# leaves the goal payment_failed for follow-up.
_PI_EVENT_MAP = {
    "payment_intent.succeeded": ("succeeded", "failed"),
    "payment_intent.payment_failed": ("failed", "payment_failed"),
}


async def _reconcile_payment_intent(
    db: AsyncSession, *, pi_id: str, goal_id: str | None, payment_status: str, goal_status: str
) -> None:
    # Idempotent: re-delivering the same event just re-applies the same row.
    await db.execute(
        text("UPDATE payments SET status = :s WHERE stripe_payment_intent_id = :pi"),
        {"s": payment_status, "pi": pi_id},
    )
    if goal_id:
        # Never override a goal the user legitimately completed.
        await db.execute(
            text("UPDATE goals SET status = :s WHERE id = :g AND status != 'verified'"),
            {"s": goal_status, "g": goal_id},
        )
    await db.commit()


@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    secret = settings.stripe_webhook_secret
    if not secret:
        # Fail closed: without a secret we cannot trust any event.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook secret not configured",
        )

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload"
        ) from None
    except stripe.error.SignatureVerificationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature"
        ) from None

    event_type = event["type"]
    mapping = _PI_EVENT_MAP.get(event_type)
    if mapping is None:
        # Unhandled event types are acknowledged (200) so Stripe stops retrying.
        return {"received": True, "handled": False}

    payment_status, goal_status = mapping
    obj = event["data"]["object"]
    pi_id = obj.get("id")
    goal_id = (obj.get("metadata") or {}).get("goal_id")

    await _reconcile_payment_intent(
        db,
        pi_id=pi_id,
        goal_id=goal_id,
        payment_status=payment_status,
        goal_status=goal_status,
    )

    logger.info("Reconciled %s for payment_intent=%s goal=%s", event_type, pi_id, goal_id)
    return {"received": True, "handled": True}
