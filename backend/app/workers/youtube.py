import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.database import async_session
from app.services.llm import judge_transcript_content
from app.services.verification_result import persist_verification_result
from app.services.youtube import fetch_video_metadata, fetch_video_transcript


async def verify_youtube_content(
    proof_data: dict,
    criteria_data: dict,
) -> dict:
    video_id = proof_data.get("video_id", "")
    min_duration = criteria_data.get("min_duration_seconds", 0)
    video_description_goal = criteria_data.get("video_description", "")

    details = {}
    failed = False

    try:
        metadata = await fetch_video_metadata(video_id)
        duration_seconds = metadata["duration_seconds"]
        video_title = metadata["title"]
        details["duration_seconds"] = duration_seconds
        details["duration_passed"] = duration_seconds >= min_duration
        details["video_title"] = video_title
        if not details["duration_passed"]:
            details["duration_failure_reason"] = (
                f"Video duration {duration_seconds}s is less than minimum {min_duration}s"
            )
            failed = True
    except ValueError as e:
        details["metadata_error"] = str(e)
        failed = True

    if not failed:
        try:
            transcript = await fetch_video_transcript(video_id)
            details["transcript_found"] = True
            details["transcript_preview"] = transcript[:200]

            judgment = await judge_transcript_content(
                goal_description=video_description_goal,
                transcript=transcript,
                video_title=details.get("video_title", ""),
            )
            details["content_passed"] = judgment.get("authentic", False)
            details["llm_reasoning"] = judgment.get("reasoning", "")
            if not details["content_passed"]:
                failed = True
        except ValueError as e:
            details["transcript_error"] = str(e)
            details["content_passed"] = False
            failed = True

    status = "failed" if failed else "verified"
    return {
        "verification_status": status,
        "verification_details": details,
    }


async def _persist_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
):
    await persist_verification_result(db, goal_id, submission_id, status, details)


async def run_youtube_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    result = await verify_youtube_content(proof_data, criteria_data)

    if db is not None:
        await _persist_result(
            db,
            goal_id,
            submission_id,
            result["verification_status"],
            result["verification_details"],
        )
    else:
        async with async_session() as session:
            await _persist_result(
                session,
                goal_id,
                submission_id,
                result["verification_status"],
                result["verification_details"],
            )

    return result


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def run_youtube_verification_task(
    self,
    goal_id_str: str,
    submission_id_str: str,
    proof_data: dict,
    criteria_data: dict,
):
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_youtube_verification(
                goal_id=uuid.UUID(goal_id_str),
                submission_id=uuid.UUID(submission_id_str),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc) from exc
    finally:
        loop.close()
