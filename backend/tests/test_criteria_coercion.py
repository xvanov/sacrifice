"""Tests for schema-driven criteria coercion.

The bug this module exists to prevent is a charge, not a crash: a criteria value
stored with the wrong type makes verification impossible, and a `failed` verdict
fires a real Stripe PaymentIntent (`app/services/verification_result.py`). So the
tests care about two things in equal measure — that an unambiguous answer becomes
the right type, and that an ambiguous one becomes nothing at all rather than a
plausible-looking guess.
"""

import pytest

from app.goal_types.registry import get_type as get_registry_type, list_types
from app.services.criteria_coercion import (
    UNUSABLE,
    _COERCERS,
    coerce_criteria,
    coerce_criteria_value,
    declared_types,
    describe_expected_type,
)

# Minimal hand-written schema: the mechanism must be provable without leaning on
# any particular goal type's definition.
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "paths": {"type": "array", "items": {"type": "string"}},
        "sizes": {"type": "array", "items": {"type": "integer"}},
        "loose_list": {"type": "array"},
        "config": {"type": "object"},
        "bounded": {"type": "integer", "minimum": 1, "maximum": 10},
        "at_least_one": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "untyped": {"description": "no type declared"},
        "nullable": {"type": ["integer", "null"]},
        "exotic": {"type": "somethingelse"},
    },
}


def coerce(field, value, schema=SCHEMA):
    return coerce_criteria_value(field, value, criteria_schema=schema)


# ── integers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("200", 200),
        ("  200  ", 200),
        ("200 OK", 200),
        ("at least 3", 3),
        (200, 200),
        (200.0, 200),
        (-5, -5),
    ],
)
def test_integer_coerces_unambiguous_values(value, expected):
    result = coerce("count", value)

    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    "value",
    ["two hundred", "abc", "", "   ", None, 2.5, True, False, [200], {"a": 1}],
)
def test_integer_refuses_to_guess(value):
    """A guessed number is worse than a missing one — it becomes a goal that
    silently cannot be won."""
    assert coerce("count", value) is UNUSABLE


def test_integer_refuses_a_boolean_even_though_bool_is_an_int():
    assert coerce("count", True) is UNUSABLE
    assert coerce("count", False) is UNUSABLE


def test_integer_refuses_to_round():
    """2.5 commits is not 2 commits."""
    assert coerce("count", "2.5") is UNUSABLE
    assert coerce("count", 2.5) is UNUSABLE


# ── numbers, strings, booleans ────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("35.898", 35.898), ("-78.941", -78.941), ("0", 0), (35.898, 35.898), (35, 35)],
)
def test_number_coerces_unambiguous_values(value, expected):
    assert coerce("ratio", value) == expected


@pytest.mark.parametrize("value", ["somewhere downtown", "", None, True, []])
def test_number_refuses_to_guess(value):
    assert coerce("ratio", value) is UNUSABLE


@pytest.mark.parametrize(
    "value,expected", [("  GET ", "GET"), ("pytest -q", "pytest -q"), (200, "200")]
)
def test_string_coerces_unambiguous_values(value, expected):
    assert coerce("name", value) == expected


@pytest.mark.parametrize("value", ["", "   ", None, True, ["a"]])
def test_string_refuses_blank_and_guessable_values(value):
    assert coerce("name", value) is UNUSABLE


@pytest.mark.parametrize(
    "value,expected",
    [
        ("yes", True),
        ("Yes!", True),
        ("true", True),
        ("sure", True),
        ("no", False),
        ("nope", False),
        ("false", False),
        (True, True),
        (False, False),
    ],
)
def test_boolean_coerces_unambiguous_values(value, expected):
    assert coerce("enabled", value) is expected


@pytest.mark.parametrize("value", ["maybe", "abc", "", 1, 0, None])
def test_boolean_refuses_to_guess(value):
    assert coerce("enabled", value) is UNUSABLE


