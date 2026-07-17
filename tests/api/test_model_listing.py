from fastapi.testclient import TestClient

from free_claude_code.application.model_metadata import ProviderModelInfo
from free_claude_code.config.settings import Settings
from tests.api.support import create_test_app, provider_manager_for_app


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    model_fable: str | None = None,
    model_opus: str | None = "open_router/anthropic/claude-opus",
    model_haiku: str | None = "deepseek/deepseek-chat",
) -> Settings:
    return Settings.model_construct(
        model=model,
        model_fable=model_fable,
        model_opus=model_opus,
        model_sonnet=None,
        model_haiku=model_haiku,
        anthropic_auth_token="",
        deepseek_api_key="deepseek-key",
        open_router_api_key="open-router-key",
        wafer_api_key="wafer-key",
    )


def _cache_models(app, provider_id: str, *model_ids: str) -> None:
    provider_manager_for_app(app).cache_model_infos(
        provider_id,
        {ProviderModelInfo(model_id) for model_id in model_ids},
    )


_DEFAULT_CONFIGURED_BLOCK = [
    "anthropic/deepseek/deepseek-chat",
    "claude-3-freecc-no-thinking/deepseek/deepseek-chat",
    "anthropic/open_router/anthropic/claude-opus",
    "claude-3-freecc-no-thinking/open_router/anthropic/claude-opus",
]

_PINNED_BLOCK = [
    "claude-opus-4-20250514",
    "claude-fable-5",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-20250514",
]


def test_models_list_includes_configured_refs_cached_provider_models_and_aliases():
    app = create_test_app(_settings())
    _cache_models(app, "deepseek", "deepseek-chat")
    _cache_models(
        app,
        "open_router",
        "meta/llama-3.3",
        "anthropic/claude-opus",
    )

    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["data"]]
    # Configured routes lead the catalog, then the pinned Claude aliases
    # (Opus base, no [1m] since override is 200K).
    assert ids[: len(_DEFAULT_CONFIGURED_BLOCK)] == _DEFAULT_CONFIGURED_BLOCK
    assert (
        ids[len(_DEFAULT_CONFIGURED_BLOCK) : len(_DEFAULT_CONFIGURED_BLOCK) + 4]
        == _PINNED_BLOCK
    )
    assert ids.count("anthropic/deepseek/deepseek-chat") == 1
    assert ids.count("anthropic/open_router/anthropic/claude-opus") == 1
    display_names = {item["id"]: item["display_name"] for item in data["data"]}
    assert (
        display_names["anthropic/open_router/meta/llama-3.3"]
        == "open_router/meta/llama-3.3"
    )
    assert (
        display_names["claude-3-freecc-no-thinking/open_router/meta/llama-3.3"]
        == "open_router/meta/llama-3.3 (no thinking)"
    )
    assert "claude-sonnet-4-20250514" in ids
    assert "claude-fable-5" in ids
    assert data["first_id"] == ids[0]
    assert data["last_id"] == ids[-1]
    assert data["has_more"] is False


def test_models_list_uses_thinking_metadata_for_cached_models():
    app = create_test_app(_settings(model_opus=None))
    manager = provider_manager_for_app(app)
    _cache_models(app, "deepseek", "deepseek-chat")
    manager.cache_model_infos(
        "open_router",
        {
            ProviderModelInfo("reasoning-model", supports_thinking=True),
            ProviderModelInfo("plain-model", supports_thinking=False),
        },
    )

    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/open_router/reasoning-model" in ids
    assert "claude-3-freecc-no-thinking/open_router/reasoning-model" in ids
    assert "anthropic/open_router/plain-model" not in ids
    assert "claude-3-freecc-no-thinking/open_router/plain-model" in ids


def test_models_list_uses_cached_metadata_for_configured_refs():
    app = create_test_app(
        _settings(
            model="open_router/plain-model",
            model_opus=None,
            model_haiku=None,
        )
    )
    provider_manager_for_app(app).cache_model_infos(
        "open_router",
        {ProviderModelInfo("plain-model", supports_thinking=False)},
    )

    response = TestClient(app).get("/v1/models")

    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/open_router/plain-model" not in ids
    assert ids[0] == "claude-3-freecc-no-thinking/open_router/plain-model"


def test_models_list_includes_cached_wafer_models():
    app = create_test_app(
        _settings(
            model="wafer/DeepSeek-V4-Pro",
            model_opus=None,
            model_haiku=None,
        )
    )
    _cache_models(app, "wafer", "DeepSeek-V4-Pro", "MiniMax-M2.7")

    response = TestClient(app).get("/v1/models")

    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/wafer/DeepSeek-V4-Pro" in ids
    assert "claude-3-freecc-no-thinking/wafer/DeepSeek-V4-Pro" in ids
    assert "anthropic/wafer/MiniMax-M2.7" in ids
    assert "claude-3-freecc-no-thinking/wafer/MiniMax-M2.7" in ids


