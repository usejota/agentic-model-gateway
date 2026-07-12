import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from api.gateway_model_ids import ONE_M_SUFFIX
from api.model_router import ModelRouter
from api.models.anthropic import MessagesRequest
from api.services import (
    _enforce_delegate_policy,
    _normalize_model_ref,
)
from config.settings import Settings
from providers.exceptions import InvalidRequestError
from providers.registry import ProviderRegistry

PREFIX = "claude-3-freecc-no-thinking/"


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    model_opus: str | None = None,
    model_delegate_approval: list[str] | None = None,
    model_delegate_allowlist: list[str] | None = None,
) -> Settings:
    return Settings.model_construct(
        model=model,
        model_opus=model_opus,
        model_sonnet=None,
        model_haiku=None,
        model_delegate_approval=model_delegate_approval or [],
        model_delegate_allowlist=model_delegate_allowlist or [],
        anthropic_auth_token="",
    )


def _delegates(app, settings, *, registry=None):
    """Wire settings/registry into a throwaway app and return the delegate ids."""
    if registry is not None:
        app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = TestClient(app).get("/v1/models/delegates")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    return response.json()["data"]


def _delegates_full(app, settings, *, registry=None):
    """Wire settings/registry into a throwaway app and return the full response."""
    if registry is not None:
        app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        response = TestClient(app).get("/v1/models/delegates")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    return response.json()


# =============================================================================
# /v1/models/delegates — catalog = allowlist + approval
# =============================================================================


def test_delegates_returns_only_no_thinking_ids():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/meta/llama-3.3",
        model_delegate_allowlist=["*"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-v4-pro"})
    registry.cache_model_ids("open_router", {"qwen/qwen-3.5"})

    ids = _delegates(app, settings, registry=registry)

    # Configured refs and cached provider models both appear.
    assert f"{PREFIX}deepseek/deepseek-chat" in ids
    assert f"{PREFIX}open_router/meta/llama-3.3" in ids
    assert f"{PREFIX}deepseek/deepseek-v4-pro" in ids
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in ids
    # Every id is a no-thinking id, deduped.
    assert all(item.startswith(PREFIX) for item in ids)
    assert len(ids) == len(set(ids))
    # The configured ref that also appears as a cached model is deduped.
    assert ids.count(f"{PREFIX}deepseek/deepseek-chat") == 1


def test_delegates_empty_lists_empty_catalog():
    """Both allowlist and approval empty = no delegates at all."""
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})
    registry.cache_model_ids("open_router", {"qwen/qwen-3.5"})

    body = _delegates_full(app, settings, registry=registry)

    assert body["data"] == []
    assert body["approval"] == []
    assert body["models"] == []


def test_delegates_allowlist_only_free():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_delegate_allowlist=["open_router/deepseek/*", "deepseek/*"]
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-v4-flash"})
    registry.cache_model_ids(
        "open_router", {"qwen/qwen-3.5", "deepseek/deepseek-v4-pro"}
    )

    body = _delegates_full(app, settings, registry=registry)

    # Free delegates are the allowlist matches; qwen is outside, approval empty.
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" in body["data"]
    assert f"{PREFIX}deepseek/deepseek-v4-flash" in body["data"]
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["data"]
    assert body["approval"] == []


def test_delegates_approval_only_approval():
    app = create_app(lifespan_enabled=False)
    settings = _settings(model_delegate_approval=["open_router/qwen/*"])
    registry = ProviderRegistry()
    registry.cache_model_ids(
        "open_router", {"qwen/qwen-3.5", "deepseek/deepseek-v4-pro"}
    )

    body = _delegates_full(app, settings, registry=registry)

    assert body["data"] == []
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in body["approval"]
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" not in body["approval"]


