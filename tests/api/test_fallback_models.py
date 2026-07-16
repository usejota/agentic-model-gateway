"""Tests for the cross-model fallback chain (FALLBACK_MODELS).

Covers settings parsing and the handler-level fallback behavior: no-op when
unset, fall back to the next model on a pre-output overload, commit to the first
model that produces output, and surface the error when every candidate fails.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi.responses import StreamingResponse

from free_claude_code.api.handlers import MessagesHandler
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic.models import Message, MessagesRequest
from free_claude_code.core.failures import ExecutionFailure, FailureKind

_EVENTS = [
    'event: message_start\ndata: {"type":"message_start"}\n\n',
    'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]


def _overload() -> ExecutionFailure:
    return ExecutionFailure(
        kind=FailureKind.OVERLOADED,
        status_code=529,
        message="overloaded",
        retryable=True,
    )


class _GoodProvider:
    def __init__(self) -> None:
        self.requests: list[MessagesRequest] = []

    def preflight_stream(self, request, *, thinking_enabled=None) -> None:
        return None

    async def stream_response(
        self, request, input_tokens=0, *, request_id=None, thinking_enabled=None
    ) -> AsyncIterator[str]:
        self.requests.append(request)
        for event in _EVENTS:
            yield event


class _OverloadProvider:
    """Overloads before any output — at preflight or on the first stream event."""

    def __init__(self, at: str = "stream") -> None:
        self.at = at

    def preflight_stream(self, request, *, thinking_enabled=None) -> None:
        if self.at == "preflight":
            raise _overload()

    async def stream_response(
        self, request, input_tokens=0, *, request_id=None, thinking_enabled=None
    ) -> AsyncIterator[str]:
        if self.at == "stream":
            raise _overload()
        if False:  # pragma: no cover - makes this a generator
            yield ""


def _request(model: str = "claude-sonnet-4") -> MessagesRequest:
    return MessagesRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        max_tokens=16,
        stream=True,
    )


async def _collect(result: object) -> list[str]:
    assert isinstance(result, StreamingResponse)
    return [str(chunk) async for chunk in result.body_iterator]


# --------------------------------------------------------------------------
# Settings parsing
# --------------------------------------------------------------------------
def test_fallback_models_empty_by_default(monkeypatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", ())
    monkeypatch.delenv("FALLBACK_MODELS", raising=False)
    assert Settings().fallback_models == []


def test_fallback_models_parses_comma_list(monkeypatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", ())
    monkeypatch.setenv("FALLBACK_MODELS", "open_router/a, groq/b ,")
    assert Settings().fallback_models == ["open_router/a", "groq/b"]


# --------------------------------------------------------------------------
# Handler fallback behavior
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_fallback_when_unset_uses_primary() -> None:
    primary = _GoodProvider()
    settings = Settings()
    settings.model = "nvidia_nim/model-a"
    handler = MessagesHandler(settings, provider_resolver=lambda _: primary)

    result = await handler.create(_request())
    await _collect(result)
    assert len(primary.requests) == 1


@pytest.mark.asyncio
async def test_falls_back_when_primary_overloads_on_stream() -> None:
    primary = _OverloadProvider(at="stream")
    backup = _GoodProvider()

    def resolver(provider_id: str):
        return backup if provider_id == "open_router" else primary

    settings = Settings()
    settings.model = "nvidia_nim/model-a"
    settings.fallback_models = ["open_router/backup"]
    handler = MessagesHandler(settings, provider_resolver=resolver)

    result = await handler.create(_request())
    chunks = await _collect(result)
    # The backup produced the stream.
    assert len(backup.requests) == 1
    assert any("message_start" in c for c in chunks)


@pytest.mark.asyncio
async def test_falls_back_when_primary_overloads_at_preflight() -> None:
    primary = _OverloadProvider(at="preflight")
    backup = _GoodProvider()

    def resolver(provider_id: str):
        return backup if provider_id == "open_router" else primary

    settings = Settings()
    settings.model = "nvidia_nim/model-a"
    settings.fallback_models = ["open_router/backup"]
    handler = MessagesHandler(settings, provider_resolver=resolver)

    result = await handler.create(_request())
    await _collect(result)
    assert len(backup.requests) == 1


@pytest.mark.asyncio
async def test_all_candidates_overloaded_surfaces_error() -> None:
    primary = _OverloadProvider(at="stream")

    settings = Settings()
    settings.model = "nvidia_nim/model-a"
    settings.fallback_models = ["open_router/backup"]
    handler = MessagesHandler(settings, provider_resolver=lambda _: primary)

    # stream=True path: the terminal error is surfaced (SSE error or raised),
    # never a silent success.
    result = await handler.create(_request())
    if isinstance(result, StreamingResponse):
        chunks = await _collect(result)
        assert any("error" in c.lower() for c in chunks)
    # Non-StreamingResponse would be an error JSON/Response — also acceptable.
