"""Unit tests for gateway model id encoding, incl. the [1m] 1M-context suffix."""

from free_claude_code.core.gateway_model_ids import (
    ONE_M_SUFFIX,
    decode_gateway_model_id,
    gateway_model_id,
    no_thinking_gateway_model_id,
    one_m_gateway_model_id,
)


def test_one_m_gateway_model_id_appends_suffix():
    assert one_m_gateway_model_id("open_router/minimax/minimax-m3") == (
        f"anthropic/open_router/minimax/minimax-m3{ONE_M_SUFFIX}"
    )


def test_decode_strips_1m_suffix_and_flags_context():
    decoded = decode_gateway_model_id("anthropic/open_router/minimax/minimax-m3[1m]")
    assert decoded is not None
    assert decoded.provider_id == "open_router"
    assert decoded.provider_model == "minimax/minimax-m3"
    assert decoded.one_m_context is True
    assert decoded.force_thinking_enabled is None


def test_decode_without_1m_suffix_leaves_context_false():
    decoded = decode_gateway_model_id(gateway_model_id("open_router/foo/bar"))
    assert decoded is not None
    assert decoded.provider_model == "foo/bar"
    assert decoded.one_m_context is False


def test_decode_no_thinking_variant_still_decodes():
    decoded = decode_gateway_model_id(no_thinking_gateway_model_id("deepseek/chat"))
    assert decoded is not None
    assert decoded.provider_id == "deepseek"
    assert decoded.provider_model == "chat"
    assert decoded.force_thinking_enabled is False


def test_decode_rejects_non_gateway_id():
    assert decode_gateway_model_id("gpt-4o") is None
