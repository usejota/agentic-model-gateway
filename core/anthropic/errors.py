"""User-facing error formatting shared by API, providers, and integrations."""

import httpx
import openai


class StreamErrorEnvelope(Exception):
    """Raised when a provider SSE stream ends with a top-level ``event: error``.

    The Anthropic SDK surfaces a top-level ``event: error`` as a real
    transport failure (the partial response is abandoned, the error is
    reported). The aggregator in :mod:`core.anthropic.aggregate` raises this
    on the same signal so the non-streaming Messages path (the auto-mode
    safety classifier, ``count_tokens``, etc.) stays fail-closed: a stream
    that ends in a transport error returns an HTTP 5xx to the client, not a
    200 with an empty body the classifier would silently accept.

    The ``envelope`` is the parsed ``data:`` payload of the event so the
    caller's :func:`get_user_facing_error_message` can render a stable
    provider message.
    """

    def __init__(self, envelope: dict) -> None:
        self.envelope = envelope
        message = ""
        if isinstance(envelope, dict):
            inner = envelope.get("error")
            if isinstance(inner, dict):
                message = str(inner.get("message", "") or "")
        super().__init__(message or "Provider stream returned event:error")


def get_user_facing_error_message(
    e: Exception,
    *,
    read_timeout_s: float | None = None,
) -> str:
    """Return a readable, non-empty error message for users.

    Known transport and OpenAI SDK exception types are mapped to stable wording
    before falling back to ``str(e)``, so empty or noisy SDK messages do not skip
    the mapped path.
    """
    if isinstance(e, httpx.ReadTimeout):
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Provider request timed out."
    if isinstance(e, httpx.ConnectTimeout):
        return "Could not connect to provider."
    if isinstance(e, TimeoutError):
        if read_timeout_s is not None:
            return f"Provider request timed out after {read_timeout_s:g}s."
        return "Request timed out."

    if isinstance(e, openai.RateLimitError):
        return "Provider rate limit reached. Please retry shortly."
    if isinstance(e, openai.AuthenticationError):
        return "Provider authentication failed. Check API key."
    if isinstance(e, openai.BadRequestError):
        return "Invalid request sent to provider."

    name = type(e).__name__
    status_code = getattr(e, "status_code", None)
    if name == "RateLimitError":
        return "Provider rate limit reached. Please retry shortly."
    if name == "AuthenticationError":
        return "Provider authentication failed. Check API key."
    if name == "InvalidRequestError":
        return "Invalid request sent to provider."
    if name == "OverloadedError":
        return "Provider is currently overloaded. Please retry."
    if name == "APIError":
        if status_code in (502, 503, 504):
            return "Provider is temporarily unavailable. Please retry."
        return "Provider API request failed."
    if name.endswith("ProviderError") or name == "ProviderError":
        return "Provider request failed."

    message = str(e).strip()
    if message:
        return message

    return "Provider request failed unexpectedly."


def format_user_error_preview(exc: Exception, *, max_len: int = 200) -> str:
    """Truncate a user-facing error string for short chat replies."""
    return get_user_facing_error_message(exc)[:max_len]


def append_request_id(message: str, request_id: str | None) -> str:
    """Append request_id suffix when available."""
    base = message.strip() or "Provider request failed unexpectedly."
    if request_id:
        return f"{base} (request_id={request_id})"
    return base
