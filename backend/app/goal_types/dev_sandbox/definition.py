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
            "repo_url": {"type": "string", "minLength": 1},
            "branch": {"type": "string", "minLength": 1},
            # An empty or unparseable command cannot be verified; rejecting it at
            # submission time keeps it out of the worker, where a failed verdict
            # would charge the pledge.
            "test_command": {"type": "string", "minLength": 1},
            "language": {"type": "string"},
            "env_vars": {"type": "object"},
            "goal_description": {"type": "string"},
        },
        "required": ["repo_url", "test_command"],
    },
}
