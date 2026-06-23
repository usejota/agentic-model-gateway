from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from api.app import create_app
from config.settings import clear_settings_cache


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Keep the lru-cached Settings from leaking the admin token across tests."""
    clear_settings_cache()
    yield
    clear_settings_cache()


def _make_request(headers: dict[str, str]) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in headers.items()
    ]
    scope = {"type": "http", "method": "GET", "headers": raw_headers}
    return Request(scope)


def _local_client(app) -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def _set_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def _clear_process_config(monkeypatch) -> None:
    for key in (
        "MODEL",
        "ANTHROPIC_AUTH_TOKEN",
        "ADMIN_API_TOKEN",
        "FCC_ENV_FILE",
        "HOST",
        "PORT",
    ):
        monkeypatch.delenv(key, raising=False)


def _build_app(monkeypatch, tmp_path: Path, *, admin_token: str | None = None):
    _set_home(monkeypatch, tmp_path)
    _clear_process_config(monkeypatch)
    if admin_token is not None:
        monkeypatch.setenv("ADMIN_API_TOKEN", admin_token)
    clear_settings_cache()
    return create_app(lifespan_enabled=False)


def test_admin_empty_secret_preserves_loopback_only(monkeypatch, tmp_path):
    """Backward-compat: with no admin secret, loopback access still works."""
    app = _build_app(monkeypatch, tmp_path)

    assert _local_client(app).get("/admin").status_code == 200
    assert _local_client(app).get("/admin/api/config").status_code == 200


def test_admin_secret_set_accepts_valid_header_token(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path, admin_token="s3cret-admin")

    client = _local_client(app)
    assert (
        client.get("/admin", headers={"X-Admin-Token": "s3cret-admin"}).status_code
        == 200
    )
    assert (
        client.get(
            "/admin/api/config", headers={"X-Admin-Token": "s3cret-admin"}
        ).status_code
        == 200
    )


def test_admin_secret_set_accepts_valid_bearer_token(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path, admin_token="bearer-secret")

    response = _local_client(app).get(
        "/admin", headers={"Authorization": "Bearer bearer-secret"}
    )
    assert response.status_code == 200


def test_admin_secret_set_rejects_missing_token(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path, admin_token="s3cret-admin")

    response = _local_client(app).get("/admin")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing admin token"


def test_admin_secret_set_rejects_wrong_token(monkeypatch, tmp_path):
    app = _build_app(monkeypatch, tmp_path, admin_token="s3cret-admin")

    response = _local_client(app).get(
        "/admin", headers={"X-Admin-Token": "wrong-token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid admin token"


def test_admin_secret_set_still_enforces_loopback(monkeypatch, tmp_path):
    """Defense-in-depth: a valid token does not bypass the loopback gate."""
    app = _build_app(monkeypatch, tmp_path, admin_token="s3cret-admin")

    remote_client = TestClient(app, client=("203.0.113.10", 50000))
    response = remote_client.get("/admin", headers={"X-Admin-Token": "s3cret-admin"})
    assert response.status_code == 403


def test_admin_token_check_uses_constant_time_comparison(monkeypatch, tmp_path):
    """The token comparison path routes through ``secrets.compare_digest``."""
    import api.admin_auth as admin_auth

    _set_home(monkeypatch, tmp_path)
    _clear_process_config(monkeypatch)
    monkeypatch.setenv("ADMIN_API_TOKEN", "s3cret-admin")
    clear_settings_cache()

    calls: list[tuple[bytes, bytes]] = []
    real_compare = admin_auth.secrets.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(admin_auth.secrets, "compare_digest", spy)

    admin_auth.require_admin_token(_make_request({"X-Admin-Token": "s3cret-admin"}))

    assert calls == [(b"s3cret-admin", b"s3cret-admin")]
