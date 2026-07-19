definition = {
    "name": "api_endpoint",
    "description": "The system polls a user-supplied API endpoint and verifies status codes, response schemas, and availability.",
    "sample_prompts": [
        "My API endpoint at https://myapi.com/health returns 200 by Friday",
        "Deploy a working /users endpoint that returns valid JSON",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string"},
            "expected_status": {"type": "integer"},
            "expected_body_schema": {"type": "object"},
            "headers": {"type": "object"},
        },
        "required": ["url", "method", "expected_status"],
    },
}
