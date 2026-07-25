"""Operator endpoints. Read-only, and not reachable by ordinary users.

Only one route so far: the blocked-goals reader, so the pledges the deadline
sweep is silently skipping can be seen without shell access to the box.

Authorization is ``require_operator`` (``app/core/operator_auth.py``), a
server-configured shared secret rather than the ``get_current_user`` bearer JWT
every other route uses. That deviation is deliberate: this route exposes other
users' goals and their email addresses, and there is no admin concept on the
``users`` table to hang a privilege check on — see that module for why the
alternatives (an email allowlist, a client-asserted claim) were rejected.

Nothing here writes. Resolving a blocked goal is deliberately CLI-only
(``sacrifice blocked-goals resolve``): a state change to another user's goal
should require access to the machine, not just a header.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.operator_auth import require_operator
from app.database import get_db
from app.services.blocked_goals import list_blocked_goals

router = APIRouter(prefix="/api/operator", tags=["operator"])


@router.get("/blocked-goals", dependencies=[Depends(require_operator)])
async def get_blocked_goals(
    needs_review_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Goals stranded on a verification we could not complete, longest first.

    ``needs_review_only=true`` narrows to those whose automatic retry budget is
    spent — the ones that will never move again without an operator.

    The response carries only the fields on
    ``app/services/blocked_goals.BlockedGoal``: never ``proof_data`` or
    ``dispatch_criteria`` (either can hold an encrypted GitHub PAT), and no user
    field beyond the email needed to contact the person waiting.
    """
    blocked = await list_blocked_goals(db, needs_review_only=needs_review_only)
    return {
        "count": len(blocked),
        "blocked_goals": [b.to_public_dict() for b in blocked],
    }
