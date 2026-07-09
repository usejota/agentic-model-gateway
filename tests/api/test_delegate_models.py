import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from api.model_router import ModelRouter
from api.models.anthropic import MessagesRequest
from api.services import _enforce_delegate_exclusions
from config.settings import Settings
from providers.exceptions import InvalidRequestError
from providers.registry import ProviderRegistry

PREFIX = "claude-3-freecc-no-thinking/"


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    model_opus: str | None = None,
    model_delegate_exclusions: list[str] | None = None,
) -> Settings:
    return Settings.model_construct(
        model=model,
        model_opus=model_opus,
        model_sonnet=None,
        model_haiku=None,
        model_delegate_exclusions=model_delegate_exclusions or [],
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


def test_delegates_returns_only_no_thinking_ids():
    app = create_app(lifespan_enabled=False)
    settings = _settings(model_opus="open_router/meta/llama-3.3")
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


def test_delegates_excludes_us_closed_vendors():
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    registry = ProviderRegistry()
    registry.cache_model_ids(
        "open_router", {"openai/gpt-oss-120b", "z-ai/glm-5.2", "google/gemini-2.5-pro"}
    )
    registry.cache_model_ids("openai", {"gpt-4"})
    registry.cache_model_ids("gemini", {"gemini-2.5-pro"})
    registry.cache_model_ids(
        "nvidia_nim", {"nvidia/nemotron-3-super-120b", "llama-3-70b"}
    )
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    ids = _delegates(app, settings, registry=registry)

    # US-closed vendors are absent.
    assert f"{PREFIX}open_router/openai/gpt-oss-120b" not in ids
    assert f"{PREFIX}openai/gpt-4" not in ids
    # Google: direct gemini provider (vendor "gemini") AND open_router-routed
    # google models (vendor "google") are both excluded — previously the direct
    # gemini id leaked because the set only had "google", not "gemini".
    assert f"{PREFIX}gemini/gemini-2.5-pro" not in ids
    assert f"{PREFIX}open_router/google/gemini-2.5-pro" not in ids
    # NVIDIA: direct nvidia_nim refs expose vendor "nvidia_nim" (the provider
    # id) for both nvidia_nim/<model> and nvidia_nim/nvidia/<model>; the set
    # needs "nvidia_nim" (previously only "nvidia", which never matched a
    # direct ref and let NVIDIA's own models leak).
    assert f"{PREFIX}nvidia_nim/nvidia/nemotron-3-super-120b" not in ids
    assert f"{PREFIX}nvidia_nim/llama-3-70b" not in ids
    # Non-US vendors are present.
    assert f"{PREFIX}deepseek/deepseek-chat" in ids
    assert f"{PREFIX}open_router/z-ai/glm-5.2" in ids


def test_delegates_applies_exclusions_exact():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/deepseek/deepseek-v4-pro",
        model_delegate_exclusions=["open_router/deepseek/deepseek-v4-pro"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-v4-flash"})
    registry.cache_model_ids("open_router", {"qwen/qwen-3.5"})

    ids = _delegates(app, settings, registry=registry)

    # The excluded configured ref is absent; the others remain.
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" not in ids
    assert f"{PREFIX}deepseek/deepseek-chat" in ids
    assert f"{PREFIX}deepseek/deepseek-v4-flash" in ids
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in ids


def test_delegates_applies_exclusions_glob():
    app = create_app(lifespan_enabled=False)
    settings = _settings(model_delegate_exclusions=["open_router/qwen/*"])
    registry = ProviderRegistry()
    registry.cache_model_ids(
        "open_router", {"qwen/qwen-3.5", "qwen/qwen-4", "deepseek/deepseek-v4-pro"}
    )
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    ids = _delegates(app, settings, registry=registry)

    # All open_router/qwen/... are filtered by the glob; siblings remain.
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in ids
    assert f"{PREFIX}open_router/qwen/qwen-4" not in ids
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" in ids
    assert f"{PREFIX}deepseek/deepseek-chat" in ids


def test_delegates_null_registry():
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    # No app.state.provider_registry set -> only configured refs, no crash.
    ids = _delegates(app, settings)

    assert ids == [f"{PREFIX}deepseek/deepseek-chat"]


def test_delegates_does_not_filter_models_picker():
    """The /model picker (/v1/models) is never filtered by delegate exclusions."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/deepseek/deepseek-v4-pro",
        model_delegate_exclusions=["open_router/deepseek/deepseek-v4-pro"],
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

    excluded_no_thinking = f"{PREFIX}open_router/deepseek/deepseek-v4-pro"
    assert excluded_no_thinking not in delegate_ids
    assert excluded_no_thinking in model_ids


# --- hard enforcement: _enforce_delegate_exclusions -------------------------


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
    _enforce_delegate_exclusions(settings, request, resolved.provider_model_ref)


def test_enforce_blocks_excluded_model_for_subagent_request():
    settings = _settings(model_delegate_exclusions=["open_router/openai/gpt-oss-120b"])
    with pytest.raises(InvalidRequestError, match="excluded for subagents"):
        _enforce(
            settings,
            "anthropic/open_router/openai/gpt-oss-120b",
            system="You are a delegate subagent running x.",
        )


def test_enforce_blocks_no_thinking_and_one_m_variants():
    settings = _settings(model_delegate_exclusions=["open_router/openai/*"])
    for model in (
        "claude-3-freecc-no-thinking/open_router/openai/gpt-oss-120b",
        "anthropic/open_router/openai/gpt-oss-120b[1m]",
    ):
        with pytest.raises(InvalidRequestError):
            _enforce(settings, model, system=None)


def test_enforce_pattern_as_advertised_id_matches_thinking_variant():
    """An exclusion written as a FULL advertised id (carrying the
    ``claude-3-freecc-no-thinking/`` prefix) must still match the thinking
    (``anthropic/``) variant — both reduce to the same canonical ref, so a
    subagent can't escape an exclusion by selecting the thinking variant of an
    excluded model. Without ref+pattern normalization this is a bypass."""
    settings = _settings(
        model_delegate_exclusions=[
            "claude-3-freecc-no-thinking/open_router/openai/gpt-oss-120b"
        ]
    )
    with pytest.raises(InvalidRequestError):
        _enforce(
            settings,
            "anthropic/open_router/openai/gpt-oss-120b",
            system="You are a delegate subagent running a task.",
        )


def test_enforce_allows_main_loop_requests():
    """The human /model picker (Claude Code main loop) is never blocked."""
    settings = _settings(model_delegate_exclusions=["open_router/openai/gpt-oss-120b"])
    _enforce(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system="You are Claude Code, Anthropic's official CLI for Claude.",
    )
    # Also as a system-blocks list.
    _enforce(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system=[{"type": "text", "text": "You are Claude Code, official CLI."}],
    )
    # Output styles REPLACE the CLI prompt, but the claudim launcher's
    # append-system-prompt sentinel lands in a later block — still main loop.
    _enforce(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system=[
            {"type": "text", "text": "Respond terse like smart caveman."},
            {
                "type": "text",
                "text": "You are inside claudim (gateway session). Your agent "
                "list includes delegate-* subagents.",
            },
        ],
    )


def test_enforce_allows_non_excluded_models_for_subagents():
    settings = _settings(model_delegate_exclusions=["open_router/openai/gpt-oss-120b"])
    _enforce(
        settings,
        "anthropic/open_router/deepseek/deepseek-v4-pro",
        system="You are a delegate subagent running deepseek.",
    )


def test_enforce_noop_when_no_exclusions():
    settings = _settings()
    _enforce(settings, "anthropic/open_router/openai/gpt-oss-120b", system=None)
