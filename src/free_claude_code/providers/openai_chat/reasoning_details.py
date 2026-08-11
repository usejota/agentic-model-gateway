"""OpenRouter-format structured reasoning replay and stream conversion."""

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Literal

from free_claude_code.core.anthropic import (
    is_synthetic_openai_tool_turn_boundary,
)
from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.core.anthropic.streaming import AnthropicStreamLedger
from free_claude_code.core.reasoning import ReasoningPolicy

from .error_text import error_status_code, error_text

# Reasoning-detail kinds whose payload is opaque and endpoint-bound.
_OPAQUE_REASONING_KINDS = ("encrypted", "redacted", "compaction")

# OpenRouter pins an encrypted reasoning payload to the endpoint that produced it
# and answers 404 once a later turn of the same conversation lands elsewhere (its
# endpoint choice moves as the prompt grows). Replay is best-effort, so the fix is
# to drop the opaque items and retry the turn once without them.
_ENCRYPTED_REPLAY_REJECTION_MARKERS = ("encrypted reasoning", "encrypted payload")


def is_encrypted_reasoning_replay_rejection(error: Exception) -> bool:
    """Return whether upstream refused a replayed opaque reasoning payload."""
    if error_status_code(error) != 404:
        return False
    text = error_text(error)
    return any(marker in text for marker in _ENCRYPTED_REPLAY_REJECTION_MARKERS)


def clone_without_encrypted_reasoning(
    body: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a clone with replayed opaque reasoning details removed.

    Returns ``None`` when the body replays none, so callers skip a pointless
    identical retry.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return None

    retry_messages: list[Any] = []
    stripped = False
    for message in messages:
        details = (
            message.get("reasoning_details") if isinstance(message, dict) else None
        )
        if not isinstance(details, list) or not any(
            _is_opaque_reasoning_detail(detail) for detail in details
        ):
            retry_messages.append(message)
            continue
        stripped = True
        retained = [
            detail for detail in details if not _is_opaque_reasoning_detail(detail)
        ]
        retry_message = dict(message)
        if retained:
            retry_message["reasoning_details"] = retained
        else:
            retry_message.pop("reasoning_details", None)
        retry_messages.append(retry_message)

    if not stripped:
        return None
    return {**body, "messages": retry_messages}


def _is_opaque_reasoning_detail(detail: Any) -> bool:
    kind = str(_field(detail, "type") or "").lower()
    return any(opaque in kind for opaque in _OPAQUE_REASONING_KINDS)


def apply_reasoning_details_replay(
    body: dict[str, Any], request: MessagesRequest, _policy: ReasoningPolicy
) -> None:
    """Replay opaque reasoning details on their converted assistant messages."""
    assistant_details = _assistant_reasoning_details(request.messages)
    if not assistant_details:
        return
    messages = body.get("messages")
    if not isinstance(messages, list):
        return

    cursor = 0
    for details in assistant_details:
        for index in range(cursor, len(messages)):
            message = messages[index]
            if (
                not isinstance(message, dict)
                or message.get("role") != "assistant"
                or is_synthetic_openai_tool_turn_boundary(message)
            ):
                continue
            existing = message.get("reasoning_details")
            if isinstance(existing, list):
                existing.extend(details)
            else:
                message["reasoning_details"] = list(details)
            cursor = index + 1
            break


class StructuredReasoningStream:
    """Reconcile alternate plaintext reasoning representations for one stream."""

    def __init__(self) -> None:
        self._text_source: Literal["native", "details"] | None = None

    def events(
        self,
        delta: Any,
        ledger: AnthropicStreamLedger,
        *,
        native_reasoning: str | None,
    ) -> Iterator[str]:
        """Emit plaintext once while preserving every opaque reasoning detail."""
        details = _reasoning_details(delta)
        if self._text_source is None:
            if native_reasoning:
                self._text_source = "native"
            elif any(_reasoning_detail_text(detail) for detail in details):
                self._text_source = "details"

        if self._text_source == "native" and native_reasoning:
            yield from ledger.ensure_thinking_block()
            yield ledger.emit_thinking_delta(native_reasoning)

        for detail in details:
            preserved = _preserved_reasoning_detail(detail)
            if preserved:
                yield from ledger.close_content_blocks()
                index = ledger.blocks.allocate_index()
                yield ledger.content_block_start(
                    index,
                    "redacted_thinking",
                    data=preserved,
                )
                yield ledger.content_block_stop(index)
                continue
            if self._text_source != "details":
                continue
            text = _reasoning_detail_text(detail)
            if not text:
                continue
            yield from ledger.ensure_thinking_block()
            yield ledger.emit_thinking_delta(text)


def _reasoning_details(delta: Any) -> Sequence[Any]:
    details = _field(delta, "reasoning_details")
    if details is None:
        extra = _field(delta, "model_extra")
        if isinstance(extra, Mapping):
            details = extra.get("reasoning_details")
    return details if _is_sequence(details) else ()


def _assistant_reasoning_details(messages: Any) -> list[list[dict[str, Any]]]:
    if not _is_sequence(messages):
        return []
    result: list[list[dict[str, Any]]] = []
    for message in messages:
        if _field(message, "role") != "assistant":
            continue
        details = _redacted_reasoning_details(_field(message, "content"))
        if details:
            result.append(details)
    return result


def _redacted_reasoning_details(content: Any) -> list[dict[str, Any]]:
    if not _is_sequence(content):
        return []
    details: list[dict[str, Any]] = []
    for block in content:
        if _field(block, "type") != "redacted_thinking":
            continue
        data = _field(block, "data")
        if not isinstance(data, str) or not data:
            continue
        parsed = _json_payload(data)
        if isinstance(parsed, list):
            details.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            details.append(parsed)
        else:
            details.append({"type": "reasoning.encrypted", "data": data})
    return details


def _reasoning_detail_text(detail: Any) -> str | None:
    kind = str(_field(detail, "type") or "").lower()
    if "encrypted" in kind or "redacted" in kind:
        return None
    for key in ("text", "content", "reasoning"):
        value = _field(detail, key)
        if isinstance(value, str) and value:
            return value
    return None


def _preserved_reasoning_detail(detail: Any) -> str | None:
    if not isinstance(detail, Mapping):
        return None
    kind = str(_field(detail, "type") or "").lower()
    if (
        "encrypted" in kind
        or "redacted" in kind
        or "summary" in kind
        or _reasoning_detail_text(detail) is None
    ):
        return json.dumps(dict(detail), separators=(",", ":"))
    return None


def _json_payload(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, str | bytes | bytearray
    )
