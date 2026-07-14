"""Aggregate an Anthropic SSE event stream into a single Messages JSON response.

The gateway is streaming-first: providers emit Anthropic-style SSE events. Some
clients (notably Claude Code's auto-mode safety classifier and session-title
"side queries") send ``stream: false`` and expect ONE non-streaming Messages JSON
body with a populated ``usage`` object. Reading ``usage.input_tokens`` off an SSE
stream fails (``undefined is not an object``), which makes auto mode block.

This module collapses the SSE events we already produce back into the equivalent
Messages response object, so non-streaming requests get the shape they expect.
It is provider-agnostic — every provider funnels through the same SSE format.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from .errors import StreamErrorEnvelope


def _parse_sse(chunk: str) -> list[dict[str, Any]]:
    """Parse ``data:`` JSON payloads out of one SSE text chunk.

    A chunk may contain multiple ``event:/data:`` blocks. Non-JSON / sentinel
    lines (e.g. ``[DONE]``) are skipped.
    """
    events: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


async def aggregate_sse_to_message(
    stream: AsyncIterator[str],
) -> dict[str, Any]:
    """Consume an Anthropic SSE stream and return a Messages JSON response dict.

    Rebuilds content blocks (text / thinking / tool_use), ``stop_reason``,
    ``model``, and ``usage`` from the standard event sequence
    (``message_start`` → ``content_block_*`` → ``message_delta`` →
    ``message_stop``). Missing pieces degrade gracefully to sane defaults so the
    response always carries a valid ``usage`` object.
    """
    message_id = "msg_unknown"
    model = ""
    role = "assistant"
    stop_reason: str | None = "end_turn"
    stop_sequence: str | None = None
    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}

    # index -> assembled content block
    blocks: dict[int, dict[str, Any]] = {}
    # index -> list of partial_json fragments for tool_use blocks
    tool_json_parts: dict[int, list[str]] = {}

    async for chunk in stream:
        for event in _parse_sse(chunk):
            etype = event.get("type")

            if etype == "message_start":
                message = event.get("message", {})
                message_id = message.get("id", message_id)
                model = message.get("model", model)
                role = message.get("role", role)
                if isinstance(message.get("usage"), dict):
                    usage.update(message["usage"])

            elif etype == "content_block_start":
                index = event.get("index", 0)
                block = dict(event.get("content_block", {}))
                blocks[index] = block
                if block.get("type") == "tool_use":
                    tool_json_parts.setdefault(index, [])

            elif etype == "content_block_delta":
                index = event.get("index", 0)
                delta = event.get("delta", {})
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                dtype = delta.get("type")
                if dtype == "text_delta":
                    block["text"] = block.get("text", "") + delta.get("text", "")
                elif dtype == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta.get(
                        "thinking", ""
                    )
                elif dtype == "input_json_delta":
                    tool_json_parts.setdefault(index, []).append(
                        delta.get("partial_json", "")
                    )

            elif etype == "message_delta":
                delta = event.get("delta", {})
                if "stop_reason" in delta:
                    stop_reason = delta.get("stop_reason")
                if "stop_sequence" in delta:
                    stop_sequence = delta.get("stop_sequence")
                if isinstance(event.get("usage"), dict):
                    usage.update(event["usage"])

            elif etype == "error":
                # A top-level ``event: error`` is a transport failure, NOT part
                # of the assistant's response. The non-streaming Messages path
                # (the auto-mode safety classifier, count_tokens, etc.) must
                # fail-closed: a 200 with an empty body would silently pass
                # auto-mode's token check. Raising here causes the route
                # handler to return HTTP 5xx (the same behavior as a streaming
                # client receiving the event from the SDK).
                raise StreamErrorEnvelope(event)

    # Finalize tool_use blocks: parse the accumulated partial_json into `input`.
    for index, parts in tool_json_parts.items():
        block = blocks.get(index)
        if block is None or block.get("type") != "tool_use":
            continue
        raw = "".join(parts)
        if raw:
            try:
                block["input"] = json.loads(raw)
            except json.JSONDecodeError:
                block["input"] = {}
        else:
            block.setdefault("input", {})

    content = [blocks[i] for i in sorted(blocks)]

    return {
        "id": message_id,
        "type": "message",
        "role": role,
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": stop_sequence,
        "usage": usage,
    }
