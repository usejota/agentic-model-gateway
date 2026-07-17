"""Model-list response construction for Claude-compatible clients."""

from typing import Literal

from pydantic import BaseModel

from free_claude_code.application.ports import RequestRuntimePort
from free_claude_code.config.model_refs import (
    configured_chat_model_refs,
    parse_model_name,
    parse_provider_type,
)
from free_claude_code.config.provider_catalog import (
    ONE_M_CONTEXT,
    resolve_context_window,
)
from free_claude_code.config.settings import Settings
from free_claude_code.core.gateway_model_ids import (
    ONE_M_SUFFIX,
    gateway_model_id,
    no_thinking_gateway_model_id,
    one_m_gateway_model_id,
)

DISCOVERED_MODEL_CREATED_AT = "1970-01-01T00:00:00Z"


class ModelResponse(BaseModel):
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "free-claude-code"
    created_at: str
    display_name: str
    id: str
    type: Literal["model"] = "model"


class ModelsListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelResponse]
    first_id: str | None
    has_more: bool
    last_id: str | None


SUPPORTED_CLAUDE_MODELS = [
    ModelResponse(
        id="claude-fable-5",
        display_name="Claude Fable 5",
        created_at="2026-06-09T00:00:00Z",
    ),
    ModelResponse(
        id="claude-opus-4-20250514",
        display_name="Claude Opus 4",
        created_at="2025-05-14T00:00:00Z",
    ),
    ModelResponse(
        id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4",
        created_at="2025-05-14T00:00:00Z",
    ),
    ModelResponse(
        id="claude-haiku-4-20250514",
        display_name="Claude Haiku 4",
        created_at="2025-05-14T00:00:00Z",
    ),
    ModelResponse(
        id="claude-3-opus-20240229",
        display_name="Claude 3 Opus",
        created_at="2024-02-29T00:00:00Z",
    ),
    ModelResponse(
        id="claude-3-5-sonnet-20241022",
        display_name="Claude 3.5 Sonnet",
        created_at="2024-10-22T00:00:00Z",
    ),
    ModelResponse(
        id="claude-3-haiku-20240307",
        display_name="Claude 3 Haiku",
        created_at="2024-03-07T00:00:00Z",
    ),
    ModelResponse(
        id="claude-3-5-haiku-20241022",
        display_name="Claude 3.5 Haiku",
        created_at="2024-10-22T00:00:00Z",
    ),
]


# Claude picker aliases that also get a ``<alias>[1m]`` entry when the
# configured override (MODEL_FABLE/OPUS/SONNET) is 1M-capable. Haiku is
# intentionally excluded — it is the small/fast tier.
_ONE_M_ALIAS_OVERRIDES = (
    ("claude-fable-5", "Claude Fable 5", "model_fable"),
    ("claude-opus-4-20250514", "Claude Opus 4", "model_opus"),
    ("claude-sonnet-4-20250514", "Claude Sonnet 4", "model_sonnet"),
)


