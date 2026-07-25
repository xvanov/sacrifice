"""Schema-driven coercion of user-supplied criteria values.

The chat flow collects criteria as free text and stored whatever the user
typed. Nothing downstream disagreed: ``GoalCreate.criteria`` is a bare ``dict``
(``app/schemas/goal.py``) and the create-goal paths only check that required
keys are *present*, so a goal type declaring
``expected_status: {"type": "integer"}`` happily received the string ``"200"``.

That is a billing bug, not a cosmetic one. ``app/workers/api_check.py`` compares
``response.status_code == criteria_data["expected_status"]``, and ``200 ==
"200"`` is ``False`` in Python — so a user whose endpoint did exactly what they
promised gets a ``failed`` verdict, and
``app/services/verification_result.persist_verification_result`` charges their
pledge for real. ``min_duration_seconds`` fails the same way from the other
side: ``int >= str`` raises, no verdict is ever written, and the deadline sweep
(``app/workers/deadline.py``) charges them instead.

Two rules, and the second one is the important one:

1. **Coerce where the intent is unambiguous.** ``"200"`` → ``200``,
   ``"35.898"`` → ``35.898``, ``"yes"`` → ``True``, ``"a, b"`` → ``["a", "b"]``.
2. **Otherwise return :data:`UNUSABLE` and store nothing.** A guessed value is
   worse than a missing one: a missing field makes the chat ask again, while a
   guessed one becomes a goal that silently cannot be won. ``"two hundred"`` is
   not ``200``.

Type coercion deliberately says nothing about whether a value is *meaningful*.
``0``, ``False`` and ``[]`` are valid instances of their declared types and come
back unchanged. Whether a zero threshold configures a check worth running is a
different question, asked by ``_is_criterion_set`` in ``app/routes/chat.py``
against a schema's ``anyOf`` alternatives. Keeping that judgment out of here is
what lets a future criteria field hold a legitimate zero — a radius of ``0``, an
"allow no failures" threshold — instead of being silently unstorable.

What this module *does* enforce is what the schema itself declares: a field
whose schema says ``minimum: 1`` rejects ``0``. That is not a global rule about
zero, it is the field's own stated contract, which is why ``radius_m`` (no
declared minimum) still accepts ``0`` while ``min_commits`` (``minimum: 1``)
does not.
"""

from __future__ import annotations

import json
import re

from app.services.input_parsing import coerce_number


