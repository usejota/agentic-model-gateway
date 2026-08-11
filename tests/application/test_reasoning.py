import pytest

from free_claude_code.application.reasoning import (
    client_reasoning_policy,
    resolve_reasoning_policy,
)
from free_claude_code.config.reasoning import ReasoningPreference
from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.core.reasoning import (
    ReasoningControl,
    ReasoningEffort,
    ReasoningPolicy,
)


def _request(**overrides) -> MessagesRequest:
    payload = {
        "model": "provider/model",
        "messages": [{"role": "user", "content": "hello"}],
    }
    payload.update(overrides)
    return MessagesRequest.model_validate(payload)


def test_client_without_reasoning_control_uses_provider_default() -> None:
    assert client_reasoning_policy(_request()) == ReasoningPolicy.provider_default()


def test_client_reasoning_preserves_effort_and_exact_budget() -> None:
    policy = client_reasoning_policy(
        _request(
            thinking={"type": "enabled", "budget_tokens": 4096},
            output_config={"effort": "xhigh"},
        )
    )

    assert policy == ReasoningPolicy.on(
        effort=ReasoningEffort.XHIGH,
        budget_tokens=4096,
    )


def test_named_effort_preserves_intent_without_exact_client_budget() -> None:
    policy = client_reasoning_policy(_request(output_config={"effort": "high"}))

    assert policy == ReasoningPolicy(
        control=ReasoningControl.DEFAULT,
        effort=ReasoningEffort.HIGH,
    )
    assert policy.budget_tokens is None
    assert policy.requests_reasoning is True


def test_invalid_budget_does_not_implicitly_enable_reasoning() -> None:
    policy = client_reasoning_policy(_request(thinking={"budget_tokens": 0}))

    assert policy == ReasoningPolicy.provider_default()


@pytest.mark.parametrize(
    "messages_request",
    [
        _request(thinking={"type": "disabled"}),
        _request(output_config={"effort": "none"}),
    ],
)
def test_client_disable_is_explicit(messages_request: MessagesRequest) -> None:
    policy = client_reasoning_policy(messages_request)

    assert policy.control is ReasoningControl.OFF
    assert policy.output_enabled is False
    assert policy.requests_reasoning is False


def test_disabled_thinking_preserves_independent_effort_intent() -> None:
    policy = client_reasoning_policy(
        _request(
            thinking={"type": "disabled"},
            output_config={"effort": "medium"},
        )
    )

    assert policy == ReasoningPolicy(
        control=ReasoningControl.OFF,
        effort=ReasoningEffort.MEDIUM,
    )
    assert policy.requests_reasoning is False


def test_fixed_route_effort_overrides_client_disable() -> None:
    policy = resolve_reasoning_policy(
        _request(thinking={"type": "disabled"}),
        ReasoningPreference.MAX,
    )

    assert policy == ReasoningPolicy.on(effort=ReasoningEffort.MAX)


def test_fixed_off_overrides_client_enable() -> None:
    policy = resolve_reasoning_policy(
        _request(thinking={"type": "enabled", "budget_tokens": 1024}),
        ReasoningPreference.OFF,
    )

    assert policy == ReasoningPolicy.off()


def test_client_preference_preserves_client_policy() -> None:
    request = _request(output_config={"effort": "low"})

    assert resolve_reasoning_policy(
        request, ReasoningPreference.CLIENT
    ) == client_reasoning_policy(request)


def test_unresolved_inherit_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be resolved"):
        resolve_reasoning_policy(_request(), ReasoningPreference.INHERIT)


