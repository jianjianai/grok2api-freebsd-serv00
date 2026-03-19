"""
WSGI entrypoint for Passenger / Serv00 deployments.

This keeps the FastAPI application as-is, but boots it on a dedicated
event loop and exposes a WSGI callable via a2wsgi.
"""

import atexit
import asyncio
import threading

from a2wsgi import ASGIMiddleware

from main import app, logger, shutdown_app, startup_app

_wsgi_loop = asyncio.new_event_loop()
_wsgi_loop_thread = threading.Thread(
    target=_wsgi_loop.run_forever,
    name="grok2api-wsgi-loop",
    daemon=True,
)
_wsgi_loop_thread.start()


def _run_on_wsgi_loop(coro):
    return asyncio.run_coroutine_threadsafe(coro, _wsgi_loop).result()


_run_on_wsgi_loop(startup_app(app))


def _shutdown_wsgi_runtime() -> None:
    if _wsgi_loop.is_closed():
        return

    try:
        _run_on_wsgi_loop(shutdown_app(app))
    except Exception as exc:  # pragma: no cover - process shutdown path
        logger.warning(f"WSGI shutdown failed: {exc}")
    finally:
        try:
            _wsgi_loop.call_soon_threadsafe(_wsgi_loop.stop)
        except RuntimeError:
            return

        if _wsgi_loop_thread.is_alive():
            _wsgi_loop_thread.join(timeout=5)
        if not _wsgi_loop.is_running() and not _wsgi_loop.is_closed():
            _wsgi_loop.close()


atexit.register(_shutdown_wsgi_runtime)

application = ASGIMiddleware(app, loop=_wsgi_loop)

