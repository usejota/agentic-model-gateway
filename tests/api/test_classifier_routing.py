"""Tests for the classifier reroute path (CLASSIFIER_ROUTE).

Covers the service-layer decisions:
- no-op when request is not a safety classifier request,
- no-op when CLASSIFIER_ROUTE is unset,
- no-op when primary is already the classifier route target,
- reroute when request matches the classifier signature AND CLASSIFIER_ROUTE is set,
- classifier signature detection (system text, tools guard, no-system guard).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.responses import StreamingResponse

from api.models.anthropic import Message, MessagesRequest
from api.services import ClaudeProxyService
from config.settings import Settings
from providers.base import BaseProvider
from providers.exceptions import InvalidRequestError


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    classifier_route: str | None = None,
    image_route: str | None = None,
    fallback_models: str | None = None,
    model_delegate_exclusions: str | None = None,
) -> Settings:
    kwargs: dict[str, str] = {
        "MODEL": model,
        "MODEL_OPUS": model,
        "MODEL_SONNET": model,
        "MODEL_HAIKU": model,
        "MESSAGING_PLATFORM": "none",
    }
    if classifier_route is not None:
        kwargs["CLASSIFIER_ROUTE"] = classifier_route
    if image_route is not None:
        kwargs["IMAGE_ROUTE"] = image_route
    if fallback_models is not None:
        kwargs["FALLBACK_MODELS"] = fallback_models
    if model_delegate_exclusions is not None:
        kwargs["MODEL_DELEGATE_EXCLUSIONS"] = model_delegate_exclusions
    return Settings.model_validate(kwargs)


CLASSIFIER_SYSTEM = "You are a security monitor for autonomous AI coding agents."


def _classifier_request(
    model: str = "claude-sonnet-4",
    *,
    with_tools: bool = False,
) -> MessagesRequest:
    return MessagesRequest(
        model=model,
        messages=[
            Message(
                role="user",
                content=cast(Any, [{"type": "text", "text": "do thing"}]),
            )
        ],
        system=cast(Any, [{"type": "text", "text": CLASSIFIER_SYSTEM}]),
        max_tokens=64,
        stream=True,
    )


def _text_request(
    model: str = "claude-sonnet-4",
    *,
    with_tools: bool = False,
) -> MessagesRequest:
    return MessagesRequest(
        model=model,
        messages=[
            Message(
                role="user",
                content=cast(Any, [{"type": "text", "text": "hello"}]),
            )
        ],
        max_tokens=16,
        stream=True,
    )


class _RecordingProvider:
    """Captures the model it was asked to stream for and emits a trivial SSE."""

    def __init__(self) -> None:
        self.streamed_models: list[str] = []

    def preflight_stream(
        self, request: object, *, thinking_enabled: bool | None = None
    ) -> None:
        return None

    async def stream_response(
        self, request: MessagesRequest, *, input_tokens, request_id, thinking_enabled
    ):
        self.streamed_models.append(request.model)
        yield 'event: message_start\ndata: {"type":"message_start"}\n\n'
        yield "[DONE]\n\n"


def _service(settings: Settings, provider: object | None = None) -> ClaudeProxyService:
    provider = cast(BaseProvider, provider or _RecordingProvider())
    return ClaudeProxyService(settings, provider_getter=lambda _pid: provider)


async def _drain(result: object) -> list[str]:
    assert isinstance(result, StreamingResponse)
    return [str(chunk) async for chunk in result.body_iterator]


# --------------------------------------------------------------------------
# Decision matrix
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_route_no_reroute() -> None:
    """CLASSIFIER_ROUTE unset → request goes upstream as-is."""
    provider = _RecordingProvider()
    svc = _service(_settings(model="deepseek/deepseek-chat"), provider)
    result = svc.create_message(_classifier_request())
    await _drain(result)
    assert provider.streamed_models == ["deepseek-chat"]


@pytest.mark.asyncio
async def test_route_set_non_classifier_no_reroute() -> None:
    """Route set, but request is not a classifier → primary still used."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            classifier_route="open_router/qwen/qwen3-30b",
        ),
        provider,
    )
    result = svc.create_message(_text_request())
    await _drain(result)
    assert provider.streamed_models == ["deepseek-chat"]


