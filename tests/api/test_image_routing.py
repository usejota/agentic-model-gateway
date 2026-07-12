"""Tests for the image reroute path (IMAGE_ROUTE).

Covers the service-layer decisions:
- no-op when request has no images,
- no-op when IMAGE_ROUTE is unset,
- no-op when primary is already image-capable (native_anthropic),
- reroute when request has images AND primary is text-only AND IMAGE_ROUTE is set,
- fallback chain skips image-incompatible candidates when the request has images,
- image content survives the reroute intact (no stripping).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.responses import StreamingResponse

from api.models.anthropic import Message, MessagesRequest
from api.services import ClaudeProxyService
from config.settings import Settings
from providers.base import BaseProvider
from providers.exceptions import OverloadedError


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    image_route: str | None = None,
    fallback_models: str | None = None,
) -> Settings:
    """Build a Settings instance with the given routing config.

    Default primary model is DeepSeek (text-only) — exactly the case IMAGE_ROUTE
    reroutes around. Sets ``MODEL_OPUS/SONNET/HAIKU`` explicitly (via alias
    names) so the fixture doesn't pick up the user's ``~/.fcc/.env`` overrides
    for those tier mappings.
    """
    kwargs: dict[str, str] = {
        "MODEL": model,
        "MODEL_OPUS": model,
        "MODEL_SONNET": model,
        "MODEL_HAIKU": model,
        "MESSAGING_PLATFORM": "none",
    }
    if image_route is not None:
        kwargs["IMAGE_ROUTE"] = image_route
    if fallback_models is not None:
        kwargs["FALLBACK_MODELS"] = fallback_models
    return Settings.model_validate(kwargs)


def _text_request(
    *, with_image: bool, model: str = "claude-sonnet-4"
) -> MessagesRequest:
    content: list[dict] = [{"type": "text", "text": "what is this?"}]
    if with_image:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "BASE64DATA",
                },
            }
        )
    return MessagesRequest(
        model=model,
        messages=[Message(role="user", content=cast(Any, content))],
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
async def test_no_images_no_route_no_reroute() -> None:
    """Plain text request → primary stream used (default MODEL=deepseek-chat)."""
    provider = _RecordingProvider()
    svc = _service(_settings(model="deepseek/deepseek-chat"), provider)
    result = svc.create_message(_text_request(with_image=False))
    await _drain(result)
    assert provider.streamed_models == ["deepseek-chat"]


@pytest.mark.asyncio
async def test_no_images_with_route_no_reroute() -> None:
    """Image route set, but no images in request → primary still used."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_text_request(with_image=False))
    await _drain(result)
    assert provider.streamed_models == ["deepseek-chat"]


@pytest.mark.asyncio
async def test_images_without_route_passthrough() -> None:
    """IMAGE_ROUTE unset → request goes upstream as-is, no reroute."""
    provider = _RecordingProvider()
    svc = _service(_settings(model="deepseek/deepseek-chat"), provider)
    result = svc.create_message(_text_request(with_image=True))
    await _drain(result)
    # Primary still used; we don't strip the image — pass-through preserved.
    assert provider.streamed_models == ["deepseek-chat"]


