"""Application services for the Claude-compatible API."""

from __future__ import annotations

import fnmatch
import traceback
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from config.settings import Settings
from core.anthropic import get_token_count, get_user_facing_error_message
from core.anthropic.aggregate import aggregate_sse_to_message
from core.anthropic.image_detection import has_images
from core.anthropic.sse import ANTHROPIC_SSE_RESPONSE_HEADERS
from core.delegates import ref_in_catalog_union
from core.trace import api_messages_request_snapshot, trace_event, traced_async_stream
from providers.base import BaseProvider
from providers.exceptions import InvalidRequestError, OverloadedError, ProviderError

from .gateway_model_ids import ONE_M_SUFFIX, decode_gateway_model_id
from .model_router import ModelRouter, RoutedMessagesRequest
from .models.anthropic import MessagesRequest, TokenCountRequest
from .models.responses import TokenCountResponse
from .optimization_handlers import try_optimizations
from .web_tools.egress import WebFetchEgressPolicy
from .web_tools.request import (
    is_web_server_tool_request,
    openai_chat_upstream_server_tool_error,
)
from .web_tools.streaming import stream_web_server_tool_response

TokenCounter = Callable[[list[Any], str | list[Any] | None, list[Any] | None], int]

ProviderGetter = Callable[[str], BaseProvider]

# Providers that use ``/chat/completions`` + Anthropic-to-OpenAI conversion (not native Messages).
_OPENAI_CHAT_UPSTREAM_IDS = frozenset({"nvidia_nim", "opencode", "opencode_go"})


def anthropic_sse_streaming_response(
    body: AsyncIterator[str],
) -> StreamingResponse:
    """Return a :class:`StreamingResponse` for Anthropic-style SSE streams."""
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers=ANTHROPIC_SSE_RESPONSE_HEADERS,
    )


def _http_status_for_unexpected_service_exception(_exc: BaseException) -> int:
    """HTTP status for uncaught non-provider failures (stable client contract)."""
    return 500


def _log_unexpected_service_exception(
    settings: Settings,
    exc: BaseException,
    *,
    context: str,
    request_id: str | None = None,
) -> None:
    """Log service-layer failures without echoing exception text unless opted in."""
    if settings.log_api_error_tracebacks:
        if request_id is not None:
            logger.error("{} request_id={}: {}", context, request_id, exc)
        else:
            logger.error("{}: {}", context, exc)
        logger.error(traceback.format_exc())
        return
    if request_id is not None:
        logger.error(
            "{} request_id={} exc_type={}",
            context,
            request_id,
            type(exc).__name__,
        )
    else:
        logger.error("{} exc_type={}", context, type(exc).__name__)


def _require_non_empty_messages(messages: list[Any]) -> None:
    if not messages:
        raise InvalidRequestError("messages cannot be empty")


# Markers identifying Claude Code's MAIN conversation loop (the model the human
# drives via /model). Subagent (Agent tool) requests carry the agent's own
# prompt instead. Multiple markers because output styles REPLACE the CLI's
# default system prompt (dropping "You are Claude Code"), while the launcher's
# --append-system-prompt sentinel survives any output style — it is appended to
# the main loop only, never to subagent prompts.
#
# The primary sentinel is name-agnostic ("the model gateway session") so the
# launcher is renameable: a binary installed as `claudim`, `loclaudim`, or any
# other name emits the same string and the gateway matches it. The legacy
# "You are inside claudim (gateway session)" marker is kept so already-deployed
# launchers that still emit the old (name-bearing) sentinel keep matching —
# renameability without breaking retro-compat.
_MAIN_LOOP_MARKERS = (
    "You are Claude Code",
    "You are inside the model gateway session",
    "You are inside claudim (gateway session)",
)


def _system_prompt_texts(system: Any) -> list[str]:
    """Return every text block of an Anthropic ``system`` value."""
    if isinstance(system, str):
        return [system]
    if isinstance(system, list):
        texts = []
        for block in system:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                texts.append(text)
        return texts
    return []


def _is_main_loop_request(request: MessagesRequest) -> bool:
    return any(
        marker in text
        for text in _system_prompt_texts(request.system)
        for marker in _MAIN_LOOP_MARKERS
    )


