"""Gateway-safe model id encoding for Claude Code model discovery."""

from __future__ import annotations

from dataclasses import dataclass

GATEWAY_MODEL_ID_PREFIX = "anthropic"

# Claude Code currently treats any model id containing ``claude-3-`` as not
# supporting thinking. This intentionally uses that client-side capability
# heuristic while keeping the real provider/model ref reversible for routing.
NO_THINKING_GATEWAY_MODEL_ID_PREFIX = "claude-3-freecc-no-thinking"

# Sibling prefix that signals a 1M-context model. Claude Code's
# ``has1mContext()`` (gated to first-party Anthropic endpoint — see
# github.com/anthropics/claude-code issue #46416) parses the model name *it
# sent* and checks for a ``[1m]`` suffix. To surface the 1M ceiling to
# Claude Code when routing through this proxy, we advertise a 1M-context
# variant under this prefix and append ``[1m]`` to the model string before
# forwarding to upstream.
ONE_M_GATEWAY_MODEL_ID_PREFIX = "claude-3-freecc-1m"


@dataclass(frozen=True, slots=True)
class DecodedGatewayModelId:
    provider_id: str
    provider_model: str
    force_thinking_enabled: bool | None = None
    one_m_context: bool = False


def gateway_model_id(provider_model_ref: str) -> str:
    """Return the normal Claude Code-discoverable id for a provider/model ref."""
    return f"{GATEWAY_MODEL_ID_PREFIX}/{provider_model_ref}"


def no_thinking_gateway_model_id(provider_model_ref: str) -> str:
    """Return a Claude Code-discoverable id that disables client thinking."""
    return f"{NO_THINKING_GATEWAY_MODEL_ID_PREFIX}/{provider_model_ref}"


def one_m_gateway_model_id(provider_model_ref: str) -> str:
    """Return a Claude Code-discoverable id that signals a 1M-context model."""
    return f"{ONE_M_GATEWAY_MODEL_ID_PREFIX}/{provider_model_ref}"


def decode_gateway_model_id(model_name: str) -> DecodedGatewayModelId | None:
    """Decode a model id advertised by this gateway, if it is one."""
    prefix, separator, remainder = model_name.partition("/")
    if not separator:
        return None

    force_thinking_enabled: bool | None
    one_m_context: bool
    if prefix == GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = None
        one_m_context = False
    elif prefix == NO_THINKING_GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = False
        one_m_context = False
    elif prefix == ONE_M_GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = None
        one_m_context = True
    else:
        return None

    provider_id, provider_separator, provider_model = remainder.partition("/")
    if not provider_separator or not provider_model:
        return None

    return DecodedGatewayModelId(
        provider_id=provider_id,
        provider_model=provider_model,
        force_thinking_enabled=force_thinking_enabled,
        one_m_context=one_m_context,
    )