@pytest.mark.parametrize("budget", [0, -1, True])
def test_reasoning_budget_requires_a_positive_integer(budget: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ReasoningPolicy.on(budget_tokens=budget)


def test_reasoning_budget_requires_explicit_on_control() -> None:
    with pytest.raises(ValueError, match="control to be on"):
        ReasoningPolicy(budget_tokens=100)


@pytest.mark.parametrize(
    ("effort", "expected"),
    (
        (ReasoningEffort.MINIMAL, 512),
        (ReasoningEffort.LOW, 512),
        (ReasoningEffort.MEDIUM, 1_024),
        (ReasoningEffort.HIGH, 2_048),
        (ReasoningEffort.XHIGH, 4_096),
        (ReasoningEffort.MAX, 8_192),
    ),
)
def test_reasoning_effort_has_one_fcc_numeric_budget(
    effort: ReasoningEffort, expected: int
) -> None:
    assert ReasoningPolicy.on(effort=effort).numeric_budget_tokens == expected


def test_exact_reasoning_budget_takes_precedence_over_effort_mapping() -> None:
    policy = ReasoningPolicy.on(
        effort=ReasoningEffort.XHIGH,
        budget_tokens=777,
    )

    assert policy.numeric_budget_tokens == 777


@pytest.mark.parametrize(
    "policy",
    (
        ReasoningPolicy.provider_default(),
        ReasoningPolicy.off(),
        ReasoningPolicy.on(),
    ),
)
def test_reasoning_without_numeric_intensity_has_no_budget(
    policy: ReasoningPolicy,
) -> None:
    assert policy.numeric_budget_tokens is None


@pytest.mark.parametrize("effort", list(ReasoningEffort))
def test_adaptive_thinking_preserves_each_effort_level(effort: ReasoningEffort) -> None:
    policy = client_reasoning_policy(
        _request(
            thinking={"type": "adaptive"},
            output_config={"effort": effort.value},
        )
    )

    assert policy == ReasoningPolicy.on(effort=effort)


def test_output_effort_none_disables_reasoning() -> None:
    policy = client_reasoning_policy(_request(output_config={"effort": "none"}))

    assert policy == ReasoningPolicy.off()


def test_invalid_effort_string_is_ignored() -> None:
    policy = client_reasoning_policy(_request(output_config={"effort": "bogus"}))

    assert policy == ReasoningPolicy.provider_default()


def test_effort_with_no_thinking_block_is_preserved_as_default_control() -> None:
    policy = client_reasoning_policy(_request(output_config={"effort": "low"}))

    assert policy == ReasoningPolicy(
        control=ReasoningControl.DEFAULT,
        effort=ReasoningEffort.LOW,
    )


def test_thinking_enabled_false_disables_reasoning() -> None:
    policy = client_reasoning_policy(_request(thinking={"enabled": False}))

    assert policy.control is ReasoningControl.OFF
    assert policy.requests_reasoning is False


@pytest.mark.parametrize("budget", [0, -1])
def test_zero_or_negative_budget_tokens_are_ignored(budget: int) -> None:
    policy = client_reasoning_policy(_request(thinking={"budget_tokens": budget}))

    assert policy == ReasoningPolicy.provider_default()


def test_positive_budget_tokens_enables_reasoning_with_that_budget() -> None:
    policy = client_reasoning_policy(_request(thinking={"budget_tokens": 1234}))

    assert policy == ReasoningPolicy.on(budget_tokens=1234)


@pytest.mark.parametrize(
    "preference",
    [ReasoningPreference.CLIENT, ReasoningPreference.HIGH, ReasoningPreference.MAX],
)
def test_tiny_max_tokens_forces_reasoning_off(
    preference: ReasoningPreference,
) -> None:
    """Background calls (terminal title) cannot fit thinking plus text."""

    policy = resolve_reasoning_policy(_request(max_tokens=1024), preference)

    assert policy == ReasoningPolicy.off()


def test_tiny_max_tokens_overrides_client_enable() -> None:
    policy = resolve_reasoning_policy(
        _request(max_tokens=512, thinking={"type": "enabled", "budget_tokens": 4096}),
        ReasoningPreference.CLIENT,
    )

    assert policy == ReasoningPolicy.off()


def test_effort_budget_that_cannot_fit_disables_reasoning() -> None:
    """HIGH maps to a 2048 numeric budget; 1025 max_tokens cannot fit it."""

    policy = resolve_reasoning_policy(
        _request(max_tokens=1025), ReasoningPreference.HIGH
    )

    assert policy == ReasoningPolicy.off()


def test_max_tokens_above_effort_budget_keeps_route_reasoning() -> None:
    policy = resolve_reasoning_policy(
        _request(max_tokens=2049), ReasoningPreference.HIGH
    )

    assert policy == ReasoningPolicy.on(effort=ReasoningEffort.HIGH)


def test_client_budget_that_cannot_fit_disables_reasoning() -> None:
    policy = resolve_reasoning_policy(
        _request(max_tokens=4096, thinking={"type": "enabled", "budget_tokens": 4096}),
        ReasoningPreference.CLIENT,
    )

    assert policy == ReasoningPolicy.off()


def test_client_budget_below_max_tokens_keeps_reasoning() -> None:
    policy = resolve_reasoning_policy(
        _request(max_tokens=4097, thinking={"type": "enabled", "budget_tokens": 4096}),
        ReasoningPreference.CLIENT,
    )

    assert policy == ReasoningPolicy.on(budget_tokens=4096)


def test_missing_max_tokens_keeps_route_reasoning() -> None:
    policy = resolve_reasoning_policy(_request(), ReasoningPreference.HIGH)

    assert policy == ReasoningPolicy.on(effort=ReasoningEffort.HIGH)
