from __future__ import annotations

import uuid

from app.config import settings


def media_storage_path(
    user_id: uuid.UUID,
    goal_id: uuid.UUID | None,
    upload_id: uuid.UUID,
) -> str:
    """Return the configured storage path for a media upload.

    Convention: <root>/<user_id>/<goal_or_orphan>/<upload_id>.mp4
    """
    segment = settings.sacrifice_media_orphan_segment if goal_id is None else str(goal_id)
    return f"{settings.sacrifice_media_dir}/{user_id}/{segment}/{upload_id}.mp4"