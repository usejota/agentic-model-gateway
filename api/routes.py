"""FastAPI route handlers."""

import inspect

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger

from config.provider_catalog import ONE_M_CONTEXT, resolve_context_window
from config.settings import Settings
from core.anthropic import get_token_count
from core.delegates import build_delegate_catalog
from core.trace import trace_event
from providers.registry import ProviderRegistry

from . import dependencies
from .dependencies import get_settings, require_api_key
from .gateway_model_ids import (
    ONE_M_SUFFIX,
    gateway_model_id,
    no_thinking_gateway_model_id,
    one_m_gateway_model_id,
)
from .models.anthropic import MessagesRequest, TokenCountRequest
from .models.responses import ModelResponse, ModelsListResponse
from .services import ClaudeProxyService, _normalize_model_ref

router = APIRouter()

DISCOVERED_MODEL_CREATED_AT = "1970-01-01T00:00:00Z"


SUPPORTED_CLAUDE_MODELS = [
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
    ModelResponse(
        id="claude-fable-5",
        display_name="Fable",
        created_at="2025-05-14T00:00:00Z",
    ),
]


def get_proxy_service(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ClaudeProxyService:
    """Build the request service for route handlers."""
    return ClaudeProxyService(
        settings,
        provider_getter=lambda provider_type: dependencies.resolve_provider(
            provider_type, app=request.app, settings=settings
        ),
        token_counter=get_token_count,
    )


def _probe_response(allow: str) -> Response:
    """Return an empty success response for compatibility probes."""
    return Response(status_code=204, headers={"Allow": allow})


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


def _context_window_for_ref(
    provider_model_ref: str, provider_registry: ProviderRegistry | None
) -> int:
    """Resolve a prefixed ``provider/model`` ref's context window (override > auto > 200K)."""
    auto_lookup = (
        provider_registry.cached_context_window
        if provider_registry is not None
        else None
    )
    return resolve_context_window(
        Settings.parse_provider_type(provider_model_ref),
        Settings.parse_model_name(provider_model_ref),
        auto_lookup=auto_lookup,
    )


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
    # ModelRouter strips it before forwarding upstream.
    if context_window >= ONE_M_CONTEXT:
        _append_unique_model(
            models,
            seen,
            _discovered_model_response(
                one_m_gateway_model_id(provider_model_ref),
                display_name=f"{provider_model_ref} (1M context)",
            ),
        )


def _append_alias_1m_if_override_supports(
    models: list[ModelResponse],
    seen: set[str],
    alias: str,
    override_ref: str | None,
    provider_registry: ProviderRegistry | None,
) -> None:
    """Advertise ``<alias>[1m]`` when the configured override has 1M context.

    Lets the user pick the familiar Claude alias (Opus/Sonnet/Fable) with the
    ``[1m]`` signal Claude Code's ``has1mContext()`` recognises, while the
    override routes to a cheap 1M-capable provider/model under the hood.
    Haiku is intentionally excluded upstream (the caller skips it).
    """
    if override_ref is None:
        return
    if _context_window_for_ref(override_ref, provider_registry) < ONE_M_CONTEXT:
        return
    _append_unique_model(
        models,
        seen,
        _discovered_model_response(
            f"{alias}{ONE_M_SUFFIX}",
            display_name=f"{alias.replace('claude-', 'Claude ').replace('-20250514', '').replace('-5', ' 5').strip()} (1M context)",
        ),
    )


def _build_models_list_response(
    settings: Settings, provider_registry: ProviderRegistry | None
) -> ModelsListResponse:
    models: list[ModelResponse] = []
    seen: set[str] = set()

    for ref in settings.configured_chat_model_refs():
        supports_thinking = None
        if provider_registry is not None:
            supports_thinking = provider_registry.cached_model_supports_thinking(
                ref.provider_id, ref.model_id
            )
        _append_provider_model_variants(
            models,
            seen,
            ref.model_ref,
            supports_thinking=supports_thinking,
            context_window=_context_window_for_ref(ref.model_ref, provider_registry),
        )

    if provider_registry is None:
        registry_infos: list = []
    else:
        registry_infos = list(provider_registry.cached_prefixed_model_infos())

    # When the user has configured a Fable override, the bare ``claude-fable-5``
    # is the fictional alias they pick in the picker (it routes through
    # MODEL_FABLE in ModelRouter). The real ``open_router/anthropic/claude-fable-5``
    # from the registry is the costly upstream — hide it so a casual pick
    # doesn't bypass the override.
    if settings.model_fable is not None:
        registry_infos = [
            info
            for info in registry_infos
            if not info.model_id.endswith("/anthropic/claude-fable-5")
            and not info.model_id.endswith("~anthropic/claude-fable-latest")
        ]

    for model_info in registry_infos:
        _append_provider_model_variants(
            models,
            seen,
            model_info.model_id,
            supports_thinking=model_info.supports_thinking,
            context_window=_context_window_for_ref(
                model_info.model_id, provider_registry
            ),
        )

    for model in SUPPORTED_CLAUDE_MODELS:
        # Fable is a fictional alias with no built-in Claude Code catalog entry.
        # When the override can serve 1M, advertise the alias WITH the [1m]
        # suffix (same "Fable" display name) so picking "Fable" just works —
        # instead of listing a bare 200K row next to a separate 1M row.
        if (
            model.id == "claude-fable-5"
            and settings.model_fable is not None
            and _context_window_for_ref(settings.model_fable, provider_registry)
            >= ONE_M_CONTEXT
        ):
            model = ModelResponse(
                id=f"{model.id}{ONE_M_SUFFIX}",
                display_name=model.display_name,
                created_at=model.created_at,
            )
        _append_unique_model(models, seen, model)

    # When a Claude alias (Opus / Sonnet) is overridden to a 1M-capable
    # model, advertise a ``<alias>[1m]`` variant so the picker shows the
    # familiar Claude name with the 1M context signal intact. (Haiku is excluded
    # by design: cheap model, no 1M window. Fable is handled above — it
    # substitutes the bare id in-place rather than adding a separate row.)
    _append_alias_1m_if_override_supports(
        models, seen, "claude-opus-4-20250514", settings.model_opus, provider_registry
    )
    _append_alias_1m_if_override_supports(
        models,
        seen,
        "claude-sonnet-4-20250514",
        settings.model_sonnet,
        provider_registry,
    )

    return ModelsListResponse(
        data=models,
        first_id=models[0].id if models else None,
        has_more=False,
        last_id=models[-1].id if models else None,
    )


# =============================================================================
# Routes
# =============================================================================
@router.post("/v1/messages")
async def create_message(
    request_data: MessagesRequest,
    service: ClaudeProxyService = Depends(get_proxy_service),
    _auth=Depends(require_api_key),
):
    """Create a message. Streams by default; honors ``stream: false`` with an
    aggregated non-streaming Messages JSON response (awaitable in that case)."""
    result = service.create_message(request_data)
    if inspect.isawaitable(result):
        return await result
    return result


@router.api_route("/v1/messages", methods=["HEAD", "OPTIONS"])
async def probe_messages(_auth=Depends(require_api_key)):
    """Respond to Claude compatibility probes for the messages endpoint."""
    return _probe_response("POST, HEAD, OPTIONS")


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request_data: TokenCountRequest,
    service: ClaudeProxyService = Depends(get_proxy_service),
    _auth=Depends(require_api_key),
):
    """Count tokens for a request."""
    return service.count_tokens(request_data)


