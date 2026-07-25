"""A stored Stripe customer id that the current account does not recognise.

Stripe customer ids are **mode-scoped**: one created with a ``sk_test_`` key does
not exist to a ``sk_live_`` key. So the moment the app switched to live keys, every
request to the payments page raised ``InvalidRequestError: No such customer`` and
returned HTTP 500 — on the one page whose purpose is adding a card, with nothing
shown to explain it. The stored id was trusted unconditionally.

The same thing happens without a mode switch: delete a customer in the Stripe
dashboard and that user's payments page breaks permanently.

What these pin is the asymmetry that makes healing safe. "Gone" must mean
*definitively* gone — Stripe's ``resource_missing``, or an object flagged deleted.
On any other failure the error propagates, because creating a second customer for a
user who already has one orphans the card saved against the first: the next
off-session charge then finds no payment method
(``workers/payments._resolve_payment_method`` returns None) and a pledge that
should have been collected silently cannot be.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
import stripe

from app.models.user import User
from app.routes.payment import _get_or_create_stripe_customer, _stripe_customer_is_gone


def _user(customer_id: str | None) -> User:
    return User(
        id=uuid.uuid4(),
        email="card@example.com",
        display_name="Card Owner",
        auth_provider="google",
        auth_provider_id="sub-card",
        stripe_customer_id=customer_id,
    )


class _Db:
    """Only ``commit`` is exercised by the helper."""

    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _missing_customer_error() -> stripe.InvalidRequestError:
    err = stripe.InvalidRequestError(
        "No such customer: 'cus_stale'", param="customer", code="resource_missing"
    )
    return err


# ── Is it gone? ────────────────────────────────────────────────────────────


def test_resource_missing_means_gone():
    """The exact error live mode produced against a test-mode customer."""
    with patch("stripe.Customer.retrieve", side_effect=_missing_customer_error()):
        assert _stripe_customer_is_gone("cus_stale") is True


def test_a_deleted_customer_is_gone():
    """Some API versions return the object with ``deleted`` set rather than a 404.

    It can be neither charged nor attached to, so it is unusable either way.
    """
    deleted = MagicMock()
    deleted.deleted = True
    with patch("stripe.Customer.retrieve", return_value=deleted):
        assert _stripe_customer_is_gone("cus_deleted") is True


def test_a_live_customer_is_not_gone():
    live = MagicMock()
    live.deleted = False
    with patch("stripe.Customer.retrieve", return_value=live):
        assert _stripe_customer_is_gone("cus_good") is False


@pytest.mark.parametrize(
    "error",
    [
        stripe.APIConnectionError("network down"),
        stripe.RateLimitError("slow down"),
        stripe.AuthenticationError("bad key"),
        stripe.InvalidRequestError("something else entirely", param="other"),
    ],
)
def test_any_other_failure_propagates_rather_than_meaning_gone(error):
    """The dangerous direction, refused by name.

    An outage, a rate limit, a bad key, or a different InvalidRequestError says
    nothing about whether the customer exists. Reading any of them as "gone" would
    mint a duplicate customer and orphan the card saved against the original.
    """
    with patch("stripe.Customer.retrieve", side_effect=error):
        with pytest.raises(type(error)):
            _stripe_customer_is_gone("cus_good")


# ── Healing ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stale_customer_is_replaced_and_persisted():
    """The fix for the 500: a fresh customer, stored, and the request proceeds."""
    user = _user("cus_stale")
    db = _Db()
    created = MagicMock()
    created.id = "cus_fresh"

    with (
        patch("stripe.Customer.retrieve", side_effect=_missing_customer_error()),
        patch("stripe.Customer.create", return_value=created) as create,
    ):
        result = await _get_or_create_stripe_customer(user, db)

    assert result == "cus_fresh"
    assert user.stripe_customer_id == "cus_fresh", "the new id must be persisted"
    assert db.commits == 1
    assert create.call_args.kwargs["metadata"] == {"user_id": str(user.id)}


@pytest.mark.asyncio
async def test_a_usable_customer_is_reused_and_nothing_is_created():
    """No churn on the normal path — and no duplicate customers per request."""
    user = _user("cus_good")
    db = _Db()
    live = MagicMock()
    live.deleted = False

    with (
        patch("stripe.Customer.retrieve", return_value=live),
        patch("stripe.Customer.create") as create,
    ):
        result = await _get_or_create_stripe_customer(user, db)

    assert result == "cus_good"
    create.assert_not_called()
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_user_with_no_customer_gets_one_without_a_lookup():
    """Nothing to verify, so no wasted API call on first use."""
    user = _user(None)
    db = _Db()
    created = MagicMock()
    created.id = "cus_first"

    with (
        patch("stripe.Customer.retrieve") as retrieve,
        patch("stripe.Customer.create", return_value=created),
    ):
        result = await _get_or_create_stripe_customer(user, db)

    assert result == "cus_first"
    retrieve.assert_not_called()
    assert user.stripe_customer_id == "cus_first"


@pytest.mark.asyncio
async def test_an_outage_does_not_mint_a_duplicate_customer():
    """End-to-end version of the asymmetry: the request fails, the id is kept.

    A 500 the user can retry is the correct outcome here. Silently repointing them
    at a new customer would lose the card behind their live pledges.
    """
    user = _user("cus_good")
    db = _Db()

    with (
        patch("stripe.Customer.retrieve", side_effect=stripe.APIConnectionError("x")),
        patch("stripe.Customer.create") as create,
    ):
        with pytest.raises(stripe.APIConnectionError):
            await _get_or_create_stripe_customer(user, db)

    create.assert_not_called()
    assert user.stripe_customer_id == "cus_good", "the existing id must be untouched"
    assert db.commits == 0
