from api.gateway_model_ids import (
    GATEWAY_MODEL_ID_PREFIX,
    NO_THINKING_GATEWAY_MODEL_ID_PREFIX,
    ONE_M_GATEWAY_MODEL_ID_PREFIX,
    decode_gateway_model_id,
    gateway_model_id,
    no_thinking_gateway_model_id,
    one_m_gateway_model_id,
)


def test_gateway_model_id_encodes_default_prefix():
    assert gateway_model_id("open_router/minimax/minimax-m3") == (
        f"{GATEWAY_MODEL_ID_PREFIX}/open_router/minimax/minimax-m3"
    )


def test_no_thinking_gateway_model_id_encodes_no_thinking_prefix():
    assert no_thinking_gateway_model_id("deepseek/deepseek-v4-pro") == (
        f"{NO_THINKING_GATEWAY_MODEL_ID_PREFIX}/deepseek/deepseek-v4-pro"
    )


def test_one_m_gateway_model_id_encodes_1m_prefix():
    assert one_m_gateway_model_id("open_router/minimax/minimax-m3") == (
        f"{ONE_M_GATEWAY_MODEL_ID_PREFIX}/open_router/minimax/minimax-m3"
    )


def test_decode_default_gateway_id():
    decoded = decode_gateway_model_id("anthropic/open_router/minimax/minimax-m3")

    assert decoded is not None
    assert decoded.provider_id == "open_router"
    assert decoded.provider_model == "minimax/minimax-m3"
    assert decoded.force_thinking_enabled is None
    assert decoded.one_m_context is False


def test_decode_no_thinking_gateway_id():
    decoded = decode_gateway_model_id(
        "claude-3-freecc-no-thinking/deepseek/deepseek-v4-pro"
    )

    assert decoded is not None
    assert decoded.provider_id == "deepseek"
    assert decoded.provider_model == "deepseek-v4-pro"
    assert decoded.force_thinking_enabled is False
    assert decoded.one_m_context is False


def test_decode_one_m_gateway_id():
    decoded = decode_gateway_model_id(
        "claude-3-freecc-1m/open_router/minimax/minimax-m3"
    )

    assert decoded is not None
    assert decoded.provider_id == "open_router"
    assert decoded.provider_model == "minimax/minimax-m3"
    assert decoded.force_thinking_enabled is None
    assert decoded.one_m_context is True


def test_decode_one_m_gateway_id_with_nested_model():
    decoded = decode_gateway_model_id(
        "claude-3-freecc-1m/open_router/minimax/minimax-m3"
    )

    assert decoded is not None
    assert decoded.provider_model == "minimax/minimax-m3"


def test_decode_returns_none_for_unknown_prefix():
    assert decode_gateway_model_id("unknown/open_router/model") is None


def test_decode_returns_none_for_plain_claude_model():
    assert decode_gateway_model_id("claude-sonnet-4-20250514") is None


def test_decode_returns_none_for_unprefixed_provider_model():
    assert decode_gateway_model_id("open_router/minimax/minimax-m3") is None


def test_decode_returns_none_for_missing_model_segment():
    assert decode_gateway_model_id("claude-3-freecc-1m/open_router") is None
