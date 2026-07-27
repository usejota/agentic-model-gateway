import pytest

from free_claude_code.core.reasoning import ReasoningEffort, ReasoningPolicy
from free_claude_code.providers.openai_chat.reasoning import ReasoningObject

_EFFORTS = (
    (ReasoningEffort.LOW, "low"),
    (ReasoningEffort.HIGH, "high"),
)


def test_reasoning_object_control_off_sets_enabled_false() -> None:
    body: dict = {}

    ReasoningObject(_EFFORTS).encode(body, ReasoningPolicy.off())

    assert body == {"extra_body": {"reasoning": {"enabled": False}}}


def test_reasoning_object_budget_maps_to_max_tokens() -> None:
    body: dict = {}

    ReasoningObject(_EFFORTS).encode(body, ReasoningPolicy.on(budget_tokens=512))

    assert body == {"extra_body": {"reasoning": {"max_tokens": 512}}}


@pytest.mark.parametrize(("effort", "expected"), _EFFORTS)
def test_reasoning_object_each_effort_maps_to_documented_value(
    effort: ReasoningEffort, expected: str
) -> None:
    body: dict = {}

    ReasoningObject(_EFFORTS).encode(body, ReasoningPolicy.on(effort=effort))

    assert body == {"extra_body": {"reasoning": {"effort": expected}}}


def test_reasoning_object_control_on_with_no_effort_or_budget_sets_enabled_true() -> (
    None
):
    body: dict = {}

    ReasoningObject(_EFFORTS).encode(body, ReasoningPolicy.on())

    assert body == {"extra_body": {"reasoning": {"enabled": True}}}


def test_reasoning_object_default_control_writes_nothing() -> None:
    body: dict = {}

    ReasoningObject(_EFFORTS).encode(body, ReasoningPolicy.provider_default())

    assert body == {}


def test_reasoning_object_without_budget_support_falls_through_to_effort() -> None:
    body: dict = {}
    policy = ReasoningPolicy.on(effort=ReasoningEffort.HIGH, budget_tokens=2048)

    ReasoningObject(_EFFORTS, supports_budget=False).encode(body, policy)

    assert body == {"extra_body": {"reasoning": {"effort": "high"}}}


def test_reasoning_object_raises_when_extra_body_is_not_a_dict() -> None:
    body = {"extra_body": "not-a-dict"}

    with pytest.raises(TypeError):
        ReasoningObject(_EFFORTS).encode(body, ReasoningPolicy.off())
