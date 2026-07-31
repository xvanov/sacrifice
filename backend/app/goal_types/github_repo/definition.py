definition = {
    "name": "github_repo",
    "description": "The system checks if a GitHub repo meets specified criteria (files exist, commits pushed, a PR opened).",
    "sample_prompts": [
        "Push a working implementation to my github repo by Saturday",
        "Open a PR with at least 3 commits by next Thursday",
    ],
    # NOTE: at least one of min_commits / required_files / require_pr /
    # conditions must be set. The verifier refuses to certify a goal whose
    # criteria express nothing checkable — it returns ``failed`` with a
    # "no verifiable criteria" reason rather than a free pass, so criteria
    # naming only the repo are useless to the user. ``anyOf`` records that
    # contract for the clients and criteria-generation prompts that read this
    # schema from ``GET /api/goal-types``.
    "criteria_schema": {
        "type": "object",
        "properties": {
            "repo_owner": {"type": "string"},
            "repo_name": {"type": "string"},
            "branch": {
                "type": "string",
                "description": "Branch the checks run against; defaults to main.",
            },
            "min_commits": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Minimum number of commits on the branch, counted from "
                    "commits_since onward."
                ),
            },
            # The time anchor for every commit count on this goal. Without it,
            # "push 3 commits by Saturday" was satisfied by three commits pushed
            # last year: app/workers/github_repo.py counted the branch's entire
            # history, so any goal on a repo that already had min_commits commits
            # passed the moment it was created.
            #
            # Server-assigned, never collected: a user who could choose the
            # anchor would set it before the history they already have, which is
            # the same free pass by a different route. ``x-server-assigned``
            # tells app/services/criteria_gate.stamp_goal_created_at to overwrite
            # whatever arrives with the goal's creation time — read off the schema
            # so a future goal type opts in the same way, with no goal-type name
            # hardcoded in the writer.
            "commits_since": {
                "type": "string",
                "format": "date-time",
                "x-server-assigned": "goal_created_at",
                "description": (
                    "Server-assigned ISO-8601 instant (the goal's creation "
                    "time). Only commits after it count; commits that predate "
                    "the goal do not. Supplied values are ignored."
                ),
            },
            "required_files": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Repo-relative paths that must exist on the branch.",
            },
            "require_pr": {
                "type": "boolean",
                "description": "Require an open or merged PR for the branch.",
            },
            "conditions": {
                "type": "array",
                "description": (
                    "Legacy condition list, still honoured: entries of type "
                    "commits, lines_changed or tickets_closed."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["commits", "lines_changed", "tickets_closed"],
                        },
                        "min_count": {"type": "integer", "minimum": 1},
                        "since_date": {"type": "string"},
                        "tickets": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["repo_owner", "repo_name"],
        "anyOf": [
            {"required": ["min_commits"]},
            {"required": ["required_files"]},
            {"required": ["require_pr"]},
            {"required": ["conditions"]},
        ],
    },
}
