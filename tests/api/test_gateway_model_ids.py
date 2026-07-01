"""Tests for gateway model-id encoding/decoding, including the [1m] 1M signal."""

from api.gateway_model_ids import (
    ONE_M_SUFFIX,
    DecodedGatewayModelId,
    decode_gateway_model_id,
    gateway_model_id,
    no_thinking_gateway_model_id,
    one_m_gateway_model_id,
)


def test_gateway_model_id_round_trips_without_one_m():
    encoded = gateway_model_id("open_router/minimax/minimax-m3")
    assert encoded == "anthropic/open_router/minimax/minimax-m3"

    decoded = decode_gateway_model_id(encoded)
    assert decoded == DecodedGatewayModelId(
        provider_id="open_router",
        provider_model="minimax/minimax-m3",
        force_thinking_enabled=None,
        one_m_context=False,
    )


def test_no_thinking_gateway_model_id_round_trips():
    encoded = no_thinking_gateway_model_id("deepseek/deepseek-chat")
    assert encoded == "claude-3-freecc-no-thinking/deepseek/deepseek-chat"

    decoded = decode_gateway_model_id(encoded)
    assert decoded is not None
    assert decoded.force_thinking_enabled is False
    assert decoded.one_m_context is False
    assert decoded.provider_id == "deepseek"
    assert decoded.provider_model == "deepseek-chat"


def test_one_m_gateway_model_id_appends_suffix_to_thinking_id():
    encoded = one_m_gateway_model_id("open_router/minimax/minimax-m3")
    assert encoded == "anthropic/open_router/minimax/minimax-m3[1m]"
    assert encoded.endswith(ONE_M_SUFFIX)


def test_decode_strips_one_m_suffix_and_flags_context():
    decoded = decode_gateway_model_id("anthropic/open_router/minimax/minimax-m3[1m]")
    assert decoded == DecodedGatewayModelId(
        provider_id="open_router",
        provider_model="minimax/minimax-m3",
        force_thinking_enabled=None,
        one_m_context=True,
    )
    # The stripped provider_model is clean for upstream (no [1m]).
    assert decoded is not None
    assert ONE_M_SUFFIX not in decoded.provider_model


def test_decode_one_m_round_trip_via_encoder():
    ref = "open_router/minimax/minimax-m3"
    decoded = decode_gateway_model_id(one_m_gateway_model_id(ref))
    assert decoded is not None
    assert decoded.one_m_context is True
    assert decoded.provider_id == "open_router"
    assert decoded.provider_model == "minimax/minimax-m3"


def test_one_m_suffix_works_on_no_thinking_variant():
    decoded = decode_gateway_model_id(
        "claude-3-freecc-no-thinking/deepseek/deepseek-v4-pro[1m]"
    )
    assert decoded is not None
    assert decoded.force_thinking_enabled is False
    assert decoded.one_m_context is True
    assert decoded.provider_id == "deepseek"
    assert decoded.provider_model == "deepseek-v4-pro"


def test_decode_rejects_non_gateway_ids():
    assert decode_gateway_model_id("claude-opus-4-20250514") is None
    assert decode_gateway_model_id("open_router/minimax/minimax-m3") is None
    # A [1m] suffix on a non-gateway id is not decoded here (handled by the router).
    assert decode_gateway_model_id("open_router/minimax/minimax-m3[1m]") is None
