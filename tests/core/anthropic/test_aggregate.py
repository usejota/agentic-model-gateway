"""Tests for SSE-stream -> Messages JSON aggregation (non-streaming responses)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from core.anthropic.aggregate import aggregate_sse_to_message


def _evt(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_aggregates_text_and_usage() -> None:
    events = [
        _evt(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "backend/model-x",
                    "content": [],
                    "usage": {"input_tokens": 42, "output_tokens": 1},
                },
            },
        ),
        _evt(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _evt(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello "},
            },
        ),
        _evt(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "world"},
            },
        ),
        _evt("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _evt(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"input_tokens": 42, "output_tokens": 7},
            },
        ),
        _evt("message_stop", {"type": "message_stop"}),
    ]

    msg = await aggregate_sse_to_message(_stream(*events))

    assert msg["id"] == "msg_1"
    assert msg["model"] == "backend/model-x"
    assert msg["role"] == "assistant"
    assert msg["stop_reason"] == "end_turn"
    # The classifier reads usage.input_tokens — the whole point of this path.
    assert msg["usage"]["input_tokens"] == 42
    assert msg["usage"]["output_tokens"] == 7
    assert msg["content"] == [{"type": "text", "text": "Hello world"}]


@pytest.mark.asyncio
async def test_aggregates_tool_use_block() -> None:
    events = [
        _evt(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_2",
                    "model": "m",
                    "role": "assistant",
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                },
            },
        ),
        _evt(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "Bash",
                    "input": {},
                },
            },
        ),
        _evt(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"command":'},
            },
        ),
        _evt(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": ' "ls"}'},
            },
        ),
        _evt("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _evt(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        ),
    ]

    msg = await aggregate_sse_to_message(_stream(*events))

    assert msg["stop_reason"] == "tool_use"
    block = msg["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "Bash"
    assert block["input"] == {"command": "ls"}


@pytest.mark.asyncio
async def test_always_has_usage_even_on_sparse_stream() -> None:
    # A minimal/garbled stream must still yield a valid usage object so clients
    # reading usage.input_tokens don't crash.
    msg = await aggregate_sse_to_message(_stream("event: ping\ndata: [DONE]\n\n"))
    assert "usage" in msg
    assert msg["usage"]["input_tokens"] == 0
    assert msg["content"] == []


@pytest.mark.asyncio
async def test_top_level_event_error_raises_to_fail_closed() -> None:
    """A top-level ``event: error`` is a transport failure, NOT assistant text.

    The aggregator must raise (not return 200 with an empty body), so the
    auto-mode safety classifier / count_tokens path stays fail-closed. A
    silent 200 with content=[] would let auto-mode's token check pass on
    a stream that actually died.
    """
    from core.anthropic.errors import StreamErrorEnvelope

    events = [
        _evt(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_err",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "test-model",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        _evt(
            "error",
            {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "Provider stream ended without message_stop.",
                },
                "message_id": "msg_err",
            },
        ),
    ]
    with pytest.raises(StreamErrorEnvelope) as exc_info:
        await aggregate_sse_to_message(_stream(*events))
    # The envelope is preserved so the caller can render a stable message.
    assert (
        exc_info.value.envelope["error"]["message"]
        == "Provider stream ended without message_stop."
    )


@pytest.mark.asyncio
async def test_event_error_with_partial_response_still_fails_closed() -> None:
    """Even with a prior content_block_start, a top-level event:error must
    raise. The auto-mode classifier path must not return a partial response
    that looks like the model said something.
    """
    from core.anthropic.errors import StreamErrorEnvelope

    events = [
        _evt(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_partial",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "test-model",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        _evt(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _evt(
            "error",
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "truncated"},
                "message_id": "msg_partial",
            },
        ),
    ]
    with pytest.raises(StreamErrorEnvelope):
        await aggregate_sse_to_message(_stream(*events))
