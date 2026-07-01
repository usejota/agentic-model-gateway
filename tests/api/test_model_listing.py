from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings
from providers.model_listing import ProviderModelInfo
from providers.registry import ProviderRegistry


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    model_opus: str | None = "open_router/anthropic/claude-opus",
    model_haiku: str | None = "deepseek/deepseek-chat",
) -> Settings:
    return Settings.model_construct(
        model=model,
        model_opus=model_opus,
        model_sonnet=None,
        model_haiku=model_haiku,
        anthropic_auth_token="",
    )


def test_models_list_includes_configured_refs_cached_provider_models_and_aliases():
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})
    registry.cache_model_ids("open_router", {"meta/llama-3.3", "anthropic/claude-opus"})
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["data"]]

    assert ids[:6] == [
        "anthropic/deepseek/deepseek-chat",
        "claude-3-freecc-no-thinking/deepseek/deepseek-chat",
        "anthropic/open_router/anthropic/claude-opus",
        "claude-3-freecc-no-thinking/open_router/anthropic/claude-opus",
        "anthropic/open_router/meta/llama-3.3",
        "claude-3-freecc-no-thinking/open_router/meta/llama-3.3",
    ]
    assert ids.count("anthropic/deepseek/deepseek-chat") == 1
    assert ids.count("claude-3-freecc-no-thinking/deepseek/deepseek-chat") == 1
    assert ids.count("anthropic/open_router/anthropic/claude-opus") == 1
    assert (
        ids.count("claude-3-freecc-no-thinking/open_router/anthropic/claude-opus") == 1
    )
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
    assert data["first_id"] == ids[0]
    assert data["last_id"] == ids[-1]
    assert data["has_more"] is False


def test_models_list_uses_openrouter_thinking_metadata_for_cached_models():
    app = create_app(lifespan_enabled=False)
    settings = _settings(model_opus=None)
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})
    registry.cache_model_infos(
        "open_router",
        {
            ProviderModelInfo("reasoning-model", supports_thinking=True),
            ProviderModelInfo("plain-model", supports_thinking=False),
        },
    )
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/open_router/reasoning-model" in ids
    assert "claude-3-freecc-no-thinking/open_router/reasoning-model" in ids
    assert "anthropic/open_router/plain-model" not in ids
    assert "claude-3-freecc-no-thinking/open_router/plain-model" in ids


def test_models_list_uses_cached_metadata_for_configured_openrouter_refs():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model="open_router/plain-model",
        model_opus=None,
        model_haiku=None,
    )
    registry = ProviderRegistry()
    registry.cache_model_infos(
        "open_router",
        {ProviderModelInfo("plain-model", supports_thinking=False)},
    )
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/open_router/plain-model" not in ids
    assert ids[0] == "claude-3-freecc-no-thinking/open_router/plain-model"


def test_models_list_includes_cached_wafer_models():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model="wafer/DeepSeek-V4-Pro",
        model_opus=None,
        model_haiku=None,
    )
    registry = ProviderRegistry()
    registry.cache_model_ids("wafer", {"DeepSeek-V4-Pro", "MiniMax-M2.7"})
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/wafer/DeepSeek-V4-Pro" in ids
    assert "claude-3-freecc-no-thinking/wafer/DeepSeek-V4-Pro" in ids
    assert "anthropic/wafer/MiniMax-M2.7" in ids
    assert "claude-3-freecc-no-thinking/wafer/MiniMax-M2.7" in ids


def test_models_list_advertises_one_m_variant_for_autodiscovered_window():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model="open_router/big-model", model_opus=None, model_haiku=None
    )
    registry = ProviderRegistry()
    registry.cache_model_infos(
        "open_router",
        {
            ProviderModelInfo(
                "big-model", supports_thinking=True, context_window=1_000_000
            ),
            ProviderModelInfo(
                "small-model", supports_thinking=True, context_window=200_000
            ),
        },
    )
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()["data"]
    ids = [item["id"] for item in data]
    display_names = {item["id"]: item["display_name"] for item in data}

    assert "anthropic/open_router/big-model[1m]" in ids
    assert (
        display_names["anthropic/open_router/big-model[1m]"]
        == "open_router/big-model (1M context)"
    )
    # The sub-1M model gets no [1m] variant.
    assert "anthropic/open_router/small-model[1m]" not in ids


def test_models_list_advertises_one_m_variant_for_manual_override():
    app = create_app(lifespan_enabled=False)
    settings = _settings(
        model="deepseek/deepseek-v4-pro", model_opus=None, model_haiku=None
    )
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    # deepseek/deepseek-v4-pro is a manual 1M override (catalog), no registry needed.
    assert "anthropic/deepseek/deepseek-v4-pro[1m]" in ids


def test_models_list_works_without_provider_registry():
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids[:4] == [
        "anthropic/deepseek/deepseek-chat",
        "claude-3-freecc-no-thinking/deepseek/deepseek-chat",
        "anthropic/open_router/anthropic/claude-opus",
        "claude-3-freecc-no-thinking/open_router/anthropic/claude-opus",
    ]
    assert "claude-sonnet-4-20250514" in ids
