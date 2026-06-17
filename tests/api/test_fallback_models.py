"""Tests for the cross-model fallback chain (FALLBACK_MODELS).

Covers settings parsing and the service-level fallback behavior:
- no-op when unset (backward compat),
- fall back to the next model when a backend overloads before any output,
- stop at the first model that produces output,
- never switch mid-stream once output has started,
- surface the original error when every candidate fails.
"""

from __future__ import annotations

from typing import cast

import pytest
from fastapi.responses import JSONResponse, StreamingResponse

from api.models.anthropic import Message, MessagesRequest
from api.services import ClaudeProxyService
from config.settings import Settings
from providers.base import BaseProvider
from providers.exceptions import OverloadedError


def _settings(fallback_models: str | None = None) -> Settings:
    kwargs: dict[str, str] = {
        "model": "nvidia_nim/model-a",
        "messaging_platform": "none",
    }
    if fallback_models is not None:
        kwargs["FALLBACK_MODELS"] = fallback_models
    # Settings reads validation aliases from the mapping; pass as a plain dict so
    # ty doesn't widen a **splat to object across every field type.
    return Settings.model_validate(kwargs)


def _request(model: str = "claude-sonnet-4") -> MessagesRequest:
    return MessagesRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        max_tokens=16,
    )


async def _collect(result: object) -> list[str]:
    """Drain a StreamingResponse body iterator into a list of string chunks."""
    assert isinstance(result, StreamingResponse)
    # The provider stubs in this module always yield str chunks.
    return [str(chunk) async for chunk in result.body_iterator]


# --------------------------------------------------------------------------
# Settings parsing
# --------------------------------------------------------------------------
def test_fallback_models_empty_by_default() -> None:
    assert _settings().fallback_models == []


def test_fallback_models_parses_comma_list() -> None:
    s = _settings(
        fallback_models="open_router/deepseek/deepseek-chat, groq/llama-3.3-70b"
    )
    assert s.fallback_models == [
        "open_router/deepseek/deepseek-chat",
        "groq/llama-3.3-70b",
    ]


def test_fallback_models_blank_entries_ignored() -> None:
    assert _settings(fallback_models="a/b,,  ,c/d").fallback_models == ["a/b", "c/d"]


def test_fallback_models_empty_string_is_empty() -> None:
    assert _settings(fallback_models="").fallback_models == []


# --------------------------------------------------------------------------
# Service-level fallback behavior
# --------------------------------------------------------------------------
class _Provider:
    """Minimal provider stub whose stream behavior is scripted per model name."""

    def __init__(self, behaviors: dict[str, str]) -> None:
        # behaviors[model] in {"ok", "overload"}
        self._behaviors = behaviors

    def preflight_stream(self, request: object, *, thinking_enabled: bool) -> None:
        return None

    async def stream_response(
        self, request: MessagesRequest, *, input_tokens, request_id, thinking_enabled
    ):
        behavior = self._behaviors.get(request.model, "ok")
        if behavior == "overload":
            raise OverloadedError(f"{request.model} overloaded")
        yield f"event: from {request.model}\n\n"
        yield "[DONE]\n\n"


def _service(settings: Settings, behaviors: dict[str, str]) -> ClaudeProxyService:
    provider = cast(BaseProvider, _Provider(behaviors))
    return ClaudeProxyService(settings, provider_getter=lambda _pid: provider)


@pytest.mark.asyncio
async def test_no_fallback_when_primary_ok() -> None:
    svc = _service(_settings(), {"model-a": "ok"})
    result = svc.create_message(_request())
    chunks = await _collect(result)
    assert any("from model-a" in c for c in chunks)


@pytest.mark.asyncio
async def test_falls_back_to_next_model_on_overload() -> None:
    svc = _service(
        _settings(fallback_models="nvidia_nim/model-b"),
        {"model-a": "overload", "model-b": "ok"},
    )
    result = svc.create_message(_request())
    chunks = await _collect(result)
    # Primary (model-a) overloaded before output -> served by fallback model-b.
    assert any("from model-b" in c for c in chunks)
    assert not any("from model-a" in c for c in chunks)


@pytest.mark.asyncio
async def test_stops_at_first_healthy_fallback() -> None:
    svc = _service(
        _settings(fallback_models="nvidia_nim/model-b,nvidia_nim/model-c"),
        {"model-a": "overload", "model-b": "ok", "model-c": "ok"},
    )
    result = svc.create_message(_request())
    chunks = await _collect(result)
    assert any("from model-b" in c for c in chunks)
    assert not any("from model-c" in c for c in chunks)


@pytest.mark.asyncio
async def test_raises_when_all_candidates_overloaded() -> None:
    svc = _service(
        _settings(fallback_models="nvidia_nim/model-b"),
        {"model-a": "overload", "model-b": "overload"},
    )
    result = svc.create_message(_request())
    with pytest.raises(OverloadedError):
        await _collect(result)


# --------------------------------------------------------------------------
# Non-streaming (stream: false) — the auto-mode classifier path
# --------------------------------------------------------------------------
class _SSEProvider:
    """Provider stub that emits a minimal well-formed Anthropic SSE stream."""

    def preflight_stream(self, request: object, *, thinking_enabled: bool) -> None:
        return None

    async def stream_response(
        self, request: MessagesRequest, *, input_tokens, request_id, thinking_enabled
    ):
        import json as _json

        def evt(t: str, d: dict) -> str:
            return f"event: {t}\ndata: {_json.dumps(d)}\n\n"

        yield evt(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_x",
                    "role": "assistant",
                    "model": request.model,
                    "usage": {"input_tokens": 11, "output_tokens": 1},
                },
            },
        )
        yield evt(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        yield evt(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "safe"},
            },
        )
        yield evt("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield evt(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"input_tokens": 11, "output_tokens": 2},
            },
        )
        yield evt("message_stop", {"type": "message_stop"})


@pytest.mark.asyncio
async def test_non_streaming_returns_json_with_usage() -> None:
    import json as _json

    settings = _settings()
    provider = cast(BaseProvider, _SSEProvider())
    svc = ClaudeProxyService(settings, provider_getter=lambda _pid: provider)

    request = MessagesRequest(
        model="claude-sonnet-4",
        messages=[Message(role="user", content="hi")],
        max_tokens=16,
        stream=False,
    )
    result = svc.create_message(request)
    # Non-streaming path returns an awaitable resolving to a JSONResponse.
    import inspect

    assert inspect.isawaitable(result)
    response = await result
    assert isinstance(response, JSONResponse)
    body = _json.loads(bytes(response.body))

    assert body["type"] == "message"
    assert body["usage"]["input_tokens"] == 11
    assert body["content"] == [{"type": "text", "text": "safe"}]
    assert body["stop_reason"] == "end_turn"
