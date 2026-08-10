"""Recovery from upstream rejection of replayed encrypted reasoning payloads."""

from unittest.mock import AsyncMock, patch

import openai
import pytest
from httpx import Request, Response

from free_claude_code.providers.base import ProviderConfig
from free_claude_code.providers.open_router import OpenRouterProvider
from free_claude_code.providers.openai_chat.reasoning_details import (
    clone_without_encrypted_reasoning,
    is_encrypted_reasoning_replay_rejection,
)
from tests.providers.support import immediate_admission

_OPENROUTER_404_BODY = {
    "error": {
        "message": (
            "Your request contains encrypted reasoning or compaction content that "
            "was produced under a different model. Encrypted payloads can only be "
            "replayed to the endpoint that created them."
        ),
        "code": 404,
        "metadata": {
            "pinned_endpoint_slug": "openai/gpt-5.6-luna-20260709|openai",
            "available_endpoint_count": 9,
        },
    }
}


def _error(status: int, body: object) -> openai.APIStatusError:
    response = Response(
        status,
        request=Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    return openai.APIStatusError("upstream rejected", response=response, body=body)


def test_rejection_matches_openrouter_encrypted_replay_404():
    assert is_encrypted_reasoning_replay_rejection(_error(404, _OPENROUTER_404_BODY))


def test_rejection_ignores_other_404s_and_other_statuses():
    assert not is_encrypted_reasoning_replay_rejection(
        _error(404, {"error": {"message": "No endpoints found for model"}})
    )
    assert not is_encrypted_reasoning_replay_rejection(
        _error(400, _OPENROUTER_404_BODY)
    )


def test_clone_strips_opaque_details_and_keeps_plaintext():
    body = {
        "model": "openai/gpt-5.6-luna",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_details": [
                    {"type": "reasoning.encrypted", "data": "zzz"},
                    {"type": "reasoning.text", "text": "visible"},
                ],
            },
            {
                "role": "assistant",
                "content": "done",
                "reasoning_details": [{"type": "reasoning.compaction", "data": "yyy"}],
            },
        ],
    }

    retry_body = clone_without_encrypted_reasoning(body)

    assert retry_body is not None
    assert retry_body["messages"][1]["reasoning_details"] == [
        {"type": "reasoning.text", "text": "visible"}
    ]
    assert "reasoning_details" not in retry_body["messages"][2]
    # The caller's body must stay intact for any other retry path.
    assert len(body["messages"][1]["reasoning_details"]) == 2
    assert "reasoning_details" in body["messages"][2]


def test_clone_returns_none_without_opaque_details():
    body = {
        "model": "m",
        "messages": [
            {"role": "assistant", "content": "ok"},
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_details": [{"type": "reasoning.text", "text": "visible"}],
            },
        ],
    }

    assert clone_without_encrypted_reasoning(body) is None
    assert clone_without_encrypted_reasoning({"model": "m"}) is None


@pytest.fixture
def open_router_provider():
    return OpenRouterProvider(
        ProviderConfig(
            api_key="test_openrouter_key",
            base_url="https://openrouter.ai/api/v1",
            rate_limit=10,
            rate_window=60,
        ),
        admission=immediate_admission(),
    )


@pytest.mark.asyncio
async def test_create_stream_retries_without_encrypted_reasoning(open_router_provider):
    body = {
        "model": "openai/gpt-5.6-luna",
        "messages": [
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "zzz"}],
            },
            {"role": "user", "content": "continue"},
        ],
    }
    create = AsyncMock(side_effect=[_error(404, _OPENROUTER_404_BODY), object()])

    with patch.object(open_router_provider._client.chat.completions, "create", create):
        _stream, used_body, attempt = await open_router_provider._create_stream(
            body,
            open_router_provider._admission.new_retry_session(),
        )
        await attempt.aclose()

    assert create.call_count == 2
    assert "reasoning_details" not in create.call_args_list[1].kwargs["messages"][0]
    assert "reasoning_details" not in used_body["messages"][0]


@pytest.mark.asyncio
async def test_create_stream_gives_up_when_the_second_404_repeats(
    open_router_provider,
):
    body = {
        "model": "openai/gpt-5.6-luna",
        "messages": [
            {
                "role": "assistant",
                "content": "ok",
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "zzz"}],
            }
        ],
    }
    create = AsyncMock(side_effect=_error(404, _OPENROUTER_404_BODY))

    with (
        patch.object(open_router_provider._client.chat.completions, "create", create),
        pytest.raises(openai.APIStatusError),
    ):
        await open_router_provider._create_stream(
            body,
            open_router_provider._admission.new_retry_session(),
        )

    # One strip retry only: the stripped body carries no opaque payload to remove.
    assert create.call_count == 2
