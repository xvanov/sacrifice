import re
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings
from app.services.fault_attribution import Fault, classify_our_upstream_status
from app.services.verification_result import REASON_UPSTREAM_UNAVAILABLE as _UNAVAILABLE


class YouTubeUpstreamError(Exception):
    """The YouTube Data API could not answer us, for a reason that is ours.

    Deliberately NOT a ValueError: the call sites treat ValueError as "this video
    does not satisfy the goal", which charges the pledge. Our expired key is not
    the user failing their goal.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[\w-]{11}$")


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname in ("youtu.be", "www.youtu.be"):
        path = parsed.path.lstrip("/")
        if YOUTUBE_VIDEO_ID_PATTERN.match(path):
            return path
        return None
    if parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        qs = parse_qs(parsed.query)
        video_ids = qs.get("v", [])
        if video_ids and YOUTUBE_VIDEO_ID_PATTERN.match(video_ids[0]):
            return video_ids[0]
    return None


async def fetch_video_metadata(video_id: str) -> dict:
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,contentDetails",
        "id": video_id,
        "key": settings.youtube_api_key,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10)
    except httpx.HTTPError as exc:
        # No response at all from an upstream WE chose: ours outright.
        raise YouTubeUpstreamError(
            f"Could not reach the YouTube Data API: {exc}", reason=_UNAVAILABLE
        ) from exc

    if resp.status_code != 200:
        # The API key and the request quota are OURS. A 401/403/429 here means
        # our credential is missing, revoked or over quota — the user cannot fix
        # it and must not be billed for it. Left as a plain ValueError this
        # surfaced as a `failed` verdict, which charged every affected user's
        # pledge on our own misconfiguration.
        fault, reason = classify_our_upstream_status(resp.status_code)
        if fault is Fault.OURS:
            raise YouTubeUpstreamError(
                f"YouTube Data API unavailable to us (HTTP {resp.status_code})",
                reason=reason or _UNAVAILABLE,
            )
        raise ValueError(f"YouTube API error: {resp.status_code}")

    data = resp.json()
    items = data.get("items", [])
    if not items:
        raise ValueError("Video not found")

    item = items[0]
    snippet = item.get("snippet", {})
    content_details = item.get("contentDetails", {})

    duration_iso = content_details.get("duration", "PT0S")
    duration_seconds = _parse_iso_duration(duration_iso)

    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "duration_seconds": duration_seconds,
    }


def _parse_iso_duration(duration: str) -> int:
    import re as re_mod

    match = re_mod.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


async def fetch_video_transcript(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(item["text"] for item in transcript_list)
    except Exception as e:
        raise ValueError(f"Transcript not available: {e}")
