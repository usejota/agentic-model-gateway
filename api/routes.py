"""FastAPI route handlers."""

import fnmatch
import inspect

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger

from config.provider_catalog import ONE_M_CONTEXT, resolve_context_window
from config.settings import Settings
from core.anthropic import get_token_count
from core.trace import trace_event
from providers.registry import ProviderRegistry

from . import dependencies
from .dependencies import get_settings, require_api_key
from .gateway_model_ids import (
    gateway_model_id,
    no_thinking_gateway_model_id,
    one_m_gateway_model_id,
)
from .models.anthropic import MessagesRequest, TokenCountRequest
from .models.responses import ModelResponse, ModelsListResponse
from .services import ClaudeProxyService

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

    if provider_registry is not None:
        for model_info in provider_registry.cached_prefixed_model_infos():
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
        _append_unique_model(models, seen, model)

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


# Vendors whose models are excluded from claudim delegate discovery. Sourced
# verbatim from the launcher's former US_CLOSED set (deploy/claudim) so the
# gateway is the single source of truth for the non-American filter.
US_CLOSED_VENDORS = frozenset(
    {
        "openai",
        "anthropic",
        # Google: "gemini" matches direct refs (gemini/gemini-*) and
        # gateway-routed (claude-3-freecc-no-thinking/gemini/...); "google"
        # matches open_router-routed google models (open_router/google/...).
        "gemini",
        "google",
        "x-ai",
        "amazon",
        "nvidia",
        "ibm-granite",
        "liquid",
        "rekaai",
        "relace",
    }
)


def _delegate_vendor(ref: str) -> str:
    """Return the vendor segment of a ``provider/vendor/model`` (or ``vendor/model``) ref."""
    parts = ref.split("/")
    vendor = parts[1] if len(parts) >= 3 else parts[0]
    return vendor.lstrip("~")


def _build_delegate_model_ids(
    settings: Settings, provider_registry: ProviderRegistry | None
) -> list[str]:
    """Build the flat list of no-thinking gateway ids available for claudim delegates.

    Sources match ``_build_models_list_response`` (configured refs + discovered
    prefixed infos), deduped order-preserving. US-closed vendors and refs
    matching ``settings.model_delegate_exclusions`` (fnmatch) are skipped.
    """
    refs: list[str] = []
    seen: set[str] = set()

    for ref in settings.configured_chat_model_refs():
        if ref.model_ref not in seen:
            seen.add(ref.model_ref)
            refs.append(ref.model_ref)

    if provider_registry is not None:
        for model_info in provider_registry.cached_prefixed_model_infos():
            if model_info.model_id not in seen:
                seen.add(model_info.model_id)
                refs.append(model_info.model_id)

    exclusions = settings.model_delegate_exclusions
    ids: list[str] = []
    for ref in refs:
        if _delegate_vendor(ref) in US_CLOSED_VENDORS:
            continue
        if any(fnmatch.fnmatchcase(ref, pattern) for pattern in exclusions):
            continue
        ids.append(no_thinking_gateway_model_id(ref))
    return ids


@router.get("/v1/models/delegates")
async def list_delegate_models(
    request: Request,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_api_key),
):
    """List no-thinking gateway model ids available for claudim native delegates.

    Excludes US-closed vendors and admin-configured ``MODEL_DELEGATE_EXCLUSIONS``
    patterns. ``/v1/models`` is intentionally NOT filtered — the human model
    picker still sees every model.
    """
    trace_event(stage="ingress", event="api.models.delegates", source="api")
    registry = getattr(request.app.state, "provider_registry", None)
    provider_registry = registry if isinstance(registry, ProviderRegistry) else None
    return {"data": _build_delegate_model_ids(settings, provider_registry)}


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
