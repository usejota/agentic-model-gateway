"""Gateway-safe model id encoding for Claude Code model discovery."""

from __future__ import annotations

from dataclasses import dataclass

GATEWAY_MODEL_ID_PREFIX = "anthropic"

# Claude Code currently treats any model id containing ``claude-3-`` as not
# supporting thinking. This intentionally uses that client-side capability
# heuristic while keeping the real provider/model ref reversible for routing.
#
# Caveat (Claude Code internals, verified v2.1.190-2.1.195): the same ``claude-3-``
# substring that disables thinking also makes the context-window getter ignore
# ``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` (its guard is ``!normalize(id).startsWith
# ("claude-")``). So a no-thinking model can express only 200K or — via the
# ``[1m]`` suffix, which the getter checks first — 1M. Exact non-1M windows
# (256K/2M) require the thinking ``anthropic/…`` variant. The two behaviours
# share one cause and cannot be separated without losing the no-thinking signal.
NO_THINKING_GATEWAY_MODEL_ID_PREFIX = "claude-3-freecc-no-thinking"

# Suffix Claude Code's ``has1mContext()`` recognises (a regex ``/\[1m\]/i`` test on
# the model name it sent — endpoint-independent). Appended to the *client-facing*
# advertised id only and stripped before forwarding upstream: it is a Claude-Code
# signal that providers reject (OpenRouter 400s on ``minimax/minimax-m3[1m]``).
ONE_M_SUFFIX = "[1m]"


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
    """Return a thinking gateway id that signals a 1M context window to Claude Code.

    The ``[1m]`` suffix rides on the client-facing id only; :func:`decode_gateway_model_id`
    strips it so the upstream model id stays clean.
    """
    return f"{gateway_model_id(provider_model_ref)}{ONE_M_SUFFIX}"


def decode_gateway_model_id(model_name: str) -> DecodedGatewayModelId | None:
    """Decode a model id advertised by this gateway, if it is one."""
    one_m_context = model_name.endswith(ONE_M_SUFFIX)
    if one_m_context:
        model_name = model_name[: -len(ONE_M_SUFFIX)]

    prefix, separator, remainder = model_name.partition("/")
    if not separator:
        return None

    force_thinking_enabled: bool | None
    if prefix == GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = None
    elif prefix == NO_THINKING_GATEWAY_MODEL_ID_PREFIX:
        force_thinking_enabled = False
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
