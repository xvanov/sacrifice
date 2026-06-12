"""Factory directions volume service.

Writes synthesized direction directories to the configured
factory_directions_path (default /var/factory/directions) so the
factory chain can pick them up from the bind-mounted host directory.
"""

import json
import os
from datetime import datetime, timezone


from app.config import settings


def write_direction(
    direction_name: str,
    payload: dict,
    *,
    base_path: str | None = None,
) -> str:
    """Write a direction directory containing direction.json and .manifest.

    Returns the absolute path to the created direction directory.
    """
    root = base_path or settings.factory_directions_path
    dir_path = os.path.join(root, direction_name)
    os.makedirs(dir_path, exist_ok=True)

    direction_json_path = os.path.join(dir_path, "direction.json")
    with open(direction_json_path, "w") as fh:
        json.dump(payload, fh, indent=2)

    manifest = {
        "direction": direction_name,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = os.path.join(dir_path, ".manifest")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    return dir_path