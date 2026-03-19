"""
HTTP middleware helpers.

These middlewares avoid BaseHTTPMiddleware so streaming SSE responses keep
flowing chunk-by-chunk under ASGI and WSGI/Passenger adapters.
"""

import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import config
from app.core.logger import logger


class EnsureConfigLoadedMiddleware:
    """Ensure runtime config is available before HTTP handlers run."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        await config.ensure_loaded()
        await self.app(scope, receive, send)


class SSEHeadersMiddleware:
    """Disable proxy buffering for SSE responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = message.setdefault("headers", [])
                headers = Headers(raw=raw_headers)
                content_type = headers.get("content-type", "")
                if content_type.lower().startswith("text/event-stream"):
                    mutable_headers = MutableHeaders(raw=raw_headers)
                    mutable_headers["Cache-Control"] = "no-cache, no-transform"
                    mutable_headers["Connection"] = "keep-alive"
                    mutable_headers["X-Accel-Buffering"] = "no"

            await send(message)

        await self.app(scope, receive, send_wrapper)


class ResponseLoggerMiddleware:
    """
    Request logging / response tracking middleware.

    Implemented as native ASGI middleware to avoid buffering streaming
    responses through BaseHTTPMiddleware.
    """

    _SKIP_PATHS = {
        "/",
        "/login",
        "/imagine",
        "/voice",
        "/admin",
        "/admin/login",
        "/admin/config",
        "/admin/cache",
        "/admin/token",
    }

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = str(uuid.uuid4())
        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["trace_id"] = trace_id

        method = scope.get("method", "")
        path = scope.get("path", "")
        start_time = time.time()
        status_code = 500

        if path.startswith("/static/") or path in self._SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        logger.info(
            f"Request: {method} {path}",
            extra={
                "traceID": trace_id,
                "method": method,
                "path": path,
            },
        )

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration = (time.time() - start_time) * 1000
            logger.info(
                f"Response: {method} {path} - {status_code} ({duration:.2f}ms)",
                extra={
                    "traceID": trace_id,
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": round(duration, 2),
                },
            )
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            logger.error(
                f"Response Error: {method} {path} - {str(e)} ({duration:.2f}ms)",
                extra={
                    "traceID": trace_id,
                    "method": method,
                    "path": path,
                    "duration_ms": round(duration, 2),
                    "error": str(e),
                },
            )
            raise

