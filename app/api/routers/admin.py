"""Internal, admin-only product analytics.

Three routes, layered so that the page itself carries no data:

* ``GET /admin/dashboard`` — a static HTML shell with a login box. **It contains no
  analytics.** Anyone who finds the URL gets an empty page and a password prompt. Its CSS
  and JS come from ``GET /admin/assets/<name>`` (``app/static/admin/``), equally data-free.
* ``GET /admin/analytics`` — the aggregate JSON, behind a valid Firebase ID token *and*
  membership of ``ADMIN_OWNER_IDS``. Counts only, no identity.
* ``GET /admin/analytics/owners`` — the owners behind one clicked number, behind the same
  gate. This one **does** carry identity (owner name and email, never a renter's); its SQL is
  kept apart in ``app/analytics/drilldown.py``.

Three deliberate choices:

**404, never 403.** Every rejection — no token, bad token, valid token belonging to a
non-admin — returns the same 404 as a route that does not exist. A 403 would confirm to an
attacker that they had found something real and that the only remaining problem was
authorisation. There is exactly one exception, 429 from the rate limiter, which fires before
identity is known and so reveals nothing about it.

**No token in the query string.** The JSON route reads ``Authorization: Bearer`` only. Query
strings end up in access logs, proxy logs, browser history and ``Referer`` headers, so a
token in one is a token that leaks by default.

**Rate limited before authentication.** Otherwise the route is a free oracle for testing
stolen tokens, and every attempt costs a Firebase verification round-trip.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.exceptions import TransportError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from sqlalchemy.orm import Session

from app.analytics import service as analytics_service
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# auto_error=False so a missing header returns None instead of raising 401 — this route
# answers 404 for every failure, and a 401 would leak the route's existence.
_bearer = HTTPBearer(auto_error=False)
_google_request = google_requests.Request()

_DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "static" / "admin"
_DASHBOARD_HTML = _DASHBOARD_DIR / "index.html"

# The page's CSS and JS, by exact name. An allowlist rather than a static mount: nothing else in
# the folder — or above it — is reachable, whatever the request path says.
_ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
_ASSETS = {
    p.name: _ASSET_TYPES[p.suffix]
    for p in _DASHBOARD_DIR.glob("*")
    if p.suffix in _ASSET_TYPES
}

# ── rate limiting ────────────────────────────────────────────────────────────
# A fixed window per client IP, in memory. No Redis: the brief rules out adding a service,
# and per-worker limiting is sufficient for a single-admin route — the worst case is N
# workers each allowing the limit, which is still a small number.
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """Railway terminates TLS upstream, so the socket peer is a proxy. Take the first entry
    of X-Forwarded-For when present; it is spoofable, but the limiter is a brake on casual
    scanning, not an authentication boundary."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    limit = settings.ADMIN_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return
    key = _client_ip(request)
    now = time.monotonic()
    cutoff = now - 60.0
    with _hits_lock:
        recent = [t for t in _hits.get(key, []) if t > cutoff]
        if len(recent) >= limit:
            _hits[key] = recent
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests.",
                headers={"Retry-After": "60"},
            )
        recent.append(now)
        _hits[key] = recent
        if len(_hits) > 2048:  # crude bound; this dict is keyed by attacker-controlled IPs
            for k in [k for k, v in _hits.items() if not v or max(v) < cutoff]:
                _hits.pop(k, None)


def _not_found() -> HTTPException:
    """The single rejection used for every authentication and authorisation failure, so the
    two are indistinguishable from outside."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Verify the Firebase ID token and check it against ADMIN_OWNER_IDS. Every failure is a
    404. Returns the admin's uid."""
    allowed = settings.admin_owner_ids
    if not allowed:
        # Nobody configured: the dashboard does not exist. Fail closed — an empty allowlist
        # must never mean "allow everyone".
        raise _not_found()
    if credentials is None or not credentials.credentials:
        raise _not_found()

    try:
        payload = id_token.verify_firebase_token(
            credentials.credentials,
            _google_request,
            audience=settings.FIREBASE_PROJECT_ID,
        )
    except ValueError:
        raise _not_found()
    except TransportError:
        # Firebase itself is unreachable. This one is not the caller's fault and is not an
        # identity signal, so it may answer honestly.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth provider unreachable.",
        )

    uid = payload.get("sub") or payload.get("user_id")
    if not uid or uid not in allowed:
        # Log the attempt but never the token. A uid is not personal data on its own and is
        # what makes a real intrusion attempt traceable.
        logger.warning("admin analytics denied for uid=%s ip=%s", uid, _client_ip(request))
        raise _not_found()
    return uid


