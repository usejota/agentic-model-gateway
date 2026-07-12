"""Canonical Anthropic-style SSE sequence for provider-side streaming errors."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from core.anthropic.sse import SSEBuilder


def iter_provider_stream_error_sse_events(
    *,
    request: Any,
    input_tokens: int,
    error_message: str,
    sent_any_event: bool,
    log_raw_sse_events: bool,
    message_id: str | None = None,
    error_type: str = "api_error",
    message_id_from_stream: str | None = None,
) -> Iterator[str]:
    """Yield a top-level ``event: error`` to signal a streaming transport failure.

    The Anthropic SDK treats a top-level ``event: error`` (a frame whose event
    line is ``error`` and whose data is the standard ``{"type": "error",
    "error": {...}}`` envelope) as a transport-level failure, NOT as part of
    the assistant's response. Earlier versions of this helper emitted the
    error message as a ``content_block`` of type ``text`` — the Claude Code
    client then rendered that text as if the model had said it, killing the
    user's turn. The new top-level shape surfaces the failure as a real
    error, so the client (and the auto-mode fail-closed path) reacts
    correctly: the partial response is abandoned and the failure is reported.

    The ``message_id`` is included in the error data when known so the client
    can correlate the failure with any in-flight ``message_start`` already
    received. ``error_type`` follows the Anthropic ``ProviderError`` taxonomy
    (``api_error``, ``overloaded_error``, ``rate_limit_error``, ...).
    """
    mid = message_id or message_id_from_stream or f"msg_{uuid.uuid4()}"
    model = getattr(request, "model", "") or ""
    sse = SSEBuilder(
        mid,
        model,
        input_tokens,
        log_raw_events=log_raw_sse_events,
    )
    if not sent_any_event:
        yield sse.message_start()
    yield sse.emit_top_level_error(error_message, error_type=error_type)
    yield sse.message_delta("end_turn", 1)
    yield sse.message_stop()