def test_delegates_both_lists_approval_wins():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_delegate_allowlist=["open_router/deepseek/*"],
        model_delegate_approval=["open_router/deepseek/deepseek-v4-pro"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids(
        "open_router", {"deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash"}
    )

    body = _delegates_full(app, settings, registry=registry)

    # The v4-pro matches both -> approval; v4-flash matches only allowlist -> free.
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-flash" in body["data"]
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" in body["approval"]
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" not in body["data"]


def test_delegates_null_registry():
    app = create_app(lifespan_enabled=False)
    settings = _settings(model_delegate_allowlist=["deepseek/*"])
    # No app.state.provider_registry set -> only configured refs, no crash.
    ids = _delegates(app, settings)

    assert ids == [f"{PREFIX}deepseek/deepseek-chat"]


def test_delegates_does_not_filter_models_picker():
    """The /model picker (/v1/models) is never filtered by delegate policy."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_allowlist=["deepseek/*"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        delegate_ids = TestClient(app).get("/v1/models/delegates").json()["data"]
        model_ids = [
            item["id"] for item in TestClient(app).get("/v1/models").json()["data"]
        ]
    finally:
        app.dependency_overrides.clear()

    # qwen is outside the deepseek/* allowlist -> absent from delegates, but the
    # /model picker still sees it.
    outside = f"{PREFIX}open_router/qwen/qwen-3.5"
    assert outside not in delegate_ids
    assert outside in model_ids


# =============================================================================
# hard enforcement: _enforce_delegate_policy
# =============================================================================


def _messages_request(model: str, system=None):
    return MessagesRequest.model_validate(
        {
            "model": model,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
            "system": system,
        }
    )


def _enforce(settings, model: str, system=None):
    request = _messages_request(model, system=system)
    resolved = ModelRouter(settings).resolve(model)
    _enforce_delegate_policy(settings, request, resolved.provider_model_ref)


def test_policy_noop_when_both_lists_empty():
    """An unconfigured gateway imposes no subagent restrictions."""
    settings = _settings()
    _enforce(settings, "anthropic/open_router/openai/gpt-oss-120b", system=None)


def test_policy_blocks_outside_union_for_subagent():
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="not in the delegate catalog"):
        _enforce(
            settings,
            "anthropic/open_router/qwen/qwen-4",
            system=None,
        )


def test_policy_active_with_approval_only():
    """A model outside the union is blocked when only approval is configured."""
    settings = _settings(model_delegate_approval=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="not in the delegate catalog"):
        _enforce(
            settings,
            "anthropic/open_router/qwen/qwen-4",
            system=None,
        )


def test_policy_allows_model_in_allowlist():
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    _enforce(
        settings,
        "anthropic/open_router/deepseek/deepseek-v4-pro",
        system=None,
    )


def test_policy_allows_model_in_approval():
    settings = _settings(
        model_delegate_allowlist=["open_router/deepseek/*"],
        model_delegate_approval=["open_router/qwen/*"],
    )
    _enforce(
        settings,
        "anthropic/open_router/qwen/qwen-4",
        system=None,
    )


def test_policy_blocks_no_thinking_variant():
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="not in the delegate catalog"):
        _enforce(
            settings,
            "claude-3-freecc-no-thinking/open_router/qwen/qwen-4",
            system=None,
        )


def test_policy_allows_main_loop_even_if_outside_union():
    """Main loop identified by gateway sentinel, not just "You are Claude Code"."""
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    _enforce(
        settings,
        "anthropic/open_router/qwen/qwen-4",
        system="You are inside the model gateway session",
    )


def test_policy_pattern_as_advertised_id_matches_thinking_variant():
    """A pattern written as a FULL advertised id (carrying the
    ``claude-3-freecc-no-thinking/`` prefix) must still match the thinking
    (``anthropic/``) variant — both reduce to the same canonical ref."""
    settings = _settings(
        model_delegate_allowlist=[
            "claude-3-freecc-no-thinking/open_router/deepseek/deepseek-v4-pro"
        ]
    )
    _enforce(
        settings,
        "anthropic/open_router/deepseek/deepseek-v4-pro",
        system="You are a delegate subagent running a task.",
    )


def test_policy_blocks_native_subagent_outside_union():
    """Native subagent prompts open with "You are an agent for Claude Code" —
    no main-loop marker matches, so enforcement applies."""
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="not in the delegate catalog"):
        _enforce(
            settings,
            "anthropic/open_router/qwen/qwen-4",
            system=(
                "You are an agent for Claude Code, Anthropic's official CLI for "
                "Claude. Given the user's message, you should use the tools "
                "available to complete the task."
            ),
        )


# =============================================================================
# _normalize_model_ref
# =============================================================================


def test_normalize_model_ref_strips_no_thinking_prefix():
    assert (
        _normalize_model_ref(f"{PREFIX}open_router/openai/gpt-oss-120b")
        == "open_router/openai/gpt-oss-120b"
    )


def test_normalize_model_ref_strips_thinking_prefix():
    assert (
        _normalize_model_ref("anthropic/open_router/openai/gpt-oss-120b")
        == "open_router/openai/gpt-oss-120b"
    )


def test_normalize_model_ref_strips_one_m_suffix():
    assert (
        _normalize_model_ref(f"anthropic/open_router/openai/gpt-oss-120b{ONE_M_SUFFIX}")
        == "open_router/openai/gpt-oss-120b"
    )


def test_normalize_model_ref_strips_no_thinking_prefix_and_one_m_suffix():
    assert (
        _normalize_model_ref(f"{PREFIX}open_router/openai/gpt-oss-120b{ONE_M_SUFFIX}")
        == "open_router/openai/gpt-oss-120b"
    )


def test_normalize_model_ref_leaves_bare_ref_unchanged():
    assert _normalize_model_ref("open_router/openai/gpt-oss-120b") == (
        "open_router/openai/gpt-oss-120b"
    )


def test_normalize_model_ref_leaves_non_gateway_prefix_unchanged():
    assert _normalize_model_ref("deepseek/deepseek-chat") == "deepseek/deepseek-chat"
