"""Demo routes for UX audit observability.

Exposes deterministic goal-type generation banner states so the UX audit
can observe each documented status-banner transition and the final
notification-driven return path without real background factory work.

Backend seam for downstream trigger/read-path stories
------------------------------------------------------
The in-memory ``DemoGenerationSequence`` class in
``app.services.direction_synth`` is the canonical state source.
Downstream stories that need to observe individual generation states
without going through this HTTP endpoint should call::

    from app.services.direction_synth import DemoGenerationSequence

    seq = DemoGenerationSequence()
    state = seq.get_state("pr_open")       # single lookup
    states = seq.get_states()              # full ordered sequence

This avoids filesystem coupling and keeps trigger/read-path logic
testable without the demo config gate.

Trigger path:  GET /api/demo/generation-states
Config gate:   settings.sacrifice_demo_generation_states = True
Stories:       320 (route + fixture), 367 (sequence abstraction + seam)
"""

from fastapi import APIRouter, HTTPException, status

from app.config import settings
from app.services.direction_synth import ensure_demo_directions

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.get("/generation-states")
async def demo_generation_states():
    """Return fixture-backed goal-type generation banner states.

    Returns a list of demo direction entries, each representing one
    documented status-banner state in the generation lifecycle:

    ==================== ==============================================
    banner_label         description
    ==================== ==============================================
    ``queued``           waiting for factory pick-up
    ``in progress``      factory is generating the module
    ``pull request open`` pull request is open for review
    ``merging``          pull request is approved and merging
    (null)               goal type ready, notification sent (final
                         return path — ``notification`` carries
                         ``goal_type_ready``)
    ==================== ==============================================

    Each entry includes ``direction_id``, ``status`` (coarse API status),
    ``raw_status`` (raw factory status for audit traceability),
    ``banner_label`` (the documented audit-facing banner label, or null),
    ``pr_url``, ``summary``, and ``notification`` (``null`` except for the
    return-path entry, where it carries the ``goal_type_ready`` notification
    signal).

    This endpoint is gated behind
    ``settings.sacrifice_demo_generation_states``.  When the flag is
    ``False`` (default), the endpoint returns 404 — it does not reveal
    its existence.

    The response shape is built from ``DemoGenerationSequence.get_states()``
    via ``ensure_demo_directions()`` so the in-memory contract and the
    HTTP response contract stay in sync.
    """
    if not settings.sacrifice_demo_generation_states:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    entries = await ensure_demo_directions()
    return {"states": entries}
