"""Tests for ``core.anthropic.image_detection``.

Covers: detection (top-level + nested), placeholder strip round-trip, and
cache scoping. The module is pure — no provider knowledge, no I/O.
"""

from __future__ import annotations

import copy

from core.anthropic.image_detection import (
    ImageCache,
    has_images,
    restore_images,
    strip_to_placeholders,
)


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _image_block(media_type: str = "image/png", data: str = "BASE64DATA") -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


# --------------------------------------------------------------------------
# has_images
# --------------------------------------------------------------------------
def test_has_images_empty_messages() -> None:
    assert has_images([]) is False
    assert has_images(None) is False  # type: ignore[arg-type]


def test_has_images_string_content() -> None:
    assert has_images([{"role": "user", "content": "hello"}]) is False


def test_has_images_text_only_list_content() -> None:
    msgs = [{"role": "user", "content": [_text_block("hi"), _text_block("there")]}]
    assert has_images(msgs) is False


def test_has_images_top_level_image() -> None:
    msgs = [{"role": "user", "content": [_text_block("what's this?"), _image_block()]}]
    assert has_images(msgs) is True


def test_has_images_tool_result_with_nested_image() -> None:
    """Read tool returns: tool_result.content is a list with an image element."""
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [_text_block("read result"), _image_block()],
                }
            ],
        },
    ]
    assert has_images(msgs) is True


def test_has_images_tool_result_text_only_is_false() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "plain text result",
                }
            ],
        },
    ]
    assert has_images(msgs) is False


def test_has_images_unvalidated_input_is_safe() -> None:
    """Anything that doesn't match the spec returns False, never raises."""
    assert has_images({"not": "a list"}) is False  # type: ignore[arg-type]
    assert has_images([{"role": "user", "content": 42}]) is False  # type: ignore[list-item]
    assert has_images("a string") is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# strip_to_placeholders
# --------------------------------------------------------------------------
def test_strip_no_images_returns_original() -> None:
    msgs = [{"role": "user", "content": "hello"}]
    out, cache = strip_to_placeholders(msgs)
    assert out == msgs
    assert len(cache) == 0


def test_strip_replaces_top_level_image_with_placeholder() -> None:
    img = _image_block(data="PNG_BYTES")
    msgs = [{"role": "user", "content": [_text_block("what is this?"), img]}]
    out, cache = strip_to_placeholders(msgs)

    assert len(cache) == 1
    assert cache.get(1) == {
        "type": "base64",
        "media_type": "image/png",
        "data": "PNG_BYTES",
    }

    new_blocks = out[0]["content"]
    assert len(new_blocks) == 2
    assert new_blocks[0] == _text_block("what is this?")
    placeholder = new_blocks[1]
    assert placeholder["type"] == "text"
    assert placeholder["text"].startswith("[Image #1]This is an image")
    assert "extract the imageId" in placeholder["text"]


def test_strip_multiple_images_assigns_ids_monotonically() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                _image_block(data="A"),
                _text_block("middle"),
                _image_block(data="B"),
            ],
        },
        {"role": "user", "content": [_image_block(data="C")]},
    ]
    out, cache = strip_to_placeholders(msgs)

    assert len(cache) == 3
    src1 = cache.get(1)
    assert src1 is not None
    assert src1["data"] == "A"
    src2 = cache.get(2)
    assert src2 is not None
    assert src2["data"] == "B"
    src3 = cache.get(3)
    assert src3 is not None
    assert src3["data"] == "C"

    first_msg_blocks = out[0]["content"]
    assert first_msg_blocks[0]["text"].startswith("[Image #1]")
    assert first_msg_blocks[1] == _text_block("middle")
    assert first_msg_blocks[2]["text"].startswith("[Image #2]")

    second_msg_blocks = out[1]["content"]
    assert second_msg_blocks[0]["text"].startswith("[Image #3]")


def test_strip_tool_result_preserves_non_image_content() -> None:
    """Read tool: image element becomes a placeholder text; text siblings stay."""
    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [
                        _text_block("file contents"),
                        _image_block(data="READPNG"),
                    ],
                }
            ],
        }
    ]
    out, cache = strip_to_placeholders(msgs)

    assert len(cache) == 1
    src_read = cache.get(1)
    assert src_read is not None
    assert src_read["data"] == "READPNG"

    tool_result = out[0]["content"][0]
    new_inner = tool_result["content"]
    assert len(new_inner) == 2
    assert new_inner[0] == _text_block("file contents")
    assert new_inner[1]["text"].startswith("[Image #1]")


def test_strip_does_not_mutate_input() -> None:
    img = _image_block(data="ORIG")
    msgs = [{"role": "user", "content": [img]}]
    snapshot = copy.deepcopy(msgs)
    strip_to_placeholders(msgs)
    assert msgs == snapshot


def test_strip_string_content_preserves_shape() -> None:
    """If content was a string, the output keeps it a string."""
    msgs = [{"role": "user", "content": "just text"}]
    out, _ = strip_to_placeholders(msgs)
    assert out[0]["content"] == "just text"


def test_image_cache_is_request_scoped() -> None:
    """No implicit sharing — each strip call gets its own cache by default."""
    msgs = [{"role": "user", "content": [_image_block(data="X")]}]
    _, cache_a = strip_to_placeholders(msgs)
    _, cache_b = strip_to_placeholders(msgs)
    assert cache_a is not cache_b
    assert len(cache_a) == 1
    assert len(cache_b) == 1


def test_image_cache_explicit_instance_is_used() -> None:
    cache = ImageCache()
    msgs = [{"role": "user", "content": [_image_block(data="X")]}]
    _, returned = strip_to_placeholders(msgs, cache=cache)
    assert returned is cache
    assert 1 in cache


# --------------------------------------------------------------------------
# restore_images
# --------------------------------------------------------------------------
def test_restore_round_trip_recovers_original_blocks() -> None:
    original = [
        _text_block("what is this?"),
        _image_block(data="PNG_BYTES"),
        _text_block("and this?"),
        _image_block(data="OTHER"),
    ]
    msgs = [{"role": "user", "content": list(original)}]
    stripped, cache = strip_to_placeholders(msgs)
    assert has_images(stripped) is False

    restored = restore_images(stripped, cache)
    assert restored[0]["content"] == original


def test_restore_no_cache_returns_input_unchanged() -> None:
    msgs = [{"role": "user", "content": [_text_block("hi")]}]
    out = restore_images(msgs, ImageCache())
    assert out == msgs


def test_restore_preserves_string_content() -> None:
    msgs = [{"role": "user", "content": "no images here"}]
    out = restore_images(msgs, ImageCache())
    assert out[0]["content"] == "no images here"
