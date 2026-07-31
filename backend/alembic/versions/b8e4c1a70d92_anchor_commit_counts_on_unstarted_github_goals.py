"""anchor commit counts on unstarted github_repo goals

``min_commits`` used to be counted over a branch's entire history, so "push 3
commits by Saturday" was satisfied by three commits pushed last year — every
real repository already contains them. New goals now carry a server-assigned
``commits_since`` (their creation time) and only commits after it count; see
``app/services/criteria_gate.stamp_goal_created_at`` and the "commit-count time
anchor" section of ``app/workers/github_repo.py``.

That leaves the goals written before the anchor existed, and this migration is
the deliberate answer to them rather than an omission. The split is by whether a
pledge is live:

**Backfilled — ``draft`` and ``awaiting_goal_type``.** Nothing is at stake: the
deadline sweep does not enforce these statuses, no proof has been submitted, and
no card can be charged. A draft can also sit for months and then be activated,
so leaving it unanchored is not a bounded exposure — it is the vacuous pass,
indefinitely deferred. The anchor written is the goal's own ``created_at``, which
is exactly what ``create_goal`` would have stamped, so the goal ends up
indistinguishable from one created today. Nobody is charged for work already
done, because ``created_at`` precedes every commit that could have been made for
the goal.

**Left alone — ``active``, ``pending_review`` and every terminal status.** These
are live commitments whose owners were told, implicitly, that their whole history
counted. Narrowing what counts turns a passing goal into ``failed``, and
``failed`` charges a real card (``app/services/verification_result.py``). Doing
that to somebody mid-goal, without telling them, is the wrongful-charge half of
``app/services/fault_attribution`` — the exact thing the anchor work was careful
not to do. Their exposure is bounded: an enforceable goal resolves at its
deadline, so this population empties itself and the hole closes without anyone
being re-judged.

No notification is sent, because for the rows this touches nothing the owner was
promised has changed: a draft was never a commitment, and its criteria are
visible in the goal before they activate it.

Measured before writing, not assumed: on the production database at the time of
this migration the affected count was **zero** — ``sacrifice_live`` held four
``geolocation`` goals, all terminal, and no ``github_repo`` goal at all. So this
runs as a provable no-op there and exists for the deployments and drafts that
appear between now and rollout.

Revision ID: b8e4c1a70d92
Revises: a3c7d9e1f2b4
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8e4c1a70d92"
down_revision: Union[str, None] = "a3c7d9e1f2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Kept as literals rather than imported from ``app``: a migration has to keep
#: describing the schema as it was at this revision, and an import would silently
#: change what already-applied history meant.
_CRITERIA_FIELD = "commits_since"
_NOT_YET_ENFORCEABLE = ("draft", "awaiting_goal_type")

# ``?`` is "does this jsonb have this top-level key", so a goal that already has
# an anchor is skipped and re-running changes nothing. ``|| jsonb_build_object``
# merges rather than replacing, so no other criterion is disturbed.
#
# ``to_char`` at second precision with an explicit ``Z``, matching what
# ``_resolve_commit_anchor`` normalises to. Truncating the fraction of a second
# rounds the window *earlier*, which can only ever include one more commit —
# never exclude one, which would be the direction that charges someone.
_BACKFILL = sa.text(
    """
    UPDATE goal_criteria AS c
    SET criteria_data = COALESCE(c.criteria_data, '{}'::jsonb)
        || jsonb_build_object(
               :field,
               to_char(g.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
           )
    FROM goals AS g
    WHERE g.id = c.goal_id
      AND g.goal_type = 'github_repo'
      AND g.status::text = ANY(:statuses)
      AND NOT (COALESCE(c.criteria_data, '{}'::jsonb) ? :field)
      AND g.created_at IS NOT NULL
    """
)

_ROLLBACK = sa.text(
    """
    UPDATE goal_criteria AS c
    SET criteria_data = COALESCE(c.criteria_data, '{}'::jsonb) - :field
    FROM goals AS g
    WHERE g.id = c.goal_id
      AND g.goal_type = 'github_repo'
      AND g.status::text = ANY(:statuses)
    """
)


def upgrade() -> None:
    result = op.get_bind().execute(
        _BACKFILL,
        {"field": _CRITERIA_FIELD, "statuses": list(_NOT_YET_ENFORCEABLE)},
    )
    print(
        f"anchored {result.rowcount} unstarted github_repo goal(s) "
        f"with {_CRITERIA_FIELD}"
    )


def downgrade() -> None:
    # Removing the key restores whole-history counting, which is the *generous*
    # reading — a downgrade can therefore only ever un-fail a goal, never bill
    # one. Scoped to the same statuses so it cannot strip an anchor that
    # ``create_goal`` legitimately wrote on a goal that has since started.
    op.get_bind().execute(
        _ROLLBACK,
        {"field": _CRITERIA_FIELD, "statuses": list(_NOT_YET_ENFORCEABLE)},
    )
