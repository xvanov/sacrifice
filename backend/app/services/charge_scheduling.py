"""When a failed goal's pledge is actually allowed to charge.

A goal resolves to `failed` at its deadline — any time of day. Charging the
instant that happens moves money at an arbitrary hour, mid-morning as easily
as at 3am. This module computes the buffer: the next local midnight, in the
goal's own `timezone`, after the moment it failed. The goal is already
`failed`; only the Stripe charge itself waits.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def midnight_after(moment: datetime, tz_name: str | None) -> datetime:
    """The next local midnight strictly after ``moment``, as a UTC instant.

    ``moment`` is normally a goal's ``deadline`` (or "now", for the immediate-
    fail path) — the failure fired during that calendar day, in the goal's own
    timezone, and this returns the UTC instant of that day's end.

    An unrecognized or missing zone name falls back to UTC rather than
    raising: a goal predating stricter validation, or a client that sent
    something malformed, must not make the charge un-schedulable.
    """
    try:
        zone = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    local_moment = moment.astimezone(zone)
    next_midnight_local = datetime.combine(
        local_moment.date() + timedelta(days=1), time.min, tzinfo=zone
    )
    return next_midnight_local.astimezone(timezone.utc)
