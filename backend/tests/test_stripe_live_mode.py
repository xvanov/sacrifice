"""The switch that decides whether real cards are charged.

A real card in test mode is refused by Stripe ("Your request was in test mode, but
used a non test card"), which is the symptom that leads someone here. Turning the
switch the other way means a failed goal moves real money out of a real account —
so the two failure modes this guards are:

* **Believing you are live when you are not.** Users enter real cards, every
  attempt is refused, and nothing in the app says why. That is what a silent
  fallback to the test keys produces.
* **Believing you are testing when you are not.** Worse: real money moves during
  what somebody thinks is a rehearsal.

Both are avoided the same way — the mode is one named boolean, the live keys live
under their own names so no key is ever pasted over another, and a mismatch is a
startup failure rather than a fallback.

``Settings`` is constructed directly here with explicit kwargs. The real one reads
``../.env``, and a test that depended on the operator's actual keys would either
leak them or fail on a machine that has none.
"""

import pytest

from app.config import Settings

# ``database_url`` and ``jwt_secret`` must be passed explicitly: an unrelated
# validator rejects their hardcoded defaults (secrets must come from the
# environment), so every construction below supplies them.
_BASE = {
    "database_url": "postgresql+asyncpg://postgres:postgres@localhost:5433/x",
    "jwt_secret": "test-jwt-secret-not-for-production",
    "stripe_secret_key": "sk_test_abc",
    "stripe_publishable_key": "pk_test_abc",
    "stripe_webhook_secret": "whsec_test",
}

_LIVE = {
    "stripe_live_secret_key": "sk_live_abc",
    "stripe_live_publishable_key": "pk_live_abc",
}


def test_test_mode_is_the_default():
    """Nothing about adding live keys to .env should switch modes by itself.

    The keys sit in the environment long before anyone intends to use them, so
    their presence must not be what decides. Only the boolean decides.
    """
    settings = Settings(**_BASE, **_LIVE)

    assert settings.stripe_live_mode is False
    assert settings.stripe_secret_key == "sk_test_abc"
    assert settings.stripe_publishable_key == "pk_test_abc"


def test_live_mode_promotes_the_live_keys():
    """Everything reads ``stripe_secret_key``, so that is what has to change.

    ``stripe.api_key`` is assigned from it at import in ``routes/payment.py`` and
    ``workers/payments.py``, and the frontend fetches the publishable key from
    ``GET /api/payment/config`` — so promoting these two fields moves the server
    and the client together.
    """
    settings = Settings(**_BASE, **_LIVE, stripe_live_mode=True)

    assert settings.stripe_secret_key == "sk_live_abc"
    assert settings.stripe_publishable_key == "pk_live_abc"


@pytest.mark.parametrize(
    "omit", ["stripe_live_secret_key", "stripe_live_publishable_key"]
)
def test_live_mode_without_the_live_keys_refuses_to_start(omit):
    """Fails closed and loud, rather than falling back to the test keys.

    A fallback would report a healthy live deployment while Stripe refused every
    real card, with nothing in the app to explain it.
    """
    live = {**_LIVE, omit: ""}

    with pytest.raises(ValueError) as exc:
        Settings(**_BASE, **live, stripe_live_mode=True)

    assert omit.upper() in str(exc.value)
    assert "fall back" in str(exc.value)


def test_swapped_live_and_test_keys_are_refused():
    """The typo guard: a test key sitting in the live variable.

    Not security — anyone who can set the env can set anything — but it catches
    the one mistake this layout invites, which is pasting the pair in backwards.
    """
    with pytest.raises(ValueError) as exc:
        Settings(
            **_BASE,
            stripe_live_secret_key="sk_test_oops",
            stripe_live_publishable_key="pk_live_abc",
            stripe_live_mode=True,
        )

    assert "sk_live_" in str(exc.value)


def test_the_test_webhook_secret_is_not_reused_for_live_events():
    """A test signing secret cannot verify live events, so it must not be kept.

    Keeping it would make every live event fail verification — safe, because the
    route rejects with 400 rather than acting on an unverified event, but silent.
    An empty secret makes the webhook route refuse outright, which is the honest
    version of the same state.
    """
    settings = Settings(**_BASE, **_LIVE, stripe_live_mode=True)

    assert settings.stripe_webhook_secret == ""


def test_a_live_webhook_secret_is_used_when_supplied():
    settings = Settings(
        **_BASE,
        **_LIVE,
        stripe_live_webhook_secret="whsec_live_abc",
        stripe_live_mode=True,
    )

    assert settings.stripe_webhook_secret == "whsec_live_abc"


def test_payment_config_serves_whichever_key_is_active():
    """The client cannot end up on a different mode than the server.

    ``GET /api/payment/config`` is the only place the frontend gets a publishable
    key (``frontend/services/api.ts``), so this is what keeps a live server from
    handing a browser a test key — the mismatch that produces a card refused for
    reasons the user cannot see.
    """
    live = Settings(**_BASE, **_LIVE, stripe_live_mode=True)
    test = Settings(**_BASE, **_LIVE)

    assert live.stripe_publishable_key.startswith("pk_live_")
    assert test.stripe_publishable_key.startswith("pk_test_")
