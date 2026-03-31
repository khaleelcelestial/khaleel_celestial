import logging
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Cross-cutting concern: logs every incoming request and its response status + duration.
    Kept separate from routers/services (SRP).
    """

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "%s %s | %d | %dms",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
