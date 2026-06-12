# Media Module

## What this module owns
The media module covers backend video upload intake and metadata lookup. It is the part of the system that takes a recorded file, checks that it is an allowed video, stores it on disk, writes a matching database row, and returns a stable handle the rest of the product can reference. Think of it like a coat check: the file gets stored in the back room, and the API gives back a ticket with the details you need later.

## HTTP contract
### `POST /api/uploads/video`
Implemented in `backend/app/routes/uploads.py`.

- Authenticated endpoint under the `/api/uploads` router.
- Accepts `multipart/form-data` with:
  - `file` — required `UploadFile`
  - `duration_seconds` — required positive float from `Form(...)`
  - `goal_id` — optional UUID from `Form(...)`
- Accepts only `video/mp4` and `video/quicktime`.
- Rejects oversized payloads using `settings.max_upload_size_bytes` from `backend/app/config.py`.
- If `goal_id` is provided, the route queries `Goal` and requires `Goal.user_id == current_user.id` before proceeding.
- Returns `201` with `UploadResponse` from `backend/app/schemas/upload.py`:
  - `upload_id`
  - `sha256`
  - `size_bytes`
  - `duration_seconds`
  - `mime_type`

### `GET /api/uploads/{upload_id}`
Also implemented in `backend/app/routes/uploads.py`.

- Fetches upload metadata through `get_upload_by_id(...)`.
- Returns `404` when no upload exists for the id.
- Returns `403` when the upload exists but belongs to another user.
- Returns `200` with `UploadDetailResponse` from `backend/app/schemas/upload.py`:
  - `upload_id`
  - `goal_id`
  - `sha256`
  - `size_bytes`
  - `duration_seconds`
  - `mime_type`
  - `created_at`

## Storage and persistence
The storage workflow lives in `backend/app/services/uploads.py`.

- `write_upload(...)` computes a SHA-256 digest of the received bytes.
- The service creates a `MediaUpload` row in `backend/app/models/upload.py` and flushes so the upload id is available before writing the file.
- `_resolve_storage_path(...)` builds the destination path as:
  - `<media_dir>/<user_id>/<goal_id>/<upload_id>.<ext>` when the upload is tied to a goal
  - `<media_dir>/<user_id>/orphan/<upload_id>.<ext>` when no goal is attached
- File extension mapping is MIME-based:
  - `video/mp4` → `.mp4`
  - `video/quicktime` → `.mov`
- The service creates parent directories, writes the file, stores the final `storage_path`, commits, and refreshes the row.

## Data model
`MediaUpload` in `backend/app/models/upload.py` stores:
- `id`
- `user_id`
- `goal_id` (nullable)
- `sha256`
- `size_bytes`
- `duration_seconds`
- `mime_type`
- `storage_path`
- `created_at`

Relationships:
- `User.uploads` in `backend/app/models/user.py`
- `Goal.uploads` in `backend/app/models/goal.py`

Schema migration:
- `backend/alembic/versions/29683944c0b5_add_media_uploads.py`

## Configuration
Defined in `backend/app/config.py`.

- `media_dir`: base filesystem directory for stored uploads. Default `/var/sacrifice/media`.
- `max_upload_size_bytes`: maximum accepted upload size. Default `100 * 1024 * 1024`.

## Testing
- Success-path smoke coverage lives in `backend/tests/test_video_upload_smoke.py`.
- The test uses the fixture file `e2e/fixtures/minimal.mp4`.
- It authenticates through the existing dev-token helper route, uploads the fixture over HTTP, asserts `201`, validates that `upload_id` parses as a UUID, and checks the contract fields against the fixture bytes.
- Pytest marks this category with `@smoke`, registered in `backend/pyproject.toml`.