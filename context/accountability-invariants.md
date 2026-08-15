# Accountability Invariants

> **This document is part of the hardened system design.**
> It describes the invariants that make Sacrifice work as an accountability product.
> All agents and contributors must treat these as non-negotiable constraints.
> The goal is not to make the app hard to use — it is to make it impossible for a
> user to escape a commitment they made to themselves, even if they later gain access
> to the codebase or an AI coding agent.

---

## Why these invariants exist

The product charges real money when a user fails a goal. That mechanism is only
meaningful if the owner cannot circumvent it after setting the goal. Without these
guards, a user with terminal access or an AI assistant could:

- Cancel an active goal minutes before it fails
- Push the deadline back 2 hours before it expires
- Delete the goal record entirely
- Flip the goal status to "verified" directly in the database or via an API endpoint

These are not hypothetical risks — they are the exact failure mode the app is
designed to prevent. The invariants below are the technical expression of that
design intent.

---

## Invariant 1 — Active goals are not user-cancellable

**Enforcement points:**

1. `backend/app/routes/goals.py` — `_USER_STATUS_TRANSITIONS`
   Does not include `"active"` as a key.
   Any status change on an active goal via `PUT /api/goals/{id}` is `403 Forbidden`.

2. `backend/app/services/goal.py` — `ALLOWED_TRANSITIONS`
   The service-layer DAG allows `active → cancelled` only for system writes
   (verification/deadline workers), never for the user-facing route path.

**What must not change:**
- `"active"` must never appear as a key in `_USER_STATUS_TRANSITIONS`
- No new endpoint may accept a user-initiated cancel of an active goal
- No "pause", "snooze", "archive", or "grace period" transition may be added without
  re-instating the same financial accountability on resume

---

## Invariant 2 — The 3-hour deadline lock is inviolable

**Enforcement point:**
`backend/app/services/goal.py`

```python
DEADLINE_LOCK_WINDOW = timedelta(hours=3)  # do not reduce; keep == DEADLINE_MIN_LEAD
_DEADLINE_ECHO_TOLERANCE = timedelta(seconds=1)  # do not expand

def _deadline_locked(current_deadline):
    return (current_deadline - now(utc)) <= DEADLINE_LOCK_WINDOW
```

`update_goal()` raises `DeadlineLocked` — mapped to `403` by the route — whenever:
- The goal is `active` or `pending_review` (enforceable statuses)
- The stored deadline is within `DEADLINE_LOCK_WINDOW` of now
- The requested deadline is a real move (not an echo within `_DEADLINE_ECHO_TOLERANCE`)

**Lock direction:** The lock is measured against the **stored** deadline.
Moving a distant deadline *closer* (harder for the user) stays legal inside the window.
Moving a deadline that is already within 3 hours — in either direction — is blocked.

**What must not change:**
- `DEADLINE_LOCK_WINDOW` must not be reduced (to minutes, 0, or made conditional),
  and must not move independently of `DEADLINE_MIN_LEAD`
- No `force`, `override`, `bypass_lock`, or `admin_override` flag may be added
- The check must remain on every PUT path — not moved to a decorator that can be
  omitted or feature-flagged
- `_DEADLINE_ECHO_TOLERANCE` must stay at ≤1 second; expanding it opens a real
  edit window disguised as round-trip noise

**Bounded by `DEADLINE_MIN_LEAD`** (`app/services/input_parsing.py`) — the lead must
never EXCEED the lock. The guards run in sequence: the lock refuses an edit inside the
window, then the too-soon guard refuses any replacement deadline under the lead. If the
lead is the longer of the two, the difference is a band in which the goal sits outside
the lock (its deadline is editable) while every new deadline is rejected as too soon —
so the only move the API will accept is pushing the deadline further out, with no
proof. That is the evasion this invariant exists to close, reached without touching
either guard. `test_the_minimum_lead_never_exceeds_the_lock_window` pins it.