def test_models_list_appends_1m_variant_for_manual_override_context():
    """A configured ref with a manual 1M context override gets a [1m] variant."""
    app = create_test_app(
        _settings(
            model="deepseek/deepseek-v4-pro",
            model_opus=None,
            model_haiku=None,
        )
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]
    assert "anthropic/deepseek/deepseek-v4-pro" in ids
    assert "anthropic/deepseek/deepseek-v4-pro[1m]" in ids


def test_models_list_appends_1m_variant_from_openrouter_context_length():
    """An OpenRouter model advertising >=1M context_length gets a [1m] variant."""
    app = create_test_app(
        _settings(model="open_router/big", model_opus=None, model_haiku=None)
    )
    provider_manager_for_app(app).cache_model_infos(
        "open_router",
        {ProviderModelInfo("big", supports_thinking=True, context_window=1_000_000)},
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]
    assert "anthropic/open_router/big[1m]" in ids


def test_models_list_no_1m_variant_below_threshold():
    app = create_test_app(
        _settings(model="open_router/small", model_opus=None, model_haiku=None)
    )
    provider_manager_for_app(app).cache_model_infos(
        "open_router",
        {ProviderModelInfo("small", supports_thinking=True, context_window=200_000)},
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]
    assert "anthropic/open_router/small[1m]" not in ids


def test_models_list_appends_alias_1m_for_1m_capable_fable_override():
    app = create_test_app(
        _settings(
            model_fable="deepseek/deepseek-v4-pro",
            model_opus=None,
            model_haiku=None,
        )
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]
    assert "claude-fable-5[1m]" in ids
    assert "claude-fable-5" in ids


def test_models_list_no_alias_1m_when_override_not_1m():
    app = create_test_app(
        _settings(
            model_fable="open_router/anthropic/claude-opus",
            model_opus=None,
            model_haiku=None,
        )
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]
    assert "claude-fable-5[1m]" not in ids


def test_models_list_works_with_empty_discovery_catalog():
    app = create_test_app(_settings())

    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids[: len(_DEFAULT_CONFIGURED_BLOCK)] == _DEFAULT_CONFIGURED_BLOCK
    assert (
        ids[len(_DEFAULT_CONFIGURED_BLOCK) : len(_DEFAULT_CONFIGURED_BLOCK) + 4]
        == _PINNED_BLOCK
    )
    assert "claude-sonnet-4-20250514" in ids


def test_pinned_models_come_first_and_remaining_are_alphabetical():
    app = create_test_app(_settings())
    _cache_models(app, "deepseek", "deepseek-chat")
    _cache_models(app, "open_router", "meta/llama-3.3", "anthropic/claude-opus")

    items = TestClient(app).get("/v1/models").json()["data"]
    ids = [item["id"] for item in items]

    configured_len = len(_DEFAULT_CONFIGURED_BLOCK)
    assert ids[:configured_len] == _DEFAULT_CONFIGURED_BLOCK
    assert ids[configured_len : configured_len + 4] == _PINNED_BLOCK

    remaining = ids[configured_len + 4 :]
    remaining_display = [
        item["display_name"] for item in items if item["id"] in remaining
    ]
    assert remaining_display == sorted(remaining_display, key=str.casefold)

    assert len(ids) == len(set(ids))


def test_pinned_prefers_1m_alias_over_base_for_opus_and_fable():
    app = create_test_app(
        _settings(
            model_fable="deepseek/deepseek-v4-pro",
            model_opus="deepseek/deepseek-v4-pro",
            model_haiku=None,
        )
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]

    pinned = [i for i in ids if i.startswith(("claude-opus", "claude-fable"))]
    assert pinned[0] == "claude-opus-4-20250514[1m]"
    assert pinned[1] == "claude-fable-5[1m]"


def test_pinned_falls_back_to_base_when_no_1m_override():
    app = create_test_app(_settings(model_opus="open_router/small", model_haiku=None))
    provider_manager_for_app(app).cache_model_infos(
        "open_router",
        {ProviderModelInfo("small", supports_thinking=True, context_window=200_000)},
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]

    pinned = [i for i in ids if i.startswith(("claude-opus", "claude-fable"))]
    assert pinned[0] == "claude-opus-4-20250514"
    assert pinned[1] == "claude-fable-5"


def test_fable_override_hides_real_upstream_fable_from_catalog():
    app = create_test_app(
        _settings(
            model_fable="deepseek/deepseek-v4-pro",
            model_opus=None,
            model_haiku=None,
        )
    )
    _cache_models(
        app,
        "open_router",
        "anthropic/claude-fable-5",
        "meta/llama-3.3",
    )

    ids = [item["id"] for item in TestClient(app).get("/v1/models").json()["data"]]

    assert not any("open_router/anthropic/claude-fable-5" in i for i in ids)
    assert "anthropic/open_router/meta/llama-3.3" in ids
    assert "claude-fable-5[1m]" in ids or "claude-fable-5" in ids