@pytest.mark.asyncio
async def test_images_with_route_text_only_primary_reroutes() -> None:
    """Image request + IMAGE_ROUTE set + text-only primary → reroute."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_text_request(with_image=True))
    await _drain(result)
    # The provider receives the IMAGE_ROUTE model, not the primary. OpenRouter
    # model ids include a nested slash (vendor/model), so the full provider
    # model is ``minimax/minimax-m3``.
    assert provider.streamed_models == ["minimax/minimax-m3"]


@pytest.mark.asyncio
async def test_images_reroute_overrides_native_anthropic_primary() -> None:
    """IMAGE_ROUTE set + image in request → ALWAYS reroute, regardless of primary.

    We don't try to detect whether the primary model is vision-capable: the
    transport (``native_anthropic`` vs ``openai_chat``) doesn't predict it.
    The user opts in by setting ``IMAGE_ROUTE``; if they want their primary
    to handle images, they leave the var unset.
    """
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="open_router/anthropic/claude-sonnet-4",  # vision-capable primary
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    request = MessagesRequest(
        model="claude-sonnet-4",
        messages=[
            Message(
                role="user",
                content=cast(
                    Any,
                    [
                        {"type": "text", "text": "look at this"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "BASE64DATA",
                            },
                        },
                    ],
                ),
            )
        ],
        max_tokens=16,
        stream=True,
    )
    result = svc.create_message(request)
    await _drain(result)
    # Primary is vision-capable Claude but IMAGE_ROUTE was set → rerouted.
    # OpenRouter model ids include a nested slash (vendor/model), so the full
    # provider model is ``minimax/minimax-m3``.
    assert provider.streamed_models == ["minimax/minimax-m3"]


# --------------------------------------------------------------------------
# Image content survives reroute intact (no stripping in v1)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rerouted_request_carries_image_intact() -> None:
    """The rerouted request body must contain the original image block, not a
    stripped placeholder. The multimodal provider sees the real base64."""

    seen_payloads: list[list] = []

    class _CapturingProvider:
        def preflight_stream(self, request, *, thinking_enabled=None):
            return None

        async def stream_response(
            self,
            request: MessagesRequest,
            *,
            input_tokens,
            request_id,
            thinking_enabled,
        ):
            seen_payloads.append(request.model_dump()["messages"])
            yield "event: message_start\ndata: {}\n\n"
            yield "[DONE]\n\n"

    provider = cast(BaseProvider, _CapturingProvider())
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_text_request(with_image=True))
    await _drain(result)

    assert seen_payloads, "Provider should have been called"
    messages = seen_payloads[0]
    user_msg = messages[0]
    blocks = user_msg["content"]
    image_blocks = [
        b for b in blocks if isinstance(b, dict) and b.get("type") == "image"
    ]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["data"] == "BASE64DATA"


# --------------------------------------------------------------------------
# Fallback chain is image-aware
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fallback_chain_skips_image_incompatible_when_images_present() -> None:
    """When the primary is text-only and falls back to FALLBACK_MODELS, every
    fallback candidate is also checked against the IMAGE_ROUTE target; any
    fallback whose provider differs from IMAGE_ROUTE is skipped when the
    request has images — there's no point pre-flighting a text-only candidate
    when the request carries image content.

    In this test the primary overloads, IMAGE_ROUTE points to OpenRouter, and
    the only fallback is also text-only DeepSeek on OpenRouter. The chain
    should run out of candidates and raise OverloadedError — NOT silently
    serve a text-only fallback that would 400 upstream.
    """
    from providers.exceptions import OverloadedError

    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            fallback_models="open_router/deepseek/deepseek-chat",
            image_route="open_router/anthropic/claude-sonnet-4",
        ),
        provider,
    )

    class _AlwaysOverloaded:
        def preflight_stream(self, request, *, thinking_enabled=None):
            raise OverloadedError("overloaded")

        async def stream_response(
            self, request, *, input_tokens, request_id, thinking_enabled
        ):
            raise OverloadedError("overloaded")
            yield ""  # pragma: no cover

    # Swap the provider getter so every attempt raises OverloadedError.
    svc._provider_getter = lambda _pid: cast(BaseProvider, _AlwaysOverloaded())
    with pytest.raises(OverloadedError):
        result = svc.create_message(_text_request(with_image=True))
        await _drain(result)


@pytest.mark.asyncio
async def test_fallback_chain_uses_image_route_provider_when_available() -> None:
    """If FALLBACK_MODELS contains the IMAGE_ROUTE provider, it survives the
    image-aware filter and serves the request when the primary fails."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            fallback_models="open_router/anthropic/claude-sonnet-4",
            image_route="open_router/anthropic/claude-sonnet-4",
        ),
        provider,
    )

    class _PrimaryOverloaded:
        def preflight_stream(self, request, *, thinking_enabled=None):
            raise OverloadedError("primary down")

        async def stream_response(
            self, request, *, input_tokens, request_id, thinking_enabled
        ):
            raise OverloadedError("primary down")
            yield ""  # pragma: no cover

    primary_calls: list[str] = []

    class _FallbackProvider:
        """Returns the fallback model name on stream_response; tracks invocations."""

        def preflight_stream(self, request, *, thinking_enabled=None):
            return None

        async def stream_response(
            self,
            request: MessagesRequest,
            *,
            input_tokens,
            request_id,
            thinking_enabled,
        ):
            primary_calls.append(request.model)
            yield "event: message_start\ndata: {}\n\n"
            yield "[DONE]\n\n"

    def provider_for(pid: str) -> BaseProvider:
        if pid == "deepseek":
            return cast(BaseProvider, _PrimaryOverloaded())
        return cast(BaseProvider, _FallbackProvider())

    svc._provider_getter = provider_for
    result = svc.create_message(_text_request(with_image=True))
    await _drain(result)
    # OpenRouter model ids include a nested slash (vendor/model), so the
    # provider receives the full ``anthropic/claude-sonnet-4`` model name.
    assert primary_calls == ["anthropic/claude-sonnet-4"]


