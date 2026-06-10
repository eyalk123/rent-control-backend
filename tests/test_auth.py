"""Tests for the real get_current_user dependency (Firebase token verification).

Unlike the rest of the suite, these do NOT override get_current_user; they
monkeypatch the Firebase verification call so no network request is made.
"""
import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import TransportError

import app.api.dependencies as deps
from app.database import get_db
from app.main import app


@pytest.fixture
def auth_client(db_session):
    # Only the DB is overridden; auth runs for real.
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _patch_verify(monkeypatch, result=None, exc=None):
    def fake(token, request, audience=None):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(deps.id_token, "verify_firebase_token", fake)


def test_valid_token_authenticates(auth_client, monkeypatch):
    _patch_verify(monkeypatch, result={"sub": "firebase-uid-1"})
    resp = auth_client.get("/properties", headers={"Authorization": "Bearer goodtoken"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_invalid_token_returns_401(auth_client, monkeypatch):
    _patch_verify(monkeypatch, exc=ValueError("bad token"))
    resp = auth_client.get("/properties", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 401


def test_transport_error_returns_503(auth_client, monkeypatch):
    _patch_verify(monkeypatch, exc=TransportError("jwks down"))
    resp = auth_client.get("/properties", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 503


def test_missing_uid_claim_returns_401(auth_client, monkeypatch):
    _patch_verify(monkeypatch, result={"email": "no-uid@example.com"})
    resp = auth_client.get("/properties", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 401


def test_user_id_claim_fallback(auth_client, monkeypatch):
    # No "sub", but "user_id" present -> still authenticates.
    _patch_verify(monkeypatch, result={"user_id": "firebase-uid-2"})
    resp = auth_client.get("/properties", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200


def test_missing_authorization_header_rejected(auth_client):
    resp = auth_client.get("/properties")
    assert resp.status_code in (401, 403)