A lead *shorter* than the lock is correct and is the current state (1 hour against 3).
It means goals can be created with a deadline already inside the lock window, which
Invariant 2c keeps survivable. This was previously written as "the two must stay
equal"; that overstated it, and a restore that took it literally raised the lead to 3
hours and made same-afternoon goals impossible to create.

---

## Invariant 2b — The stake is frozen inside the deadline lock window

**Enforcement point:**
`backend/app/services/goal.py` — the guard in `update_goal()` directly after the
deadline lock

```python
if (goal.status in _ENFORCEABLE_STATUSES
        and goal.deadline is not None
        and _deadline_locked(goal.deadline)
        and _stake_changed(goal, data)):
    raise StakeLocked(...)          # -> 403
```

`pledge_amount` and `charity_id` are immutable on an `active` / `pending_review`
goal once its stored deadline is inside `DEADLINE_LOCK_WINDOW`.

**Why this exists separately from Invariant 2:** the deadline decides *whether* the
pledge is charged; the stake decides *what failing costs*. Locking only the deadline
leaves the identical escape one field over — leave the date alone, drop the pledge
to a token amount (or move it to a recipient the owner is happy to fund), and the
goal survives its own failure at no cost. Both halves have to freeze together or
neither is worth freezing.

**Direction:** both. Raising the pledge is not an escape, but a stake that can still
move is not settled, and settled is the property the lock is buying.

**What must not change:**
- No exemption for "the user is making it harder" (raising the pledge) — the
  direction argument is the thread that unpicks this guard
- Clearing `charity_id` with an explicit null counts as a change; `_stake_changed`
  keys off `model_fields_set`, not truthiness, precisely for this
- The echo carve-out must stay exact equality — unlike the deadline it needs no
  tolerance, and any tolerance introduced here is a real edit window
- `StakeLocked` must keep subclassing `CommitmentLocked` (hence `ValueError`), so a
  caller catching either still refuses the write

**Serving:** `stake_locked` on the goal payload mirrors `deadline_locked` so a client
can disable the fields. The server remains the enforcement point.

---

## Invariant 2c — The creation grace period

**Enforcement point:**
`backend/app/services/goal.py` — `CREATION_GRACE_PERIOD`, `_within_creation_grace`,
and `_terms_are_frozen`, through which both locks and both payload flags run.

```python
CREATION_GRACE_PERIOD = timedelta(minutes=10)   # anchored on created_at
```

For 10 minutes after creation, Invariants 2 and 2b do not apply to a goal.

**Why this is a fix and not a hole.** The chat flow creates goals `active`
(`app/routes/chat.py`), so a goal whose deadline is already inside the lock window
arrived frozen: deadline un-editable (2), pledge and recipient un-editable (2b), and
un-deletable as well, since deletion is draft-only (Invariant 3). There was no legal
move anywhere in the API — a mistyped hour meant a certain charge. That is a trap,
not accountability.

Nothing is escaped by it. The anchor is `created_at`, which no update path writes, so
the window only ever shrinks toward expiry; and a goal minutes old has not been tested
by its deadline yet, so anything the owner changes here they could equally have typed
at creation.

**What must not change:**
- The anchor stays `created_at`. Keying off `updated_at` — or any field a write
  refreshes — converts this into a renewable lease: touch the goal every nine minutes
  and the deadline lock never engages
- The grace must not cover a goal whose deadline has already passed. Past the deadline
  the goal is failed and awaiting the sweep, and an edit is the plain escape.
  `_within_creation_grace` returns False in that case, and does so independently of
  `DEADLINE_MIN_LEAD` so a future cut to the lead cannot open it silently
- The grace must stay short enough that a real deadline cannot arrive inside it
- Both locks and both payload flags must keep going through `_terms_are_frozen`, so
  what the API enforces and what the client renders cannot drift apart

---

## Invariant 3 — Only draft goals can be deleted

**Enforcement point:**
`backend/app/routes/goals.py` — `DELETE /api/goals/{id}`

```python
if goal.status != "draft":
    raise HTTPException(400, "Only draft goals can be deleted")
```

