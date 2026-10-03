"""GET /app-version: what the mobile update prompt compares itself against."""
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_unconfigured_reports_nulls_so_nothing_prompts(client, monkeypatch):
    for name in (
        "MOBILE_LATEST_VERSION_IOS",
        "MOBILE_LATEST_VERSION_ANDROID",
        "MOBILE_MIN_VERSION_IOS",
        "MOBILE_MIN_VERSION_ANDROID",
    ):
        monkeypatch.setattr(settings, name, "")
    r = client.get("/app-version")
    assert r.status_code == 200
    assert r.json() == {
        "ios": {"latest": None, "minimum": None},
        "android": {"latest": None, "minimum": None},
    }


def test_reports_configured_versions_per_platform(client, monkeypatch):
    monkeypatch.setattr(settings, "MOBILE_LATEST_VERSION_IOS", "1.4.0")
    monkeypatch.setattr(settings, "MOBILE_MIN_VERSION_IOS", "1.2.0")
    monkeypatch.setattr(settings, "MOBILE_LATEST_VERSION_ANDROID", "1.4.1")
    monkeypatch.setattr(settings, "MOBILE_MIN_VERSION_ANDROID", "")
    body = client.get("/app-version").json()
    assert body["ios"] == {"latest": "1.4.0", "minimum": "1.2.0"}
    assert body["android"] == {"latest": "1.4.1", "minimum": None}


def test_needs_no_auth():
    """An outdated build must be told to update even when it can no longer sign in."""
    app.dependency_overrides.clear()
    assert TestClient(app).get("/app-version").status_code == 200
