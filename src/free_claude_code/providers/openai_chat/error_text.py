"""Shared error introspection for OpenAI-compatible retry decisions."""

import json
from typing import Any


def error_status_code(error: Exception) -> int | None:
    """Return the HTTP status carried by an SDK error, if any."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) if response is not None else None
    return status if isinstance(status, int) else None


def error_text(error: Exception, *, include_response_text: bool = False) -> str:
    """Return the lowercased message, JSON body, and optional response text."""
    parts: list[Any] = [str(error)]
    body = getattr(error, "body", None)
    if body is not None:
        parts.append(json.dumps(body, default=str))
    if include_response_text:
        response = getattr(error, "response", None)
        text = getattr(response, "text", None) if response is not None else None
        if isinstance(text, str) and text:
            parts.append(text)
    return " ".join(str(part) for part in parts).lower()
