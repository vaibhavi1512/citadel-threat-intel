from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from queue import Queue

import jwt
import pytest
from fastapi.testclient import TestClient

from backend import auth, main


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_USERS_PATH", tmp_path / "auth_users.json")
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "test-secret-key-with-32-characters-minimum")
    monkeypatch.setattr(auth, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(auth, "ACCESS_TOKEN_EXPIRE_MINUTES", 60)
    auth._failed_logins.clear()
    return TestClient(main.app)


def register(client, email="Vaibhavi@example.com", password="CitadelPass1"):
    return client.post("/auth/register", json={"name": "Vaibhavi", "email": email, "password": password})


def test_registration_normalizes_email_hashes_password_and_omits_hash(auth_client):
    response = register(auth_client)
    assert response.status_code == 201
    assert response.json()["email"] == "vaibhavi@example.com"
    assert "password_hash" not in response.json()
    row = json.loads(auth.AUTH_USERS_PATH.read_text())[0]
    assert row["password_hash"] != "CitadelPass1"
    assert auth.password_hash.verify("CitadelPass1", row["password_hash"])
    assert "CitadelPass1" not in auth.AUTH_USERS_PATH.read_text()


def test_duplicate_normalized_email_is_rejected(auth_client):
    assert register(auth_client).status_code == 201
    duplicate = register(auth_client, "  vaibhavi@EXAMPLE.com  ")
    assert duplicate.status_code == 409


@pytest.mark.parametrize("email,password", [("not-an-email", "CitadelPass1"), ("valid@example.com", "weakpass")])
def test_invalid_registration_is_rejected_without_echoing_input(auth_client, email, password):
    response = auth_client.post("/auth/register", json={"name": "Tester", "email": email, "password": password})
    assert response.status_code == 422
    assert password not in response.text


def test_empty_auth_storage_is_treated_as_an_empty_user_list(auth_client):
    auth.AUTH_USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth.AUTH_USERS_PATH.write_text("", encoding="utf-8")

    response = register(auth_client)

    assert response.status_code == 201


def test_corrupted_auth_storage_fails_safely_and_logs_details(auth_client, caplog):
    auth.AUTH_USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth.AUTH_USERS_PATH.write_text("{not valid json", encoding="utf-8")

    response = register(auth_client)

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication storage is unavailable."}
    assert "invalid JSON" in caplog.text
    assert "{not valid json" not in response.text


def test_unexpected_registration_failure_is_logged_but_not_exposed(auth_client, monkeypatch, caplog):
    def fail_hash(_password):
        raise RuntimeError("test hash provider failure")

    monkeypatch.setattr(auth.password_hash, "hash", fail_hash)

    response = register(auth_client)

    assert response.status_code == 500
    assert response.json() == {"detail": "Registration could not be completed."}
    assert "test hash provider failure" in caplog.text
    assert "CitadelPass1" not in response.text


def test_auth_configuration_503_has_cors_headers(auth_client, monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "")

    response = auth_client.post(
        "/auth/register",
        headers={"Origin": "http://localhost:5173"},
        json={"name": "Tester", "email": "cors@example.com", "password": "CitadelPass1"},
    )

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_login_success_and_generic_failure_for_wrong_or_unknown_user(auth_client):
    register(auth_client)
    success = auth_client.post("/auth/login", json={"email": "VAIBHAVI@example.com", "password": "CitadelPass1"})
    assert success.status_code == 200
    data = success.json()
    assert data["token_type"] == "bearer"
    assert "access_token" in data
    assert "password_hash" not in data["user"]
    claims = jwt.decode(data["access_token"], auth.JWT_SECRET_KEY, algorithms=["HS256"])
    assert set(claims).issubset({"sub", "email", "iat", "exp"})
    wrong = auth_client.post("/auth/login", json={"email": "vaibhavi@example.com", "password": "WrongPass2"})
    unknown = auth_client.post("/auth/login", json={"email": "unknown@example.com", "password": "WrongPass2"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_current_user_requires_valid_nonexpired_token(auth_client):
    register(auth_client)
    token = auth_client.post("/auth/login", json={"email": "vaibhavi@example.com", "password": "CitadelPass1"}).json()["access_token"]
    assert auth_client.get("/auth/me").status_code == 401
    assert auth_client.get("/auth/me", headers={"Authorization": "Bearer malformed"}).status_code == 401
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["email"] == "vaibhavi@example.com"
    expired = jwt.encode({"sub": json.loads(auth.AUTH_USERS_PATH.read_text())[0]["id"], "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, auth.JWT_SECRET_KEY, algorithm="HS256")
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_health_public_and_citadel_apis_and_sse_protected(auth_client):
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/alerts").status_code == 401
    assert auth_client.get("/events/stream").status_code == 401
    register(auth_client)
    token = auth_client.post("/auth/login", json={"email": "vaibhavi@example.com", "password": "CitadelPass1"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert auth_client.get("/alerts", headers=headers).status_code == 200
    assert auth.resolve_token(token)["email"] == "vaibhavi@example.com"


def test_stream_generator_continues_to_emit_authenticated_event_frames(monkeypatch):
    subscriber = Queue()
    subscriber.put('{"event_type":"case_updated"}')

    class Bus:
        def subscribe(self):
            return subscriber

        def unsubscribe(self, queue):
            assert queue is subscriber

    monkeypatch.setattr(main, "event_bus", Bus())
    response = main.stream_events()
    first_frame = asyncio.run(anext(response.body_iterator))
    assert first_frame == 'data: {"event_type":"case_updated"}\n\n'
    asyncio.run(response.body_iterator.aclose())


def test_local_development_generates_an_ephemeral_jwt_secret():
    from utils.config import resolve_jwt_secret

    generated = resolve_jwt_secret("development", None)
    assert len(generated) >= 32
    assert generated != resolve_jwt_secret("development", None)


def test_configured_development_jwt_secret_is_preserved():
    from utils.config import resolve_jwt_secret

    secret = "development-test-secret-that-is-long-enough-32"
    assert resolve_jwt_secret("development", secret) == secret


def test_production_missing_jwt_secret_fails_auth_configuration(monkeypatch):
    monkeypatch.setattr(auth, "ENVIRONMENT", "production")
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "")

    with pytest.raises(RuntimeError, match="production requires.*JWT_SECRET_KEY"):
        auth.validate_auth_configuration()


def test_production_configured_jwt_secret_passes_auth_configuration(monkeypatch):
    monkeypatch.setattr(auth, "ENVIRONMENT", "production")
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "prod-test-secret-with-at-least-32-characters")

    auth.validate_auth_configuration()


def test_inactive_account_token_is_rejected(auth_client):
    register(auth_client)
    user = json.loads(auth.AUTH_USERS_PATH.read_text())[0]
    user["is_active"] = False
    auth.AUTH_USERS_PATH.write_text(json.dumps([user]))
    token = jwt.encode({"sub": user["id"], "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, auth.JWT_SECRET_KEY, algorithm="HS256")
    assert auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
