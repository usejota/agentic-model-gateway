"""Tests for ``core.anthropic.image_detection.has_images``.

Covers detection of top-level and tool_result-nested images, and safety on
unvalidated input. The module is pure — no provider knowledge, no I/O.
"""

from free_claude_code.core.anthropic.image_detection import has_images


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _image_block(media_type: str = "image/png", data: str = "BASE64DATA") -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def test_has_images_empty_messages() -> None:
    assert has_images([]) is False
    assert has_images(None) is False


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
    assert has_images({"not": "a list"}) is False
    assert has_images([{"role": "user", "content": 42}]) is False
    assert has_images("a string") is False
