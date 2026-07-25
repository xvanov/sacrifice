"""The one gate every goal writer passes: criteria a verifier can check.

``app/routes/chat.py`` learned to validate criteria properly — coerce each value
to the type its goal type declares, insist on the schema's ``required`` fields,
and honour the ``anyOf`` "at least one checkable criterion" contract. But it
learned it *in the chat flow*, and ``POST /api/goals`` hands a bare
``GoalCreate`` straight to ``create_goal``, so every one of those checks was
skippable by not using the chat. Verified by execution, not inspection::

    POST /api/goals {expected_status: "200"} -> 201 draft
    PUT  /api/goals/{id} {status: "active"}  -> 200 active   # and chargeable

Each of those is a money bug, because a goal that cannot be won is a goal that
gets charged:

* ``api_endpoint`` with a string ``"200"`` — ``app/workers/api_check.py``
  compares ``actual_status == expected_status`` and ``200 == "200"`` is ``False``
  in Python forever. The endpoint does exactly what the user promised,
  verification says ``failed``, and
  ``app/services/verification_result.persist_verification_result`` charges a real
  PaymentIntent.
* ``youtube_video`` with ``min_duration_seconds: "5 minutes"`` —
  ``app/workers/youtube.py`` evaluates ``duration_seconds >= min_duration``,
  which raises ``TypeError``; the surrounding ``except ValueError`` does not catch
  it, so no verdict is ever written, the goal stays ``active``, and
  ``app/workers/deadline.py`` charges it at the deadline.
* ``github_repo`` with empty criteria — nothing checkable, so the verifier can
  only ever refuse to certify it. Unwinnable by construction.

So the gate belongs on the goal payload, not in one route. :func:`gate_criteria`
is applied by ``app/services/goal.create_goal`` — the one function that writes a
``goal_criteria`` row, which both write paths call — and again by the
``PUT /api/goals/{id}`` activation path in ``app/routes/goals.py``, for the drafts
already in the database from before the gate existed.

Not on the ``GoalCreate`` schema, which was the first instinct: it fires before
the chat endpoint's own consistency checks and replaces their more pointed
diagnosis ("you cannot change the goal type of a confirmed draft") with a
complaint about criteria that only fail because the type changed. ``create_goal``
is equally unskippable and correctly ordered — and it is already where the
analogous ``ValueError`` for a too-soon deadline lives.

Two rules it does **not** break:

1. **No invented defaults.** A missing required or checkable criterion is a 422
   naming the field, never a silent ``min_commits: 1``. Defaulting fabricates a
   commitment the user never made, and a failed goal charges their card — the
   guess is the more expensive error.
2. **Silence about fields the schema does not describe.** Legacy ``conditions``
   payloads, and anything a new goal type stores before its schema catches up,
   pass through untouched. That pass-through is what
   ``app/services/criteria_coercion`` deliberately guarantees; a validation step
   that dropped unknown keys would quietly rewrite goals it does not understand.

An unregistered goal type (the ``__generated__`` placeholder the chat uses while
a goal type is still being built, and any type whose plugin has not merged yet)
has no ``criteria_schema`` to check against, so the gate has no opinion and
returns the criteria unchanged. That is explicit, not an accident of a caught
exception: ``awaiting_goal_type`` is a real status a real goal sits in.
"""

from __future__ import annotations

import re
from collections.abc import Container

from app.services.criteria_coercion import (
    coerce_criteria,
    declared_types,
    describe_expected_type,
)

#: A number and nothing else. ``app/services/input_parsing.coerce_number`` is
#: deliberately looser — it pulls the first number out of any text, which is
#: right for the field it was written for (``radius_m``, where the unit is
#: implied by the field name) and wrong for a field whose name states a unit the
#: answer may not be in. ``min_duration_seconds: "5 minutes"`` coerces to ``5``
#: through that path: an integer, so nothing downstream raises, and a goal that
#: any five-second clip satisfies. That is the same fabrication as defaulting a
#: missing field — the user promised five minutes and the stored commitment is
#: 1/60th of it — so a numeric field given a string that carries anything beyond
#: the number is refused here rather than guessed at.
_BARE_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