@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(rate_limit)])
def admin_dashboard() -> HTMLResponse:
    """The page shell. Deliberately unauthenticated and deliberately empty: it ships no
    numbers, so serving it to a stranger discloses nothing beyond the fact that a login box
    exists. The data arrives only after the browser authenticates against /admin/analytics.
    """
    try:
        html = _DASHBOARD_HTML.read_text(encoding="utf-8")
    except OSError:
        logger.exception("admin dashboard HTML missing at %s", _DASHBOARD_HTML)
        raise HTTPException(status_code=500, detail="Dashboard asset missing")

    # The Firebase *web* config, so the login box can use the admin's existing account.
    # These are public client identifiers, not secrets — the web app already serves them to
    # every visitor — but they are injected from the environment rather than committed.
    # json.dumps, not an f-string: the page parses this as a JS literal.
    config = (
        {
            "apiKey": settings.FIREBASE_WEB_API_KEY,
            "authDomain": f"{settings.FIREBASE_PROJECT_ID}.firebaseapp.com",
            "projectId": settings.FIREBASE_PROJECT_ID,
            "appId": settings.FIREBASE_WEB_APP_ID,
        }
        if settings.FIREBASE_WEB_API_KEY
        else {}
    )
    html = html.replace("__FIREBASE_CONFIG__", json.dumps(config))

    return HTMLResponse(
        content=html,
        headers={
            "X-Robots-Tag": "noindex, nofollow",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


# Not rate limited: one page load fetches every asset at once, which would spend a third of
# the per-minute budget before the first number arrived. The limiter guards the token check,
# and these carry no data and check no token.
@router.get("/assets/{name}")
def admin_dashboard_asset(name: str) -> Response:
    """The page's code. As public as the shell: no data, no config, no secrets."""
    media_type = _ASSETS.get(name)
    if media_type is None:
        raise _not_found()
    return Response(
        content=(_DASHBOARD_DIR / name).read_bytes(),
        media_type=media_type,
        # Revalidate on every load, so a deploy is picked up without a hard refresh.
        headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/analytics", dependencies=[Depends(rate_limit)])
def admin_analytics(
    response: Response,
    _admin_uid: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    range: Annotated[str, Query(pattern="^(7d|30d|90d|all)$")] = "90d",
    granularity: Annotated[str, Query(pattern="^(day|week)$")] = "week",
    country: Annotated[str | None, Query(max_length=2)] = None,
) -> dict:
    """Aggregate product metrics. Never returns tenant data — see app/analytics/queries.py."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return analytics_service.get_analytics(db, range, granularity, country)


@router.get("/analytics/owners", dependencies=[Depends(rate_limit)])
def admin_analytics_owners(
    response: Response,
    admin_uid: Annotated[str, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    segment: Annotated[str, Query(pattern="^[a-z_]{1,40}$")],
    range: Annotated[str, Query(pattern="^(7d|30d|90d|all)$")] = "90d",
    granularity: Annotated[str, Query(pattern="^(day|week)$")] = "week",
    country: Annotated[str | None, Query(max_length=2)] = None,
    bucket: Annotated[str | None, Query(max_length=40)] = None,
    value: Annotated[str | None, Query(max_length=200)] = None,
    group: Annotated[str | None, Query(max_length=40)] = None,
    exclude: Annotated[str | None, Query(max_length=400)] = None,
) -> dict:
    """The owners behind one clicked bar, point, slice or cell. **Returns identity** — owner
    name and email, never a renter's — so it sits behind the same gate as the aggregates, and
    every call is logged by admin uid and segment (never by what came back)."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    try:
        result = analytics_service.get_owner_list(
            db, segment, range, granularity, country, bucket, value, group, exclude
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    logger.info(
        "admin owner list uid=%s segment=%s rows=%d", admin_uid, segment, result["total"]
    )
    return result
