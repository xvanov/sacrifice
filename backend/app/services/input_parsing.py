"""Forgiving parsers for conversational goal input.

Users type things like "7/18/2026 6am" or paste `35°53'53.4"N 78°56'27.9"W`
straight from Google Maps. The chat flow (and the create-goal endpoint) run
these parsers so honest input never dies with a pydantic 422.
"""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

# ── deadlines ──────────────────────────────────────────────────────────────

_RELATIVE_DAYS = {"tomorrow": 1, "tonight": 0, "today": 0}


def _resolve_tz(tz_name: str | None):
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 — bad client tz falls back to UTC
            pass
    return UTC


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

    relative_date = None
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
    except (ValueError, OverflowError):
        return None

    # A time component that matches across both parses was explicitly given;
    # one that differs was inherited from the sentinels. No hour → end of
    # day; hour without minutes/seconds ("6am") → top of the hour.
    if parsed.hour != parsed_b.hour:
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

_DECIMAL_PAIR_RE = re.compile(r"(?P<lat>-?\d{1,3}\.\d+)\s*[,;\s]\s*(?P<lng>-?\d{1,3}\.\d+)")


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