_NUMERIC_TYPES = frozenset({"integer", "number"})


class CriteriaRejected(ValueError):
    """Criteria that would create a goal its owner cannot win.

    A ``ValueError`` so the callers that already translate ``ValueError`` from
    ``create_goal`` into a 422 — the chat's create-from-session path does — need no
    change to report it.
    """


def criteria_schema_for(goal_type_name: str) -> dict:
    """Return a goal type's criteria schema, or ``{}`` for an unknown type.

    ``{}`` means "no declared contract", which every check below reads as "no
    opinion" — see the module docstring on unregistered types.
    """
    # Imported lazily so importing this module does not trigger plugin
    # discovery; ``app/schemas/goal.py`` does the same for the same reason.
    from app.goal_types.registry import get_type

    try:
        return get_type(goal_type_name).criteria_schema or {}
    except (KeyError, ImportError):
        return {}


def is_missing_value(value: object) -> bool:
    """True when *value* was never supplied.

    Presence only. ``0``, ``False`` and ``[]`` were supplied and are therefore
    not missing — whether they configure a check worth running is
    :func:`is_criterion_set`'s question, asked only where a schema says so.

    Mirrors ``_is_missing_value`` in ``app/routes/chat.py``; the two are pinned
    to each other by ``tests/test_goal_criteria_gate.py`` until chat imports
    this one.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def is_criterion_set(value: object) -> bool:
    """True when *value* configures something a verifier can actually check.

    Deliberately stricter than :func:`is_missing_value`: an "at least one of
    these" contract is only satisfied by a criterion that expresses a real
    requirement, and the verifiers treat ``0``, ``[]`` and ``False`` as
    configuring nothing — ``app/workers/github_repo.py`` calls exactly those
    "inert" and fails the goal when nothing runnable is left. Counting them as
    collected is how an unwinnable goal gets created.

    Mirrors ``_is_criterion_set`` in ``app/routes/chat.py``; see
    :func:`is_missing_value`.
    """
    if value is None:
        return False
    # bool before int — bool is a subclass of int, and require_pr=False asks
    # for no check at all.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value > 0
    return True


def unsatisfied_any_of_field(
    criteria_schema: dict,
    criteria_data: dict,
    *,
    prefer: Container[str] = (),
) -> str | None:
    """Return one field to collect when a schema's ``anyOf`` contract is unmet.

    A goal type declares "at least one of these criteria is required" as
    ``anyOf: [{"required": ["a"]}, {"required": ["b"]}]``. ``github_repo`` does,
    because its verifier refuses to certify a goal whose criteria name only the
    repo — plain ``required`` cannot express a choice.

    Read off the schema rather than per goal type: any type that adopts the
    pattern is enforced automatically, and no goal-type name is hardcoded here
    to rot.

    Returns ``None`` when the schema declares no such contract or one
    alternative is already satisfied. Otherwise returns a field to name in the
    error, preferring one in *prefer* — the chat passes its prompt table so it
    never asks about an alternative it cannot collect (github_repo's legacy
    ``conditions``).

    Mirrors ``_unsatisfied_any_of_field`` in ``app/routes/chat.py``; see
    :func:`is_missing_value`.
    """
    branches = criteria_schema.get("anyOf")
    if not isinstance(branches, list):
        return None

    alternatives: list[list[str]] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        fields = [f for f in branch.get("required", []) if isinstance(f, str)]
        if fields:
            alternatives.append(fields)
    if not alternatives:
        return None

    for fields in alternatives:
        if all(is_criterion_set(criteria_data.get(field)) for field in fields):
            return None

    for fields in alternatives:
        for field in fields:
            if field in prefer:
                return field
    return alternatives[0][0]


def unwrap_criteria(criteria: dict) -> dict:
    """Return the flat criteria dict from either accepted request shape.

    The API spec's canonical shape is ``{criteria_type, criteria_data}``;
    ``GoalCreate.criteria`` is a flat dict. Both arrive in practice, and
    ``create_goal`` stores whatever it is handed verbatim as
    ``goal_criteria.criteria_data`` — so a wrapped payload used to store
    ``{"criteria_data": {...}}``, one level deeper than every verifier reads.
    Unwrap on ``criteria_data`` alone: requiring ``criteria_type`` too is the
    bypass ``app/routes/chat.py`` had to close.
    """
    inner = criteria.get("criteria_data")
    if isinstance(inner, dict):
        return inner
    if "criteria_data" in criteria:
        # Present but not a dict: refuse rather than guess. Passing it on would
        # put an uncoerced, unvalidated value into stored criteria.
        raise CriteriaRejected(
            "criteria.criteria_data must be an object when supplied."
        )
    return criteria


def ambiguous_numeric_strings(criteria: dict, criteria_schema: dict) -> list[str]:
    """Fields given a string that is *nearly* a number for a numeric field.

    ``"200"`` is a number written as text and its intent is unambiguous, so it
    coerces. ``"5 minutes"`` and a pasted ``35°53'53.4"N`` are not: they carry a
    unit or a notation that the plain numeric read silently discards, leaving a
    value with the right type and the wrong meaning. Naming them in a 422 costs
    the caller one retry; guessing costs them their pledge.
    """
    ambiguous: list[str] = []
    for field, value in criteria.items():
        if not isinstance(value, str):
            continue
        if not _NUMERIC_TYPES.intersection(declared_types(field, criteria_schema)):
            continue
        remainder = _BARE_NUMBER_RE.sub("", value.strip(), count=1).strip()
        if remainder:
            ambiguous.append(field)
    return ambiguous


def gate_criteria(
    goal_type_name: str,
    criteria: dict,
    *,
    prefer: Container[str] = (),
) -> dict:
    """Return *criteria* coerced to its goal type's declared shape.

    Raises :class:`CriteriaRejected` — never guesses, never defaults — when the
    criteria would produce a goal that cannot be verified. The returned dict is
    what should be stored: coercion is the point, so ``expected_status: "200"``
    comes back as ``200`` rather than being rejected for a value whose intent is
    unambiguous.
    """
    criteria = unwrap_criteria(criteria)
    criteria_schema = criteria_schema_for(goal_type_name)
    if not criteria_schema:
        # Unregistered / still-being-generated goal type: no declared contract,
        # so nothing to check and nothing to coerce against.
        return criteria

    ambiguous = ambiguous_numeric_strings(criteria, criteria_schema)
    if ambiguous:
        details = ", ".join(
            f"{field} expects {describe_expected_type(field, criteria_schema)} "
            f"written as a plain number (got {criteria[field]!r})"
            for field in ambiguous
        )
        raise CriteriaRejected(f"Invalid criteria value: {details}.")

    coerced, unusable = coerce_criteria(criteria, criteria_schema=criteria_schema)
    if unusable:
        details = ", ".join(
            f"{field} expects {describe_expected_type(field, criteria_schema)}"
            for field in unusable
        )
        raise CriteriaRejected(f"Invalid criteria value: {details}.")

    for field in criteria_schema.get("required", []):
        if is_missing_value(coerced.get(field)):
            raise CriteriaRejected(f"Missing required criteria field: {field}")

    unsatisfied = unsatisfied_any_of_field(criteria_schema, coerced, prefer=prefer)
    if unsatisfied is not None:
        raise CriteriaRejected(
            f"Criteria must set at least one checkable requirement "
            f"(e.g. {unsatisfied}); without one the goal can never be verified."
        )

    return coerced
