import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from api.gateway_model_ids import ONE_M_SUFFIX
from api.model_router import ModelRouter
from api.models.anthropic import MessagesRequest
from api.services import (
    _enforce_delegate_allowlist,
    _enforce_delegate_exclusions,
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
    model_delegate_exclusions: list[str] | None = None,
    model_delegate_approval: list[str] | None = None,
    model_delegate_allowlist: list[str] | None = None,
) -> Settings:
    return Settings.model_construct(
        model=model,
        model_opus=model_opus,
        model_sonnet=None,
        model_haiku=None,
        model_delegate_exclusions=model_delegate_exclusions or [],
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
    """The human /model picker (Claude Code main loop) is never blocked.
    The main loop opens with "You are Claude Code"; subagents open with
    "You are an agent for Claude Code" and do NOT match."""
    settings = _settings(model_delegate_exclusions=["open_router/openai/gpt-oss-120b"])
    _enforce(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system="You are Claude Code, Anthropic's official CLI for Claude.",
    )
    # Also as a system-blocks list, and via the launcher sentinel.
    _enforce(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system=[{"type": "text", "text": "You are inside the model gateway session"}],
    )
    # Output styles REPLACE the CLI prompt, but the launcher's
    # append-system-prompt sentinel lands in a later block — still main loop.
    # The name-agnostic sentinel is the primary marker (renameable launcher);
    # the legacy claudim-bearing one is kept for retro-compat.
    _enforce(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system=[
            {"type": "text", "text": "Respond terse like smart caveman."},
            {
                "type": "text",
                "text": "You are inside the model gateway session. Your agent "
                "list includes delegate-* subagents.",
            },
        ],
    )
    # Already-deployed launchers that still emit the legacy (name-bearing)
    # sentinel must keep matching after the gateway adds the name-agnostic
    # marker — retro-compat.
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


# =============================================================================
# hard enforcement: _enforce_delegate_allowlist
# =============================================================================


def _enforce_allowlist(settings, model: str, system=None):
    request = _messages_request(model, system=system)
    resolved = ModelRouter(settings).resolve(model)
    _enforce_delegate_allowlist(settings, request, resolved.provider_model_ref)


def test_allowlist_enforce_blocks_outside_union_for_subagent():
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="delegate allowlist"):
        _enforce_allowlist(
            settings,
            "anthropic/open_router/qwen/qwen-4",
            system=None,
        )


def test_allowlist_enforce_allows_model_in_allowlist():
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    _enforce_allowlist(
        settings,
        "anthropic/open_router/deepseek/deepseek-v4-pro",
        system=None,
    )


def test_allowlist_enforce_allows_model_in_approval():
    settings = _settings(
        model_delegate_allowlist=["open_router/deepseek/*"],
        model_delegate_approval=["open_router/qwen/*"],
    )
    _enforce_allowlist(
        settings,
        "anthropic/open_router/qwen/qwen-4",
        system=None,
    )


def test_allowlist_enforce_blocks_no_thinking_variant():
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="delegate allowlist"):
        _enforce_allowlist(
            settings,
            "claude-3-freecc-no-thinking/open_router/qwen/qwen-4",
            system=None,
        )


def test_allowlist_enforce_allows_main_loop_even_if_outside_union():
    """Main loop identified by gateway sentinel, not just "You are Claude Code"."""
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    _enforce_allowlist(
        settings,
        "anthropic/open_router/qwen/qwen-4",
        system="You are inside the model gateway session",
    )


# Real Claude Code subagent system-prompt opening (verified against the CLI
# binary). It does NOT contain the "You are Claude Code" substring, so the
# main-loop exemption never applies to subagents.
_SUBAGENT_PROMPT = (
    "You are an agent for Claude Code, Anthropic's official CLI for Claude. "
    "Given the user's message, you should use the tools available to complete "
    "the task."
)


def test_allowlist_enforce_blocks_native_subagent_outside_union():
    """Native subagent prompts open with "You are an agent for Claude Code" —
    no main-loop marker matches, so enforcement applies."""
    settings = _settings(model_delegate_allowlist=["open_router/deepseek/*"])
    with pytest.raises(InvalidRequestError, match="delegate allowlist"):
        _enforce_allowlist(
            settings,
            "anthropic/open_router/qwen/qwen-4",
            system=_SUBAGENT_PROMPT,
        )


