from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings

app = create_app()


def test_anthropic_auth_token_required_and_accepts_x_api_key():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "s3cr3t"
    app.dependency_overrides[get_settings] = lambda: settings

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch("api.routes.get_token_count", return_value=1):
        # No header -> 401
        r = client.post("/v1/messages/count_tokens", json=payload)
        assert r.status_code == 401

        # X-API-Key header -> 200
        r = client.post(
            "/v1/messages/count_tokens", json=payload, headers={"X-API-Key": "s3cr3t"}
        )
        assert r.status_code == 200
        assert r.json()["input_tokens"] == 1

    app.dependency_overrides.clear()


def test_anthropic_auth_token_accepts_bearer_authorization():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "b3artoken"
    app.dependency_overrides[get_settings] = lambda: settings

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch("api.routes.get_token_count", return_value=2):
        # Authorization Bearer -> 200
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"Authorization": "Bearer b3artoken"},
        )
        assert r.status_code == 200
        assert r.json()["input_tokens"] == 2

    app.dependency_overrides.clear()


def test_anthropic_auth_token_normalizes_configured_whitespace():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "  spaced-token  \n"
    app.dependency_overrides[get_settings] = lambda: settings

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with patch("api.routes.get_token_count", return_value=3):
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"Authorization": "Bearer spaced-token"},
        )
        assert r.status_code == 200
        assert r.json()["input_tokens"] == 3

    app.dependency_overrides.clear()


def test_anthropic_auth_token_applies_to_models_endpoint():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "models-token"
    app.dependency_overrides[get_settings] = lambda: settings

    r = client.get("/v1/models")
    assert r.status_code == 401

    r = client.get("/v1/models", headers={"X-API-Key": "models-token"})
    assert r.status_code == 200
    assert "data" in r.json()

    app.dependency_overrides.clear()


def test_root_get_requires_auth_but_root_probes_are_public():
    client = TestClient(app)
    settings = Settings()
    settings.anthropic_auth_token = "root-token"
    app.dependency_overrides[get_settings] = lambda: settings

    response = client.get("/")
    assert response.status_code == 401

    head = client.head("/")
    assert head.status_code == 204
    assert head.headers["Allow"] == "GET, HEAD, OPTIONS"

    options = client.options("/")
    assert options.status_code == 204
    assert options.headers["Allow"] == "GET, HEAD, OPTIONS"

    app.dependency_overrides.clear()


def _override_settings(**kwargs: object) -> Settings:
    settings = Settings()
    for key, value in kwargs.items():
        setattr(settings, key, value)
    app.dependency_overrides[get_settings] = lambda: settings
    return settings


def test_empty_config_is_noop():
    client = TestClient(app)
    _override_settings(anthropic_auth_token="", proxy_user_tokens={})

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api.routes.get_token_count", return_value=1):
        # No header at all -> allowed (no-op)
        r = client.post("/v1/messages/count_tokens", json=payload)
        assert r.status_code == 200

    app.dependency_overrides.clear()


def test_shared_token_still_works_with_per_user_configured():
    client = TestClient(app)
    _override_settings(
        anthropic_auth_token="shared-secret",
        proxy_user_tokens={"alice": "tok-a", "bob": "tok-b"},
    )

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api.routes.get_token_count", return_value=1):
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"X-API-Key": "shared-secret"},
        )
        assert r.status_code == 200

    app.dependency_overrides.clear()


def test_per_user_token_resolves_identity_via_state():
    settings = Settings()
    settings.anthropic_auth_token = ""
    settings.proxy_user_tokens = {"alice": "tok-a", "bob": "tok-b"}

    from starlette.datastructures import Headers
    from starlette.requests import Request

    from api.dependencies import require_api_key

    def call(token: str) -> str:
        scope = {
            "type": "http",
            "headers": Headers({"x-api-key": token}).raw,
            "state": {},
        }
        request = Request(scope)
        require_api_key(request, settings)
        return request.state.proxy_user

    assert call("tok-a") == "alice"
    assert call("tok-b") == "bob"


def test_per_user_valid_token_authorizes_request():
    client = TestClient(app)
    _override_settings(anthropic_auth_token="", proxy_user_tokens={"alice": "tok-a"})

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api.routes.get_token_count", return_value=1):
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"X-API-Key": "tok-a"},
        )
        assert r.status_code == 200

    app.dependency_overrides.clear()


def test_per_user_wrong_token_is_401():
    client = TestClient(app)
    _override_settings(anthropic_auth_token="", proxy_user_tokens={"alice": "tok-a"})

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api.routes.get_token_count", return_value=1):
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"X-API-Key": "wrong"},
        )
        assert r.status_code == 401

    app.dependency_overrides.clear()


def test_per_user_missing_token_is_401():
    client = TestClient(app)
    _override_settings(anthropic_auth_token="", proxy_user_tokens={"alice": "tok-a"})

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api.routes.get_token_count", return_value=1):
        r = client.post("/v1/messages/count_tokens", json=payload)
        assert r.status_code == 401

    app.dependency_overrides.clear()


def test_per_user_token_with_appended_model_still_works():
    client = TestClient(app)
    _override_settings(anthropic_auth_token="", proxy_user_tokens={"alice": "tok-a"})

    payload = {
        "model": "claude-3-sonnet",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("api.routes.get_token_count", return_value=1):
        # Token with appended ":model" must still resolve.
        r = client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={"X-API-Key": "tok-a:claude-3-sonnet"},
        )
        assert r.status_code == 200

    app.dependency_overrides.clear()


def test_proxy_user_tokens_parse_forms(monkeypatch):
    monkeypatch.setenv("PROXY_USER_TOKENS", "alice:tok-a,bob:tok-b")
    assert Settings().proxy_user_tokens == {"alice": "tok-a", "bob": "tok-b"}

    monkeypatch.setenv("PROXY_USER_TOKENS", '{"alice": "tok-a"}')
    assert Settings().proxy_user_tokens == {"alice": "tok-a"}

    monkeypatch.setenv("PROXY_USER_TOKENS", "")
    assert Settings().proxy_user_tokens == {}

    # Only the first colon splits name from token, so tokens may contain colons.
    monkeypatch.setenv("PROXY_USER_TOKENS", "alice:tok:a:b")
    assert Settings().proxy_user_tokens == {"alice": "tok:a:b"}