# ── arrays and objects ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (["README.md"], ["README.md"]),
        ("README.md, tests/", ["README.md", "tests/"]),
        ("a.py;b.py", ["a.py", "b.py"]),
        # Spaces do not split: "my notes.txt" is one path.
        ("my notes.txt", ["my notes.txt"]),
        (("a.py", "b.py"), ["a.py", "b.py"]),
    ],
)
def test_array_coerces_unambiguous_values(value, expected):
    assert coerce("paths", value) == expected


def test_array_coerces_its_items_to_the_declared_item_type():
    assert coerce("sizes", ["1", "2"]) == [1, 2]
    assert coerce("sizes", "3, 4") == [3, 4]


def test_array_refuses_the_whole_field_rather_than_dropping_a_bad_item():
    """A list missing one of the paths the user named is a different promise
    from the one they made."""
    assert coerce("sizes", ["1", "abc"]) is UNUSABLE


@pytest.mark.parametrize("value", ["", "   ", 5, None, {"a": 1}])
def test_array_refuses_to_guess(value):
    assert coerce("paths", value) is UNUSABLE


def test_array_without_declared_item_type_passes_items_through():
    assert coerce("loose_list", [{"type": "commits"}]) == [{"type": "commits"}]


@pytest.mark.parametrize(
    "value,expected",
    [({"a": 1}, {"a": 1}), ('{"a": 1}', {"a": 1}), ({}, {})],
)
def test_object_coerces_unambiguous_values(value, expected):
    assert coerce("config", value) == expected


@pytest.mark.parametrize("value", ["not json", "[1, 2]", "null", 5, None])
def test_object_refuses_non_objects(value):
    assert coerce("config", value) is UNUSABLE


# ── the design boundary: type vs. meaning ─────────────────────────────


@pytest.mark.parametrize(
    "field,value",
    [
        ("count", 0),
        ("ratio", 0),
        ("ratio", 0.0),
        ("enabled", False),
        ("loose_list", []),
    ],
)
def test_zero_false_and_empty_are_valid_instances_of_their_types(field, value):
    """Coercion must NOT inherit the "inert value" judgment that
    ``_is_criterion_set`` makes for anyOf satisfaction. A future field with a
    legitimate zero — a radius of 0, an "allow no failures" threshold — has to
    remain storable, so type coercion says nothing about meaningfulness."""
    result = coerce(field, value)

    assert result is not UNUSABLE
    assert result == value


def test_zero_is_refused_only_when_the_field_itself_declares_a_minimum():
    """The distinction that keeps the rule honest: the schema decides, not a
    blanket rule about zero."""
    assert coerce("bounded", 0) is UNUSABLE  # minimum: 1
    assert coerce("bounded", 5) == 5
    assert coerce("bounded", 11) is UNUSABLE  # maximum: 10
    assert coerce("count", 0) == 0  # no declared minimum → 0 is fine


def test_empty_list_is_refused_only_when_the_field_declares_min_items():
    assert coerce("at_least_one", []) is UNUSABLE
    assert coerce("loose_list", []) == []


def test_real_geolocation_radius_of_zero_is_storable():
    """The concrete case from review: radius_m declares no minimum."""
    schema = get_registry_type("geolocation").criteria_schema

    assert coerce_criteria_value("radius_m", 0, criteria_schema=schema) == 0


def test_real_github_repo_zero_commits_is_refused_by_its_own_minimum():
    schema = get_registry_type("github_repo").criteria_schema

    assert coerce_criteria_value("min_commits", 0, criteria_schema=schema) is UNUSABLE
    assert coerce_criteria_value("min_commits", "3", criteria_schema=schema) == 3
    assert (
        coerce_criteria_value("required_files", [], criteria_schema=schema) is UNUSABLE
    )


# ── schema edge cases ─────────────────────────────────────────────────


