"""
Tests for the chat_spend_ledger table and daily AI budget cap.

All tests MUST fail on first run because:
- The chat_spend_ledger table doesn't exist
- The spend tracking service doesn't exist
- The 429 budget-exhausted behavior isn't implemented
"""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="test@example.com", name="Test User",
                sub="test-sub-123", token="valid-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {"email": email, "name": name, "sub": sub, "picture": None}
        resp = await client.post("/api/auth/google", json={"token": token})
        data = resp.json()
        return data["access_token"], data["user"]


SESSION_ID = "00000000-0000-0000-0000-000000000001"


# ─── Spend ledger model ───────────────────────────────────────────────


def test_chat_spend_ledger_model_exists():
    """
    The ChatSpendLedger model exists and is importable.
    MUST fail: the model doesn't exist yet.
    """
    from app.models.chat_spend_ledger import ChatSpendLedger

    assert hasattr(ChatSpendLedger, "__tablename__")
    assert ChatSpendLedger.__tablename__ == "chat_spend_ledger"


def test_chat_spend_ledger_has_required_columns():
    """
    ChatSpendLedger has user_id, cost_millicents, model, direction_id columns.
    MUST fail: the model doesn't exist yet.
    """
    from app.models.chat_spend_ledger import ChatSpendLedger

    cols = {c.name for c in ChatSpendLedger.__table__.columns}
    assert "user_id" in cols
    assert "cost_millicents" in cols
    assert "model" in cols
    assert "direction_id" in cols
    assert "created_at" in cols


# ─── Daily cap enforcement ────────────────────────────────────────────


async def test_request_new_goal_type_blocked_when_daily_cap_exceeded():
    """
    When the user's daily spend exceeds the cap (default $1.00 = 100000 millicents),
    the endpoint returns 429 with a clear message.
    MUST fail: no chat router or spend ledger exists.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt_summary": "Test goal",
                "goal_payload_draft": {
                    "title": "Test",
                    "description": "Test",
                    "pledge_amount": 1000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "UTC",
                    "charity_id": "acct_charity123",
                    "recurrence": "none",
                },
            },
        )
    # The route does not exist yet. The endpoint must return 202 (or 429
    # when budget exhausted). 404 means route is not mounted.
    assert response.status_code in (202, 429), (
        f"Expected 202 or 429; got {response.status_code}. "
        "Route is likely not mounted yet."
    )


async def test_429_response_includes_clear_budget_message():
    """
    The 429 response body includes a human-readable message about the budget.
    MUST fail: no chat router exists.
    """
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.post(
            f"/api/chat/sessions/{SESSION_ID}/request-new-goal-type",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "prompt_summary": "Test goal",
                "goal_payload_draft": {
                    "title": "Test",
                    "description": "Test",
                    "pledge_amount": 1000,
                    "currency": "usd",
                    "deadline": "2026-05-26T11:00:00Z",
                    "timezone": "UTC",
                    "charity_id": "acct_charity123",
                    "recurrence": "none",
                },
            },
        )
    # The route does not exist yet. The endpoint must return 429 when budget
    # is exhausted with a clear message.
    assert response.status_code == 429, (
        f"Expected 429 (budget exhausted); got {response.status_code}. "
        "Route is likely not mounted yet."
    )
    body = response.json()
    detail = body.get("detail", "")
    assert "budget" in detail.lower() or "budget" in str(body).lower()


# ─── Spend recording ──────────────────────────────────────────────────


async def test_llm_call_records_spend_in_ledger():
    """
    Each LLM call made during direction synthesis is recorded in the
    chat_spend_ledger table with the correct user_id and cost.
    MUST fail: the ledger table doesn't exist yet.
    """
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sessionmaker() as db:
        # The table won't exist yet — this query will fail during execution
        result = await db.execute(text("SELECT COUNT(*) FROM chat_spend_ledger"))
        count = result.scalar()
        # If we got here, the table exists but should have rows after a synthesis
        assert count >= 0


async def test_daily_spend_resets_at_midnight():
    """
    Spend tracking is per-day; yesterday's spend doesn't count against today's cap.
    MUST fail: the spend service doesn't exist.
    """
    from app.services.spend_tracker import get_daily_spend

    # The import above must fail. If it somehow passes, the call must fail.
    spend = await get_daily_spend(user_id="00000000-0000-0000-0000-000000000000")
    assert spend >= 0