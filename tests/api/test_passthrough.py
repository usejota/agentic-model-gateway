"""Tests for the transparent OpenRouter passthrough route."""

import json

import httpx
from fastapi.testclient import TestClient

from free_claude_code.api import passthrough
from free_claude_code.api.dependencies import get_settings
from free_claude_code.config.settings import Settings
from tests.api.support import create_test_app


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


def _client_with_mock(monkeypatch, handler) -> TestClient:
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def fake_async_client(**_kwargs):
        return real_async_client(transport=transport)

    monkeypatch.setattr(passthrough.httpx, "AsyncClient", fake_async_client)
    settings = Settings()
    settings.anthropic_auth_token = "proxy-token"
    settings.open_router_api_key = "or-key"
    app = create_test_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_passthrough_forwards_verbatim_and_injects_key(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return _json_response(200, {"data": [{"id": "openai/gpt-5"}]})

    client = _client_with_mock(monkeypatch, handler)
    response = client.post(
        "/openrouter/api/v1/chat/completions?x=1",
        json={"model": "openai/gpt-5"},
        headers={"Authorization": "Bearer proxy-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"data": [{"id": "openai/gpt-5"}]}
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions?x=1"
    assert captured["auth"] == "Bearer or-key"
    assert captured["body"] == {"model": "openai/gpt-5"}


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


def test_passthrough_requires_proxy_auth(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream must not be called")

    client = _client_with_mock(monkeypatch, handler)
    response = client.get("/openrouter/api/v1/models")

    assert response.status_code == 401
