"""Shared assertions for canonical provider streaming error envelopes."""

from core.anthropic.stream_contracts import parse_sse_text, text_content


def assert_canonical_stream_error_envelope(
    events: list[str], *, user_message_substr: str
) -> None:
    """Native transports emit a top-level ``event: error`` to signal a transport failure.

    The Anthropic SDK treats a top-level ``event: error`` (a frame whose event
    line is ``error`` and whose data is the standard ``{"type": "error",
    "error": {...}}`` envelope) as a transport-level failure, NOT as part of
    the assistant's response. Earlier versions of this contract emitted the
    error as a ``content_block`` of type ``text`` — Claude Code then rendered
    that text as if the model had said it, killing the user's turn. The new
    contract surfaces the failure as a real error.
    """
    blob = "".join(events)
    parsed = parse_sse_text(blob)
    # A top-level event:error frame MUST be present.
    error_events = [e for e in parsed if e.event == "error"]
    assert error_events, "expected a top-level event:error for transport failure"
    # The user-facing message must be in the error data.
    error_data = error_events[0].data
    error_envelope = error_data.get("error") if isinstance(error_data, dict) else None
    error_message = ""
    if isinstance(error_envelope, dict):
        error_message = str(error_envelope.get("message", ""))
    assert user_message_substr in error_message, (
        f"expected user_message_substr={user_message_substr!r} in error envelope "
        f"message={error_message!r}"
    )
    # No fake text-block error: the error message must not appear as
    # assistant text (which Claude Code would render as the model's answer).
    assert user_message_substr not in text_content(parsed), (
        f"error message {user_message_substr!r} was rendered as assistant text "
        "instead of as a top-level event:error"
    )
    # The stream must still bookend: message_start before the error,
    # message_stop after — so the client can correlate the error with the
    # in-flight message.
    event_names = [e.event for e in parsed]
    assert "message_start" in event_names, event_names
    assert event_names[-1] == "message_stop", event_names