def _normalize_model_ref(ref: str) -> str:
    """Reduce a gateway model id (or bare ref) to its canonical provider/model ref.

    Both thinking (``anthropic/...``) and no-thinking
    (``claude-3-freecc-no-thinking/...``) advertised ids, with or without the
    ``[1m]`` suffix, reduce to the same ``provider/model`` form. A subagent
    can't escape an exclusion by picking the thinking variant of an excluded
    model: both variants normalize identically, so an exclusion written against
    one matches the other.
    """
    decoded = decode_gateway_model_id(ref)
    if decoded is not None:
        return f"{decoded.provider_id}/{decoded.provider_model}"
    if ref.endswith(ONE_M_SUFFIX):
        ref = ref[: -len(ONE_M_SUFFIX)]
    return ref


def _ref_matches_exclusions(settings: Settings, provider_model_ref: str) -> bool:
    """True if ``provider_model_ref`` matches any ``MODEL_DELEGATE_EXCLUSIONS`` glob.

    Both the ref and each pattern are normalized first, so a pattern written as
    a full advertised id (e.g. ``claude-3-freecc-no-thinking/gemini/*``) matches
    the thinking variant (``anthropic/gemini/...``) after both reduce to
    ``gemini/...``.
    """
    exclusions = settings.model_delegate_exclusions
    if not exclusions:
        return False
    ref = _normalize_model_ref(provider_model_ref)
    return any(
        fnmatch.fnmatchcase(ref, _normalize_model_ref(pattern))
        for pattern in exclusions
    )


def _enforce_delegate_exclusions(
    settings: Settings, request: MessagesRequest, provider_model_ref: str
) -> None:
    """Hard-block excluded models for subagent requests.

    ``MODEL_DELEGATE_EXCLUSIONS`` patterns are matched (fnmatch) against the
    resolved provider/model ref. A match is rejected unless the request is
    Claude Code's main conversation loop (system prompt opens with the CLI
    marker) — i.e. the human /model picker keeps working; Agent-tool subagents
    (and any other side-channel request) cannot use excluded models.
    """
    if not _ref_matches_exclusions(settings, provider_model_ref):
        return
    if _is_main_loop_request(request):
        return
    raise InvalidRequestError(
        f"model '{_normalize_model_ref(provider_model_ref)}' is excluded for "
        "subagents by MODEL_DELEGATE_EXCLUSIONS; pick a delegate model from "
        "/v1/models/delegates"
    )


def _enforce_delegate_allowlist(
    settings: Settings, request: MessagesRequest, provider_model_ref: str
) -> None:
    """Hard-block subagent models outside the allowlist + approval union.

    When ``MODEL_DELEGATE_ALLOWLIST`` is configured, only models the catalog
    would admit (allowlist matches on open vendors, plus any
    ``MODEL_DELEGATE_APPROVAL`` matches) are permitted for subagent requests.
    Uses the same matching as ``build_delegate_catalog`` so the gateway and
    the ``/v1/models/delegates`` endpoint never disagree. Main-loop requests
    are never blocked.
    """
    if not settings.model_delegate_allowlist:
        return
    if _is_main_loop_request(request):
        return
    if ref_in_catalog_union(
        _normalize_model_ref(provider_model_ref),
        allowlist=settings.model_delegate_allowlist,
        approvals=settings.model_delegate_approval,
        normalize_ref=_normalize_model_ref,
    ):
        return
    raise InvalidRequestError(
        f"model '{_normalize_model_ref(provider_model_ref)}' is not in the "
        "delegate allowlist; set MODEL_DELEGATE_ALLOWLIST or add this model to it"
    )


