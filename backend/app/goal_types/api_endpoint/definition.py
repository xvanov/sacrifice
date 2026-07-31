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
            # Bounded so a partial or shorthand answer cannot be stored as a
            # status that no response can ever equal. "2xx" reads as the integer
            # 2 under any first-number parse, and `actual_status == 2` is false
            # forever — an unwinnable goal, and a missed goal charges the card.
            "expected_status": {"type": "integer", "minimum": 100, "maximum": 599},
            "expected_body_schema": {"type": "object"},
            "headers": {"type": "object"},
        },
        "required": ["url", "method", "expected_status"],
    },
}
