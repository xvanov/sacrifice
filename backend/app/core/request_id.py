import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Global middleware that ensures every response carries an X-Request-ID header.

    Echoes the caller-supplied value when present; generates a new UUIDv4 otherwise.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER)
        if not request_id:
            request_id = str(uuid.uuid4())

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response