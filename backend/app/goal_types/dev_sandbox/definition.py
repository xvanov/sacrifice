definition = {
    "name": "dev_sandbox",
    "description": "The system clones a repo, runs the user's test command inside a sandboxed Docker container, and verifies the output.",
    "sample_prompts": [
        "Push a repo that passes all tests by Sunday",
        "Open-source my side project with passing CI by end of month",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "repo_url": {"type": "string"},
            "branch": {"type": "string"},
            "test_command": {"type": "string"},
            "language": {"type": "string"},
            "env_vars": {"type": "object"},
            "goal_description": {"type": "string"},
        },
        "required": ["repo_url", "test_command"],
    },
}
