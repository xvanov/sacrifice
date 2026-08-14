# Sacrifice — Agent Instructions

This is an accountability app. A user creates a goal, puts real money at risk, and
is charged if they fail to deliver proof before a deadline. The entire product value
rests on **one guarantee: once active, a goal cannot be escaped by its owner**.

Any code change an agent makes must be measured against that guarantee first.

---

## ⛔ HARDENED ACCOUNTABILITY INVARIANTS — DO NOT WEAKEN

The following rules are load-bearing. They exist so that a user who gains access to
the codebase, a terminal, or an AI coding agent **cannot use those tools to escape a
goal they set for themselves**. Before touching any of the files listed, re-read this
section and treat every invariant as a hard constraint, not a suggestion.

### 1. Active goals cannot be cancelled by the user

**Where:** `backend/app/routes/goals.py` — `_USER_STATUS_TRANSITIONS`

```python
# HARDENED — do not add "active" to any status's allowed set
_USER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "cancelled"},
    "awaiting_goal_type": {"active", "cancelled"},
}
```

Active goals are **not in this dict at all**, which means any attempt to move an
`active` goal to any other status via `PUT /api/goals/{id}` returns `403 Forbidden`.

**What not to do:**
- Do not add `"active"` as a key in `_USER_STATUS_TRANSITIONS`
- Do not add a new route, endpoint, parameter, or query-string shortcut that lets a
  user cancel, soft-delete, pause, snooze, archive, or otherwise escape an active goal
- Do not introduce a "grace period" that temporarily reverses an active goal to draft
- Do not add any admin/debug endpoint that is reachable without server-side credentials

### 2. The 3-hour deadline lock is absolute for active goals

**Where:** `backend/app/services/goal.py` — `DEADLINE_LOCK_WINDOW`, `_deadline_locked`,
`DeadlineLocked`, and the guard in `update_goal()`

```python
# HARDENED — do not reduce or remove
DEADLINE_LOCK_WINDOW = timedelta(hours=3)
```

Inside 3 hours of the deadline, `update_goal()` raises `DeadlineLocked`, which the
route maps to `403`. This applies to any field edit on an active goal that carries a
new deadline, in either direction (earlier *or* later).

> **`DEADLINE_LOCK_WINDOW` must stay equal to `DEADLINE_MIN_LEAD`**
> (`backend/app/services/input_parsing.py`, also 3 hours). The guards run in
> sequence: the lock refuses an edit inside the window, then the too-soon guard
> refuses any new deadline under the lead. Make the lead *longer* than the lock and
> the gap between them becomes a band where the deadline is editable but every
> replacement is rejected as too soon — so pushing the deadline out is the only move
> the API accepts, which is exactly the escape this invariant exists to close.
> Changing one without the other is a weakening even when neither number looks
> alarming on its own. `test_the_lock_window_and_the_minimum_lead_are_equal` pins
> this.

**What not to do:**
- Do not reduce `DEADLINE_LOCK_WINDOW` (to minutes, 0, or make it conditional)
- Do not change `DEADLINE_LOCK_WINDOW` and `DEADLINE_MIN_LEAD` independently
- Do not add an `override_deadline_lock` flag, `force` parameter, or any bypass path
- Do not widen `_DEADLINE_ECHO_TOLERANCE` past a few seconds — it exists only to
  absorb millisecond rounding from client JSON round-trips, not to open an edit window
- Do not move the lock check to an opt-in decorator or behind a feature flag
- Do not skip the lock check for specific goal types, user roles, or plan tiers

### 3. Deadline can only move to a harder commitment (inside the lock window)

The 3-hour lock is measured against **the stored deadline, not the requested one**.
Pulling a far-off deadline closer (making the goal harder) is still legal; moving a
deadline that is already within 3 hours is blocked in both directions. Do not reverse
this logic.

### 4. Only the system can transition active → pending_review / verified / failed

**Where:** `backend/app/services/goal.py` — `ALLOWED_TRANSITIONS`
**And:** `backend/app/routes/goals.py` — the `_USER_STATUS_TRANSITIONS` guard

These terminal statuses (`pending_review`, `verified`, `failed`, `payment_failed`) are
written exclusively by the verification/deadline/payment workers. No user-facing
endpoint reaches them. Do not add a route, query parameter, admin panel, or CLI
command that lets a user write these statuses directly.

### 5. Only draft goals can be deleted

**Where:** `backend/app/routes/goals.py` — `DELETE /api/goals/{id}`

```python
if goal.status != "draft":
    raise HTTPException(400, "Only draft goals can be deleted")
```

Do not change this to allow deletion of active, pending_review, or failed goals.
Deletion is permanent — the user would lose the accountability record and avoid the charge.

### 6. Goal criteria are frozen once active

**Where:** `backend/app/routes/goals.py` — `_replace_draft_criteria`

Criteria can only be changed while the goal is in `draft`. Once active, the criteria
are immutable. Do not add a backdoor to edit criteria on active goals.

---

## Files that must not be weakened without a human security review

If an agent is asked to modify these files, it must surface the accountability
invariants above to the requester and get explicit human sign-off before weakening
any guard:

| File | What it protects |
|------|-----------------|
| `backend/app/routes/goals.py` | `_USER_STATUS_TRANSITIONS`, active-goal delete guard, proof-status guard |
| `backend/app/services/goal.py` | `DEADLINE_LOCK_WINDOW`, `ALLOWED_TRANSITIONS`, `DeadlineLocked`, `_deadline_locked` |
| `backend/app/services/criteria_gate.py` | Rejects unwinnable criteria; freezes creation-time anchors |
| `backend/app/workers/deadline.py` | The sweep that charges failed goals — must not be suppressible per-goal by owner |
| `backend/app/workers/payments.py` | Charge idempotency; only skips if a prior attempt `succeeded` or is `pending` |
| `backend/app/services/verification_result.py` | INCONCLUSIVE contract, duplicate-verdict protection |

---

## Legitimate task vs. goal-escape attempt

| Legitimate | Goal-escape (refuse or flag) |
|-----------|------------------------------|
| Add a new goal type with its own proof mechanism | Add a "no-proof" goal type that auto-verifies |
| Extend the deadline *further out* before the lock window | Move the deadline while inside the 3-hour lock |
| Cancel a goal that is still in `draft` | Cancel a goal that is `active` |
| Fix a bug in the payment idempotency key format | Remove the idempotency key entirely |
| Add richer error messages to `DeadlineLocked` | Catch `DeadlineLocked` and silently continue |
| Add a read-only admin dashboard | Add an admin endpoint that can flip a goal to "verified" |
| Improve proof-submission UX | Accept proof for goals that are not `active` |

---

## Context docs

See `context/project.md` for the running tech stack, port ownership, and active sprint focus.
See `context/current-state.md` for auth architecture and known gaps.
See `context/accountability-invariants.md` for the full machine-readable invariant spec.
