"""The shared-secret guard on /internal/*.

These endpoints are the one part of the API with no Firebase user behind them: an external
scheduler calls them over the public domain with an `X-Cron-Secret` header. That makes the
guard the only thing between the open internet and "delete everything past its retention
window", so it is worth testing as its own surface rather than one 401 per job test.

The guard is declared on the router (`app/api/routers/internal.py`), so this suite walks
every route the router exposes rather than a hand-written list — a new endpoint added
without the header check fails here instead of shipping open.
"""
import pytest

from app.api.routers import internal
from app.config import settings

SECRET = "s3cret"

INTERNAL_ROUTES = sorted(
    f"/internal{route.path}" for route in internal.router.routes if "POST" in route.methods
)

REJECTED_HEADERS = [
    pytest.param(None, id="no header"),
    pytest.param({"X-Cron-Secret": ""}, id="empty header"),
    pytest.param({"X-Cron-Secret": "wrong"}, id="wrong value"),
    pytest.param({"X-Cron-Secret": SECRET[:-1]}, id="prefix of the real secret"),
    pytest.param({"X-Cron-Secret": SECRET + "x"}, id="real secret plus a character"),
]


def test_every_internal_route_is_covered_by_this_suite():
    """Guards the guard: the parametrization is generated from the router, so this only
    fails if the router stops exposing routes at all."""
    assert INTERNAL_ROUTES, "no POST routes found on the internal router"


@pytest.mark.parametrize("route", INTERNAL_ROUTES)
@pytest.mark.parametrize("headers", REJECTED_HEADERS)
def test_rejects_anything_but_the_exact_secret(client, monkeypatch, route, headers):
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", SECRET)

    response = client.post(route, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid cron secret"}


@pytest.mark.parametrize("route", INTERNAL_ROUTES)
def test_an_unset_secret_rejects_everything(client, monkeypatch, route):
    """Fails closed, not open: leaving `REMINDER_CRON_SECRET` empty disables the endpoints
    rather than opening them, which is what makes "not configured yet" a safe state."""
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", "")

    assert client.post(route).status_code == 401
    assert client.post(route, headers={"X-Cron-Secret": ""}).status_code == 401
    assert client.post(route, headers={"X-Cron-Secret": "anything"}).status_code == 401


@pytest.mark.parametrize("route", INTERNAL_ROUTES)
def test_the_right_secret_gets_past_the_guard(client, monkeypatch, route):
    """The control the 401s above need: without it they would still pass if the routes
    were unreachable for some unrelated reason."""
    monkeypatch.setattr(settings, "REMINDER_CRON_SECRET", SECRET)

    assert client.post(route, headers={"X-Cron-Secret": SECRET}).status_code != 401
