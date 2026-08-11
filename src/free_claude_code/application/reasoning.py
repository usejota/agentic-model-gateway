"""Resolve client reasoning input and FCC configuration exactly once."""

from collections.abc import Mapping
from typing import Any

from free_claude_code.config.reasoning import ReasoningPreference
from free_claude_code.core.anthropic.models import MessagesRequest, ThinkingConfig
from free_claude_code.core.reasoning import (
    ReasoningControl,
    ReasoningEffort,
    ReasoningPolicy,
)

# Anthropic protocol floor: extended thinking requires budget_tokens >= 1024
# and budget_tokens < max_tokens, so a request with max_tokens <= 1024 can
# never carry reasoning plus visible text. Background client calls (terminal
# titles, topic detection) use such tiny budgets; letting a reasoning-enabled
# route think there burns the whole budget and returns empty text.
MIN_REASONING_MAX_TOKENS = 1024


def resolve_reasoning_policy(
    request: MessagesRequest,
    preference: ReasoningPreference,
) -> ReasoningPolicy:
    """Apply one resolved configuration preference to the client request."""

    if preference is ReasoningPreference.INHERIT:
        raise ValueError("Reasoning preference must be resolved before application.")
    if preference is ReasoningPreference.OFF:
        return ReasoningPolicy.off()
    if (
        request.max_tokens is not None
        and request.max_tokens <= MIN_REASONING_MAX_TOKENS
    ):
        return ReasoningPolicy.off()
    if preference is not ReasoningPreference.CLIENT:
        return ReasoningPolicy.on(effort=ReasoningEffort(preference.value))
    return client_reasoning_policy(request)


def client_reasoning_policy(request: MessagesRequest) -> ReasoningPolicy:
    """Return the lossless reasoning intent expressed by one client request."""

    budget_tokens = _positive_budget(request.thinking)
    thinking_control = _thinking_control(
        request.thinking,
        budget_tokens=budget_tokens,
    )
    effort, effort_disables = _output_effort(request.output_config)

    if effort_disables:
        return ReasoningPolicy.off()
    if thinking_control is ReasoningControl.OFF:
        return ReasoningPolicy(
            control=ReasoningControl.OFF,
            effort=effort,
        )
    if thinking_control is ReasoningControl.ON or budget_tokens is not None:
        return ReasoningPolicy.on(
            effort=effort,
            budget_tokens=budget_tokens,
        )
    return ReasoningPolicy(
        control=ReasoningControl.DEFAULT,
        effort=effort,
    )


def _thinking_control(
    thinking: ThinkingConfig | None,
    *,
    budget_tokens: int | None,
) -> ReasoningControl:
    if thinking is None:
        return ReasoningControl.DEFAULT
    if thinking.type == "disabled" or (
        "enabled" in thinking.model_fields_set and thinking.enabled is False
    ):
        return ReasoningControl.OFF
    if (
        thinking.type in {"adaptive", "enabled"}
        or ("enabled" in thinking.model_fields_set and thinking.enabled is True)
        or budget_tokens is not None
    ):
        return ReasoningControl.ON
    return ReasoningControl.DEFAULT


def _output_effort(value: Any) -> tuple[ReasoningEffort | None, bool]:
    if not isinstance(value, Mapping):
        return None, False
    raw = value.get("effort")
    if not isinstance(raw, str):
        return None, False
    normalized = raw.strip().lower()
    if normalized == "none":
        return None, True
    try:
        return ReasoningEffort(normalized), False
    except ValueError:
        return None, False


def _positive_budget(thinking: ThinkingConfig | None) -> int | None:
    if thinking is None:
        return None
    value = thinking.budget_tokens
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None
