"""A live goal's deadline is immovable in its final hours.

The deadline is the whole commitment. It is the single fact the sweep in
``app/workers/deadline.py`` reads to decide whether the pledge is charged, so an
owner who can still move it has an escape hatch that costs nothing and breaks no
rule: with fifteen minutes left and no proof, push the date out a week and the
goal is not rescheduled, it is un-failed. ``PUT /api/goals/{id}`` accepted exactly
that.

Inside ``app/services/goal.DEADLINE_LOCK_WINDOW`` (30 minutes) the date is fixed:

    PUT /api/goals/{id} {deadline: <+7 days>}  -> 403, deadline unchanged

What the lock must NOT become:

* **A freeze on the whole goal.** Only the deadline is locked. The owner can still
  fix a description or raise their pledge in the last hours — none of that moves
  the line they are being measured against.
* **A trap for a draft.** A draft is chargeable by nobody, and its deadline may
  well have quietly gone by while it sat unactivated; freezing it would leave the
  goal unrepairable and unactivatable at once.
* **A refusal of unrelated edits.** The edit form submits every field it holds,
  including the deadline it was just served. An echo is not a move.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app


def make_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _auth(client, email="lock@example.com", sub="lock-sub", token="lock-token"):
    with patch("app.routes.auth.verify_google_token") as mock:
        mock.return_value = {
            "email": email,
            "name": "Deadline Owner",
            "sub": sub,
            "picture": None,
        }
        resp = await client.post("/api/auth/google", json={"token": token})
        return resp.json()["access_token"]


def _goal_body(hours_out: float, **overrides):
    """A valid goal whose deadline is ``hours_out`` hours from now."""
    return {
        "title": "Ship the thing",
        "description": "before the clock runs out",
        "deadline": (
            datetime.now(timezone.utc) + timedelta(hours=hours_out)
        ).isoformat(),
        "pledge_amount": 5000,
        "goal_type": "youtube_video",
        "criteria": {
            "min_duration_seconds": 300,
            "video_description": "a walkthrough demo",
        },
        "charity_id": "acct_charity123",
        **overrides,
    }


async def _create(client, token, hours_out: float):
    resp = await client.post(
        "/api/goals",
        headers={"Authorization": f"Bearer {token}"},
        json=_goal_body(hours_out),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_active(client, token, hours_out: float):
    """Create and activate a goal, returning ``(id, payload)`` once live."""
    goal = await _create(client, token, hours_out)
    activated = await client.put(
        f"/api/goals/{goal['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    assert activated.status_code == 200, activated.text
    return goal["id"], activated.json()


def _iso_in(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


async def _force_deadline(goal_id: str, deadline: datetime) -> None:
    """Move a goal's stored deadline, standing in for time passing.

    Straight to the DB because the API is the thing under test: there is no
    request that can put a goal's deadline inside the lock window, which is
    exactly the state a goal reaches on its own as its deadline approaches.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE goals SET deadline = :d WHERE id = :id"),
                {"d": deadline, "id": uuid.UUID(goal_id)},
            )
    finally:
        await engine.dispose()


async def _fetch(client, token, goal_id):
    resp = await client.get(
        f"/api/goals/{goal_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _create_locked(client, token, *, minutes_out: float = 15):
    """An active goal whose stored deadline sits inside the lock window.

    Created with plenty of runway and moved in afterwards, because no request can
    put a goal there directly: a deadline must carry at least ``DEADLINE_MIN_LEAD``
    of runway, which is no longer than the lock window itself. Letting the clock
    run is the only way a live goal enters the window, so that is what this fakes.
    """
    goal_id, _ = await _create_active(client, token, hours_out=24)
    await _force_deadline(
        goal_id, datetime.now(timezone.utc) + timedelta(minutes=minutes_out)
    )
    return goal_id, await _fetch(client, token, goal_id)


async def test_deadline_cannot_be_pushed_out_within_the_lock_window():
    """The evasion case: twenty minutes from the deadline, buying another week."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, before = await _create_locked(client, token, minutes_out=20)

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": _iso_in(days=7)},
        )
        assert resp.status_code == 403
        assert "fixed" in resp.json()["detail"]

        after = await client.get(
            f"/api/goals/{goal_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert after.json()["deadline"] == before["deadline"]


async def test_deadline_cannot_be_pulled_in_within_the_lock_window():
    """Locked in both directions. Moving it closer is not an escape, but the
    date a goal is judged against is settled by this point."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, _ = await _create_locked(client, token, minutes_out=25)

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": _iso_in(minutes=10)},
        )
        assert resp.status_code == 403