@pytest.mark.asyncio
async def test_classifier_with_route_reroutes() -> None:
    """Classifier request + CLASSIFIER_ROUTE set → reroute."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            classifier_route="open_router/qwen/qwen3-30b",
        ),
        provider,
    )
    result = svc.create_message(_classifier_request())
    await _drain(result)
    # OpenRouter model ids include a nested slash (vendor/model), so the
    # provider receives the full ``qwen/qwen3-30b`` model name.
    assert provider.streamed_models == ["qwen/qwen3-30b"]


@pytest.mark.asyncio
async def test_classifier_with_route_already_target_no_reroute() -> None:
    """Classifier request, but primary is already the CLASSIFIER_ROUTE target."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="open_router/qwen/qwen3-30b",
            classifier_route="open_router/qwen/qwen3-30b",
        ),
        provider,
    )
    result = svc.create_message(_classifier_request())
    await _drain(result)
    # OpenRouter model ids include a nested slash, so the provider receives
    # the full ``qwen/qwen3-30b`` model name.
    assert provider.streamed_models == ["qwen/qwen3-30b"]


# --------------------------------------------------------------------------
# Detector: is_safety_classifier_request
# --------------------------------------------------------------------------
def test_detector_matches_classifier() -> None:
    """System prompt with security-monitor marker → True."""
    from api.detection import is_safety_classifier_request

    assert is_safety_classifier_request(_classifier_request()) is True


def test_detector_rejects_normal_request() -> None:
    """Normal request without classifier system → False."""
    from api.detection import is_safety_classifier_request

    assert is_safety_classifier_request(_text_request()) is False


def test_detector_rejects_classifier_with_tools() -> None:
    """Classifier-like system but has tools → False."""
    from api.detection import is_safety_classifier_request

    req = _classifier_request(with_tools=True)
    req.tools = cast(
        Any, [{"name": "bash", "description": "run commands", "input_schema": {}}]
    )
    assert is_safety_classifier_request(req) is False


def test_detector_rejects_no_system() -> None:
    """No system prompt → False."""
    from api.detection import is_safety_classifier_request

    req = MessagesRequest(
        model="claude-sonnet-4",
        messages=[
            Message(role="user", content=cast(Any, [{"type": "text", "text": "hi"}]))
        ],
        max_tokens=64,
    )
    assert is_safety_classifier_request(req) is False


def test_detector_matches_string_system() -> None:
    """System as a plain string containing the marker → True."""
    from api.detection import is_safety_classifier_request

    req = MessagesRequest(
        model="claude-sonnet-4",
        messages=[
            Message(
                role="user",
                content=cast(Any, [{"type": "text", "text": "do thing"}]),
            )
        ],
        system=CLASSIFIER_SYSTEM,
        max_tokens=64,
    )
    assert is_safety_classifier_request(req) is True


# --------------------------------------------------------------------------
# Settings: classifier_route parsing
# --------------------------------------------------------------------------
def test_settings_classifier_route_empty_is_none() -> None:
    """Empty CLASSIFIER_ROUTE → None."""
    settings = _settings(classifier_route="")
    assert settings.classifier_route is None


def test_settings_classifier_route_missing_slash_raises() -> None:
    """CLASSIFIER_ROUTE without / → ValueError."""
    with pytest.raises(ValueError, match="must be a single 'provider/model' ref"):
        _settings(classifier_route="just-a-model")


def test_settings_classifier_route_unknown_provider_raises() -> None:
    """CLASSIFIER_ROUTE with unknown provider → ValueError."""
    with pytest.raises(ValueError, match="Invalid route provider"):
        _settings(classifier_route="nope/model")


def test_settings_classifier_route_parts() -> None:
    """classifier_route_parts returns (provider, model)."""
    settings = _settings(classifier_route="open_router/qwen/qwen3-30b")
    assert settings.classifier_route_parts == ("open_router", "qwen/qwen3-30b")


def test_settings_classifier_route_parts_none() -> None:
    """classifier_route_parts returns None when unset."""
    settings = _settings()
    assert settings.classifier_route_parts is None


# --------------------------------------------------------------------------
# Interaction with delegate enforcement
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_classifier_reroute_respects_delegate_exclusions() -> None:
    """A classifier request is NOT a main-loop request (its system prompt is the
    security-monitor prompt, not "You are Claude Code"), so it still passes
    through _enforce_delegate_exclusions after the reroute. If the
    CLASSIFIER_ROUTE target is itself in MODEL_DELEGATE_EXCLUSIONS, the
    rerouted request is rejected — the reroute never bypasses enforcement."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            classifier_route="open_router/qwen/qwen3-30b",
            model_delegate_exclusions="open_router/qwen/qwen3-30b",
        ),
        provider,
    )
    with pytest.raises(InvalidRequestError, match="excluded for subagents"):
        result = svc.create_message(_classifier_request())
        await _drain(result)