def test_exclusions_enforce_blocks_native_subagent():
    """Same: exclusions apply to native subagents, not just side-channel."""
    settings = _settings(model_delegate_exclusions=["open_router/qwen/*"])
    with pytest.raises(InvalidRequestError, match="excluded for subagents"):
        _enforce(
            settings,
            "anthropic/open_router/qwen/qwen-4",
            system=_SUBAGENT_PROMPT,
        )


def test_allowlist_enforce_noop_when_empty():
    settings = _settings()
    _enforce_allowlist(
        settings,
        "anthropic/open_router/qwen/qwen-4",
        system=None,
    )


def test_allowlist_enforce_matches_catalog_for_stripped_patterns():
    """Gateway and catalog agree on vendor-form patterns (``deepseek/*``).

    A pattern written without the provider segment must admit the same refs
    the catalog admits — otherwise a model listed in /v1/models/delegates
    would be falsely 400'd at request time."""
    settings = _settings(model_delegate_allowlist=["deepseek/*"])
    _enforce_allowlist(
        settings,
        "anthropic/open_router/deepseek/deepseek-v4-pro",
        system=None,
    )
    # Direct-provider 2-part ref admitted by the catalog is admitted here too.
    _enforce_allowlist(settings, "anthropic/deepseek/deepseek-chat", system=None)


def test_allowlist_enforce_blocks_closed_vendor_not_in_approval():
    """A US-closed-vendor model matching only the allowlist is absent from the
    catalog and must be blocked at the gateway too (no side-channel run)."""
    settings = _settings(model_delegate_allowlist=["open_router/*"])
    with pytest.raises(InvalidRequestError, match="delegate allowlist"):
        _enforce_allowlist(
            settings,
            "anthropic/open_router/openai/gpt-oss-120b",
            system=None,
        )


def test_allowlist_enforce_allows_closed_vendor_via_approval():
    settings = _settings(
        model_delegate_allowlist=["open_router/deepseek/*"],
        model_delegate_approval=["open_router/openai/*"],
    )
    _enforce_allowlist(
        settings,
        "anthropic/open_router/openai/gpt-oss-120b",
        system=None,
    )


# =============================================================================
# MODEL_DELEGATE_APPROVAL — /v1/models/delegates response shape
# =============================================================================


def test_delegates_response_has_data_and_approval_keys():
    """The response includes both ``data`` and ``approval`` lists."""
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-v4-flash"})

    body = _delegates_full(app, settings, registry=registry)

    assert "data" in body
    assert "approval" in body
    assert isinstance(body["data"], list)
    assert isinstance(body["approval"], list)


def test_delegates_approval_empty_list_all_in_data():
    """Empty approval list -> all non-excluded models in ``data``, none in ``approval``."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_approval=[],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-v4-flash"})

    body = _delegates_full(app, settings, registry=registry)

    assert len(body["data"]) >= 3  # configured refs + discovered
    assert body["approval"] == []
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in body["data"]
    assert f"{PREFIX}deepseek/deepseek-v4-flash" in body["data"]


def test_delegates_approval_exact_match():
    """An approval pattern that exactly matches a ref moves it to ``approval``."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_approval=["open_router/qwen/qwen-3.5"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-v4-flash"})
    registry.cache_model_ids("open_router", {"qwen/qwen-4"})

    body = _delegates_full(app, settings, registry=registry)

    # The matched model is in approval, not in data.
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in body["approval"]
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["data"]
    # Non-matched models stay in data.
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    assert f"{PREFIX}deepseek/deepseek-v4-flash" in body["data"]
    assert f"{PREFIX}open_router/qwen/qwen-4" in body["data"]