async def test_deadline_is_still_editable_outside_the_window():
    """Well before it falls due, the deadline is the owner's to change."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, _ = await _create_active(client, token, hours_out=24 * 30)

        new_deadline = datetime.now(timezone.utc) + timedelta(days=45)
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": new_deadline.isoformat()},
        )
        assert resp.status_code == 200
        # Same instant, whatever the serialisation: compare parsed values.
        assert datetime.fromisoformat(resp.json()["deadline"]) == new_deadline


async def test_deadline_is_editable_forty_five_minutes_out():
    """The boundary the window was narrowed to: with forty-five minutes left the
    owner can still move the date. Inside thirty they cannot."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, _ = await _create_active(client, token, hours_out=0.75)

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": _iso_in(days=7)},
        )
        assert resp.status_code == 200, resp.text


async def test_just_outside_the_lock_the_deadline_still_moves_both_ways():
    """No extend-only band. The lock and ``DEADLINE_MIN_LEAD`` are the same length,
    so wherever an edit is allowed at all, a *closer* deadline is allowed too.

    Were the lead the longer of the two, the gap between them would be a stretch
    where the goal is outside the lock — the date is editable — but every new
    deadline is refused as too soon, leaving "push it a week" as the only move the
    API accepts. That is the evasion the lock exists to close, so it is asserted
    here rather than left to the two constants happening to match.
    """
    async with make_client() as client:
        token = await _auth(client)
        goal_id, _ = await _create_active(client, token, hours_out=0.75)

        pulled_in = _iso_in(minutes=35)
        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": pulled_in},
        )
        assert resp.status_code == 200, resp.text
        assert datetime.fromisoformat(
            resp.json()["deadline"]
        ) == datetime.fromisoformat(pulled_in)


async def test_a_deadline_that_has_already_passed_is_locked():
    """A goal the sweep has not reached yet is past locking, not exempt from it —
    otherwise the escape hatch simply reopens once the deadline goes by."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, _ = await _create_active(client, token, hours_out=24)
        await _force_deadline(
            goal_id, datetime.now(timezone.utc) - timedelta(minutes=5)
        )

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": _iso_in(days=7)},
        )
        assert resp.status_code == 403


async def test_other_fields_stay_editable_within_the_window():
    """The lock is on the deadline, not on the goal."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, _ = await _create_locked(client, token)

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"description": "sharpened the scope", "pledge_amount": 9000},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "sharpened the scope"
        assert resp.json()["pledge_amount"] == 9000


async def test_resubmitting_the_same_deadline_is_not_a_move():
    """An edit form sends every field it holds; the deadline it was served coming
    back unchanged must not read as an attempt to move it."""
    async with make_client() as client:
        token = await _auth(client)
        goal_id, active = await _create_locked(client, token)

        resp = await client.put(
            f"/api/goals/{goal_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": active["deadline"], "description": "unchanged date"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "unchanged date"


async def test_a_draft_deadline_is_editable_inside_the_window():
    """Nothing is at stake on a draft and no sweep can charge it, so an imminent
    draft deadline can still be repaired."""
    async with make_client() as client:
        token = await _auth(client)
        goal = await _create(client, token, hours_out=24)
        await _force_deadline(
            goal["id"], datetime.now(timezone.utc) + timedelta(minutes=15)
        )

        resp = await client.put(
            f"/api/goals/{goal['id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"deadline": _iso_in(days=3)},
        )
        assert resp.status_code == 200


async def test_goal_payload_reports_whether_the_deadline_is_locked():
    """Served so the edit form can disable the field, rather than the owner
    discovering the rule in a 403 after typing a new date."""
    async with make_client() as client:
        token = await _auth(client)

        _, locked = await _create_locked(client, token)
        assert locked["deadline_locked"] is True

        _, still_open = await _create_active(client, token, hours_out=24 * 30)
        assert still_open["deadline_locked"] is False

        # A draft is never locked, however close its deadline.
        draft = await _create(client, token, hours_out=24)
        await _force_deadline(
            draft["id"], datetime.now(timezone.utc) + timedelta(minutes=15)
        )
        assert (await _fetch(client, token, draft["id"]))["deadline_locked"] is False


async def test_the_window_is_thirty_minutes():
    """Named once, so the rule cannot drift without this failing."""
    from app.services.goal import DEADLINE_LOCK_WINDOW

    assert DEADLINE_LOCK_WINDOW == timedelta(minutes=30)


async def test_the_lock_window_and_the_minimum_lead_are_equal():
    """The coupling that keeps the extend-only band shut. A lead longer than the
    lock opens a stretch where the only accepted deadline move is outwards; a lead
    shorter than the lock is harmless but means a goal can be created already
    locked. Retune the two together."""
    from app.services.goal import DEADLINE_LOCK_WINDOW
    from app.services.input_parsing import DEADLINE_MIN_LEAD

    assert DEADLINE_MIN_LEAD == DEADLINE_LOCK_WINDOW