# --------------------------------------------------------------------------
# IMAGE_ROUTE itself is image-capable → no recursion
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_image_route_matches_primary_no_double_reroute() -> None:
    """If IMAGE_ROUTE and MODEL point to the same provider/model, the reroute
    helper is a no-op (no infinite loop, no error)."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="open_router/minimax/minimax-m3",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_text_request(with_image=True))
    await _drain(result)
    # OpenRouter model ids include a nested slash, so the provider receives
    # the full ``minimax/minimax-m3`` model name.
    assert provider.streamed_models == ["minimax/minimax-m3"]


# --------------------------------------------------------------------------
# No-op when primary is text-only but image_route is set AND request has no images
# (covered by test_no_images_with_route_no_reroute — explicit here for clarity)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Image reroute scope: only the LAST user turn triggers reroute.
# Older images in the history do NOT lock the session on the vision model —
# they are stripped to placeholders so the text-only primary can serve the
# current turn. Reproduces the "session gets stuck on the vision model after
# one image" bug that motivated the fix.
# --------------------------------------------------------------------------
def _request_with_image_in_history_then_text_followup() -> MessagesRequest:
    """Two user turns: an old one with an image, a recent one that's pure text.

    The new behavior: do NOT reroute (the current turn is text-only); the old
    image is stripped to a placeholder so the text-only primary (deepseek-chat)
    doesn't 400 on the raw image block.
    """
    return MessagesRequest(
        model="claude-sonnet-4",
        messages=[
            Message(
                role="user",
                content=cast(
                    Any,
                    [
                        {"type": "text", "text": "look at this"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "BASE64DATA",
                            },
                        },
                    ],
                ),
            ),
            Message(role="assistant", content=cast(Any, "what a picture")),
            Message(
                role="user",
                content=cast(Any, "now explain the code"),
            ),
        ],
        max_tokens=16,
        stream=True,
    )


@pytest.mark.asyncio
async def test_old_image_text_only_turn_uses_text_primary_not_vision() -> None:
    """Old image in history + current turn is text → primary, not IMAGE_ROUTE.

    The new helper ``has_image_in_last_user_turn`` returns False (the last user
    turn has no image), so the reroute is skipped. The text-only primary
    (deepseek-chat) is used and the old image is stripped to a placeholder.
    """
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_request_with_image_in_history_then_text_followup())
    await _drain(result)
    # Primary (deepseek) used, NOT the image route (minimax).
    assert provider.streamed_models == ["deepseek-chat"]


@pytest.mark.asyncio
async def test_old_image_text_only_turn_strips_image_from_history() -> None:
    """Old image in history → stripped to placeholder before primary is called.

    The primary sees the original user turn as text + a [Image #1] placeholder
    instead of a raw base64 image block (which would 400 a text-only model).
    """
    seen_payloads: list[list] = []

    class _CapturingProvider:
        def preflight_stream(self, request, *, thinking_enabled=None):
            return None

        async def stream_response(
            self,
            request: MessagesRequest,
            *,
            input_tokens,
            request_id,
            thinking_enabled,
        ):
            seen_payloads.append(request.model_dump()["messages"])
            yield "event: message_start\ndata: {}\n\n"
            yield "[DONE]\n\n"

    provider = cast(BaseProvider, _CapturingProvider())
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_request_with_image_in_history_then_text_followup())
    await _drain(result)

    assert seen_payloads, "Provider should have been called"
    messages = seen_payloads[0]
    # First (old) user turn: image replaced with text placeholder.
    first_user_blocks = messages[0]["content"]
    assert isinstance(first_user_blocks, list)
    image_blocks_in_first = [
        b for b in first_user_blocks if isinstance(b, dict) and b.get("type") == "image"
    ]
    assert image_blocks_in_first == []
    placeholder_texts = [
        b.get("text", "")
        for b in first_user_blocks
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert any(t.startswith("[Image #1]") for t in placeholder_texts)
    # Third (current) user turn: untouched, pure text.
    assert messages[2]["content"] == "now explain the code"


@pytest.mark.asyncio
async def test_current_turn_with_image_still_reroutes() -> None:
    """The original reroute contract is preserved: image in the LAST turn
    (no history) still routes to IMAGE_ROUTE."""
    provider = _RecordingProvider()
    svc = _service(
        _settings(
            model="deepseek/deepseek-chat",
            image_route="open_router/minimax/minimax-m3",
        ),
        provider,
    )
    result = svc.create_message(_text_request(with_image=True))
    await _drain(result)
    # Image is in the only/last user turn → reroute to IMAGE_ROUTE.
    assert provider.streamed_models == ["minimax/minimax-m3"]
