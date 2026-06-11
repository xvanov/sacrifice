"""Media upload storage-path resolution.

This module provides the canonical path-resolution logic for media uploads.
Later stories (upload service, routes) will build on this base.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings


def media_storage_path(
    user_id: uuid.UUID,
    goal_id: uuid.UUID | None,
    upload_id: uuid.UUID,
    *,
    media_dir: str | None = None,
) -> Path:
    """Return the canonical storage path for a media upload.

    Convention: <media_dir>/<user_id>/<goal_or_orphan>/<upload_id>.mp4

    If *goal_id* is None the segment is ``"unassigned"``.
    """
    root = Path(media_dir if media_dir is not None else settings.sacrifice_media_dir)
    goal_segment = str(goal_id) if goal_id is not None else "unassigned"
    return root / str(user_id) / goal_segment / f"{upload_id}.mp4"