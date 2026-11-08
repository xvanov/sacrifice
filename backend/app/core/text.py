"""Small text helpers for previews (notifications, goal titles in lists)."""

ELLIPSIS = "…"


def truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to at most ``limit`` characters, ending with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[:limit] + ELLIPSIS