@router.api_route("/v1/messages/count_tokens", methods=["HEAD", "OPTIONS"])
async def probe_count_tokens(_auth=Depends(require_api_key)):
    """Respond to Claude compatibility probes for the token count endpoint."""
    return _probe_response("POST, HEAD, OPTIONS")


@router.get("/")
async def root(
    settings: Settings = Depends(get_settings), _auth=Depends(require_api_key)
):
    """Root endpoint."""
    return {
        "status": "ok",
        "provider": settings.provider_type,
        "model": settings.model,
    }


@router.api_route("/", methods=["HEAD", "OPTIONS"])
async def probe_root():
    """Respond to unauthenticated local compatibility probes for the root endpoint."""
    return _probe_response("GET, HEAD, OPTIONS")


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@router.api_route("/health", methods=["HEAD", "OPTIONS"])
async def probe_health():
    """Respond to compatibility probes for the health endpoint."""
    return _probe_response("GET, HEAD, OPTIONS")


@router.get("/v1/models", response_model=ModelsListResponse)
async def list_models(
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
):
    """List the model ids this proxy advertises to Claude-compatible clients."""
    trace_event(stage="ingress", event="api.models.list", source="api")
    registry = getattr(request.app.state, "provider_registry", None)
    provider_registry = registry if isinstance(registry, ProviderRegistry) else None
    return _build_models_list_response(settings, provider_registry)


@router.get("/v1/models/delegates")
async def list_delegate_models(
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
):
    """List no-thinking gateway model ids available for claudim native delegates.

    The catalog is the union of ``MODEL_DELEGATE_ALLOWLIST`` (free delegates)
    and ``MODEL_DELEGATE_APPROVAL`` (human-gated). Both empty = empty catalog
    (no delegates). ``/v1/models`` is intentionally NOT filtered — the human
    model picker still sees every model.
    """
    trace_event(stage="ingress", event="api.models.delegates", source="api")
    registry = getattr(request.app.state, "provider_registry", None)
    provider_registry = registry if isinstance(registry, ProviderRegistry) else None
    refs: list[str] = []
    seen: set[str] = set()
    for configured in settings.configured_chat_model_refs():
        if configured.model_ref not in seen:
            seen.add(configured.model_ref)
            refs.append(configured.model_ref)
    if provider_registry is not None:
        for info in provider_registry.cached_prefixed_model_infos():
            if info.model_id not in seen:
                seen.add(info.model_id)
                refs.append(info.model_id)
    return build_delegate_catalog(
        refs,
        approvals=settings.model_delegate_approval,
        allowlist=settings.model_delegate_allowlist,
        model_id_for_ref=no_thinking_gateway_model_id,
        normalize_ref=_normalize_model_ref,
    )


@router.post("/stop")
async def stop_cli(request: Request, _auth=Depends(require_api_key)):
    """Stop all CLI sessions and pending tasks."""
    handler = getattr(request.app.state, "message_handler", None)
    if not handler:
        # Fallback if messaging not initialized
        cli_manager = getattr(request.app.state, "cli_manager", None)
        if cli_manager:
            await cli_manager.stop_all()
            logger.info("STOP_CLI: source=cli_manager cancelled_count=N/A")
            return {"status": "stopped", "source": "cli_manager"}
        raise HTTPException(status_code=503, detail="Messaging system not initialized")

    count = await handler.stop_all_tasks()
    trace_event(
        stage="ingress",
        event="api.cli.stop_via_handler",
        source="api",
        cancelled_nodes=count,
    )
    logger.info("STOP_CLI: source=handler cancelled_count={}", count)
    return {"status": "stopped", "cancelled_count": count}
