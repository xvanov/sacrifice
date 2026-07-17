import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.crypto import decrypt_token
from app.database import async_session
from app.models.goal import Goal
from app.models.proof import ProofSubmission
from app.services.notification import notify_goal_resolution
from app.services.verification_result import persist_verification_result


GITHUB_API = "https://api.github.com"

OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")


def _parse_repo_url(url: str) -> tuple[str, str]:
    m = OWNER_REPO_RE.search(url)
    if not m:
        raise ValueError(f"Could not parse owner/repo from URL: {url}")
    return m.group(1), m.group(2)


def _parse_issue_url(url: str) -> tuple[str, str, int] | None:
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)/issues/(\d+)", url)
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


async def _github_get(
    url: str,
    token: str | None = None,
) -> dict | list:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            raise ValueError(f"GitHub resource not found: {url}")
        if resp.status_code == 403:
            raise ValueError(f"GitHub API rate limited or access denied: {url}")
        if resp.status_code != 200:
            raise ValueError(f"GitHub API error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


async def verify_github_repo(
    proof_data: dict,
    criteria_data: dict,
) -> dict:
    repo_url = proof_data.get("repo_url", criteria_data.get("repo_url", ""))
    branch = proof_data.get("branch", criteria_data.get("branch", "main"))
    raw_token = proof_data.get("github_token") or criteria_data.get("github_token")
    github_token = decrypt_token(raw_token) if raw_token else None
    conditions = criteria_data.get("conditions", [])

    owner, repo = _parse_repo_url(repo_url)

    details = {
        "repo_url": repo_url,
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "conditions": conditions,
    }
    failed = False
    condition_results = []

    for cond in conditions:
        cond_type = cond.get("type", "")
        result = {"type": cond_type, "passed": False}

        if cond_type == "commits":
            min_count = cond.get("min_count", 1)
            since_date = cond.get("since_date")
            try:
                params = {"sha": branch, "per_page": 1}
                if since_date:
                    params["since"] = since_date
                url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
                headers = {"Accept": "application/vnd.github.v3+json"}
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code != 200:
                        raise ValueError(f"GitHub API error: {resp.status_code}")
                    data = resp.json()
                    actual_count = len(data)
                    if actual_count == 100:
                        link_header = resp.headers.get("Link", "")
                        import math
                        last_page_match = re.search(r'page=(\d+)>; rel="last"', link_header)
                        if last_page_match:
                            estimated_count = int(last_page_match.group(1)) * 100
                            actual_count = estimated_count
                            pass

                result["actual"] = actual_count
                result["min_count"] = min_count
                result["since_date"] = since_date
                result["passed"] = actual_count >= min_count
                if not result["passed"]:
                    result["failure_reason"] = (
                        f"Found {actual_count} commits, need at least {min_count}"
                    )
                    failed = True
            except ValueError as e:
                result["error"] = str(e)
                failed = True

        elif cond_type == "lines_changed":
            min_count = cond.get("min_count", 1)
            since_date = cond.get("since_date")
            try:
                url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
                params = {"sha": branch, "per_page": 100}
                if since_date:
                    params["since"] = since_date
                headers = {"Accept": "application/vnd.github.v3+json"}
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"
                total_additions = 0
                total_deletions = 0
                async with httpx.AsyncClient(timeout=60.0) as client:
                    page = 1
                    while True:
                        p = dict(params)
                        p["page"] = page
                        resp = await client.get(url, params=p, headers=headers)
                        if resp.status_code != 200:
                            raise ValueError(f"GitHub API error: {resp.status_code}")
                        commits = resp.json()
                        if not commits:
                            break
                        for commit in commits:
                            detail_url = commit["url"]
                            detail_resp = await client.get(
                                detail_url, headers=headers
                            )
                            if detail_resp.status_code == 200:
                                detail = detail_resp.json()
                                stats = detail.get("stats", {})
                                total_additions += stats.get("additions", 0)
                                total_deletions += stats.get("deletions", 0)
                        if len(commits) < 100:
                            break
                        page += 1

                total_changed = total_additions + total_deletions
                result["actual"] = total_changed
                result["additions"] = total_additions
                result["deletions"] = total_deletions
                result["min_count"] = min_count
                result["since_date"] = since_date
                result["passed"] = total_changed >= min_count
                if not result["passed"]:
                    result["failure_reason"] = (
                        f"Changed {total_changed} lines, need at least {min_count}"
                    )
                    failed = True
            except ValueError as e:
                result["error"] = str(e)
                failed = True

        elif cond_type == "tickets_closed":
            tickets = cond.get("tickets", [])
            result["tickets"] = tickets
            result["closed"] = []
            result["open_or_not_found"] = []
            try:
                all_closed = True
                for ticket_url in tickets:
                    parsed = _parse_issue_url(ticket_url)
                    if not parsed:
                        result["open_or_not_found"].append(ticket_url)
                        result.setdefault("parse_errors", []).append(ticket_url)
                        all_closed = False
                        continue
                    t_owner, t_repo, issue_num = parsed
                    url = f"{GITHUB_API}/repos/{t_owner}/{t_repo}/issues/{issue_num}"
                    try:
                        data = await _github_get(url, github_token)
                        state = data.get("state", "unknown")
                        if state == "closed":
                            result["closed"].append(ticket_url)
                        else:
                            result["open_or_not_found"].append(ticket_url)
                            all_closed = False
                    except ValueError:
                        result["open_or_not_found"].append(ticket_url)
                        all_closed = False

                result["passed"] = all_closed
                if not all_closed:
                    result["failure_reason"] = (
                        f"Not all tickets are closed: {result['open_or_not_found']}"
                    )
                    failed = True
            except ValueError as e:
                result["error"] = str(e)
                failed = True

        condition_results.append(result)

    details["condition_results"] = condition_results
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


async def run_github_repo_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    result = await verify_github_repo(proof_data, criteria_data)

    if db is not None:
        await _persist_result(
            db, goal_id, submission_id,
            result["verification_status"], result["verification_details"],
        )
    else:
        async with async_session() as session:
            await _persist_result(
                session, goal_id, submission_id,
                result["verification_status"], result["verification_details"],
            )

    return result


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def run_github_repo_verification_task(
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
            run_github_repo_verification(
                goal_id=uuid.UUID(goal_id_str),
                submission_id=uuid.UUID(submission_id_str),
                proof_data=proof_data,
                criteria_data=criteria_data,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        loop.close()
