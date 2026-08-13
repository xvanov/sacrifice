"""Forgiving parsers for conversational goal input.

Users type things like "7/18/2026 6am" or paste `35°53'53.4"N 78°56'27.9"W`
straight from Google Maps. The chat flow (and the create-goal endpoint) run
these parsers so honest input never dies with a pydantic 422.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

# ── deadlines ──────────────────────────────────────────────────────────────

# Minimum runway between "now" and an enforceable deadline. A goal whose
# deadline is in the past — or only minutes away — is failed by the next
# deadline sweep before the owner can realistically act. The create/update
# guards import this so the rule lives in one place.
#
# Kept equal to ``app/services/goal.DEADLINE_LOCK_WINDOW`` (30 minutes). If this
# lead were the longer of the two, a band would open before every deadline in
# which the goal is outside the lock — so its deadline is editable — but every
# new deadline is still too soon to be accepted, leaving "push it a week" as the
# only legal move. That is the escape hatch the lock exists to close. Change the
# two together.
DEADLINE_MIN_LEAD = timedelta(minutes=30)


def describe_window(window: timedelta) -> str:
    """Render a window for an error message: "30 minutes", "1 hour", "3 hours".

    Lives with the constants and is derived from them, so no message can say one
    thing while the guard enforces another after a window is retuned.
    """
    minutes = int(window.total_seconds() // 60)
    if minutes % 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    return "1 hour" if hours == 1 else f"{hours} hours"

_RELATIVE_DAYS = {"tomorrow": 1, "tonight": 0, "today": 0}

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Spelled-out small numbers so "in three days" works alongside "in 3 days".
_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_NUM = r"\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten"
# "in 3 days", "in two weeks", "in an hour"
_IN_OFFSET_RE = re.compile(
    rf"\bin\s+(?P<num>{_NUM})\s+(?P<unit>hour|day|week)s?\b", re.IGNORECASE
)
# "3 days from now", "2 hours from now"
_FROM_NOW_RE = re.compile(
    rf"\b(?P<num>{_NUM})\s+(?P<unit>hour|day|week)s?\s+from\s+now\b", re.IGNORECASE
)


def _resolve_tz(tz_name: str | None):
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 — bad client tz falls back to UTC
            pass
    return timezone.utc


def _word_to_int(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else _WORD_NUMBERS[token]


def _extract_weekday(lowered: str, now: datetime):
    """Resolve a weekday name ("friday", "next monday") to its next future date.

    Returns ``(date, remaining_text)`` or ``(None, lowered)`` when no weekday
    word is present. If the named day is today, we roll to next week's
    occurrence — a deadline "on Monday" set on a Monday means the next one.
    """
    for name, idx in _WEEKDAYS.items():
        match = re.search(rf"\b(?:next\s+|this\s+)?{name}\b", lowered)
        if match:
            days_ahead = (idx - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = (now + timedelta(days=days_ahead)).date()
            remaining = (lowered[: match.start()] + " " + lowered[match.end():]).strip()
            return target, remaining
    return None, lowered


def parse_deadline(text: str, tz_name: str | None = None) -> str | None:
    """Parse a human deadline into an ISO-8601 string, or None.

    Handles ISO dates, US dates ("7/18/2026 6am"), month names, and
    today/tonight/tomorrow with an optional time. A date with no explicit
    time means end of that day (23:59:59).

    ``tz_name`` is the user's IANA timezone: "6am" means 6am THEIR time.
    Without it, naive input is (unavoidably) treated as UTC.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    tz = _resolve_tz(tz_name)
    now = datetime.now(tz)
    lowered = raw.lower()

    # Relative offsets, resolved against the user's *current* date in their
    # timezone. "in N hours" is a pure time offset and returns immediately;
    # "in N days/weeks", "next week", and weekday names resolve to a concrete
    # future DATE — any time-of-day in the same phrase is parsed below.
    relative_date = None
    offset_match = _IN_OFFSET_RE.search(lowered) or _FROM_NOW_RE.search(lowered)
    if offset_match:
        count = _word_to_int(offset_match.group("num"))
        unit = offset_match.group("unit").lower()
        lowered = (
            lowered[: offset_match.start()] + " " + lowered[offset_match.end():]
        ).strip()
        if unit == "hour":
            return (now + timedelta(hours=count)).replace(microsecond=0).isoformat()
        relative_date = (
            now + timedelta(days=count * (7 if unit == "week" else 1))
        ).date()
    elif "next week" in lowered:
        relative_date = (now + timedelta(days=7)).date()
        lowered = lowered.replace("next week", " ").strip()
    else:
        weekday_date, lowered = _extract_weekday(lowered, now)
        if weekday_date is not None:
            relative_date = weekday_date
        else:
            for word, offset in _RELATIVE_DAYS.items():
                if word in lowered:
                    relative_date = (now + timedelta(days=offset)).date()
                    lowered = lowered.replace(word, " ").strip()
                    break

    # "tomorrow"/"today" with nothing else → end of that day.
    if relative_date is not None and not lowered:
        dt = datetime.combine(relative_date, datetime.max.time().replace(microsecond=0))
        return dt.replace(tzinfo=tz).isoformat()

    # Parse with two different sentinel defaults: components the user
    # actually supplied come out identical, omitted ones inherit the
    # sentinel and differ — that's how we detect "no time given" without
    # contaminating a supplied time with sentinel minutes/seconds.
    source = lowered if relative_date is not None else raw
    sentinel_a = datetime(now.year, now.month, now.day, 23, 59, 59)
    sentinel_b = datetime(now.year, now.month, now.day, 11, 47, 13)
    try:
        parsed = date_parser.parse(source, default=sentinel_a, fuzzy=True)
        parsed_b = date_parser.parse(source, default=sentinel_b, fuzzy=True)
        # Third probe with a wholly different default DATE: a date component
        # that moves with the default was never in the input (inherited from
        # `now`); one that stays put was supplied by the user. This is how we
        # tell "6am" (no date) apart from "7/18/2026 6am" (explicit date).
        parsed_c = date_parser.parse(
            source, default=datetime(2000, 1, 1, 0, 0, 0), fuzzy=True
        )
    except (ValueError, OverflowError):
        return None

    date_was_defaulted = (
        parsed.year != parsed_c.year
        or parsed.month != parsed_c.month
        or parsed.day != parsed_c.day
    )

    # A time component that matches across both parses was explicitly given;
    # one that differs was inherited from the sentinels. No hour → end of
    # day; hour without minutes/seconds ("6am") → top of the hour.
    hour_given = parsed.hour == parsed_b.hour
    if not hour_given:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    else:
        if parsed.minute != parsed_b.minute:
            parsed = parsed.replace(minute=0)
        if parsed.second != parsed_b.second:
            parsed = parsed.replace(second=0)

    if relative_date is not None:
        parsed = parsed.replace(
            year=relative_date.year, month=relative_date.month, day=relative_date.day
        )

    if parsed.tzinfo is None:
        # Naive input means the USER'S wall clock ("6am" = 6am their time).
        parsed = parsed.replace(tzinfo=tz)

    # A bare time with no date ("6am") inherits today's date from `now`. Said
    # late in the day, "6am" means TOMORROW's 6am, not a moment already hours
    # in the past — otherwise the goal is born already expired and the next
    # deadline sweep fails it within seconds (observed live 2026-07-19: a "6am"
    # deadline set at 11:30pm resolved to that morning and failed on creation).
    # Roll a defaulted, time-only deadline forward to its next future
    # occurrence. Explicit dates ("7/18/2026 6am") and relative words
    # ("today 6am") are left exactly as the user wrote them.
    if relative_date is None and date_was_defaulted and hour_given and parsed <= now:
        parsed = parsed + timedelta(days=1)

    return parsed.isoformat()