An active goal cannot be erased. Deletion is permanent and removes the accountability
record. If a user could delete an active goal they would escape the charge with no
trace.

**What must not change:**
- The status guard on DELETE must remain `!= "draft"` (not widened to include active/failed)
- No "soft delete" or "hide" mechanism may be introduced without preserving the
  financial record and still applying the charge on failure

---

## Invariant 4 — Terminal statuses are system-only

`pending_review`, `verified`, `failed`, `payment_failed` are written exclusively by:
- `backend/app/workers/deadline.py` — deadline sweep
- `backend/app/services/verification_result.py` — verification outcome
- `backend/app/workers/payments.py` — payment processing

No user-facing HTTP endpoint may write these statuses. No admin shortcut may bypass
the verification or payment pipeline.

**Why:** `active → verified` without proof is a free escape. `active → failed`
without a charge is a free escape in the other direction. Both must flow through the
verified pipeline.

**Note on timing vs. authority:** `backend/app/services/verification_result.py`
leaves a goal `active` after a genuine `failed` verdict on one submission, as
long as the deadline has not passed — the owner may submit another proof, and
`app/workers/deadline.py` is what eventually resolves the goal to `failed`
(and charges) if no verified proof arrives in time. This is not a new writer
of terminal status: the same two system components (verification result,
deadline sweep) still make the call, only later than before. See that
module's docstring ("A real failure before the deadline is not yet a verdict
on the goal") for the full rationale.

---

## Invariant 5 — Goal criteria are frozen at activation

**Enforcement point:**
`backend/app/routes/goals.py` — `_replace_draft_criteria`
`_CRITERIA_EDITABLE_STATUSES = {"draft"}`

Once a goal is `active`, its `criteria_data` is immutable. Changing what counts as
proof after activation is equivalent to lowering the bar after committing — it must
not be possible.

---

## Invariant 6 — Charge safety cannot be bypassed per-goal

**Enforcement points:**
- `backend/app/workers/payments.py` — idempotency key `goal-charge-{goal_id}`,
  only skips if prior attempt is `succeeded` or `pending` (not `failed`)
- `backend/app/services/verification_result.py` — duplicate-verdict protection
  (conditional UPDATE on unsettled status)
- `backend/app/services/fault_attribution.py` — egress probe before attributing
  a network failure as the user's fault

**What must not change:**
- The idempotency key must not be made optional or parameterised per-goal
- A prior `failed` payment row must not permanently excuse the debt (only
  `succeeded` or `pending` skip the charge attempt)
- The `INCONCLUSIVE` outcome must remain distinct from `failed` to protect against
  spurious charges on egress failures

---

## How to propose a legitimate change to these areas

If a real product need requires touching one of these invariants, the PR must:

1. Name the invariant explicitly in the PR description
2. Explain the product need that requires relaxing it
3. Describe what replaces the protection (e.g. a new audit trail, a delay,
   a separate verification step)
4. Get explicit sign-off from a human reviewer — not just an agent

A change that silently weakens an invariant without acknowledgement is a red flag
regardless of how the PR is otherwise described.

---

## Attack surface summary

The specific attack an AI agent should resist when given access to this codebase:

| User action | Entry point | Invariant |
|-------------|-------------|-----------|
| "cancel my active goal" | `PUT /api/goals/{id}` with `status: cancelled` | 1 |
| "push the deadline back" inside lock window | `PUT /api/goals/{id}` with new `deadline` | 2 |
| "delete my failed goal" | `DELETE /api/goals/{id}` | 3 |
| "mark my goal as verified" directly | `PUT /api/goals/{id}` with `status: verified` | 4 |
| "change what counts as proof" after activating | `PUT /api/goals/{id}` with `criteria` | 5 |
| "remove the payment charge" for a specific goal | direct DB edit or worker bypass | 6 |

An AI agent asked to implement any of the right-column actions should **refuse**
and explain why it conflicts with the accountability guarantee. It should not find a
clever workaround that achieves the same outcome by a different path.