class ClaudeProxyService:
    """Coordinate request optimization, model routing, token count, and providers."""

    def __init__(
        self,
        settings: Settings,
        provider_getter: ProviderGetter,
        model_router: ModelRouter | None = None,
        token_counter: TokenCounter = get_token_count,
    ):
        self._settings = settings
        self._provider_getter = provider_getter
        self._model_router = model_router or ModelRouter(settings)
        self._token_counter = token_counter

    def create_message(self, request_data: MessagesRequest) -> object:
        """Create a message response or streaming response."""
        try:
            _require_non_empty_messages(request_data.messages)

            routed = self._model_router.resolve_messages_request(request_data)
            # Reroute image-bearing requests to the vision model BEFORE
            # enforcing delegate exclusions: a subagent image turn whose
            # primary model is excluded would otherwise be rejected (400)
            # instead of rerouted to the non-excluded IMAGE_ROUTE.
            routed = self._maybe_reroute_for_images(routed)
            _enforce_delegate_exclusions(
                self._settings, request_data, routed.resolved.provider_model_ref
            )
            _enforce_delegate_allowlist(
                self._settings, request_data, routed.resolved.provider_model_ref
            )
            if routed.resolved.provider_id in _OPENAI_CHAT_UPSTREAM_IDS:
                tool_err = openai_chat_upstream_server_tool_error(
                    routed.request,
                    web_tools_enabled=self._settings.enable_web_server_tools,
                )
                if tool_err is not None:
                    raise InvalidRequestError(tool_err)

            if self._settings.enable_web_server_tools and is_web_server_tool_request(
                routed.request
            ):
                input_tokens = self._token_counter(
                    routed.request.messages, routed.request.system, routed.request.tools
                )
                trace_event(
                    stage="routing",
                    event="api.optimization.web_server_tool",
                    source="api",
                    model=routed.request.model,
                )
                egress = WebFetchEgressPolicy(
                    allow_private_network_targets=self._settings.web_fetch_allow_private_networks,
                    allowed_schemes=self._settings.web_fetch_allowed_scheme_set(),
                )
                return anthropic_sse_streaming_response(
                    stream_web_server_tool_response(
                        routed.request,
                        input_tokens=input_tokens,
                        web_fetch_egress=egress,
                        verbose_client_errors=self._settings.log_api_error_tracebacks,
                    ),
                )

            optimized = try_optimizations(routed.request, self._settings)
            if optimized is not None:
                trace_event(
                    stage="routing",
                    event="api.optimization.short_circuit",
                    source="api",
                    model=routed.request.model,
                )
                return optimized
            logger.debug("No optimization matched, routing to provider")

            request_id = f"req_{uuid.uuid4().hex[:12]}"
            with logger.contextualize(request_id=request_id):
                trace_event(
                    stage="ingress",
                    event="api.request.received",
                    source="api",
                    message_count=len(routed.request.messages),
                    snapshot=api_messages_request_snapshot(routed.request),
                )

                if self._settings.log_raw_api_payloads:
                    logger.debug(
                        "FULL_PAYLOAD [{}]: {}", request_id, routed.request.model_dump()
                    )

                input_tokens = self._token_counter(
                    routed.request.messages,
                    routed.request.system,
                    routed.request.tools,
                )

                # Open the primary stream eagerly (preserves the original contract:
                # pre-stream/setup errors surface here, in this try -> HTTP 500).
                primary = self._open_stream(routed, input_tokens, request_id, index=0)

                # Wrap with cross-model fallback: if the primary stream errors with an
                # overload/5xx failure *before any output*, retry FALLBACK_MODELS in
                # order. Once any byte streams, we commit and pass through unchanged.
                fallbacks = self._fallback_candidates(request_data)
                streamed = self._stream_with_fallback(
                    primary, fallbacks, input_tokens, request_id
                )

                # Non-streaming unless the client EXPLICITLY asked to stream.
                # Per the Anthropic Messages spec, `stream` defaults to false: an
                # omitted `stream` means a single JSON response. The Anthropic SDK's
                # non-streaming `create()` omits the field entirely (it does not send
                # `stream:false`) — e.g. Claude Code's auto-mode safety classifier and
                # session-title side-queries, which then read `usage.input_tokens` off
                # one JSON body. Treating omitted as streaming returns SSE to them and
                # breaks the read ("undefined is not an object 'p.usage.input_tokens'"),
                # which makes auto mode fail-closed. So only explicit `stream: true`
                # streams; omitted / null / false aggregate to a Messages JSON body.
                if request_data.stream is not True:
                    return self._aggregate_response(streamed, routed.request.model)
                return anthropic_sse_streaming_response(streamed)

        except ProviderError:
            raise
        except Exception as e:
            _log_unexpected_service_exception(
                self._settings, e, context="CREATE_MESSAGE_ERROR"
            )
            raise HTTPException(
                status_code=_http_status_for_unexpected_service_exception(e),
                detail=get_user_facing_error_message(e),
            ) from e

    async def _aggregate_response(
        self, stream: AsyncIterator[str], model: str
    ) -> JSONResponse:
        """Collapse an SSE stream into a non-streaming Messages JSON response.

        Used for ``stream: false`` requests. Errors raised mid-stream (e.g.
        :class:`OverloadedError` from the fallback wrapper) propagate to the route's
        handler, preserving the streaming path's error contract.
        """
        message = await aggregate_sse_to_message(stream)
        if not message.get("model"):
            message["model"] = model
        return JSONResponse(content=message)

    def _fallback_candidates(
        self, request_data: MessagesRequest
    ) -> list[RoutedMessagesRequest]:
        """Resolve ``FALLBACK_MODELS`` into routed requests for the same payload.

        Each fallback ref is resolved through the same router as the primary, so a
        fallback can target any provider/model. Returns ``[]`` when unset (no-op).

        When the request carries image content, ``IMAGE_ROUTE`` is the dedicated
        vision target — fallbacks that would silently 400 upstream on an image
        are dropped from the chain so we don't waste preflight cycles on them.
        """
        candidates: list[RoutedMessagesRequest] = []
        image_route_target = self._settings.image_route_parts
        # A subagent request must not escape MODEL_DELEGATE_EXCLUSIONS via a
        # fallback: if the primary overloads/5xxes, an excluded fallback would
        # run anyway. Drop excluded candidates up front (the main loop is
        # exempt — the human /model picker can use any model).
        enforce_exclusions = bool(
            self._settings.model_delegate_exclusions
        ) and not _is_main_loop_request(request_data)
        for ref in self._settings.fallback_models:
            resolved = self._model_router.resolve(ref)
            routed = request_data.model_copy(deep=True)
            routed.model = resolved.provider_model
            # Skip text-only fallback candidates when the request has images.
            # The IMAGE_ROUTE (if set) is the dedicated vision path; a fallback
            # that doesn't accept images would 400 upstream, so don't waste a
            # preflight cycle on it.
            if (
                has_images(routed.messages)
                and image_route_target is not None
                and resolved.provider_id != image_route_target[0]
            ):
                continue
            if enforce_exclusions and _ref_matches_exclusions(
                self._settings, resolved.provider_model_ref
            ):
                continue
            candidates.append(RoutedMessagesRequest(request=routed, resolved=resolved))
        return candidates

    def _maybe_reroute_for_images(
        self, routed: RoutedMessagesRequest
    ) -> RoutedMessagesRequest:
        """Swap the primary provider to ``IMAGE_ROUTE`` when the request has images.

        No-op when ``IMAGE_ROUTE`` is unset (current behavior preserved) or the
        request carries no image content. When triggered, the request body is
        deep-copied and re-pointed to the IMAGE_ROUTE provider/model so the rest
        of the pipeline (preflight, dispatch, fallback) sees the new target.
        Messages pass through verbatim — the multimodal provider sees the real
        base64 source, no stripping.

        The contract is intentionally simple: ``IMAGE_ROUTE`` set + image in
        request → reroute. The user opts in by setting the var; if they want a
        vision-capable primary (e.g. Claude) to handle images themselves, they
        leave ``IMAGE_ROUTE`` unset and rely on whatever the primary picks. We
        don't introspect provider/model capabilities because the transport
        (``native_anthropic`` vs ``openai_chat``) doesn't predict whether the
        *model* accepts images — DeepSeek on the Anthropic-format transport is
        text-only, while OpenRouter routes to all kinds of models.
        """
        image_route = self._settings.image_route_parts
        if image_route is None:
            return routed
        if not has_images(routed.request.messages):
            return routed

        target_provider_id, target_model = image_route
        if (
            routed.resolved.provider_id == target_provider_id
            and routed.resolved.provider_model == target_model
        ):
            # Already targeting the exact image_route provider+model.
            return routed

        resolved = self._model_router.resolve(f"{target_provider_id}/{target_model}")
        new_request = routed.request.model_copy(deep=True)
        new_request.model = resolved.provider_model
        logger.info(
            "Image reroute: provider={} model={} (from provider={} model={})",
            resolved.provider_id,
            resolved.provider_model,
            routed.resolved.provider_id,
            routed.resolved.provider_model,
        )
        trace_event(
            stage="routing",
            event="api.route.image_reroute",
            source="api",
            provider_id=resolved.provider_id,
            provider_model=resolved.provider_model,
            original_provider_id=routed.resolved.provider_id,
            original_provider_model=routed.resolved.provider_model,
            gateway_model=routed.request.model,
        )
        return RoutedMessagesRequest(request=new_request, resolved=resolved)

    def _open_stream(
        self,
        routed: RoutedMessagesRequest,
        input_tokens: int,
        request_id: str,
        index: int,
    ) -> AsyncIterator[str]:
        """Preflight + open the traced provider stream for one routed candidate.

        Called synchronously so setup errors surface to the caller's try/except.
        The returned async iterator is lazy (provider body runs on first iterate).
        """
        provider = self._provider_getter(routed.resolved.provider_id)
        provider.preflight_stream(
            routed.request,
            thinking_enabled=routed.resolved.thinking_enabled,
        )
        trace_event(
            stage="routing",
            event="api.route.resolved",
            source="api",
            provider_id=routed.resolved.provider_id,
            provider_model=routed.resolved.provider_model,
            provider_model_ref=routed.resolved.provider_model_ref,
            gateway_model=routed.request.model,
            thinking_enabled=routed.resolved.thinking_enabled,
            fallback_index=index,
        )
        return traced_async_stream(
            provider.stream_response(
                routed.request,
                input_tokens=input_tokens,
                request_id=request_id,
                thinking_enabled=routed.resolved.thinking_enabled,
            ),
            stage="egress",
            source="api",
            complete_event="api.response.stream_completed",
            interrupted_event="api.response.stream_interrupted",
            chunk_event=None,
            extra={
                "request_id": request_id,
                "provider_id": routed.resolved.provider_id,
                "gateway_model": routed.request.model,
                "fallback_index": index,
            },
        )

    async def _stream_with_fallback(
        self,
        primary: AsyncIterator[str],
        fallbacks: list[RoutedMessagesRequest],
        input_tokens: int,
        request_id: str,
    ) -> AsyncIterator[str]:
        """Stream the primary; on a pre-output overload, fall back across models.

        The primary stream is tried first. If it raises :class:`OverloadedError`
        *before* yielding any output, each model in ``fallbacks`` is opened and
        tried in turn. Once any candidate produces its first event, that stream is
        passed through unchanged — we never switch mid-stream. If every candidate
        fails before producing output, the last error is re-raised so the original
        contract (and auto-mode's fail-closed behavior) is preserved.
        """
        candidate_streams: list[AsyncIterator[str] | None] = [primary]
        # Fallback streams are opened lazily (only if reached) so we don't preflight
        # backends we never use.
        last_error: BaseException | None = None

        for index in range(1 + len(fallbacks)):
            if index < len(candidate_streams):
                stream = candidate_streams[index]
            else:
                routed = fallbacks[index - 1]
                try:
                    stream = self._open_stream(
                        routed, input_tokens, request_id, index=index
                    )
                except OverloadedError as exc:
                    last_error = exc
                    self._trace_fallback(
                        routed, index, request_id, "preflight_overload"
                    )
                    continue

            if stream is None:
                continue

            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except OverloadedError as exc:
                last_error = exc
                label = "primary" if index == 0 else fallbacks[index - 1].request.model
                logger.warning(
                    "Model '{}' overloaded before output; trying fallback", label
                )
                if index >= 1:
                    self._trace_fallback(
                        fallbacks[index - 1], index, request_id, "stream_overload"
                    )
                continue

            yield first
            async for chunk in iterator:
                yield chunk
            return

        if last_error is not None:
            raise last_error
        raise OverloadedError("All configured models are unavailable")

    def _trace_fallback(
        self,
        routed: RoutedMessagesRequest,
        index: int,
        request_id: str,
        reason: str,
    ) -> None:
        trace_event(
            stage="routing",
            event="api.route.fallback",
            source="api",
            provider_id=routed.resolved.provider_id,
            provider_model=routed.resolved.provider_model,
            gateway_model=routed.request.model,
            fallback_index=index,
            reason=reason,
            request_id=request_id,
        )

    def count_tokens(self, request_data: TokenCountRequest) -> TokenCountResponse:
        """Count tokens for a request after applying configured model routing."""
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        with logger.contextualize(request_id=request_id):
            try:
                _require_non_empty_messages(request_data.messages)
                routed = self._model_router.resolve_token_count_request(request_data)
                tokens = self._token_counter(
                    routed.request.messages, routed.request.system, routed.request.tools
                )
                trace_event(
                    stage="routing",
                    event="api.route.resolved",
                    source="api",
                    kind="count_tokens",
                    provider_id=routed.resolved.provider_id,
                    provider_model=routed.resolved.provider_model,
                    provider_model_ref=routed.resolved.provider_model_ref,
                    gateway_model=routed.request.model,
                )
                trace_event(
                    stage="ingress",
                    event="api.count_tokens.completed",
                    source="api",
                    message_count=len(routed.request.messages),
                    input_tokens=tokens,
                    snapshot=api_messages_request_snapshot(routed.request),
                )
                return TokenCountResponse(input_tokens=tokens)
            except ProviderError:
                raise
            except Exception as e:
                _log_unexpected_service_exception(
                    self._settings,
                    e,
                    context="COUNT_TOKENS_ERROR",
                    request_id=request_id,
                )
                raise HTTPException(
                    status_code=_http_status_for_unexpected_service_exception(e),
                    detail=get_user_facing_error_message(e),
                ) from e
