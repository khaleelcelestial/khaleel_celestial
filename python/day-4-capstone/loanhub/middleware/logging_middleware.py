import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("app")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            f"{ts} - INFO - {request.method} {request.url.path} "
            f"| {response.status_code} | {elapsed_ms:.0f}ms"
        )
        return response
