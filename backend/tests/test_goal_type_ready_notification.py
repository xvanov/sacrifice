"""
Tests for the ``goal_type_ready`` notification type.

All tests MUST fail on first run because:
- ``goal_type_ready`` is not in the notification_type enum
- The notification creation path for this type doesn't exist
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


# ─── goal_type_ready in the notification_type enum ───────────────────


def test_goal_type_ready_in_notification_type_enum():
    """
    ``goal_type_ready`` is a valid value in the notification_type PostgreSQL enum.
    This MUST fail: the enum (defined inline on Notification.type) does not
    include ``goal_type_ready``.
    """
    from app.models.notification import Notification

    enum_col = Notification.__table__.c.type
    enum_obj = enum_col.type

    allowed = enum_obj.enums
    assert "goal_type_ready" in allowed, (
        f"goal_type_ready must be in notification_type enum; got {allowed}"
    )


# ─── goal_type_ready notification links to goal ───────────────────────


async def test_goal_type_ready_notification_persists():
    """
    A goal_type_ready notification can be persisted and retrieved.
    MUST fail: the PostgreSQL enum doesn't accept ``goal_type_ready``.
    """
    from app.models.notification import Notification
    from app.database import async_session

    async with async_session() as db:
        import uuid
        from datetime import datetime, timezone

        notif = Notification(
            id=uuid.uuid4(),
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            type="goal_type_ready",
            title="Your goal type is ready",
            body="Pushup Counter has been built. Accept to activate your goal.",
            goal_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            read=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(notif)
        await db.commit()
        await db.refresh(notif)

        assert notif.id is not None
        assert notif.type == "goal_type_ready"
        assert notif.goal_id == uuid.UUID("00000000-0000-0000-0000-000000000002")


async def test_goal_type_ready_notification_retrievable():
    """
    A goal_type_ready notification is retrievable via the notifications API
    and has the correct shape linking it to a goal and direction.
    MUST fail: the notification type doesn't exist yet (PostgreSQL enum rejects it).
    """
    from app.models.notification import Notification
    from app.database import async_session
    import uuid
    from datetime import datetime, timezone

    # Create a goal_type_ready notification directly in DB.
    # This MUST fail because the PostgreSQL enum rejects "goal_type_ready".
    async with async_session() as db:
        notif = Notification(
            id=uuid.uuid4(),
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            type="goal_type_ready",
            title="Your goal type is ready",
            body="Pushup Counter has been built. Accept to activate your goal.",
            goal_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            read=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(notif)
        await db.commit()
        await db.refresh(notif)

        # If we get here, the notification persisted — verify shape
        assert notif.type == "goal_type_ready"
        assert notif.goal_id == uuid.UUID("00000000-0000-0000-0000-000000000002")

    # Then verify it's retrievable through the API
    async with make_client() as client:
        token, _ = await _auth(client)
        response = await client.get(
            "/api/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        notifications = response.json()
        types = {n.get("type", n.get("notification_type")) for n in notifications}
        assert "goal_type_ready" in types, (
            f"goal_type_ready notification not found in API response; got types: {types}"
        )