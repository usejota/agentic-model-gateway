"""Provider-owned reasoning translations for OpenAI-compatible APIs."""

from dataclasses import dataclass
from typing import Any, Protocol

from free_claude_code.core.reasoning import (
    ReasoningControl,
    ReasoningEffort,
    ReasoningPolicy,
)

EffortValues = tuple[tuple[ReasoningEffort, str], ...]


class ReasoningEncoder(Protocol):
    """Translate provider-neutral reasoning intent into one wire shape."""

    def encode(self, body: dict[str, Any], policy: ReasoningPolicy) -> None: ...


@dataclass(frozen=True, slots=True)
class ReasoningObject:
    """Encode gateways that accept a top-level ``reasoning`` object."""

    efforts: EffortValues
    supports_budget: bool = True

    def encode(self, body: dict[str, Any], policy: ReasoningPolicy) -> None:
        if policy.control is ReasoningControl.OFF:
            _extra_body(body)["reasoning"] = {"enabled": False}
            return

        reasoning: dict[str, Any] = {}
        if policy.budget_tokens is not None and self.supports_budget:
            reasoning["max_tokens"] = policy.budget_tokens
        elif effort := dict(self.efforts).get(policy.effort):
            reasoning["effort"] = effort
        elif policy.control is ReasoningControl.ON:
            reasoning["enabled"] = True

        if reasoning:
            _extra_body(body)["reasoning"] = reasoning


def _extra_body(body: dict[str, Any]) -> dict[str, Any]:
    value = body.setdefault("extra_body", {})
    if not isinstance(value, dict):
        raise TypeError("OpenAI extra_body must be an object.")
    return value
