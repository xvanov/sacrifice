"""Unit tests for forgiving conversational input parsing."""

from datetime import UTC, datetime, timedelta

from app.services.input_parsing import coerce_number, parse_coordinates, parse_deadline


def test_parse_deadline_us_date_with_time():
    assert parse_deadline("7/18/2026 6am") == "2026-07-18T06:00:00+00:00"


def test_parse_deadline_iso_date_defaults_to_end_of_day():
    assert parse_deadline("2026-08-01") == "2026-08-01T23:59:59+00:00"


def test_parse_deadline_month_name_with_minutes():
    assert parse_deadline("July 20 2026 5:30pm") == "2026-07-20T17:30:00+00:00"


def test_parse_deadline_tomorrow_with_time():
    expected_date = (datetime.now(UTC) + timedelta(days=1)).date()
    result = parse_deadline("tomorrow 6am")
    assert result == f"{expected_date.isoformat()}T06:00:00+00:00"


def test_parse_deadline_bare_tomorrow_is_end_of_day():
    expected_date = (datetime.now(UTC) + timedelta(days=1)).date()
    result = parse_deadline("tomorrow")
    assert result == f"{expected_date.isoformat()}T23:59:59+00:00"


def test_parse_deadline_garbage_returns_none():
    assert parse_deadline("###garbage###") is None
    assert parse_deadline("") is None


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
    assert parsed.astimezone(UTC).hour == 10


def test_parse_deadline_bad_timezone_falls_back_to_utc():
    assert parse_deadline("7/18/2026 6am", "Not/AZone") == "2026-07-18T06:00:00+00:00"