def build_models_list_response(
    settings: Settings, runtime: RequestRuntimePort
) -> ModelsListResponse:
    """Return configured, cached, and compatibility model ids."""
    models: list[ModelResponse] = []
    seen: set[str] = set()

    cached_infos = runtime.cached_prefixed_model_infos()
    context_by_key = {
        (parse_provider_type(info.model_id), parse_model_name(info.model_id)): (
            info.context_window
        )
        for info in cached_infos
        if info.context_window
    }

    def auto_lookup(provider_id: str, provider_model: str) -> int | None:
        return context_by_key.get((provider_id, provider_model))

    def context_window_for(provider_model_ref: str) -> int:
        return resolve_context_window(
            parse_provider_type(provider_model_ref),
            parse_model_name(provider_model_ref),
            auto_lookup=auto_lookup,
        )

    for ref in configured_chat_model_refs(settings):
        supports_thinking = runtime.cached_model_supports_thinking(
            ref.provider_id, ref.model_id
        )
        _append_provider_model_variants(
            models,
            seen,
            ref.model_ref,
            supports_thinking=supports_thinking,
            context_window=context_window_for(ref.model_ref),
        )
    # Configured routes stay at the very top of the catalog: Claude Code's
    # picker surfaces the first gateway entries, so the models the user
    # explicitly configured must lead the list (pre-#13 fork behavior).
    configured_ids = [model.id for model in models]

    # When a Fable override is configured, the bare ``claude-fable-5`` alias is
    # what users should pick (it routes through MODEL_FABLE). Hide the real
    # upstream ``anthropic/claude-fable-5`` so a casual pick doesn't bypass the
    # override onto the costly first-party model.
    if settings.model_fable is not None:
        cached_infos = [
            info
            for info in cached_infos
            if not info.model_id.endswith("/anthropic/claude-fable-5")
            and not info.model_id.endswith("~anthropic/claude-fable-latest")
        ]

    for model_info in cached_infos:
        _append_provider_model_variants(
            models,
            seen,
            model_info.model_id,
            supports_thinking=model_info.supports_thinking,
            context_window=context_window_for(model_info.model_id),
        )

    for alias, display_name, override_attr in _ONE_M_ALIAS_OVERRIDES:
        override_ref = getattr(settings, override_attr)
        if override_ref and context_window_for(override_ref) >= ONE_M_CONTEXT:
            _append_unique_model(
                models,
                seen,
                _discovered_model_response(
                    f"{alias}{ONE_M_SUFFIX}",
                    display_name=f"{display_name} (1M context)",
                ),
            )

    for model in SUPPORTED_CLAUDE_MODELS:
        _append_unique_model(models, seen, model)

    configured_id_set = frozenset(configured_ids)
    pinned_order = [
        model_id
        for model_id in _resolve_pinned_order(seen)
        if model_id not in configured_id_set
    ]
    pinned_ids = frozenset(pinned_order)
    configured = [m for m in models if m.id in configured_id_set]
    pinned = [m for m in models if m.id in pinned_ids]
    pinned.sort(key=lambda m: pinned_order.index(m.id))
    remaining = [
        m for m in models if m.id not in pinned_ids and m.id not in configured_id_set
    ]
    remaining.sort(key=lambda m: (m.display_name.casefold(), m.id))
    models = configured + pinned + remaining

    return ModelsListResponse(
        data=models,
        first_id=models[0].id if models else None,
        has_more=False,
        last_id=models[-1].id if models else None,
    )


def _resolve_pinned_order(seen: set[str]) -> list[str]:
    pinned: list[str] = []
    if "claude-opus-4-20250514[1m]" in seen:
        pinned.append("claude-opus-4-20250514[1m]")
    elif "claude-opus-4-20250514" in seen:
        pinned.append("claude-opus-4-20250514")
    if "claude-fable-5[1m]" in seen:
        pinned.append("claude-fable-5[1m]")
    elif "claude-fable-5" in seen:
        pinned.append("claude-fable-5")
    if "claude-sonnet-4-20250514" in seen:
        pinned.append("claude-sonnet-4-20250514")
    if "claude-sonnet-4-20250514[1m]" in seen:
        pinned.append("claude-sonnet-4-20250514[1m]")
    if "claude-haiku-4-20250514" in seen:
        pinned.append("claude-haiku-4-20250514")
    return pinned


def _discovered_model_response(model_id: str, *, display_name: str) -> ModelResponse:
    return ModelResponse(
        id=model_id,
        display_name=display_name,
        created_at=DISCOVERED_MODEL_CREATED_AT,
    )


def _append_unique_model(
    models: list[ModelResponse], seen: set[str], model: ModelResponse
) -> None:
    if model.id in seen:
        return
    seen.add(model.id)
    models.append(model)


def _append_provider_model_variants(
    models: list[ModelResponse],
    seen: set[str],
    provider_model_ref: str,
    *,
    supports_thinking: bool | None = None,
    context_window: int = 0,
) -> None:
    if supports_thinking is not False:
        _append_unique_model(
            models,
            seen,
            _discovered_model_response(
                gateway_model_id(provider_model_ref),
                display_name=provider_model_ref,
            ),
        )
    _append_unique_model(
        models,
        seen,
        _discovered_model_response(
            no_thinking_gateway_model_id(provider_model_ref),
            display_name=f"{provider_model_ref} (no thinking)",
        ),
    )
    # 1M-capable models also get a [1m]-suffixed thinking variant so Claude Code's
    # has1mContext() reports 1M. The suffix lives on the client-facing id only;
    # the router strips it before forwarding upstream.
    if context_window >= ONE_M_CONTEXT:
        _append_unique_model(
            models,
            seen,
            _discovered_model_response(
                one_m_gateway_model_id(provider_model_ref),
                display_name=f"{provider_model_ref} (1M context)",
            ),
        )
