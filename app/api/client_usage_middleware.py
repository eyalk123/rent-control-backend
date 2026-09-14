"""ASGI middleware that feeds the client-usage accumulator.

Thin on purpose: everything it knows is in ``app/services/client_usage_service.py``.

**Why it runs after the request, not before.** The owner id is resolved by
``get_current_owner``, which is a route dependency and therefore runs *inside* the
application — long after any middleware has seen the request. So the dependency stashes the
uid on the request scope and this middleware reads it once the response is done. That also
means the middleware never authenticates anything itself: no token is parsed twice, and a
request that never reached an authenticated route simply has no uid to find and is ignored.

**Why raw ASGI rather than ``BaseHTTPMiddleware``.** ``POST /agent/chat`` streams SSE, and
``BaseHTTPMiddleware`` pumps the response body through an internal queue, which is exactly
the wrong thing to put in front of a stream that is meant to arrive a token at a time. A
plain ASGI wrapper passes ``send`` straight through and only peeks at the status line.
"""
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.client_usage_service import (
    CLIENT_APP_HEADER,
    CLIENT_PLATFORM_HEADER,
    CLIENT_VERSION_HEADER,
    is_write,
    recorder,
)

logger = logging.getLogger(__name__)

#: Key on ``request.state`` (i.e. ``scope["state"]``) where the auth dependency leaves the
#: Firebase uid for this middleware to find.
OWNER_ID_STATE_KEY = "owner_id"


class ClientUsageMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self._record(scope, status)

    def _record(self, scope: Scope, status: int | None) -> None:
        try:
            owner_id = (scope.get("state") or {}).get(OWNER_ID_STATE_KEY)
            if not owner_id:
                # Unauthenticated, a CORS preflight, /health, or an /internal cron call.
                return

            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            method = scope["method"]
            path = scope["path"]

            # A rejected write is not work: a 422 from a mistyped form and a 500 from a bug
            # both attempted something, but neither produced any. They still count as a
            # request, which is what `requests` is for.
            succeeded = status is not None and status < 400

            recorder.record(
                owner_id=owner_id,
                method=method,
                path=path,
                app=headers.get(CLIENT_APP_HEADER),
                platform=headers.get(CLIENT_PLATFORM_HEADER),
                app_version=headers.get(CLIENT_VERSION_HEADER),
                counted_as_write=succeeded and is_write(method, path),
            )
        except Exception as exc:  # noqa: BLE001 — telemetry must never break a request
            logger.debug("Client usage capture failed: %s", exc)