class _Unusable:
    """Sentinel type for :data:`UNUSABLE`."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNUSABLE"


#: Returned when a value cannot be coerced to its declared type without
#: guessing. Compare with ``is``: this sentinel deliberately defines no
#: ``__bool__``, because ``if coerced:`` would also swallow a legitimate ``0``.
UNUSABLE = _Unusable()


_TRUE_WORDS = frozenset(
    {
        "true",
        "yes",
        "y",
        "yep",
        "yeah",
        "yup",
        "sure",
        "ok",
        "okay",
        "on",
        "require",
        "required",
        "1",
    }
)
_FALSE_WORDS = frozenset(
    {"false", "no", "n", "nope", "nah", "off", "skip", "none", "0"}
)

# Commas, semicolons and newlines separate list items. Spaces deliberately do
# not: "my notes.txt" is one path, not two.
_LIST_SPLIT_RE = re.compile(r"[,;\n]+")


def declared_types(field: str, criteria_schema: dict) -> list[str]:
    """Return the JSON types *field* declares in *criteria_schema*.

    Empty when the field is not described by the schema, which callers treat as
    "no opinion" rather than "invalid".
    """
    prop = _property_schema(field, criteria_schema)
    declared = prop.get("type")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [entry for entry in declared if isinstance(entry, str)]
    return []


def _property_schema(field: str, criteria_schema: dict) -> dict:
    properties = criteria_schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    prop = properties.get(field)
    return prop if isinstance(prop, dict) else {}


def coerce_criteria_value(
    field: str, value: object, *, criteria_schema: dict
) -> object:
    """Coerce *value* to the type *field* declares, or return :data:`UNUSABLE`.

    A field the schema does not describe passes through untouched (strings are
    stripped). Dropping unknown keys would lose the legacy ``conditions``
    payloads and anything a new goal type stores before its schema catches up.
    """
    prop = _property_schema(field, criteria_schema)
    types = declared_types(field, criteria_schema)
    if not types:
        return value.strip() if isinstance(value, str) else value

    for declared in types:
        coerced = _COERCERS.get(declared, _coerce_unknown_type)(value, prop)
        if coerced is not UNUSABLE and _satisfies_declared_bounds(coerced, prop):
            return coerced
    return UNUSABLE


def coerce_criteria(criteria: dict, *, criteria_schema: dict) -> tuple[dict, list[str]]:
    """Coerce every entry in *criteria*.

    Returns ``(coerced, unusable_fields)``. Unusable fields are omitted from
    ``coerced`` rather than stored badly; the caller decides whether that means
    "ask the user again" (the chat state machine) or "reject the request" (the
    create-goal endpoint).
    """
    coerced: dict = {}
    unusable: list[str] = []
    for field, value in criteria.items():
        result = coerce_criteria_value(field, value, criteria_schema=criteria_schema)
        if result is UNUSABLE:
            unusable.append(field)
        else:
            coerced[field] = result
    return coerced, unusable


def describe_expected_type(field: str, criteria_schema: dict) -> str:
    """Human-readable type phrase for error messages ("an integer")."""
    articles = {
        "integer": "an integer",
        "number": "a number",
        "string": "a string",
        "boolean": "true or false",
        "array": "a list",
        "object": "an object",
        "null": "null",
    }
    types = declared_types(field, criteria_schema)
    if not types:
        return "a valid value"
    return " or ".join(articles.get(entry, entry) for entry in types)


# ── per-type coercers ─────────────────────────────────────────────────


def _coerce_string(value: object, prop: dict) -> object:
    # bool first: ``str(True)`` is "True", which is a guess, not an answer.
    if isinstance(value, bool):
        return UNUSABLE
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else UNUSABLE
    if isinstance(value, (int, float)):
        return str(value)
    return UNUSABLE


def _coerce_integer(value: object, prop: dict) -> object:
    # bool subclasses int — a yes/no answer is not a count.
    if isinstance(value, bool):
        return UNUSABLE
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else UNUSABLE
    if isinstance(value, str):
        number = coerce_number(value)
        # 2.5 commits is not 2 commits; refuse rather than round.
        if number is None or not float(number).is_integer():
            return UNUSABLE
        return int(number)
    return UNUSABLE


def _coerce_number(value: object, prop: dict) -> object:
    if isinstance(value, bool):
        return UNUSABLE
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        number = coerce_number(value)
        return UNUSABLE if number is None else number
    return UNUSABLE


def _coerce_boolean(value: object, prop: dict) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower().rstrip(".!,")
        if token in _TRUE_WORDS:
            return True
        if token in _FALSE_WORDS:
            return False
    return UNUSABLE


def _coerce_array(value: object, prop: dict) -> object:
    if isinstance(value, str):
        entries: list = [part.strip() for part in _LIST_SPLIT_RE.split(value)]
        entries = [part for part in entries if part]
        # A blank answer is not an empty list, it is no answer.
        if not entries:
            return UNUSABLE
    elif isinstance(value, (list, tuple)):
        entries = list(value)
    else:
        return UNUSABLE

    items = prop.get("items") if isinstance(prop.get("items"), dict) else {}
    if not items.get("type"):
        return entries

    coerced: list = []
    for entry in entries:
        result = _coerce_one(entry, items)
        # Never silently drop an item: a list missing one of the paths the user
        # named is a different promise from the one they made.
        if result is UNUSABLE:
            return UNUSABLE
        coerced.append(result)
    return coerced


def _coerce_object(value: object, prop: dict) -> object:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return UNUSABLE
        return parsed if isinstance(parsed, dict) else UNUSABLE
    return UNUSABLE


def _coerce_null(value: object, prop: dict) -> object:
    return None if value is None else UNUSABLE


def _coerce_unknown_type(value: object, prop: dict) -> object:
    """An unrecognised ``type`` keyword means we have no opinion, not a reject."""
    return value


_COERCERS = {
    "string": _coerce_string,
    "integer": _coerce_integer,
    "number": _coerce_number,
    "boolean": _coerce_boolean,
    "array": _coerce_array,
    "object": _coerce_object,
    "null": _coerce_null,
}


def _coerce_one(value: object, prop: dict) -> object:
    """Coerce against a bare property subschema (used for array items)."""
    declared = prop.get("type")
    types = (
        [declared]
        if isinstance(declared, str)
        else [entry for entry in declared if isinstance(entry, str)]
        if isinstance(declared, list)
        else []
    )
    for entry in types:
        coerced = _COERCERS.get(entry, _coerce_unknown_type)(value, prop)
        if coerced is not UNUSABLE and _satisfies_declared_bounds(coerced, prop):
            return coerced
    return UNUSABLE


def _satisfies_declared_bounds(value: object, prop: dict) -> bool:
    """Check *value* against the bounds the schema itself declares.

    Only the field's own stated contract is enforced here — ``minimum: 1`` on
    ``min_commits`` is why ``0`` is refused there while ``radius_m``, which
    declares no minimum, accepts it.
    """
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        minimum = prop.get("minimum")
        maximum = prop.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return False
        if isinstance(maximum, (int, float)) and value > maximum:
            return False
    if isinstance(value, list):
        min_items = prop.get("minItems")
        max_items = prop.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return False
        if isinstance(max_items, int) and len(value) > max_items:
            return False
    return True
