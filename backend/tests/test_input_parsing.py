"""Unit tests for forgiving conversational input parsing."""

from datetime import datetime, timedelta, timezone

from app.services.input_parsing import coerce_number, parse_coordinates, parse_deadline


def test_parse_deadline_us_date_with_time():
    assert parse_deadline("7/18/2026 6am") == "2026-07-18T06:00:00+00:00"


def test_parse_deadline_iso_date_defaults_to_end_of_day():
    assert parse_deadline("2026-08-01") == "2026-08-01T23:59:59+00:00"


def test_parse_deadline_month_name_with_minutes():
    assert parse_deadline("July 20 2026 5:30pm") == "2026-07-20T17:30:00+00:00"


def test_parse_deadline_tomorrow_with_time():
    expected_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    result = parse_deadline("tomorrow 6am")
    assert result == f"{expected_date.isoformat()}T06:00:00+00:00"


def test_parse_deadline_bare_tomorrow_is_end_of_day():
    expected_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    result = parse_deadline("tomorrow")
    assert result == f"{expected_date.isoformat()}T23:59:59+00:00"


def test_parse_deadline_garbage_returns_none():
    assert parse_deadline("###garbage###") is None
    assert parse_deadline("") is None


def test_parse_deadline_bare_time_rolls_forward_when_past():
    """A bare time already past today means the NEXT occurrence, not a moment
    hours in the past. Regression for the live incident where "6am" set late at
    night resolved to that morning and failed the goal on creation."""
    tz = timezone.utc
    now = datetime.now(tz)

    if now.minute > 0:
        past_hour = now.hour
        past_minute = now.minute - 1
    elif now.hour > 0:
        past_hour = now.hour - 1
        past_minute = 59
    else:
        # Exact midnight has no earlier wall-clock instant on the same date;
        # use "00:00" so the parser still rolls to the next occurrence.
        past_hour = 0
        past_minute = 0

    result = parse_deadline(f"{past_hour}:{past_minute:02d}")
    assert result is not None
    parsed = datetime.fromisoformat(result)
    assert parsed > now
    # Rolled to tomorrow at the same wall-clock time.
    assert parsed.hour == past_hour
    assert parsed.minute == past_minute
    assert parsed.date() == (now + timedelta(days=1)).date()


def test_parse_deadline_bare_time_future_today_is_unchanged():
    """A bare time still ahead of us today stays today — no needless roll."""
    tz = timezone.utc
    now = datetime.now(tz)
    future_hour = (now + timedelta(hours=2)).hour
    # Skip the wrap-past-midnight case where "+2h" lands on tomorrow anyway.
    if future_hour <= now.hour:
        return
    result = parse_deadline(f"{future_hour}:00")
    parsed = datetime.fromisoformat(result)
    assert parsed.date() == now.date()
    assert parsed.hour == future_hour


def test_parse_deadline_explicit_past_date_is_not_rolled():
    """An explicit calendar date in the past is left as written — the caller's
    future-deadline guard rejects it; the parser must not silently move it."""
    assert parse_deadline("7/18/2026 6am") == "2026-07-18T06:00:00+00:00"


def test_parse_deadline_in_n_days_is_end_of_that_day():
    """"in 3 days" resolves against today's date, not a fixed calendar date."""
    now = datetime.now(timezone.utc)
    expected_date = (now + timedelta(days=3)).date()
    result = parse_deadline("in 3 days")
    assert result == f"{expected_date.isoformat()}T23:59:59+00:00"


def test_parse_deadline_in_n_days_with_time():
    now = datetime.now(timezone.utc)
    expected_date = (now + timedelta(days=2)).date()
    result = parse_deadline("in 2 days at 6am")
    assert result == f"{expected_date.isoformat()}T06:00:00+00:00"


def test_parse_deadline_spelled_out_number():
    now = datetime.now(timezone.utc)
    expected_date = (now + timedelta(days=3)).date()
    assert parse_deadline("in three days") == f"{expected_date.isoformat()}T23:59:59+00:00"


def test_parse_deadline_in_n_weeks():
    now = datetime.now(timezone.utc)
    expected_date = (now + timedelta(days=14)).date()
    assert parse_deadline("in 2 weeks") == f"{expected_date.isoformat()}T23:59:59+00:00"


def test_parse_deadline_next_week():
    now = datetime.now(timezone.utc)
    expected_date = (now + timedelta(days=7)).date()
    assert parse_deadline("next week") == f"{expected_date.isoformat()}T23:59:59+00:00"


def test_parse_deadline_in_n_hours_is_a_pure_offset():
    """"in 2 hours" is measured from the current instant, minute-accurate."""
    now = datetime.now(timezone.utc)
    result = parse_deadline("in 2 hours")
    parsed = datetime.fromisoformat(result)
    delta = parsed - now
    assert timedelta(hours=1, minutes=59) < delta < timedelta(hours=2, minutes=1)


def test_parse_deadline_weekday_resolves_to_future():
    now = datetime.now(timezone.utc)
    result = parse_deadline("friday 5pm")
    parsed = datetime.fromisoformat(result)
    assert parsed > now
    assert parsed.weekday() == 4  # Friday
    assert parsed.hour == 17
    # Never more than a week out.
    assert parsed.date() <= (now + timedelta(days=7)).date()


def test_parse_deadline_relative_uses_user_timezone():
    """"tomorrow" is tomorrow in the user's timezone, carrying their offset."""
    result = parse_deadline("tomorrow 9am", "America/New_York")
    parsed = datetime.fromisoformat(result)
    assert parsed.utcoffset() in (timedelta(hours=-4), timedelta(hours=-5))
    assert parsed.hour == 9


def test_parse_coordinates_dms_pair_from_google_maps():
    coords = parse_coordinates("35°53'53.4\"N 78°56'27.9\"W")
    assert abs(coords["latitude"] - 35.898167) < 1e-4
    assert abs(coords["longitude"] - -78.941083) < 1e-4


def test_parse_coordinates_decimal_pair():
    coords = parse_coordinates("35.898167, -78.941083")
    assert coords["latitude"] == 35.898167
    assert coords["longitude"] == -78.941083


def test_parse_coordinates_single_decimal_is_ambiguous():
    coords = parse_coordinates("-78.941083")
    assert coords["latitude"] == coords["longitude"] == -78.941083


def test_parse_coordinates_single_dms_latitude_only():
    coords = parse_coordinates("35°53'53.4\"N")
    assert abs(coords["latitude"] - 35.898167) < 1e-4
    assert coords["longitude"] is None


def test_parse_coordinates_garbage():
    coords = parse_coordinates("fayetteville rd and james ross rd")
    assert coords["latitude"] is None
    assert coords["longitude"] is None


def test_coerce_number():
    assert coerce_number("150m") == 150
    assert coerce_number("about 200 meters") == 200
    assert coerce_number(75) == 75
    assert coerce_number("no digits") is None


def test_parse_deadline_respects_user_timezone():
    # 6am Eastern (EDT, UTC-4 in July) — not 6am UTC.
    result = parse_deadline("7/18/2026 6am", "America/New_York")
    assert result == "2026-07-18T06:00:00-04:00"
    parsed = datetime.fromisoformat(result)
    assert parsed.astimezone(timezone.utc).hour == 10


def test_parse_deadline_bad_timezone_falls_back_to_utc():
    assert parse_deadline("7/18/2026 6am", "Not/AZone") == "2026-07-18T06:00:00+00:00"