# ── coordinates ────────────────────────────────────────────────────────────

# DMS like 35°53'53.4"N — degrees, optional minutes/seconds, hemisphere.
_DMS_RE = re.compile(
    r"""(?P<deg>\d{1,3}(?:\.\d+)?)\s*[°º]\s*
        (?:(?P<min>\d{1,2}(?:\.\d+)?)\s*['’′]\s*)?
        (?:(?P<sec>\d{1,2}(?:\.\d+)?)\s*["”″]\s*)?
        (?P<hem>[NSEW])""",
    re.IGNORECASE | re.VERBOSE,
)

_DECIMAL_PAIR_RE = re.compile(
    r"(?P<lat>-?\d{1,3}\.\d+)\s*[,;\s]\s*(?P<lng>-?\d{1,3}\.\d+)"
)


def _dms_to_decimal(deg: str, minutes: str | None, seconds: str | None, hem: str) -> float:
    value = float(deg) + float(minutes or 0) / 60 + float(seconds or 0) / 3600
    if hem.upper() in ("S", "W"):
        value = -value
    return round(value, 6)


def parse_coordinates(text: str) -> dict:
    """Extract latitude/longitude from free text.

    Returns {"latitude": float|None, "longitude": float|None}. Understands
    decimal pairs ("35.898, -78.941"), Google-Maps DMS pairs
    (35°53'53.4"N 78°56'27.9"W — even when the user pastes the whole pair in
    answer to a single-coordinate question), single decimals, and single DMS
    values.
    """
    raw = (text or "").strip()
    result: dict = {"latitude": None, "longitude": None}
    if not raw:
        return result

    dms_matches = _DMS_RE.findall(raw)
    if dms_matches:
        for deg, minutes, seconds, hem in dms_matches:
            value = _dms_to_decimal(deg, minutes, seconds, hem)
            if hem.upper() in ("N", "S"):
                result["latitude"] = value
            else:
                result["longitude"] = value
        return result

    pair = _DECIMAL_PAIR_RE.search(raw)
    if pair:
        lat, lng = float(pair.group("lat")), float(pair.group("lng"))
        if abs(lat) <= 90 and abs(lng) <= 180:
            result["latitude"], result["longitude"] = lat, lng
            return result

    try:
        result["latitude"] = float(raw)
        # A bare number is ambiguous; the caller decides which axis it is.
        result["longitude"] = result["latitude"]
    except (ValueError, TypeError):
        pass
    return result


def coerce_number(text: object) -> float | None:
    """Best-effort numeric coercion for criteria fields like radius_m."""
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None
