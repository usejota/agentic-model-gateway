"""Image-block detection for Anthropic messages.

When a user pastes an image in Claude Code, the request body contains an
``image`` content block (top-level on a user message, or nested inside a
``tool_result`` block when the Read tool returns an image). Most providers
support vision directly; text-only providers (DeepSeek, etc.) will 400.

:func:`has_images` is the single source of truth for "does this request carry
image content" used by the IMAGE_ROUTE reroute. It is provider-agnostic — no
imports from ``providers/``, only stdlib. Detection walks ``messages[].content``:

- ``str`` content → no images.
- ``list`` content → look for ``item.type == "image"`` at the top level, AND
  recurse into ``tool_result`` blocks whose ``content`` is itself a list
  containing ``type == "image"`` elements (Read-tool returns).

Accepts both raw dicts and Pydantic model instances (e.g. a validated
MessagesRequest's messages) and is safe on unvalidated input.
"""

from typing import Any


def _is_image_block(block: Any) -> bool:
    """True if the given content block is an image block (per Anthropic spec)."""
    if isinstance(block, dict):
        return block.get("type") == "image"
    return getattr(block, "type", None) == "image"


def _block_has_nested_image(block: Any) -> bool:
    """True if a ``tool_result`` block contains image elements in its content array."""
    if isinstance(block, dict):
        block_type = block.get("type")
        inner = block.get("content")
    else:
        block_type = getattr(block, "type", None)
        inner = getattr(block, "content", None)
    if block_type != "tool_result":
        return False
    return isinstance(inner, list) and any(_is_image_block(item) for item in inner)


def _iter_content_blocks(content: Any) -> list[Any]:
    """Normalize a message ``content`` field to a list of blocks."""
    if content is None:
        return []
    if isinstance(content, list):
        return content
    return [content]


def _message_content(message: Any) -> Any:
    """Return the content field of a message, whether dict or Pydantic model."""
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def has_images(messages: Any) -> bool:
    """Return True if any message has an image block (top-level or tool_result).

    Walks ``messages[].content`` and recurses into ``tool_result.content[]`` for
    nested images. Anything not shaped like the Anthropic Messages spec returns
    False.
    """
    if not isinstance(messages, list):
        return False
    for message in messages:
        for block in _iter_content_blocks(_message_content(message)):
            if _is_image_block(block) or _block_has_nested_image(block):
                return True
    return False
