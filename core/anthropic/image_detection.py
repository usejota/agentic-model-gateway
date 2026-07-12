"""Image-block detection and placeholder handling for Anthropic messages.

When a user pastes an image in Claude Code, the request body contains an
``image`` content block (top-level on a user message, or nested inside a
``tool_result`` block when the Read tool returns an image). Most providers
support vision directly; text-only providers (DeepSeek, etc.) will 400.

This module is the single source of truth for "does this request have
images, and how do we hand them off cleanly". It is provider-agnostic — no
imports from ``providers/``, only from Pydantic models and stdlib.

Three operations:

- :func:`has_images` — does this request have any image content? Used by the
  service layer to decide whether to reroute.
- :func:`strip_to_placeholders` — replace each image block with a text
  placeholder ``[Image #N]This is an image, if you need to view or analyze
  it, you need to extract the imageId``, stashing the base64 source in an
  :class:`ImageCache` keyed by N.
- :func:`restore_images` — inverse of strip; used when the multimodal
  provider actually needs the real bytes.

The placeholder format matches the leaked claude-code-router pattern so a
text-only model can be told (via injected system prompt) to call a vision
tool if it needs to "see" something — see ``analyzeImage`` in upstream CCR
for the full tool pattern. We don't ship the tool in v1 (see plan), only
the reroute.

Detection walks ``messages[].content``:

- ``str`` content → no images.
- ``list`` content → look for ``item.type == "image"`` at the top level, AND
  recurse into ``tool_result`` blocks whose ``content`` is itself a list
  containing ``type == "image"`` elements (Read-tool returns).
"""

from __future__ import annotations

from typing import Any


def _is_image_block(block: Any) -> bool:
    """True if the given content block is an image block (per Anthropic spec)."""
    if isinstance(block, dict):
        return block.get("type") == "image"
    # Pydantic BaseModel (e.g. ContentBlockImage in api.models.anthropic).
    type_attr = getattr(block, "type", None)
    return type_attr == "image"


def _block_has_nested_image(block: Any) -> bool:
    """True if a ``tool_result`` block contains image elements in its content array.

    ``tool_result`` content can be either a string or a list of blocks. We only
    care about the list case where any element is an image.
    """
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
    """Normalize a message ``content`` field to a list of blocks.

    ``str`` content is a single text block; ``list`` content is already a
    sequence of blocks. Returns an empty list for ``None``/missing.
    """
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


def _message_role(message: Any) -> str | None:
    """Return the role field of a message, whether dict or Pydantic model.

    Returns ``None`` when the message is neither a dict with a ``role`` key nor
    an object with a ``role`` attribute (i.e. unvalidated input that doesn't
    match the Anthropic shape).
    """
    if isinstance(message, dict):
        role = message.get("role")
        return role if isinstance(role, str) else None
    role = getattr(message, "role", None)
    return role if isinstance(role, str) else None


def has_images(messages: Any) -> bool:
    """Return True if any message has an image block (top-level or tool_result).

    Walks ``messages[].content`` and recurses into ``tool_result.content[]``
    for nested images. Safe to call on unvalidated input — anything not shaped
    like the Anthropic Messages spec returns False. Accepts both raw dicts and
    Pydantic model instances (e.g. when called on a validated MessagesRequest).
    """
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, (dict, object)):
            continue
        for block in _iter_content_blocks(_message_content(message)):
            if _is_image_block(block) or _block_has_nested_image(block):
                return True
    return False


def has_image_in_last_user_turn(messages: Any) -> bool:
    """Return True only if the LAST ``role == "user"`` message carries an image.

    Used by the service-layer image reroute to decide whether the *current* turn
    needs the vision model. ``has_images`` (full-history) is too coarse: Claude
    Code re-sends the entire conversation on every turn, so a single image pasted
    one turn ago would otherwise keep every subsequent turn locked to the vision
    model — even text-only turns that just want a follow-up answer. The vision
    model streams long tool-heavy turns unreliably; the fix is to scope the
    reroute to the turn that actually carries the image.

    Returns False when the history has no user turn, or when the last user turn
    is text-only (string content or list of non-image blocks).
    """
    if not isinstance(messages, list):
        return False
    # Walk the messages in reverse; the most recent user turn is the one that
    # defines "this turn". Stops on the first match.
    for message in reversed(messages):
        role = _message_role(message)
        if role != "user":
            continue
        for block in _iter_content_blocks(_message_content(message)):
            if _is_image_block(block) or _block_has_nested_image(block):
                return True
        # Last user turn found but it carries no image.
        return False
    return False


class ImageCache:
    """Per-request stash of stripped image sources, keyed by integer image id.

    Image ids are 1-based and match the placeholder format produced by
    :func:`strip_to_placeholders`. The cache is request-scoped — create one
    per request, never share across requests, and let it go out of scope when
    the response is built.
    """

    def __init__(self) -> None:
        self._by_id: dict[int, dict[str, Any]] = {}

    def store(self, image_id: int, source: Any) -> None:
        """Remember the base64 source for image ``image_id``.

        If ``image_id`` was already stored, the original entry is preserved
        (first-write-wins). Re-storing would only happen on a re-strip of the
        same request, which shouldn't occur in normal flow.
        """
        self._by_id.setdefault(image_id, source)

    def get(self, image_id: int) -> Any | None:
        """Return the cached source for ``image_id``, or None if absent."""
        return self._by_id.get(image_id)

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, image_id: object) -> bool:
        return isinstance(image_id, int) and image_id in self._by_id


