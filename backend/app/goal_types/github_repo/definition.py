definition = {
    "name": "github_repo",
    "description": "The system checks if a GitHub repo meets specified criteria (files exist, commits pushed, language stats, etc.).",
    "sample_prompts": [
        "Push a working implementation to my github repo by Saturday",
        "Open a PR with at least 3 commits by next Thursday",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "repo_owner": {"type": "string"},
            "repo_name": {"type": "string"},
            "branch": {"type": "string"},
            "min_commits": {"type": "integer"},
            "required_files": {"type": "array", "items": {"type": "string"}},
            "require_pr": {"type": "boolean"},
        },
        "required": ["repo_owner", "repo_name"],
    },
}
