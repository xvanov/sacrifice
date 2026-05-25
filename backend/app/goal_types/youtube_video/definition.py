definition = {
    "name": "youtube_video",
    "description": "User uploads a video to YouTube; the system fetches the transcript and an LLM judges whether the content matches the goal description.",
    "sample_prompts": [
        "Post a YouTube walkthrough of my project by Friday",
        "Record a 5-minute video explaining my refactor",
    ],
    "criteria_schema": {
        "type": "object",
        "properties": {
            "min_duration_seconds": {"type": "integer"},
            "video_description": {"type": "string"},
        },
        "required": ["min_duration_seconds", "video_description"],
    },
}