def test_delegates_approval_glob():
    """fnmatch glob approval pattern matches multiple models."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_approval=["open_router/qwen/*"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("open_router", {"qwen/qwen-4", "deepseek/deepseek-v4-pro"})
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    body = _delegates_full(app, settings, registry=registry)

    # All open_router/qwen/* models are in approval.
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in body["approval"]
    assert f"{PREFIX}open_router/qwen/qwen-4" in body["approval"]
    # Non-matching models stay in data.
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" in body["data"]
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    # No approval models leak into data.
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["data"]
    assert f"{PREFIX}open_router/qwen/qwen-4" not in body["data"]


def test_delegates_approval_normalized_pattern_matches_thinking_variant():
    """An approval pattern written as a full advertised gateway id normalizes and
    matches the bare ref, just like exclusions do."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_approval=[f"{PREFIX}open_router/qwen/qwen-3.5"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    body = _delegates_full(app, settings, registry=registry)

    # The pattern ``claude-3-freecc-no-thinking/open_router/qwen/qwen-3.5``
    # normalizes to ``open_router/qwen/qwen-3.5``, matching the bare ref.
    assert f"{PREFIX}open_router/qwen/qwen-3.5" in body["approval"]
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["data"]


def test_delegates_approval_excluded_not_in_either():
    """Excluded models (MODEL_DELEGATE_EXCLUSIONS) appear in neither list."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_exclusions=["open_router/qwen/qwen-3.5"],
        model_delegate_approval=["open_router/qwen/*"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    body = _delegates_full(app, settings, registry=registry)

    # Excluded model is absent from both lists, even though it also matches
    # an approval pattern (exclusion is checked first).
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["data"]
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["approval"]
    # Non-excluded models are present.
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]


def test_delegates_approval_us_closed_vendors_in_approval_when_matched():
    """US_CLOSED_VENDORS models that match an approval pattern appear in ``approval``.

    Previously US_CLOSED_VENDORS were stripped entirely (from both free and approval).
    Now they go to the approval list when matched — the whole point of the approval
    feature is to make premium models (anthropic, openai, google, x-ai) available
    via per-spawn human approval."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_delegate_approval=["openai/*", "anthropic/*"],
    )
    registry = ProviderRegistry()
    # openai as a vendor is routed through open_router (like anthropic). The
    # vendor extracted by _delegate_vendor is "openai" (second segment of
    # open_router/openai/gpt-oss-120b), matching US_CLOSED_VENDORS.
    registry.cache_model_ids(
        "open_router",
        {
            "openai/gpt-oss-120b",
            "anthropic/claude-opus-4-8",
            "deepseek/deepseek-v4-pro",
        },
    )
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    body = _delegates_full(app, settings, registry=registry)

    # US-closed vendors that match approval patterns go to approval.
    assert f"{PREFIX}open_router/openai/gpt-oss-120b" in body["approval"]
    assert f"{PREFIX}open_router/anthropic/claude-opus-4-8" in body["approval"]
    # They don't leak into free.
    assert f"{PREFIX}open_router/openai/gpt-oss-120b" not in body["data"]
    assert f"{PREFIX}open_router/anthropic/claude-opus-4-8" not in body["data"]
    # Non-US vendors are still in free.
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-pro" in body["data"]


def test_delegates_approval_us_closed_vendors_excluded_when_not_matched():
    """US_CLOSED_VENDORS that do NOT match an approval pattern are still excluded."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_delegate_approval=["google/*"],  # only google, not openai
    )
    registry = ProviderRegistry()
    registry.cache_model_ids(
        "open_router",
        {
            "openai/gpt-oss-120b",
            "google/gemini-2.5-pro",
        },
    )
    registry.cache_model_ids("deepseek", {"deepseek-chat"})

    body = _delegates_full(app, settings, registry=registry)

    # Google matches approval -> in approval.
    assert f"{PREFIX}open_router/google/gemini-2.5-pro" in body["approval"]
    # OpenAI doesn't match approval AND is US_CLOSED_VENDORS -> excluded entirely.
    assert f"{PREFIX}open_router/openai/gpt-oss-120b" not in body["data"]
    assert f"{PREFIX}open_router/openai/gpt-oss-120b" not in body["approval"]
    # Non-US vendors are in free.
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]


def test_delegates_approval_null_registry():
    """Approval endpoint works with no provider registry (only configured refs)."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/qwen/qwen-3.5",
        model_delegate_approval=["open_router/qwen/*"],
    )

    body = _delegates_full(app, settings)

    assert f"{PREFIX}open_router/qwen/qwen-3.5" in body["approval"]
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    assert f"{PREFIX}open_router/qwen/qwen-3.5" not in body["data"]


# =============================================================================
# MODEL_DELEGATE_ALLOWLIST — /v1/models/delegates endpoint shape
# =============================================================================


def test_delegates_allowlist_filters_free_models():
    """Allowlist set → only matched models are free; approval models still present."""
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model_opus="open_router/deepseek/deepseek-v4-flash",
        model_delegate_allowlist=["open_router/deepseek/*"],
        model_delegate_approval=["open_router/qwen/*"],
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("open_router", {"qwen/qwen-4", "ministral/mistral-small"})

    body = _delegates_full(app, settings, registry=registry)

    # DeepSeek models are in allowlist → free
    assert f"{PREFIX}open_router/deepseek/deepseek-v4-flash" in body["data"]
    assert f"{PREFIX}deepseek/deepseek-chat" in body["data"]
    # Qwen matches approval → approval
    assert f"{PREFIX}open_router/qwen/qwen-4" in body["approval"]
    assert f"{PREFIX}open_router/qwen/qwen-4" not in body["data"]
    # Ministral is outside the union → absent
    assert f"{PREFIX}open_router/ministral/mistral-small" not in body["data"]
    assert f"{PREFIX}open_router/ministral/mistral-small" not in body["approval"]
    # Shape unchanged
    assert "data" in body
    assert "approval" in body
    assert "models" in body


# =============================================================================
# _normalize_model_ref
# =============================================================================


def test_normalize_model_ref_strips_thinking_prefix():
    """``anthropic/provider/model`` normalizes to ``provider/model``."""
    assert _normalize_model_ref("anthropic/open_router/deepseek/deepseek-chat") == (
        "open_router/deepseek/deepseek-chat"
    )


def test_normalize_model_ref_strips_no_thinking_prefix():
    """``claude-3-freecc-no-thinking/provider/model`` normalizes to ``provider/model``."""
    assert (
        _normalize_model_ref(
            "claude-3-freecc-no-thinking/open_router/deepseek/deepseek-chat"
        )
        == "open_router/deepseek/deepseek-chat"
    )


def test_normalize_model_ref_strips_one_m_suffix():
    """``[1m]`` suffix is stripped from non-gateway ids."""
    assert (
        _normalize_model_ref(f"open_router/deepseek/deepseek-chat{ONE_M_SUFFIX}")
        == "open_router/deepseek/deepseek-chat"
    )


def test_normalize_model_ref_strips_thinking_prefix_and_one_m_suffix():
    """Gateway prefix AND ``[1m]`` suffix are both stripped."""
    assert (
        _normalize_model_ref(
            f"anthropic/open_router/deepseek/deepseek-chat{ONE_M_SUFFIX}"
        )
        == "open_router/deepseek/deepseek-chat"
    )


def test_normalize_model_ref_strips_no_thinking_prefix_and_one_m_suffix():
    """No-thinking prefix AND ``[1m]`` suffix are both stripped."""
    assert (
        _normalize_model_ref(
            f"claude-3-freecc-no-thinking/open_router/deepseek/deepseek-chat{ONE_M_SUFFIX}"
        )
        == "open_router/deepseek/deepseek-chat"
    )


def test_normalize_model_ref_leaves_bare_ref_unchanged():
    """A bare ``provider/model`` ref without gateway prefix or suffix is unchanged."""
    assert (
        _normalize_model_ref("open_router/deepseek/deepseek-chat")
        == "open_router/deepseek/deepseek-chat"
    )


def test_normalize_model_ref_leaves_non_gateway_prefix_unchanged():
    """A ref whose prefix is not a gateway prefix is unchanged."""
    assert (
        _normalize_model_ref("some_other_prefix/deepseek/deepseek-chat")
        == "some_other_prefix/deepseek/deepseek-chat"
    )