def test_fields_the_schema_does_not_describe_pass_through_untouched():
    """Dropping unknown keys would lose legacy ``conditions`` payloads and
    anything a new goal type stores before its schema catches up."""
    assert coerce("untyped", {"anything": True}) == {"anything": True}
    assert coerce("not_in_schema_at_all", [1, 2]) == [1, 2]
    assert coerce("untyped", "  spaces  ") == "spaces"


def test_a_union_type_accepts_either_branch():
    assert coerce("nullable", None) is None
    assert coerce("nullable", "7") == 7
    assert coerce("nullable", "abc") is UNUSABLE


def test_an_unrecognised_type_keyword_is_not_a_rejection():
    assert coerce("exotic", "whatever") == "whatever"


def test_empty_schema_passes_everything_through():
    assert coerce_criteria_value("anything", " x ", criteria_schema={}) == "x"


def test_legacy_github_repo_conditions_survive_coercion():
    """The nested legacy shape must not be mangled — the worker still reads it."""
    schema = get_registry_type("github_repo").criteria_schema
    conditions = [{"type": "commits", "min_count": 2, "since_date": "2026-01-01"}]

    assert (
        coerce_criteria_value("conditions", conditions, criteria_schema=schema)
        == conditions
    )


# ── bulk coercion ─────────────────────────────────────────────────────


def test_coerce_criteria_reports_and_omits_unusable_fields():
    coerced, unusable = coerce_criteria(
        {"name": "GET", "count": "200", "ratio": "nowhere", "enabled": "yes"},
        criteria_schema=SCHEMA,
    )

    assert coerced == {"name": "GET", "count": 200, "enabled": True}
    assert unusable == ["ratio"]


def test_coerce_criteria_leaves_a_clean_payload_untouched():
    payload = {"name": "GET", "count": 200, "enabled": True, "paths": ["a.py"]}

    coerced, unusable = coerce_criteria(payload, criteria_schema=SCHEMA)

    assert coerced == payload
    assert unusable == []


# ── the api_endpoint money bug, at module level ───────────────────────


def test_api_endpoint_expected_status_becomes_an_integer():
    """The exact silent-charge bug: `api_check.py` compares
    `response.status_code == criteria["expected_status"]`, and `200 == "200"` is
    False forever, so a user whose endpoint returned 200 was charged."""
    schema = get_registry_type("api_endpoint").criteria_schema

    result = coerce_criteria_value("expected_status", "200", criteria_schema=schema)

    assert result == 200
    assert type(result) is int


def test_youtube_min_duration_becomes_an_integer():
    schema = get_registry_type("youtube_video").criteria_schema

    result = coerce_criteria_value(
        "min_duration_seconds", "300", criteria_schema=schema
    )

    assert result == 300
    assert type(result) is int


# ── canary over the real registry ─────────────────────────────────────


def test_every_declared_type_in_every_goal_type_is_one_we_handle():
    """A new goal type declaring a type this module ignores would silently get
    no coercion, which is how the original bug shipped."""
    unhandled: list[str] = []
    for name in list_types():
        schema = get_registry_type(name).criteria_schema
        for field in schema.get("properties") or {}:
            for declared in declared_types(field, schema):
                if declared not in _COERCERS:
                    unhandled.append(f"{name}.{field}: {declared}")

    assert unhandled == []


def test_every_required_field_of_every_goal_type_declares_a_type():
    """A required field with no declared type gets no coercion at all."""
    untyped: list[str] = []
    for name in list_types():
        schema = get_registry_type(name).criteria_schema
        for field in schema.get("required", []):
            if not declared_types(field, schema):
                untyped.append(f"{name}.{field}")

    assert untyped == []


def test_describe_expected_type_reads_as_a_sentence_fragment():
    assert describe_expected_type("count", SCHEMA) == "an integer"
    assert describe_expected_type("enabled", SCHEMA) == "true or false"
    assert describe_expected_type("paths", SCHEMA) == "a list"
    assert describe_expected_type("nullable", SCHEMA) == "an integer or null"
    assert describe_expected_type("missing", SCHEMA) == "a valid value"
