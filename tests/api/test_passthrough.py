"""Tests for the transparent OpenRouter passthrough route."""

import json

import httpx
from fastapi.testclient import TestClient

from free_claude_code.api import passthrough
from free_claude_code.api.dependencies import get_settings
from free_claude_code.config.settings import Settings
from tests.api.support import create_test_app

_SSE_BYTES = b'data: {"id":"gen-1","choices":[{"delta":{"content":"hi"}}]}\n\n'


class _AsyncBody(httpx.AsyncByteStream):
    """Unread async stream so ``aiter_raw`` works like a live response."""

    def __init__(self, data: bytes):
        self._data = data

    async def __aiter__(self):
        yield self._data


def _json_response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        stream=_AsyncBody(json.dumps(payload).encode()),
    )


def _client_with_mock(
    monkeypatch,
    handler,
    *,
    settings: Settings | None = None,
    raise_server_exceptions: bool = True,
    captured_client_kwargs: dict | None = None,
) -> TestClient:
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(**kwargs):
        if captured_client_kwargs is not None:
            captured_client_kwargs.update(kwargs)
        return real_async_client(transport=transport)

    monkeypatch.setattr(passthrough.httpx, "AsyncClient", fake_async_client)
    if settings is None:
        settings = Settings()
        settings.anthropic_auth_token = "proxy-token"
        settings.open_router_api_key = "or-key"
    app = create_test_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_passthrough_forwards_verbatim_and_injects_key(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        captured["headers"] = {
            key.lower(): value for key, value in request.headers.items()
        }
        return _json_response(200, {"data": [{"id": "openai/gpt-5"}]})

    client = _client_with_mock(monkeypatch, handler)
    response = client.post(
        "/openrouter/api/v1/chat/completions?x=1",
        json={"model": "openai/gpt-5"},
        headers={
            "Authorization": "Bearer proxy-token",
            "Cookie": "session=abc",
            "X-Api-Key": "client-key",
            "Anthropic-Auth-Token": "anth-token",
            "Proxy-Authorization": "Basic xxx",
            "Keep-Alive": "timeout=5",
            "TE": "trailers",
            "Trailer": "Expires",
            "Upgrade": "websocket",
            "Connection": "keep-alive, x-custom-hop",
            "X-Custom-Hop": "drop-me",
            "X-Client-Feature": "keep-me",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"data": [{"id": "openai/gpt-5"}]}
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions?x=1"
    assert captured["auth"] == "Bearer or-key"
    assert captured["body"] == {"model": "openai/gpt-5"}
    headers = captured["headers"]
    assert headers["authorization"] == "Bearer or-key"
    assert headers["host"] == "openrouter.ai"
    assert headers["x-client-feature"] == "keep-me"
    for name in (
        "cookie",
        "x-api-key",
        "anthropic-auth-token",
        "proxy-authorization",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "transfer-encoding",
        "x-custom-hop",
    ):
        assert name not in headers


def test_passthrough_streams_upstream_errors(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response(402, {"error": {"message": "insufficient credits"}})

    client = _client_with_mock(monkeypatch, handler)
    response = client.get(
        "/openrouter/api/v1/models",
        headers={"Authorization": "Bearer proxy-token"},
    )

    assert response.status_code == 402
    assert response.json()["error"]["message"] == "insufficient credits"


def test_passthrough_forwards_sse_bytes_unmodified(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_AsyncBody(_SSE_BYTES),
        )

    client = _client_with_mock(monkeypatch, handler)
    response = client.post(
        "/openrouter/api/v1/chat/completions",
        json={"model": "openai/gpt-5", "stream": True},
        headers={"Authorization": "Bearer proxy-token"},
    )

    assert response.status_code == 200
    assert response.content == _SSE_BYTES


def test_passthrough_requires_proxy_auth(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream must not be called")

    client = _client_with_mock(monkeypatch, handler)
    response = client.get("/openrouter/api/v1/models")

    assert response.status_code == 401


def test_passthrough_empty_key_fails_closed(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream must not be called")

    settings = Settings()
    settings.anthropic_auth_token = "proxy-token"
    settings.open_router_api_key = "   "
    client = _client_with_mock(monkeypatch, handler, settings=settings)
    response = client.get(
        "/openrouter/api/v1/models",
        headers={"Authorization": "Bearer proxy-token"},
    )

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["type"] == "api_error"
    assert "OPENROUTER_API_KEY" in body["error"]["message"]


def test_passthrough_uses_settings_timeouts(monkeypatch):
    client_kwargs: dict = {}

    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"ok": True})

    settings = Settings()
    settings.anthropic_auth_token = "proxy-token"
    settings.open_router_api_key = "or-key"
    settings.http_read_timeout = 12.0
    settings.http_write_timeout = 3.0
    settings.http_connect_timeout = 1.5
    client = _client_with_mock(
        monkeypatch,
        handler,
        settings=settings,
        captured_client_kwargs=client_kwargs,
    )
    response = client.get(
        "/openrouter/api/v1/models",
        headers={"Authorization": "Bearer proxy-token"},
    )

    assert response.status_code == 200
    timeout = client_kwargs["timeout"]
    assert timeout.connect == 1.5
    assert timeout.read == 12.0
    assert timeout.write == 3.0


def test_passthrough_connect_failure_is_openai_shaped(monkeypatch):
    real_async_client = httpx.AsyncClient

    class _FailingClient:
        def __init__(self, **_kwargs):
            self._inner = real_async_client()

        def build_request(self, *args, **kwargs):
            return self._inner.build_request(*args, **kwargs)

        async def send(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

        async def aclose(self):
            await self._inner.aclose()

    monkeypatch.setattr(passthrough.httpx, "AsyncClient", _FailingClient)
    settings = Settings()
    settings.anthropic_auth_token = "proxy-token"
    settings.open_router_api_key = "or-key"
    app = create_test_app()
    app.dependency_overrides[get_settings] = lambda: settings
    response = TestClient(app, raise_server_exceptions=False).get(
        "/openrouter/api/v1/models",
        headers={"Authorization": "Bearer proxy-token"},
    )

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["type"] == "api_error"
    assert body["error"]["param"] is None
    assert body["error"]["code"] is None
