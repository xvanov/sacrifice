definition = {
    "name": "geolocation",
    "description": (
        "Verify the user is physically at a target location before the goal "
        "deadline. Proof is the device's GPS coordinates captured at "
        "submission time; verification checks the distance to the target "
        "against an allowed radius."
    ),
    "sample_prompts": [
        "Be at the gym by 7am tomorrow",
        "Check in at the office before 9 on Friday",
        "Make it to the library today",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "target_latitude": {"type": "number"},
            "target_longitude": {"type": "number"},
            "radius_m": {"type": "integer"},
        },
        "required": ["target_latitude", "target_longitude"],
    },
}
