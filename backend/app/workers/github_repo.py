"""Verification worker for the ``github_repo`` goal type.

Two criteria shapes are supported and both are honoured in a single run:

* **Declarative** (``goal_types/github_repo/definition.py``, what the chat flow
  collects): ``repo_owner``, ``repo_name``, ``branch``, ``min_commits``,
  ``required_files``, ``require_pr``.
* **Legacy** ``conditions`` list: ``commits``, ``lines_changed``,
  ``tickets_closed`` — the shape stored on older goals.

The single most important invariant: **a configuration that expresses no
check we can actually run never returns ``verified``.** "We checked nothing" is
not evidence that the goal was met.

But the mirror-image invariant matters just as much, because a ``failed``
verification **charges the user's card** (``services/verification_result.py``
dispatches the pledge charge on ``failed``, and ``failed`` is terminal — proof
submission requires an ``active`` goal). So this module distinguishes three
outcomes, not two:

``verified``
    Every configured check passed.
``failed``
    A check the **user controls** came back negative: too few commits, a
    missing file on a branch that exists, no pull request, an open ticket, a
    repo or branch that is absent or private, a proof pointing at a different
    repo than the goal named. The pledge is charged.
``inconclusive``
    We could not look, or we did not know what to look for: 429/5xx, a network
    error or timeout, a 403 rate limit, or criteria the user did not author and
    cannot fix (no verifiable criteria, an unsupported condition type, a
    non-numeric threshold). **Never charges.**

The boundary is deliberately drawn at *who controls the input*: a user must not
be able to dodge a pledge by making verification error out, so anything they
can manufacture — deleting the repo, flipping it private, never creating the
branch — stays ``failed``. And when checks disagree, a confirmed failure wins
over an inconclusive one: criteria are conjunctive, so "2 of the 5 commits" is
terminal on its own even if a sibling check was rate-limited. Only when there
is no confirmed failure does an inconclusive result suppress the charge.

``inconclusive`` is persisted through the shared contract in
``services/verification_result.py``: every check that cannot answer carries an
``inconclusive_reason`` from that module's closed allowlist, and
``verification_outcome`` folds them into the single reason the run reports. The
reason is not decoration — it decides whether the outcome is retried (the
reconciler re-dispatches transient ones) or escalated to an operator
immediately (a criteria set we can never evaluate).
"""

import asyncio
import logging
import re
import uuid
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.crypto import decrypt_token
from app.database import async_session
from app.services.verification_result import (
    FAILED,
    INCONCLUSIVE,
    REASON_CRITERIA_NOT_EVALUABLE,
    REASON_INTERNAL_ERROR,
    REASON_UPSTREAM_RATE_LIMITED,
    REASON_UPSTREAM_UNAVAILABLE,
    VERIFIED,
    persist_verification_result,
)


logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Repo names legitimately contain dots (``three.js``, ``user.github.io``,
# ``dotfiles.old``). The old ``[^/.]+`` truncated at the first dot, which — once
# the proof/criteria mismatch guard started comparing the two — turned every
# dotted repo into a false "wrong repository" failure *and a charge*. Stop at a
# path separator or a URL suffix instead, then strip a trailing ``.git``.
OWNER_REPO_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s#?]+)")
_ISSUE_URL_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s#?]+)/issues/(\d+)")

# ``Link: <...page=7>; rel="last"`` — with ``per_page=1`` the last page number
# is exactly the total item count.
_LINK_LAST_RE = re.compile(r'[?&]page=(\d+)[^>]*>\s*;\s*rel="last"')
_LINK_NEXT_RE = re.compile(r'>\s*;\s*rel="next"')

_HTTP_TIMEOUT = 60.0
_PAGE_SIZE = 100
# The proof schema defaults ``branch`` to this, so receiving it proves nothing
# about what the user intended. See ``_resolve_branch``.
_AMBIGUOUS_DEFAULT_BRANCH = "main"
# Hard cap on any pagination walk so a huge repo cannot pin the worker.
_MAX_PAGES = 100


# ``VERIFIED``/``FAILED``/``INCONCLUSIVE`` are re-exported from
# ``services/verification_result`` rather than redefined here: they are the
# values that module branches on to decide whether to charge, and a local copy
# is exactly the kind of literal that drifts.

# Statuses that mean "ask again later", never "the user did not do the work".
_TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Which inconclusive reason a run reports when its checks disagree. Order is
# meaningful, most-significant first:
#
# * ``CRITERIA_NOT_EVALUABLE`` is permanent — the contract saturates the attempt
#   counter for it, so reporting it first sends the row to an operator now
#   instead of after four staleness windows that cannot change the answer.
# * ``INTERNAL_ERROR`` is a bug in our code and the least expected, so it should
#   not be masked by an upstream fault sitting next to it.
# * The two upstream reasons are equivalent for retry purposes; the rate-limit
#   signal is the more specific diagnosis, so it wins.
_REASON_PRECEDENCE = (
    REASON_CRITERIA_NOT_EVALUABLE,
    REASON_INTERNAL_ERROR,
    REASON_UPSTREAM_RATE_LIMITED,
    REASON_UPSTREAM_UNAVAILABLE,
)

# Body/header signals that a 403 is GitHub throttling us rather than denying
# access. A bare 403 must not map to ``UPSTREAM_RATE_LIMITED`` — the contract
# calls that out by name, because GitHub uses 403 for both.
_RATE_LIMIT_BODY_MARKERS = (
    "rate limit",
    "abuse detection",
    "secondary rate limit",
)

NO_REPO_INCONCLUSIVE_DETAIL = (
    "No GitHub repository was identified for this goal: supply repo_url in the "
    "proof, or repo_owner and repo_name in the criteria."
)
NO_CRITERIA_INCONCLUSIVE_DETAIL = (
    "No verifiable criteria were configured for this goal, so nothing could be "
    "checked. Configure at least one of min_commits, required_files, require_pr, "
    "or a conditions entry."
)
UNPARSEABLE_REPO_INCONCLUSIVE_DETAIL = (
    "The repository reference for this goal could not be read as a GitHub "
    "repository, so nothing could be checked."
)
REPO_MISMATCH_FAILURE_REASON = (
    "The repository in the submitted proof is not the repository named in the "
    "goal criteria."
)


class GithubApiError(ValueError):
    """A GitHub call that did not answer the question.

    Subclasses ``ValueError`` so the pre-existing ``except ValueError`` call
    sites keep working. ``transient`` decides charge-safety: transient errors
    are ours (rate limit, outage, network) and must never charge, while a
    definitive negative answer about a user-controlled resource must.

    ``reason`` carries the ``INCONCLUSIVE_REASONS`` code to persist, and is set
    exactly when ``transient`` is true — the two are decided together at the
    point where the status code is known, so they cannot disagree later.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        transient: bool = False,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.reason = reason


def _strip_git_suffix(name: str) -> str:
    return name[:-4] if name.endswith(".git") else name


def _parse_repo_url(url: str) -> tuple[str, str]:
    m = OWNER_REPO_RE.search(url)
    if not m:
        raise ValueError(f"Could not parse owner/repo from URL: {url}")
    return m.group(1), _strip_git_suffix(m.group(2))


def _parse_issue_url(url: str) -> tuple[str, str, int] | None:
    m = _ISSUE_URL_RE.search(url)
    if not m:
        return None
    return m.group(1), _strip_git_suffix(m.group(2)), int(m.group(3))


def _owner_repo_from(data: dict, prefer_fields: bool = False) -> tuple[str, str] | None:
    """Resolve ``(owner, repo)`` from either shape, or ``None`` if absent.

    ``prefer_fields`` flips the precedence to ``repo_owner``/``repo_name``
    first. Criteria are read that way because ``submit_proof`` copies the
    user-supplied ``repo_url`` into the criteria dict; the declared
    owner/name fields are what the goal actually committed to, so they must
    win when the two disagree.
    """
    url = data.get("repo_url")
    owner = data.get("repo_owner")
    name = data.get("repo_name")

    if prefer_fields and owner and name:
        return str(owner), str(name)
    if url:
        return _parse_repo_url(url)
    if owner and name:
        return str(owner), str(name)
    return None


def _headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _link_header(resp) -> str:
    link = resp.headers.get("Link") or resp.headers.get("link") or ""
    return link if isinstance(link, str) else ""


def _body_text(resp) -> str:
    text = getattr(resp, "text", "")
    return text if isinstance(text, str) else ""


def _header(resp, name: str) -> str:
    """Case-insensitive header read that also works on a plain-dict mock."""
    headers = getattr(resp, "headers", None) or {}
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    value = getter(name)
    if value is None and isinstance(headers, dict):
        lowered = name.lower()
        for key, candidate in headers.items():
            if isinstance(key, str) and key.lower() == lowered:
                value = candidate
                break
    return str(value) if value is not None else ""


def _is_rate_limited(resp) -> bool:
    """Is this response GitHub throttling us, as opposed to denying access?

    GitHub answers 403 for both, so the reason code cannot be read off the
    status alone. Two documented signals settle it: the exhausted-quota header,
    and the wording GitHub uses in the body for primary, secondary and abuse
    limits.
    """
    if _header(resp, "x-ratelimit-remaining").strip() == "0":
        return True
    body = _body_text(resp).lower()
    return any(marker in body for marker in _RATE_LIMIT_BODY_MARKERS)


def _is_ref_not_found(resp) -> bool:
    """Distinguish "that branch does not exist" from "that path does not exist".

    The contents API answers 404 for both, so reading a 404 as "file missing"
    told users their files were gone when the *branch* was what we could not
    find. GitHub's body disambiguates it for free.
    """
    body = _body_text(resp).lower()
    return "no commit found for the ref" in body or "invalid ref" in body


def _raise_for_status(resp, url: str) -> None:
    """Turn a non-200 GitHub response into a classified ``GithubApiError``.

    Never interpolates the token — only the URL, status and a body snippet.
    """
    status = resp.status_code
    if status == 200:
        return
    if status == 404:
        # Definitive: the resource is absent (or private to us). User-controlled.
        raise GithubApiError(
            f"GitHub API error 404: resource not found: {url}",
            status=404,
            transient=False,
        )
    if status == 403:
        # GitHub returns 403 for rate limiting and for blocked access; a private
        # repo read is a 404, not a 403. Either way this is our side of the
        # fence, not evidence about the user's work, so it must not charge — but
        # the two get different reason codes, because the contract forbids
        # calling every 403 a rate limit.
        rate_limited = _is_rate_limited(resp)
        raise GithubApiError(
            f"GitHub API error 403: rate limited or access denied: {url}",
            status=403,
            transient=True,
            reason=(
                REASON_UPSTREAM_RATE_LIMITED
                if rate_limited
                else REASON_UPSTREAM_UNAVAILABLE
            ),
        )
    transient = status in _TRANSIENT_STATUS_CODES
    snippet = _body_text(resp)[:200]
    raise GithubApiError(
        f"GitHub API error {status}: {snippet}",
        status=status,
        transient=transient,
        reason=(
            (
                REASON_UPSTREAM_RATE_LIMITED
                if status == 429 or _is_rate_limited(resp)
                else REASON_UPSTREAM_UNAVAILABLE
            )
            if transient
            else None
        ),
    )


async def _github_get(
    client,
    url: str,
    token: str | None = None,
    params: dict | None = None,
):
    resp = await client.get(url, params=params, headers=_headers(token))
    _raise_for_status(resp, url)
    return resp.json()


# ─── Individual checks ─────────────────────────────────────────────


def _branch_label(branch: str | None) -> str:
    """How to name the branch under test in a user-facing message."""
    return branch if branch else "the default branch"


async def _count_commits(
    client,
    owner: str,
    repo: str,
    branch: str | None,
    token: str | None,
    since: str | None = None,
) -> int:
    """Return the true number of commits on ``branch``.

    Asks for a single commit per page so the ``Link: rel="last"`` page number
    *is* the commit count — one request, exact for 0, 1, several and >100.
    Falls back to walking pages if GitHub advertises a next page without a
    last page.

    ``branch=None`` omits ``sha`` so GitHub resolves the repo's own default
    branch. That is what makes a ``master``-default repo work: hardcoding
    ``"main"`` produced a 404 and charged the user for our wrong guess.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    params: dict = {"per_page": 1}
    if branch:
        params["sha"] = branch
    if since:
        params["since"] = since

    resp = await client.get(url, params=params, headers=_headers(token))
    if resp.status_code == 409:
        # GitHub reports an empty repository as 409 Conflict.
        return 0
    _raise_for_status(resp, url)

    data = resp.json()
    if not isinstance(data, list) or not data:
        return 0

    link = _link_header(resp)
    last = _LINK_LAST_RE.search(link)
    if last:
        return int(last.group(1))
    if _LINK_NEXT_RE.search(link):
        return await _walk_commit_count(client, url, params, token)
    # Single page of one item → exactly one commit.
    return len(data)


async def _walk_commit_count(
    client,
    url: str,
    base_params: dict,
    token: str | None,
) -> int:
    total = 0
    for page in range(1, _MAX_PAGES + 1):
        params = dict(base_params)
        params["per_page"] = _PAGE_SIZE
        params["page"] = page
        resp = await client.get(url, params=params, headers=_headers(token))
        _raise_for_status(resp, url)
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        total += len(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return total


async def _check_commit_count(
    client,
    spec: dict,
    owner: str,
    repo: str,
    branch: str | None,
    token: str | None,
) -> dict:
    min_count = spec["min_count"]
    since_date = spec.get("since_date")
    actual = await _count_commits(client, owner, repo, branch, token, since_date)
    result = {
        "type": spec["type"],
        "passed": actual >= min_count,
        "actual": actual,
        "min_count": min_count,
        "since_date": since_date,
    }
    if not result["passed"]:
        result["failure_reason"] = (
            f"Found {actual} commits on {_branch_label(branch)}, "
            f"need at least {min_count}"
        )
    return result


async def _check_lines_changed(
    client,
    spec: dict,
    owner: str,
    repo: str,
    branch: str | None,
    token: str | None,
) -> dict:
    min_count = spec["min_count"]
    since_date = spec.get("since_date")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    base_params: dict = {"per_page": _PAGE_SIZE}
    if branch:
        base_params["sha"] = branch
    if since_date:
        base_params["since"] = since_date

    additions = 0
    deletions = 0
    for page in range(1, _MAX_PAGES + 1):
        params = dict(base_params)
        params["page"] = page
        resp = await client.get(url, params=params, headers=_headers(token))
        _raise_for_status(resp, url)
        commits = resp.json()
        if not isinstance(commits, list) or not commits:
            break
        for commit in commits:
            detail_url = commit["url"]
            detail_resp = await client.get(detail_url, headers=_headers(token))
            # A detail fetch we could not read used to be skipped silently,
            # which undercounts and reports a confident wrong total.
            _raise_for_status(detail_resp, detail_url)
            stats = (detail_resp.json() or {}).get("stats", {})
            additions += stats.get("additions", 0)
            deletions += stats.get("deletions", 0)
        if len(commits) < _PAGE_SIZE:
            break

    total_changed = additions + deletions
    result = {
        "type": spec["type"],
        "passed": total_changed >= min_count,
        "actual": total_changed,
        "additions": additions,
        "deletions": deletions,
        "min_count": min_count,
        "since_date": since_date,
    }
    if not result["passed"]:
        result["failure_reason"] = (
            f"Changed {total_changed} lines, need at least {min_count}"
        )
    return result


async def _check_tickets_closed(
    client,
    spec: dict,
    owner: str,
    repo: str,
    branch: str,
    token: str | None,
) -> dict:
    tickets = spec["tickets"]
    result: dict = {
        "type": spec["type"],
        "passed": False,
        "tickets": tickets,
        "closed": [],
        "open_or_not_found": [],
    }
    # Tracked per ticket, not per check: a list of tickets is itself conjunctive,
    # so one ticket we confirmed is open makes the whole check a real failure
    # even if a second ticket was rate-limited. Marking the check inconclusive
    # whenever *any* ticket errored would let one 403 launder a confirmed miss
    # into a free pass.
    confirmed_failure = False
    reasons: set[str] = set()

    for ticket_url in tickets:
        parsed = _parse_issue_url(ticket_url)
        if not parsed:
            # An unparseable ticket URL is in criteria the user did not author
            # and cannot edit, so it is ours, not a missed goal.
            result["open_or_not_found"].append(ticket_url)
            result.setdefault("parse_errors", []).append(ticket_url)
            reasons.add(REASON_CRITERIA_NOT_EVALUABLE)
            continue
        t_owner, t_repo, issue_num = parsed
        url = f"{GITHUB_API}/repos/{t_owner}/{t_repo}/issues/{issue_num}"
        try:
            data = await _github_get(client, url, token)
        except (ValueError, httpx.HTTPError) as exc:
            # Still counts as "not closed" (fail closed), but record *why* so a
            # rate limit is not silently reported as an open ticket — and, if we
            # never got an answer, do not bill the pledge over it.
            result["open_or_not_found"].append(ticket_url)
            result.setdefault("errors", {})[ticket_url] = _redact(str(exc), token)
            reason = _inconclusive_reason(exc)
            if reason is None:
                # A 404 on an issue: deleted, or in a repo made private. The
                # user controls both.
                confirmed_failure = True
            else:
                reasons.add(reason)
            continue
        if (data or {}).get("state") == "closed":
            result["closed"].append(ticket_url)
        else:
            result["open_or_not_found"].append(ticket_url)
            confirmed_failure = True

    result["passed"] = not result["open_or_not_found"]
    if not result["passed"]:
        if confirmed_failure:
            result["failure_reason"] = (
                f"Not all tickets are closed: {result['open_or_not_found']}"
            )
        else:
            result["inconclusive"] = True
            result["inconclusive_reason"] = _worst_reason(reasons)
            result["failure_reason"] = (
                f"Ticket state could not be read: {result['open_or_not_found']}"
            )
    return result


async def _check_required_files(
    client,
    spec: dict,
    owner: str,
    repo: str,
    branch: str,
    token: str | None,
) -> dict:
    paths = spec["required_files"]
    result: dict = {
        "type": "required_files",
        "passed": False,
        "required_files": list(paths),
        "branch": branch,
        "present": [],
        "missing": [],
    }
    for path in paths:
        clean = str(path).lstrip("/")
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{quote(clean, safe='/')}"
        params = {"ref": branch} if branch else None
        resp = await client.get(url, params=params, headers=_headers(token))
        if resp.status_code == 200:
            result["present"].append(path)
            continue
        if resp.status_code == 404:
            if _is_ref_not_found(resp):
                # The branch is missing, not the file. Reporting "your files are
                # missing" here would be a lie, and the accurate answer is a
                # different (still user-controlled) failure.
                result["ref_missing"] = True
                result["passed"] = False
                result["failure_reason"] = (
                    f"Branch {_branch_label(branch)} does not exist in "
                    f"{owner}/{repo}, so its files could not be checked"
                )
                return result
            result["missing"].append(path)
            continue
        # 403 / 5xx / anything else is an inconclusive answer, not an absence.
        _raise_for_status(resp, url)

    result["passed"] = not result["missing"]
    if not result["passed"]:
        result["failure_reason"] = (
            f"Missing required file(s) on {_branch_label(branch)}: {result['missing']}"
        )
    return result


async def _check_require_pr(
    client,
    spec: dict,
    owner: str,
    repo: str,
    branch: str,
    token: str | None,
) -> dict:
    # Deliberately NOT the ``head=owner:branch`` filter. That searches for a PR
    # whose *head* is the branch, so with the branch left unspecified it looked
    # for a PR from the default branch — which never exists — and it silently
    # excludes PRs opened from forks, where head owner != repo owner. List and
    # match locally on ``head.ref`` instead.
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    params = {"state": "all", "per_page": _PAGE_SIZE}
    resp = await client.get(url, params=params, headers=_headers(token))
    _raise_for_status(resp, url)
    payload = resp.json()
    pulls = payload if isinstance(payload, list) else []

    summary = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        head_ref = head.get("ref")
        if branch and head_ref != branch:
            continue
        summary.append(
            {
                "number": pr.get("number"),
                "state": pr.get("state"),
                "merged": bool(pr.get("merged_at")),
                "head_ref": head_ref,
                "head_label": head.get("label"),
            }
        )

    open_prs = [pr for pr in summary if pr["state"] == "open"]
    merged_prs = [pr for pr in summary if pr["merged"]]

    result: dict = {
        "type": "require_pr",
        "passed": bool(open_prs or merged_prs),
        "branch": branch,
        "pull_requests": summary,
        "open": [pr["number"] for pr in open_prs],
        "merged": [pr["number"] for pr in merged_prs],
    }
    if not result["passed"]:
        scope = f"branch {branch}" if branch else "this repository"
        if summary:
            result["failure_reason"] = (
                f"No open or merged pull request for {scope}; found only "
                f"closed, unmerged pull request(s): {[pr['number'] for pr in summary]}"
            )
        else:
            result["failure_reason"] = f"No pull request found for {scope}"
    return result


_CHECK_HANDLERS = {
    "min_commits": _check_commit_count,
    "commits": _check_commit_count,
    "lines_changed": _check_lines_changed,
    "tickets_closed": _check_tickets_closed,
    "required_files": _check_required_files,
    "require_pr": _check_require_pr,
}


# ─── Criteria planning ─────────────────────────────────────────────


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_FALSY_STRINGS = frozenset({"", "false", "0", "no", "off", "none", "null"})


def _as_bool(value) -> bool:
    """Truthiness for criteria that survived a JSON round-trip.

    Plain ``bool(value)`` makes the string ``"false"`` true, which silently
    switched on a check the goal had turned off.
    """
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_STRINGS
    return bool(value)


def _plan_checks(criteria_data: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Split declared criteria into (runnable, inert, unverifiable).

    * **runnable** — specs a handler can execute and that can actually fail.
    * **inert** — declared but degenerate (``min_commits: 0``,
      ``required_files: []``, ``require_pr: false``, ``tickets_closed: []``).
      Recorded for transparency; they neither pass nor fail, they just do not
      count as evidence.
    * **unverifiable** — promised something we cannot check (unsupported
      condition type, non-numeric threshold). Never the user's fault, so these
      route to ``inconclusive`` and do not charge.

    Every unverifiable entry is built by ``_unverifiable`` so it cannot be
    created without its ``inconclusive`` marker. Only the unsupported-condition
    branch used to carry one, which meant a malformed threshold *next to* a
    runnable check (``{"min_commits": "lots", "required_files": ["README.md"]}``)
    folded into a chargeable failure — the no-runnable-checks path that made the
    other cases safe was never reached.
    """
    runnable: list[dict] = []
    inert: list[dict] = []
    unverifiable: list[dict] = []

    def _unverifiable(check_type: str, failure_reason: str) -> dict:
        return {
            "type": check_type,
            "passed": False,
            # Not chargeable: the user did not author these criteria and cannot
            # edit them, so they must not pay for our inability to check them.
            "inconclusive": True,
            "inconclusive_reason": REASON_CRITERIA_NOT_EVALUABLE,
            "failure_reason": failure_reason,
        }

    # ── declarative fields ──
    if criteria_data.get("min_commits") is not None:
        raw = criteria_data["min_commits"]
        n = _as_int(raw)
        if n is None:
            unverifiable.append(
                _unverifiable("min_commits", f"min_commits is not a number: {raw!r}")
            )
        elif n > 0:
            runnable.append({"type": "min_commits", "min_count": n})
        else:
            inert.append(
                {
                    "type": "min_commits",
                    "skipped": True,
                    "min_count": n,
                    "note": "min_commits must be >= 1 to verify anything",
                }
            )

    required_files = criteria_data.get("required_files")
    if required_files is not None:
        if isinstance(required_files, (list, tuple)):
            paths = [p for p in required_files if str(p).strip()]
            if paths:
                runnable.append({"type": "required_files", "required_files": paths})
            else:
                inert.append(
                    {
                        "type": "required_files",
                        "skipped": True,
                        "note": "required_files is empty, so no file was checked",
                    }
                )
        else:
            unverifiable.append(
                _unverifiable(
                    "required_files",
                    f"required_files must be a list of paths, got "
                    f"{type(required_files).__name__}",
                )
            )

    if criteria_data.get("require_pr") is not None:
        if _as_bool(criteria_data["require_pr"]):
            runnable.append({"type": "require_pr"})
        else:
            inert.append(
                {
                    "type": "require_pr",
                    "skipped": True,
                    "note": "require_pr is off, so no pull request was checked",
                }
            )

    # ── legacy conditions list ──
    conditions = criteria_data.get("conditions") or []
    for cond in conditions:
        if not isinstance(cond, dict):
            unverifiable.append(
                _unverifiable("unknown", f"Malformed condition entry: {cond!r}")
            )
            continue

        cond_type = cond.get("type", "")

        if cond_type in ("commits", "lines_changed"):
            n = _as_int(cond.get("min_count", 1))
            if n is None:
                unverifiable.append(
                    _unverifiable(
                        cond_type,
                        f"min_count is not a number: {cond.get('min_count')!r}",
                    )
                )
            elif n > 0:
                runnable.append(
                    {
                        "type": cond_type,
                        "min_count": n,
                        "since_date": cond.get("since_date"),
                    }
                )
            else:
                inert.append(
                    {
                        "type": cond_type,
                        "skipped": True,
                        "min_count": n,
                        "note": "min_count must be >= 1 to verify anything",
                    }
                )

        elif cond_type == "tickets_closed":
            tickets = [t for t in (cond.get("tickets") or []) if str(t).strip()]
            if tickets:
                runnable.append({"type": cond_type, "tickets": tickets})
            else:
                inert.append(
                    {
                        "type": cond_type,
                        "skipped": True,
                        "note": "no tickets listed, so no ticket was checked",
                    }
                )

        else:
            unverifiable.append(
                _unverifiable(
                    cond_type or "unknown",
                    f"Unsupported condition type {cond_type!r}; this goal cannot "
                    "be verified automatically",
                )
            )

    return runnable, inert, unverifiable


def _result(status: str, details: dict, reason: str | None = None) -> dict:
    """Build the worker's return value.

    ``inconclusive_reason`` is a sibling of ``verification_status``, deliberately
    NOT a key inside ``details``: ``details`` is echoed back to the user through
    the verification-status endpoint and the contract treats it as untrusted for
    charge decisions, so the reason travels on the outside where the persistence
    call reads it.
    """
    return {
        "verification_status": status,
        "verification_details": details,
        "inconclusive_reason": reason if status == INCONCLUSIVE else None,
    }


def _redact(text: str, token: str | None) -> str:
    """Belt-and-braces: never let a PAT ride along inside an error string."""
    if token and token in text:
        return text.replace(token, "[redacted]")
    return text


def _inconclusive_reason(exc: Exception) -> str | None:
    """The reason code for an exception, or ``None`` if the user is at fault.

    ``None`` is the only value that leads to a charge, so every branch that
    returns it has to be a definitive negative answer about something the user
    controls. Everything else is ours and fails safe.
    """
    if isinstance(exc, GithubApiError):
        # ``reason`` is set with ``transient`` at the point the status is known.
        return exc.reason if exc.transient else None
    if isinstance(exc, (httpx.HTTPError, asyncio.TimeoutError, OSError)):
        # Network-level trouble (connect/read/timeout/protocol) is ours, as is
        # an asyncio timeout and a socket error.
        return REASON_UPSTREAM_UNAVAILABLE
    # An unexpected exception is a bug in this module — a response shape we did
    # not anticipate, a key that was not there. Not evidence about the user's
    # work, so it fails safe rather than charging.
    return REASON_INTERNAL_ERROR


def _worst_reason(reasons) -> str | None:
    """Pick the reason a run reports, per ``_REASON_PRECEDENCE``."""
    for candidate in _REASON_PRECEDENCE:
        if candidate in reasons:
            return candidate
    # Defensive: an unrecognised code would be rejected by the contract's
    # allowlist, and guessing a specific upstream fault we did not observe would
    # be a lie. Report it as ours.
    return REASON_INTERNAL_ERROR if reasons else None


async def _execute(
    client,
    spec: dict,
    owner: str,
    repo: str,
    branch: str | None,
    token: str | None,
) -> dict:
    handler = _CHECK_HANDLERS[spec["type"]]
    try:
        return await handler(client, spec, owner, repo, branch, token)
    except Exception as exc:  # noqa: BLE001 - a check must never escape as a crash
        message = str(exc) or type(exc).__name__
        error = (
            message
            if isinstance(exc, ValueError)
            else (f"{type(exc).__name__}: {message}")
        )
        error = _redact(error, token)
        reason = _inconclusive_reason(exc)
        result = {
            "type": spec["type"],
            "passed": False,
            "error": error,
            "failure_reason": (
                f"The {spec['type']} check could not be completed: {error}"
            ),
        }
        if reason is not None:
            # Charge-safety: a rate limit or an outage must not be billed as a
            # missed goal, and must stay retryable instead of going terminal.
            result["inconclusive"] = True
            result["inconclusive_reason"] = reason
        return result


def _resolve_branch(proof_data: dict, criteria_data: dict) -> tuple[str | None, dict]:
    """Decide which branch the checks run against, and explain the choice.

    The criteria branch **wins** when present. ``submit_proof`` used to copy the
    proof's ``branch`` over the criteria value, and the proof form lets the user
    edit it — so reading the proof first let anyone retarget verification at a
    branch full of pre-existing history and pass a goal about ``feature/x``
    without touching ``feature/x``.

    Returns ``(branch, note)`` where ``branch is None`` means "whatever GitHub
    considers this repo's default branch" — expressed by omitting ``sha``/``ref``
    rather than guessing ``"main"``, which 404s on ``master`` repositories.
    """
    criteria_branch = criteria_data.get("branch")
    proof_branch = proof_data.get("branch")
    note: dict = {}

    if criteria_branch:
        branch = str(criteria_branch)
        if proof_branch and str(proof_branch) != branch:
            # Recorded, not honoured: the goal decides what counts as proof.
            note["submitted_branch_ignored"] = str(proof_branch)
        return branch, note

    # ``GithubRepoProofSubmission.branch`` defaults to ``"main"``
    # (app/schemas/proof.py:41), so a proof that says ``main`` is
    # indistinguishable from a proof that said nothing. Treat it as unspecified
    # and let GitHub name the default branch, because guessing ``"main"`` on a
    # ``master`` repository 404s — and a 404 charges the user.
    if proof_branch and str(proof_branch) != _AMBIGUOUS_DEFAULT_BRANCH:
        return str(proof_branch), note

    note["branch_resolution"] = "repository default branch (none specified)"
    return None, note


def verification_outcome(condition_results: list[dict]) -> tuple[str, str | None]:
    """Fold per-check results into an outcome and its inconclusive reason.

    A confirmed, user-controlled failure outranks an inconclusive one, so a
    rate limit on one check cannot launder a definite miss on another. The
    reason is returned together with the status, rather than derived separately
    afterwards, because the two must not be able to disagree: a ``FAILED``
    outcome always carries ``None``, which is what the contract requires.
    """
    confirmed_failure = False
    reasons: set[str] = set()
    for result in condition_results:
        if result.get("skipped"):
            continue
        if result.get("passed"):
            continue
        if result.get("inconclusive"):
            reason = result.get("inconclusive_reason")
            # A check marked inconclusive without a reason cannot be persisted
            # (the contract's allowlist rejects ``None``). Treating it as ours
            # keeps the no-charge guarantee; the alternative — falling through
            # to ``failed`` — would charge on a bookkeeping slip.
            reasons.add(reason if reason else REASON_INTERNAL_ERROR)
        else:
            confirmed_failure = True

    if confirmed_failure:
        return FAILED, None
    if reasons:
        return INCONCLUSIVE, _worst_reason(reasons)
    return VERIFIED, None


async def verify_github_repo(
    proof_data: dict,
    criteria_data: dict,
) -> dict:
    proof_data = proof_data or {}
    criteria_data = criteria_data or {}

    branch, branch_note = _resolve_branch(proof_data, criteria_data)
    raw_token = proof_data.get("github_token") or criteria_data.get("github_token")
    github_token = decrypt_token(raw_token) if raw_token else None
    conditions = criteria_data.get("conditions") or []

    try:
        proof_target = _owner_repo_from(proof_data)
        criteria_target = _owner_repo_from(criteria_data, prefer_fields=True)
    except ValueError as exc:
        # ``GithubRepoProofSubmission.repo_url`` is a bare ``str`` with no URL
        # validation (app/schemas/proof.py:40), so an unparseable value reaches
        # this far. It used to escape as a ValueError, which the Celery task
        # retried three times and then dropped — leaving the submission
        # ``pending`` forever, where the deadline sweep charges the pledge
        # anyway. We accepted the input, so this is ours: persisting it as
        # inconclusive makes ``goal_verification_is_blocked`` true for the goal
        # and puts the row in front of an operator.
        return _result(
            INCONCLUSIVE,
            {
                "repo_url": "",
                "owner": None,
                "repo": None,
                "branch": branch,
                "conditions": conditions,
                "condition_results": [],
                "inconclusive_detail": UNPARSEABLE_REPO_INCONCLUSIVE_DETAIL,
                "parse_error": _redact(str(exc), github_token),
                **branch_note,
            },
            REASON_CRITERIA_NOT_EVALUABLE,
        )
    # Criteria first for the same reason the criteria branch wins: the goal
    # decides what counts as proof. Where both resolve they must agree (the
    # mismatch guard below enforces it), so this only settles whose spelling of
    # a case-insensitive owner/repo is recorded and called.
    target = criteria_target or proof_target

    details: dict = {
        # Canonical, never the raw submitted string: a URL like
        # ``https://user:ghp_x@github.com/o/r`` would otherwise be persisted
        # verbatim and served by the verification-status endpoint.
        "repo_url": (f"https://github.com/{target[0]}/{target[1]}" if target else ""),
        "owner": target[0] if target else None,
        "repo": target[1] if target else None,
        "branch": branch,
        "conditions": conditions,
        "condition_results": [],
        **branch_note,
    }

    if target is None:
        # Our criteria are unusable, not the user's doing → no charge. The text
        # goes in ``inconclusive_detail``: a top-level ``failure_reason`` is how
        # every verifier says "here is why YOU failed", and the contract raises
        # rather than persist that alongside an inconclusive outcome.
        details["inconclusive_detail"] = NO_REPO_INCONCLUSIVE_DETAIL
        return _result(INCONCLUSIVE, details, REASON_CRITERIA_NOT_EVALUABLE)

    if (
        proof_target
        and criteria_target
        and tuple(s.lower() for s in proof_target)
        != tuple(s.lower() for s in criteria_target)
    ):
        details["expected_repo"] = f"{criteria_target[0]}/{criteria_target[1]}"
        details["submitted_repo"] = f"{proof_target[0]}/{proof_target[1]}"
        details["failure_reason"] = REPO_MISMATCH_FAILURE_REASON
        return _result(FAILED, details)

    owner, repo = target
    runnable, inert, unverifiable = _plan_checks(criteria_data)

    if not runnable:
        # Nothing could be checked. Not ``verified`` — checking nothing is not
        # evidence — but not ``failed`` either: the chat flow is still capable of
        # producing criteria like ``{repo_owner, repo_name}`` with nothing
        # checkable in them, and legacy goals carry condition types we dropped.
        # Charging a user for a goal we never gave them a way to pass would be
        # worse than the vacuous pass this replaced.
        details["condition_results"] = inert + unverifiable
        reasons = [r["failure_reason"] for r in unverifiable]
        details["inconclusive_detail"] = (
            "; ".join(reasons) if reasons else NO_CRITERIA_INCONCLUSIVE_DETAIL
        )
        return _result(INCONCLUSIVE, details, REASON_CRITERIA_NOT_EVALUABLE)

    executed: list[dict] = []
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        for spec in runnable:
            executed.append(
                await _execute(client, spec, owner, repo, branch, github_token)
            )

    all_results = executed + inert + unverifiable
    details["condition_results"] = all_results
    status, reason = verification_outcome(all_results)

    # Split the explanation by who it is about. ``failure_reason`` is the
    # user-facing "why you failed" and must quote only checks that actually
    # measured them; a check we could not run belongs in the other bucket even
    # on a failed run, or the notification would tell someone they missed a
    # criterion that was never evaluated.
    negative = [
        r
        for r in executed + unverifiable
        if not r.get("skipped") and not r.get("passed") and r.get("failure_reason")
    ]
    confirmed = [r["failure_reason"] for r in negative if not r.get("inconclusive")]
    unresolved = [r["failure_reason"] for r in negative if r.get("inconclusive")]

    if status == FAILED:
        if confirmed:
            details["failure_reason"] = "; ".join(confirmed)
        if unresolved:
            # Recorded, but does not soften the verdict: criteria are
            # conjunctive, so a confirmed miss is terminal on its own.
            details["unresolved_checks"] = unresolved
    elif status == INCONCLUSIVE and unresolved:
        details["inconclusive_detail"] = "; ".join(unresolved)

    return _result(status, details, reason)


async def _persist_result(
    db: AsyncSession,
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    status: str,
    details: dict,
    reason: str | None = None,
):
    await persist_verification_result(
        db,
        goal_id,
        submission_id,
        status,
        details,
        inconclusive_reason=reason,
    )


async def run_github_repo_verification(
    goal_id: uuid.UUID,
    submission_id: uuid.UUID,
    proof_data: dict,
    criteria_data: dict,
    db: AsyncSession | None = None,
) -> dict:
    """Verify a submission and record the outcome.

    All three outcomes are persisted through the same call.
    ``persist_verification_result`` is THE charge boundary: it branches on the
    status alone and there is no path from ``inconclusive`` to
    ``process_charge_for_goal``, so this function does not need — and must not
    have — a second charge decision of its own. An earlier version returned
    early on ``inconclusive`` without writing anything, which kept the money
    safe but lost the outcome: the row stayed indistinguishable from "never
    dispatched", so nothing recorded that a check had been attempted and
    ``goal_verification_is_blocked`` could not see it, leaving the deadline
    sweep free to charge for our own outage.
    """
    result = await verify_github_repo(proof_data, criteria_data)
    status = result["verification_status"]
    reason = result.get("inconclusive_reason")

    if status == INCONCLUSIVE:
        logger.warning(
            "github_repo verification inconclusive for goal %s submission %s: "
            "reason=%s detail=%s",
            goal_id,
            submission_id,
            reason,
            result["verification_details"].get("inconclusive_detail"),
        )

    if db is not None:
        await _persist_result(
            db,
            goal_id,
            submission_id,
            status,
            result["verification_details"],
            reason,
        )
    else:
        async with async_session() as session:
            await _persist_result(
                session,
                goal_id,
                submission_id,
                status,
                result["verification_details"],
                reason,
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
    """Celery entry point.

    ``self.retry`` covers faults that stop the outcome from being *recorded* —
    a dead database, a broken session — where retrying in ten seconds is the
    right move and the alternative is losing the result.

    It is deliberately not the retry path for a GitHub fault. Those are caught
    per check, persisted as ``inconclusive``, and re-dispatched by
    ``workers/reconcile_dispatch.py`` on the staleness window
    (``verification_dispatch_stale_minutes``, 15) up to
    ``verification_dispatch_max_attempts``. That is the correct mechanism for
    the fault we actually see most: unauthenticated GitHub allows 60 requests
    per hour per server IP, and a quota exhausted now is still exhausted in the
    ten seconds Celery would wait. The old code reached neither path — a blanket
    ``except`` swallowed every transient error into a terminal ``failed``, so
    nothing was retried and the pledge was charged instead.
    """
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