def _strip_top_level_images(
    blocks: list[Any], next_id: list[int], cache: ImageCache
) -> list[Any]:
    """Replace top-level ``image`` blocks with ``[Image #N]`` text placeholders.

    Mutates ``next_id[0]`` as images are consumed (1-based, monotonically
    increasing across the whole request). Non-image blocks are returned
    unchanged.
    """
    out: list[Any] = []
    for block in blocks:
        if _is_image_block(block):
            image_id = next_id[0]
            cache.store(image_id, block.get("source"))
            out.append(
                {
                    "type": "text",
                    "text": (
                        f"[Image #{image_id}]This is an image, if you need to view "
                        "or analyze it, you need to extract the imageId"
                    ),
                }
            )
            next_id[0] += 1
        else:
            out.append(block)
    return out


def _strip_tool_result_images(
    block: dict[str, Any], next_id: list[int], cache: ImageCache
) -> dict[str, Any]:
    """Replace ``tool_result`` content lists containing images with placeholders.

    Each image element becomes a top-level text placeholder appended after the
    surviving non-image elements of the tool_result content list. The tool
    result itself is preserved (Claude Code's Read tool needs to see the
    result come back); only the image elements are replaced.
    """
    inner = block.get("content")
    if not isinstance(inner, list):
        return block
    new_inner: list[Any] = []
    placeholders: list[dict[str, Any]] = []
    for item in inner:
        if _is_image_block(item):
            image_id = next_id[0]
            cache.store(image_id, item.get("source"))
            placeholders.append(
                {
                    "type": "text",
                    "text": (
                        f"[Image #{image_id}]This is an image, if you need to view "
                        "or analyze it, you need to extract the imageId"
                    ),
                }
            )
            next_id[0] += 1
        else:
            new_inner.append(item)
    if not placeholders:
        return block
    new_block = dict(block)
    new_block["content"] = new_inner + placeholders
    return new_block


def strip_to_placeholders(
    messages: Any, cache: ImageCache | None = None
) -> tuple[list[Any], ImageCache]:
    """Replace all image blocks with ``[Image #N]`` text placeholders.

    Walks each message's content; for ``tool_result`` blocks whose content
    list contains images, the images are replaced with appended text
    placeholders (the tool result body itself is preserved). Returns the new
    messages list and the cache used (created if not provided). The cache
    holds the original ``source`` dicts by image id, in the order they
    appeared (1, 2, 3, …).

    If no images are found, returns the original list unchanged and an empty
    cache. The input is not mutated — deep-style replacement is used.
    """
    if cache is None:
        cache = ImageCache()
    if not isinstance(messages, list):
        return list(messages) if messages is not None else [], cache

    # Short-circuit when nothing to do (common case — no images).
    if not has_images(messages):
        return list(messages), cache

    next_id = [1]
    new_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            new_messages.append(message)
            continue
        content = message.get("content")
        blocks = _iter_content_blocks(content)
        new_blocks: list[Any] = []
        for block in blocks:
            if _is_image_block(block):
                image_id = next_id[0]
                cache.store(image_id, block.get("source"))
                new_blocks.append(
                    {
                        "type": "text",
                        "text": (
                            f"[Image #{image_id}]This is an image, if you need to "
                            "view or analyze it, you need to extract the imageId"
                        ),
                    }
                )
                next_id[0] += 1
            elif _block_has_nested_image(block):
                new_blocks.append(_strip_tool_result_images(block, next_id, cache))
            else:
                new_blocks.append(block)
        new_message = dict(message)
        # When ``content`` was a string, ``_iter_content_blocks`` wrapped it in
        # a list. Restore the original shape for round-trip fidelity.
        if isinstance(content, str):
            new_message["content"] = content
        else:
            new_message["content"] = new_blocks
        new_messages.append(new_message)
    return new_messages, cache


def restore_images(messages: Any, cache: ImageCache) -> list[Any]:
    """Inverse of :func:`strip_to_placeholders`.

    Replaces text placeholders ``[Image #N]…`` produced by stripping with
    the original image blocks stashed in ``cache``. Placeholders whose id is
    not in the cache are left as text (so a half-stripped request doesn't
    silently lose information).

    Currently unused by the v1 reroute (we reroute the **whole** request to
    the multimodal provider, never stripping). Kept here because the
    analyzeImage tool pattern (v2) will need it: the text-only model will
    hand back an imageId in a tool_use, and the multimodal re-dispatch will
    need to swap the placeholder for the real bytes.

    A placeholder text block produced by stripping always starts with
    ``[Image #N]`` and contains nothing else, so we match it as a whole and
    emit one image block in its place. Any prefix text on the block (text
    before the marker) is preserved as a separate text block.
    """
    if not isinstance(messages, list):
        return messages
    if len(cache) == 0:
        return list(messages)
    new_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            new_messages.append(message)
            continue
        content = message.get("content")
        blocks = _iter_content_blocks(content)
        restored: list[Any] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                restored.append(block)
                continue
            text = block.get("text", "")
            if not isinstance(text, str) or not text.startswith("[Image #"):
                restored.append(block)
                continue
            num_start = len("[Image #")
            num_end = num_start
            while num_end < len(text) and text[num_end].isdigit():
                num_end += 1
            if num_end == num_start or num_end >= len(text) or text[num_end] != "]":
                restored.append(block)
                continue
            image_id = int(text[num_start:num_end])
            source = cache.get(image_id)
            if source is None:
                # Unknown id — keep the placeholder as text rather than
                # silently lose the image reference.
                restored.append(block)
                continue
            restored.append({"type": "image", "source": source})
        new_message = dict(message)
        if isinstance(content, str):
            new_message["content"] = content
        else:
            new_message["content"] = restored
        new_messages.append(new_message)
    return new_messages
